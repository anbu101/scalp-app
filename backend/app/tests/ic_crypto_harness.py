#!/usr/bin/env python3
"""
ic_crypto_harness.py — BTC daily-expiry Iron Condor backtest (Phase A)
======================================================================
Standalone. Reads ~/.scalp-app/backtest/crypto_backtest.db built by
crypto_corpus.py. Touches NOTHING in the app trees. No network.

Trade model (locked decisions):
  D7  Entry 17:45 IST on D-1; scheduled exit 17:15 IST on expiry day.
  D10 Shorts by premium ratio vs ATM straddle; wings by premium ratio
      vs the short leg. Scale-invariant across the whole spot range.
  D11 Exits: MTM SL (sl_mult x credit), optional TP (tp_ratio x credit),
      else scheduled exit. MTM checked every minute of the 24h hold.
  D12 P&L in USD: contract_value BTC per contract x contracts.
  D13 FEE MODEL ASSUMPTION: per leg per fill,
          fee = min(taker_rate * spot_notional, premium_cap * premium)
      using product fields observed on Delta India (0.0001 / 0.035).
      VERIFY against the official fee schedule before trusting absolute
      P&L. Use --fee-mult 0 for gross figures.
  D14 Any day that cannot produce a complete, priced 4-leg condor is
      SKIPPED with a reason code and counted — never silently traded.

Usage:
  python3 ic_crypto_harness.py run
  python3 ic_crypto_harness.py run --short-ratio 0.20 --sl-mult 2.0 \
      --contracts 100 --csv trades.csv
"""

import argparse
import csv as csvmod
import datetime as dt
import os
import sqlite3
import sys

DB_PATH = os.path.expanduser("~/.scalp-app/backtest/crypto_backtest.db")
PRICE_SCALE = 100

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ENTRY_HM_IST = (17, 45)          # D-1 evening
EXIT_HM_IST = (17, 15)           # expiry day
EXPIRY_HM_IST = (17, 30)
SNAP_TOL_S = 600                 # entry snapshot: candle within 10 min

CONTRACT_VALUE = 0.001           # BTC per contract (BTC options, Delta India)
TAKER_RATE = 0.0001              # observed product field (D13 assumption)
PREMIUM_CAP = 0.035              # observed product field (D13 assumption)

SKIP_REASONS = [
    "NO_CHAIN", "NO_SPOT", "NO_ATM_PREMS", "NO_SHORT_CALL", "NO_SHORT_PUT",
    "NO_WING_CALL", "NO_WING_PUT", "SPARSE_PATH",
]


# ----------------------------------------------------------------------
# Time / symbol helpers
# ----------------------------------------------------------------------
def expiry_dt(ddmmyy: str) -> dt.datetime:
    d, m, y = int(ddmmyy[0:2]), int(ddmmyy[2:4]), 2000 + int(ddmmyy[4:6])
    return dt.datetime(y, m, d, *EXPIRY_HM_IST, tzinfo=IST)


def entry_epoch(ddmmyy: str) -> int:
    e = expiry_dt(ddmmyy) - dt.timedelta(days=1)
    return int(e.replace(hour=ENTRY_HM_IST[0], minute=ENTRY_HM_IST[1]).timestamp())


def exit_epoch(ddmmyy: str) -> int:
    e = expiry_dt(ddmmyy)
    return int(e.replace(hour=EXIT_HM_IST[0], minute=EXIT_HM_IST[1]).timestamp())


def sym(side: str, strike: int, ddmmyy: str) -> str:
    return f"{side}-BTC-{strike}-{ddmmyy}"


# ----------------------------------------------------------------------
# Corpus access
# ----------------------------------------------------------------------
def open_db() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"ERROR: corpus db not found at {DB_PATH}")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


def expiries(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT expiry_ddmmyy FROM expiry_chain").fetchall()
    # sort chronologically, not lexically
    return sorted((r[0] for r in rows),
                  key=lambda s: (s[4:6], s[2:4], s[0:2]))


def chain_strikes(conn, ddmmyy: str) -> list[int]:
    return sorted(r[0] for r in conn.execute(
        "SELECT strike FROM expiry_chain WHERE expiry_ddmmyy=?", (ddmmyy,)))


def entry_spot(conn, ddmmyy: str) -> float | None:
    ep = entry_epoch(ddmmyy)
    row = conn.execute(
        "SELECT close FROM perp_candles_1m WHERE symbol='BTCUSD' "
        "AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (ep, ep - SNAP_TOL_S)).fetchone()
    return row[0] if row else None


def mark_map(conn, symbol: str) -> dict[int, float]:
    """ts -> close (float) for the MARK series of one option symbol."""
    return {ts: c / PRICE_SCALE for ts, c in conn.execute(
        "SELECT ts, close FROM option_candles_1m "
        "WHERE symbol=? AND series='MARK' AND close IS NOT NULL",
        (symbol,))}


def snap(series: dict[int, float], epoch: int, tol: int = SNAP_TOL_S):
    """Close at-or-before epoch within tol, else None."""
    best = None
    for ts in range(epoch, epoch - tol - 60, -60):
        if ts in series:
            return series[ts]
    # fallback: linear scan (irregular ts alignment)
    cand = [ts for ts in series if epoch - tol <= ts <= epoch]
    if cand:
        best = series[max(cand)]
    return best


# ----------------------------------------------------------------------
# Leg selection (D10)
# ----------------------------------------------------------------------
def pick_legs(conn, ddmmyy: str, spot: float, short_ratio: float,
              wing_prem_ratio: float):
    """Returns dict(legs) or (None, reason)."""
    strikes = chain_strikes(conn, ddmmyy)
    if len(strikes) < 5:
        return None, "NO_CHAIN"
    ep = entry_epoch(ddmmyy)
    atm = min(strikes, key=lambda k: abs(k - spot))

    prem = {}          # (side, strike) -> entry premium

    def p(side, k):
        key = (side, k)
        if key not in prem:
            prem[key] = snap(mark_map(conn, sym(side, k, ddmmyy)), ep)
        return prem[key]

    c_atm, p_atm = p("C", atm), p("P", atm)
    if c_atm is None or p_atm is None:
        return None, "NO_ATM_PREMS"
    straddle = c_atm + p_atm
    target = short_ratio * straddle

    def pick_short(side, cands):
        best, bd = None, None
        for k in cands:
            v = p(side, k)
            if v is None:
                continue
            d = abs(v - target)
            if bd is None or d < bd:
                best, bd = k, d
        return best

    sc = pick_short("C", [k for k in strikes if k > spot])
    if sc is None:
        return None, "NO_SHORT_CALL"
    sp = pick_short("P", [k for k in strikes if k < spot])
    if sp is None:
        return None, "NO_SHORT_PUT"

    def pick_wing(side, short_k, outward):
        cap = wing_prem_ratio * p(side, short_k)
        cands = sorted((k for k in strikes
                        if (k > short_k if outward > 0 else k < short_k)),
                       reverse=(outward < 0))
        for k in cands:                      # nearest-outward first
            v = p(side, k)
            if v is not None and v <= cap:
                return k
        # none satisfied the cap: take the furthest priced strike (max protection
        # available in-corpus) — still a valid condor, just a fatter wing
        for k in reversed(cands):
            if p(side, k) is not None:
                return k
        return None

    wc = pick_wing("C", sc, +1)
    if wc is None:
        return None, "NO_WING_CALL"
    wp = pick_wing("P", sp, -1)
    if wp is None:
        return None, "NO_WING_PUT"

    legs = {
        "atm": atm, "straddle": straddle,
        "sc": sc, "sp": sp, "wc": wc, "wp": wp,
        "prem": {"sc": p("C", sc), "sp": p("P", sp),
                 "wc": p("C", wc), "wp": p("P", wp)},
    }
    return legs, None


# ----------------------------------------------------------------------
# Fees (D13 — flagged assumption)
# ----------------------------------------------------------------------
def leg_fee(spot: float, premium: float, contracts: int,
            fee_mult: float) -> float:
    per_contract = min(TAKER_RATE * spot * CONTRACT_VALUE,
                       PREMIUM_CAP * premium * CONTRACT_VALUE)
    return per_contract * contracts * fee_mult


# ----------------------------------------------------------------------
# Simulate one expiry
# ----------------------------------------------------------------------
def run_day(conn, ddmmyy: str, args):
    spot = entry_spot(conn, ddmmyy)
    if spot is None:
        return {"expiry": ddmmyy, "skip": "NO_SPOT"}
    legs, reason = pick_legs(conn, ddmmyy, spot,
                             args.short_ratio, args.wing_prem_ratio)
    if legs is None:
        return {"expiry": ddmmyy, "skip": reason}

    ep_in, ep_out = entry_epoch(ddmmyy), exit_epoch(ddmmyy)
    m_sc = mark_map(conn, sym("C", legs["sc"], ddmmyy))
    m_sp = mark_map(conn, sym("P", legs["sp"], ddmmyy))
    m_wc = mark_map(conn, sym("C", legs["wc"], ddmmyy))
    m_wp = mark_map(conn, sym("P", legs["wp"], ddmmyy))

    pr = legs["prem"]
    credit = pr["sc"] + pr["sp"] - pr["wc"] - pr["wp"]   # per 1 BTC
    if credit <= 0:
        return {"expiry": ddmmyy, "skip": "NO_CHAIN"}    # degenerate pricing

    # minute walk: combo(t) = Cs + Ps - Cw - Pw, ffill wings if a minute gap
    last = {"wc": pr["wc"], "wp": pr["wp"], "sc": pr["sc"], "sp": pr["sp"]}
    exit_reason, exit_ts, exit_combo = "TIME", ep_out, None
    n_path = 0
    worst = 0.0
    for ts in range(ep_in + 60, ep_out + 60, 60):
        for key, m in (("sc", m_sc), ("sp", m_sp), ("wc", m_wc), ("wp", m_wp)):
            if ts in m:
                last[key] = m[ts]
        combo = last["sc"] + last["sp"] - last["wc"] - last["wp"]
        pnl = credit - combo
        worst = min(worst, pnl)
        n_path += 1
        if pnl <= -args.sl_mult * credit:
            exit_reason, exit_ts, exit_combo = "SL", ts, combo
            break
        if args.tp_ratio > 0 and pnl >= args.tp_ratio * credit:
            exit_reason, exit_ts, exit_combo = "TP", ts, combo
            break
    if exit_combo is None:
        # scheduled exit: use last-known marks at/before ep_out
        for key, m in (("sc", m_sc), ("sp", m_sp), ("wc", m_wc), ("wp", m_wp)):
            v = snap(m, ep_out)
            if v is not None:
                last[key] = v
        exit_combo = last["sc"] + last["sp"] - last["wc"] - last["wp"]

    expected = (ep_out - ep_in) // 60
    if n_path < expected * 0.5 and exit_reason == "TIME":
        return {"expiry": ddmmyy, "skip": "SPARSE_PATH"}

    pnl_unit = credit - exit_combo                       # per 1 BTC
    usd_gross = pnl_unit * CONTRACT_VALUE * args.contracts
    fees = sum(leg_fee(spot, pr[k], args.contracts, args.fee_mult)
               for k in ("sc", "sp", "wc", "wp")) * 2    # entry + exit fills
    return {
        "expiry": ddmmyy, "skip": None, "spot": spot,
        "atm": legs["atm"], "sc": legs["sc"], "sp": legs["sp"],
        "wc": legs["wc"], "wp": legs["wp"],
        "credit": credit, "exit_reason": exit_reason,
        "exit_ts": exit_ts, "pnl_unit": pnl_unit,
        "worst_unit": worst,
        "usd_gross": usd_gross, "usd_fees": fees,
        "usd_net": usd_gross - fees,
    }


# ----------------------------------------------------------------------
# Runner + report
# ----------------------------------------------------------------------
def run(args):
    conn = open_db()
    days = expiries(conn)
    if not days:
        print("corpus has no expiries — run crypto_corpus.py backfill-days first")
        return
    trades, skips = [], {}
    for d in days:
        r = run_day(conn, d, args)
        if r.get("skip"):
            skips[r["skip"]] = skips.get(r["skip"], 0) + 1
        else:
            trades.append(r)

    print("RUN_PARAMS  short_ratio={sr} wing_prem_ratio={wr} sl_mult={sl} "
          "tp_ratio={tp} contracts={ct} fee_mult={fm} "
          "entry=17:45IST(D-1) exit=17:15IST".format(
              sr=args.short_ratio, wr=args.wing_prem_ratio, sl=args.sl_mult,
              tp=args.tp_ratio, ct=args.contracts, fm=args.fee_mult))
    print(f"days={len(days)}  traded={len(trades)}  skipped={sum(skips.values())}"
          f"  {skips if skips else ''}")
    if not trades:
        return

    wins = [t for t in trades if t["usd_net"] > 0]
    net = sum(t["usd_net"] for t in trades)
    fees = sum(t["usd_fees"] for t in trades)
    by_exit = {}
    for t in trades:
        by_exit[t["exit_reason"]] = by_exit.get(t["exit_reason"], 0) + 1
    # equity curve max drawdown (USD, net)
    eq = peak = mdd = 0.0
    for t in trades:
        eq += t["usd_net"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    avg_credit = sum(t["credit"] for t in trades) / len(trades)
    print(f"net USD     : {net:>12,.2f}   (fees {fees:,.2f} — D13 assumption)")
    print(f"win rate    : {len(wins)}/{len(trades)} = {len(wins)/len(trades):.1%}")
    print(f"avg credit  : {avg_credit:>12,.2f}  per 1 BTC")
    print(f"exits       : {by_exit}")
    print(f"max DD      : {mdd:>12,.2f}  USD (net, sequential)")
    worst_day = min(trades, key=lambda t: t["usd_net"])
    print(f"worst day   : {worst_day['expiry']}  {worst_day['usd_net']:,.2f} USD "
          f"({worst_day['exit_reason']})")

    if args.csv:
        cols = ["expiry", "spot", "atm", "sc", "sp", "wc", "wp", "credit",
                "exit_reason", "exit_ts", "pnl_unit", "worst_unit",
                "usd_gross", "usd_fees", "usd_net"]
        with open(args.csv, "w", newline="") as f:
            w = csvmod.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)
        print(f"trades csv  : {args.csv}")


def main():
    ap = argparse.ArgumentParser(description="BTC daily IC backtest (Phase A)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--short-ratio", type=float, default=0.25,
                   help="short premium as fraction of ATM straddle")
    r.add_argument("--wing-prem-ratio", type=float, default=0.25,
                   help="wing premium cap as fraction of short premium")
    r.add_argument("--sl-mult", type=float, default=1.5,
                   help="MTM SL at this multiple of credit")
    r.add_argument("--tp-ratio", type=float, default=0.0,
                   help="profit target as fraction of credit (0=off)")
    r.add_argument("--contracts", type=int, default=100)
    r.add_argument("--fee-mult", type=float, default=1.0,
                   help="fee scaler; 0 = gross P&L")
    r.add_argument("--csv", default="")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a)


if __name__ == "__main__":
    main()
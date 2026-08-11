# ── CRYPTO_LAB BEGIN ──
# backend/app/backtest/crypto/crypto_ic_engine.py
#
# Generalized BTC daily-options premium-selling backtest engine.
#
# Structures : iron condor (defined risk) | short strangle (no wings; MTM SL
#              is the ONLY risk cap, so sl_mult > 0 is enforced).
# Selection  : shorts by premium-ratio vs ATM straddle (scale/vol invariant)
#              OR by fixed OTM % distance from entry spot.
#              wings by premium-ratio vs the short leg OR by OTM gap % beyond
#              the short strike.
# Timing     : entry 0/1/2 days before expiry at any HH:MM IST; exit HH:MM
#              IST on expiry day (<= 17:30). MTM evaluated EVERY minute.
# Filters    : ISO date range, expiry-weekday whitelist, exclude-date list.
# Costs      : per leg per fill fee = min(taker*spot_notional, cap*premium),
#              x fee_mult, x (1 + gst_pct/100).  FLAGGED ASSUMPTION (D13):
#              verify against the official Delta India fee schedule before
#              trusting absolute P&L.
#
# Integrity  : any day that cannot produce a complete, priced structure is
#              skipped WITH a reason code — never traded with substitutes.
#              Sparse price paths are excluded, not assumed benign.
#
# Pure backtest module: reads the crypto corpus DB only. No broker, no live
# engine, no shared-state imports. Strategy-isolation rules respected.

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.backtest.crypto.delta_corpus import (
    DB_PATH, IST, PRICE_SCALE, db, expiry_dt_from_ddmmyy, opt_symbol,
)

CONTRACT_VALUE = 0.001            # BTC per contract (Delta India BTC options)
TAKER_RATE = 0.0001               # D13 assumption (product metadata)
PREMIUM_CAP = 0.035               # D13 assumption (product metadata)
SNAP_TOL_S = 600                  # entry/exit snapshot tolerance (10 min)

SKIP_REASONS = (
    "FILTERED", "NO_SPOT", "NO_CHAIN", "NO_ATM_PREMS", "NO_SHORT_CALL",
    "NO_SHORT_PUT", "NO_WING_CALL", "NO_WING_PUT", "BAD_CREDIT",
    "SPARSE_PATH",
)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
@dataclass
class LabConfig:
    structure: str = "condor"              # condor | strangle
    entry_days_before: int = 1             # 0 | 1 | 2
    entry_hm: str = "17:45"                # IST
    exit_hm: str = "17:15"                 # IST, expiry day, <= 17:30
    short_mode: str = "premium_ratio"      # premium_ratio | otm_pct
    short_ratio: float = 0.25              # x ATM straddle
    short_otm_pct: float = 1.5             # % of spot
    wing_mode: str = "premium_ratio"       # premium_ratio | otm_gap_pct
    wing_prem_ratio: float = 0.25          # x short premium
    wing_gap_pct: float = 1.0              # % of spot beyond short strike
    sl_mult: float = 1.5                   # MTM SL at sl_mult x credit (0=off)
    tp_ratio: float = 0.0                  # TP at tp_ratio x credit (0=off)
    contracts: int = 100
    fee_mult: float = 1.0
    gst_pct: float = 0.0                   # GST on fees (verify: usually 18)
    margin_buffer_pct: float = 10.0        # safety buffer on margin estimate
    margin_shock_pct: float = 10.0         # strangle scenario shock (% of spot)
    date_from: str = ""                    # ISO yyyy-mm-dd (expiry date)
    date_to: str = ""
    weekdays: list = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    exclude_dates: list = field(default_factory=list)   # ddmmyy strings

    @classmethod
    def from_dict(cls, d: dict) -> "LabConfig":
        cfg = cls(**{k: v for k, v in d.items()
                     if k in cls.__dataclass_fields__})
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.structure not in ("condor", "strangle"):
            raise ValueError("structure must be condor|strangle")
        if self.structure == "strangle" and self.sl_mult <= 0:
            raise ValueError(
                "strangle has no wings: sl_mult must be > 0 (MTM stop is the "
                "only risk cap)")
        if self.entry_days_before not in (0, 1, 2):
            raise ValueError("entry_days_before must be 0, 1 or 2")
        eh, em = _parse_hm(self.entry_hm)
        xh, xm = _parse_hm(self.exit_hm)
        if (xh, xm) > (17, 30):
            raise ValueError("exit_hm must be <= 17:30 IST (settlement)")
        if self.entry_days_before == 0 and (eh, em) >= (xh, xm):
            raise ValueError("same-day entry must be before exit_hm")
        if self.short_mode not in ("premium_ratio", "otm_pct"):
            raise ValueError("short_mode must be premium_ratio|otm_pct")
        if self.wing_mode not in ("premium_ratio", "otm_gap_pct"):
            raise ValueError("wing_mode must be premium_ratio|otm_gap_pct")
        if self.short_mode == "premium_ratio" and not (0 < self.short_ratio < 1):
            raise ValueError("short_ratio must be in (0, 1)")
        if self.short_mode == "otm_pct" and not (0 < self.short_otm_pct <= 15):
            raise ValueError("short_otm_pct must be in (0, 15]")
        if self.contracts <= 0:
            raise ValueError("contracts must be > 0")
        if not (0 <= self.tp_ratio <= 0.95):
            raise ValueError(
                "tp_ratio must be 0..0.95 — max profit of a credit structure "
                "IS the credit (1.0), so a target above ~0.95 can never fire")
        for wd in self.weekdays:
            if wd not in (0, 1, 2, 3, 4, 5, 6):
                raise ValueError("weekdays entries must be 0..6 (Mon=0)")
        if not self.weekdays:
            raise ValueError("weekdays cannot be empty")
        if not (0 <= self.margin_buffer_pct <= 100):
            raise ValueError("margin_buffer_pct must be 0..100")
        if not (1 <= self.margin_shock_pct <= 50):
            raise ValueError("margin_shock_pct must be 1..50")


def _parse_hm(hm: str) -> tuple:
    try:
        h, m = hm.strip().split(":")
        h, m = int(h), int(m)
        assert 0 <= h <= 23 and 0 <= m <= 59
        return h, m
    except Exception:
        raise ValueError(f"bad HH:MM time: {hm!r}")


# ----------------------------------------------------------------------
# Time / data access
# ----------------------------------------------------------------------
def entry_epoch(ddmmyy: str, cfg: LabConfig) -> int:
    e = expiry_dt_from_ddmmyy(ddmmyy) - dt.timedelta(days=cfg.entry_days_before)
    h, m = _parse_hm(cfg.entry_hm)
    return int(e.replace(hour=h, minute=m).timestamp())


def exit_epoch(ddmmyy: str, cfg: LabConfig) -> int:
    e = expiry_dt_from_ddmmyy(ddmmyy)
    h, m = _parse_hm(cfg.exit_hm)
    return int(e.replace(hour=h, minute=m).timestamp())


def _chain_strikes(conn, ddmmyy: str) -> list:
    return sorted(r[0] for r in conn.execute(
        "SELECT strike FROM expiry_chain WHERE expiry_ddmmyy=?", (ddmmyy,)))


def _entry_spot(conn, ep: int):
    row = conn.execute(
        "SELECT close FROM perp_candles_1m WHERE symbol='BTCUSD' "
        "AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (ep, ep - SNAP_TOL_S)).fetchone()
    return row[0] if row else None


def _mark_map(conn, symbol: str) -> dict:
    return {ts: c / PRICE_SCALE for ts, c in conn.execute(
        "SELECT ts, close FROM option_candles_1m "
        "WHERE symbol=? AND series='MARK' AND close IS NOT NULL", (symbol,))}


def _snap(series: dict, epoch: int, tol: int = SNAP_TOL_S):
    cand = [ts for ts in series if epoch - tol <= ts <= epoch]
    return series[max(cand)] if cand else None


# ----------------------------------------------------------------------
# Leg selection
# ----------------------------------------------------------------------
def _pick_legs(conn, ddmmyy: str, spot: float, cfg: LabConfig):
    """Returns (legs_dict, None) or (None, skip_reason)."""
    strikes = _chain_strikes(conn, ddmmyy)
    if len(strikes) < (5 if cfg.structure == "condor" else 3):
        return None, "NO_CHAIN"
    ep = entry_epoch(ddmmyy, cfg)

    cache = {}

    def prem(side, k):
        key = (side, k)
        if key not in cache:
            cache[key] = _snap(_mark_map(conn, opt_symbol(side, k, ddmmyy)), ep)
        return cache[key]

    calls = [k for k in strikes if k > spot]
    puts = [k for k in strikes if k < spot]
    if not calls or not puts:
        return None, "NO_CHAIN"

    if cfg.short_mode == "premium_ratio":
        atm = min(strikes, key=lambda k: abs(k - spot))
        c_atm, p_atm = prem("C", atm), prem("P", atm)
        if c_atm is None or p_atm is None:
            return None, "NO_ATM_PREMS"
        target = cfg.short_ratio * (c_atm + p_atm)

        def pick_short(side, cands):
            best, bd = None, None
            for k in cands:
                v = prem(side, k)
                if v is None:
                    continue
                d = abs(v - target)
                if bd is None or d < bd:
                    best, bd = k, d
            return best
        sc = pick_short("C", calls)
        sp = pick_short("P", puts)
    else:  # otm_pct: nearest priced strike to spot*(1±pct)
        tc = spot * (1 + cfg.short_otm_pct / 100.0)
        tp = spot * (1 - cfg.short_otm_pct / 100.0)

        def nearest_priced(side, cands, tgt):
            for k in sorted(cands, key=lambda k: abs(k - tgt)):
                if prem(side, k) is not None:
                    return k
            return None
        sc = nearest_priced("C", calls, tc)
        sp = nearest_priced("P", puts, tp)

    if sc is None:
        return None, "NO_SHORT_CALL"
    if sp is None:
        return None, "NO_SHORT_PUT"

    wc = wp = None
    if cfg.structure == "condor":
        if cfg.wing_mode == "premium_ratio":
            def pick_wing(side, short_k, outward):
                cap = cfg.wing_prem_ratio * prem(side, short_k)
                cands = sorted(
                    (k for k in strikes
                     if (k > short_k if outward > 0 else k < short_k)),
                    reverse=(outward < 0))
                for k in cands:                    # nearest-outward first
                    v = prem(side, k)
                    if v is not None and v <= cap:
                        return k
                for k in reversed(cands):          # fallback: furthest priced
                    if prem(side, k) is not None:
                        return k
                return None
        else:  # otm_gap_pct: nearest priced strike to short ± spot*gap%
            def pick_wing(side, short_k, outward):
                tgt = short_k + outward * spot * cfg.wing_gap_pct / 100.0
                cands = [k for k in strikes
                         if (k > short_k if outward > 0 else k < short_k)]
                for k in sorted(cands, key=lambda k: abs(k - tgt)):
                    if prem(side, k) is not None:
                        return k
                return None
        wc = pick_wing("C", sc, +1)
        if wc is None:
            return None, "NO_WING_CALL"
        wp = pick_wing("P", sp, -1)
        if wp is None:
            return None, "NO_WING_PUT"

    legs = {"sc": sc, "sp": sp, "wc": wc, "wp": wp,
            "prem": {"sc": prem("C", sc), "sp": prem("P", sp),
                     "wc": prem("C", wc) if wc else 0.0,
                     "wp": prem("P", wp) if wp else 0.0}}
    return legs, None


# ----------------------------------------------------------------------
# Fees (D13 flagged assumption)
# ----------------------------------------------------------------------
def _leg_fee(spot: float, premium: float, cfg: LabConfig) -> float:
    per_contract = min(TAKER_RATE * spot * CONTRACT_VALUE,
                       PREMIUM_CAP * premium * CONTRACT_VALUE)
    return per_contract * cfg.contracts * cfg.fee_mult


# ----------------------------------------------------------------------
# Margin estimate — MODEL ASSUMPTION, verify against exchange at order time.
# Delta India documents risk-based (portfolio) margining that analyses same-
# underlying groups together and recognises spreads. We therefore estimate:
#   condor   : worst-case structural loss = max(side width) - credit
#   strangle : worst intrinsic loss under a +/- margin_shock_pct spot move,
#              net of credit (no width exists; shock is the assumption)
# Both x (1 + margin_buffer_pct/100), floored at a small premium-based
# minimum. Per 1 BTC, converted to USD via contract size like P&L.
# ----------------------------------------------------------------------
def _margin_unit(cfg: LabConfig, spot: float, legs: dict, credit: float):
    pr = legs["prem"]
    if cfg.structure == "condor":
        width = max(legs["wc"] - legs["sc"], legs["sp"] - legs["wp"])
        base = max(width - credit, 0.0)
    else:
        s = cfg.margin_shock_pct / 100.0
        up = max(spot * (1 + s) - legs["sc"], 0.0) - credit
        dn = max(legs["sp"] - spot * (1 - s), 0.0) - credit
        base = max(up, dn, 0.0)
    floor = 0.5 * (pr["sc"] + pr["sp"])      # never below half the short prems
    return max(base, floor) * (1.0 + cfg.margin_buffer_pct / 100.0)


# ----------------------------------------------------------------------
# One expiry
# ----------------------------------------------------------------------
def run_day(conn, ddmmyy: str, cfg: LabConfig) -> dict:
    exp_date = expiry_dt_from_ddmmyy(ddmmyy).date()
    iso = exp_date.isoformat()
    if cfg.date_from and iso < cfg.date_from:
        return {"expiry": ddmmyy, "skip": "FILTERED"}
    if cfg.date_to and iso > cfg.date_to:
        return {"expiry": ddmmyy, "skip": "FILTERED"}
    if exp_date.weekday() not in cfg.weekdays:
        return {"expiry": ddmmyy, "skip": "FILTERED"}
    if ddmmyy in cfg.exclude_dates:
        return {"expiry": ddmmyy, "skip": "FILTERED"}

    ep_in, ep_out = entry_epoch(ddmmyy, cfg), exit_epoch(ddmmyy, cfg)
    spot = _entry_spot(conn, ep_in)
    if spot is None:
        return {"expiry": ddmmyy, "skip": "NO_SPOT"}
    legs, reason = _pick_legs(conn, ddmmyy, spot, cfg)
    if legs is None:
        return {"expiry": ddmmyy, "skip": reason}

    pr = legs["prem"]
    credit = pr["sc"] + pr["sp"] - pr["wc"] - pr["wp"]
    if credit <= 0:
        return {"expiry": ddmmyy, "skip": "BAD_CREDIT"}

    maps = {"sc": _mark_map(conn, opt_symbol("C", legs["sc"], ddmmyy)),
            "sp": _mark_map(conn, opt_symbol("P", legs["sp"], ddmmyy))}
    if cfg.structure == "condor":
        maps["wc"] = _mark_map(conn, opt_symbol("C", legs["wc"], ddmmyy))
        maps["wp"] = _mark_map(conn, opt_symbol("P", legs["wp"], ddmmyy))

    last = dict(pr)
    exit_reason, exit_ts, exit_combo = "TIME", ep_out, None
    n_path, worst, best = 0, 0.0, 0.0
    for ts in range(ep_in + 60, ep_out + 60, 60):
        for key, m in maps.items():
            if ts in m:
                last[key] = m[ts]
        combo = last["sc"] + last["sp"] - last["wc"] - last["wp"]
        pnl = credit - combo
        worst = min(worst, pnl)
        best = max(best, pnl)
        n_path += 1
        if cfg.sl_mult > 0 and pnl <= -cfg.sl_mult * credit:
            exit_reason, exit_ts, exit_combo = "SL", ts, combo
            break
        if cfg.tp_ratio > 0 and pnl >= cfg.tp_ratio * credit:
            exit_reason, exit_ts, exit_combo = "TP", ts, combo
            break
    if exit_combo is None:
        for key, m in maps.items():
            v = _snap(m, ep_out)
            if v is not None:
                last[key] = v
        exit_combo = last["sc"] + last["sp"] - last["wc"] - last["wp"]

    expected = max(1, (ep_out - ep_in) // 60)
    if n_path < expected * 0.5 and exit_reason == "TIME":
        return {"expiry": ddmmyy, "skip": "SPARSE_PATH"}

    pnl_unit = credit - exit_combo
    usd_gross = pnl_unit * CONTRACT_VALUE * cfg.contracts
    n_fill_legs = ["sc", "sp"] + (["wc", "wp"]
                                  if cfg.structure == "condor" else [])
    fees = sum(_leg_fee(spot, pr[k], cfg) for k in n_fill_legs) * 2
    fees *= (1.0 + cfg.gst_pct / 100.0)

    margin_unit = _margin_unit(cfg, spot, legs, credit)
    margin_usd = margin_unit * CONTRACT_VALUE * cfg.contracts

    def ist(ts):
        return dt.datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M")
    return {
        "expiry": ddmmyy, "skip": None, "date": iso,
        "weekday": exp_date.weekday(), "spot": round(spot, 1),
        "entry_ts": ep_in, "entry_ist": ist(ep_in),
        "exit_ts": exit_ts, "exit_ist": ist(exit_ts),
        "sc": legs["sc"], "sp": legs["sp"], "wc": legs["wc"], "wp": legs["wp"],
        "sc_prem": round(pr["sc"], 2), "sp_prem": round(pr["sp"], 2),
        "wc_prem": round(pr["wc"], 2) if legs["wc"] is not None else None,
        "wp_prem": round(pr["wp"], 2) if legs["wp"] is not None else None,
        "sc_xprem": round(last["sc"], 2), "sp_xprem": round(last["sp"], 2),
        "wc_xprem": round(last["wc"], 2) if legs["wc"] is not None else None,
        "wp_xprem": round(last["wp"], 2) if legs["wp"] is not None else None,
        "credit": round(credit, 2), "exit_debit": round(exit_combo, 2),
        "sl_level": round(cfg.sl_mult * credit, 2) if cfg.sl_mult > 0 else None,
        "tp_level": round(cfg.tp_ratio * credit, 2) if cfg.tp_ratio > 0 else None,
        "exit_reason": exit_reason,
        "hold_min": int((exit_ts - ep_in) // 60),
        "pnl_unit": round(pnl_unit, 2),
        "best_unit": round(best, 2), "worst_unit": round(worst, 2),
        "usd_gross": round(usd_gross, 2), "usd_fees": round(fees, 2),
        "usd_net": round(usd_gross - fees, 2),
        "margin_unit": round(margin_unit, 2),
        "margin_usd": round(margin_usd, 2),
    }


# ----------------------------------------------------------------------
# Full run + persistence
# ----------------------------------------------------------------------
def run_lab_backtest(cfg: LabConfig,
                     progress_cb: Optional[Callable[[dict], None]] = None,
                     cancel: Optional[threading.Event] = None) -> dict:
    conn = db()
    try:
        days = sorted(
            (r[0] for r in conn.execute(
                "SELECT DISTINCT expiry_ddmmyy FROM expiry_chain")),
            key=lambda s: (s[4:6], s[2:4], s[0:2]))
        trades, skips = [], {}
        for i, d in enumerate(days):
            if cancel is not None and cancel.is_set():
                break
            r = run_day(conn, d, cfg)
            if r.get("skip"):
                skips[r["skip"]] = skips.get(r["skip"], 0) + 1
            else:
                trades.append(r)
            if progress_cb and i % 25 == 0:
                progress_cb({"done": i + 1, "total": len(days)})

        summary = _summarize(cfg, days, trades, skips)
        run_id = dt.datetime.now(IST).strftime("%Y%m%d-%H%M%S-") \
            + uuid.uuid4().hex[:6]
        conn.execute(
            "INSERT INTO lab_runs VALUES(?,?,?,?,?)",
            (run_id, int(dt.datetime.now(IST).timestamp()),
             json.dumps(cfg.__dict__), json.dumps(summary),
             json.dumps(trades)))
        conn.commit()
        return {"run_id": run_id, "summary": summary, "trades": trades}
    finally:
        conn.close()


def _summarize(cfg: LabConfig, days, trades, skips) -> dict:
    out = {"days": len(days), "traded": len(trades),
           "skipped": sum(skips.values()), "skips": skips,
           "net_usd": 0.0, "gross_usd": 0.0, "fees_usd": 0.0,
           "win_rate": 0.0, "avg_credit": 0.0, "exits": {},
           "max_dd_usd": 0.0, "worst_day": None, "params": cfg.__dict__}
    if not trades:
        return out
    out["net_usd"] = round(sum(t["usd_net"] for t in trades), 2)
    out["gross_usd"] = round(sum(t["usd_gross"] for t in trades), 2)
    out["fees_usd"] = round(sum(t["usd_fees"] for t in trades), 2)
    wins = sum(1 for t in trades if t["usd_net"] > 0)
    out["win_rate"] = round(wins / len(trades), 4)
    out["avg_credit"] = round(
        sum(t["credit"] for t in trades) / len(trades), 2)
    for t in trades:
        out["exits"][t["exit_reason"]] = out["exits"].get(
            t["exit_reason"], 0) + 1
    margins = [t["margin_usd"] for t in trades if "margin_usd" in t]
    if margins:
        out["peak_margin_usd"] = round(max(margins), 2)
        out["avg_margin_usd"] = round(sum(margins) / len(margins), 2)
        if out["peak_margin_usd"] > 0:
            out["ret_on_peak_margin_pct"] = round(
                100.0 * out["net_usd"] / out["peak_margin_usd"], 2)
    eq = peak = mdd = 0.0
    for t in trades:
        eq += t["usd_net"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    out["max_dd_usd"] = round(mdd, 2)
    w = min(trades, key=lambda t: t["usd_net"])
    out["worst_day"] = {"expiry": w["expiry"], "usd_net": w["usd_net"],
                        "exit_reason": w["exit_reason"]}
    return out


# ----------------------------------------------------------------------
# Run library
# ----------------------------------------------------------------------
def list_runs(limit: int = 50) -> list:
    conn = db()
    try:
        return [{"run_id": r[0], "created_at": r[1],
                 "params": json.loads(r[2]), "summary": json.loads(r[3])}
                for r in conn.execute(
                    "SELECT run_id, created_at, params_json, summary_json "
                    "FROM lab_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,))]
    finally:
        conn.close()


def get_run(run_id: str):
    conn = db()
    try:
        r = conn.execute(
            "SELECT run_id, created_at, params_json, summary_json, "
            "trades_json FROM lab_runs WHERE run_id=?", (run_id,)).fetchone()
        if not r:
            return None
        return {"run_id": r[0], "created_at": r[1],
                "params": json.loads(r[2]), "summary": json.loads(r[3]),
                "trades": json.loads(r[4])}
    finally:
        conn.close()
# ── CRYPTO_LAB END ──
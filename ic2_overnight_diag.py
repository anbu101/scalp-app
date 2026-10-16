#!/usr/bin/env python3
# tools/lab/ic2/ic2_overnight_diag.py
# ── IC2_OVERNIGHT_DIAG_20261015 ──
#
# READ-ONLY diagnostic. Question (Anbu, 2026-10-15): IC_V2's open MTM at
# ~15:15 often looks better than what the 09:16 NEXT_OPEN square-off books,
# "especially when L1·ADJ / L2·ADJ BUY legs are carried". Is that real across
# 2020-2026, which leg types pay or cost overnight, and would any 15:15 rule
# have beaten the sealed carry?
#
# Inputs : one IC_V2 Backtest export CSV (NEXT_OPEN run) + backtest.db.
# Output : a text report to stdout, and <csv>.overnight_legs.csv (one row per
#          carried leg with its 15:15 mark). Opens the DB with mode=ro.
#          Writes nothing anywhere else. Not wired into the app.
#
# Method
#   * Every carried leg (exit session > entry session; IC_V2 is one-night max)
#     is marked at the entry session's MARK minute with the SAME convention the
#     runner uses for its EOD square-off: close of the last candle STRICTLY
#     before day_start + MARK (15:15 -> the candle stamped 15:14), staleness
#     <= 15 min.
#   * overnight_delta = sign * (exit - mark) * qty     (sign +1 BUY, -1 SELL)
#     = what carrying earned/cost versus closing at MARK. Charges are held
#     equal (one exit either way); the STT difference is noise at these prices.
#   * Legs that cannot be marked (SYN- synthetic strikes, dark contracts,
#     entries after MARK) are left as carried (delta 0) and COUNTED. Coverage
#     is printed per leg kind; a rule acting on <95%-covered legs is flagged.
#   * Rules are replayed as COUNTERFACTUALS on the run's own legs. That is
#     exact here because a 15:15 close cannot change anything else in IC_V2:
#     carried legs already close at 09:16, entry is 09:18, so D7 never sees a
#     difference; the next day's book is identical. Closed-at-mark P&L is
#     booked on the entry session, carried P&L on the exit session.
#
# Pre-registered rule set (fixed BEFORE looking at results — do not add rules
# after reading the output; that is how the TSG bank experiment went wrong):
#   BASE              sealed carry
#   C_ALL             close every open leg at MARK          (control: no carry)
#   C_SHORTS          close carried shorts only             (control)
#   A_ALL             close every carried ADJ BUY leg at MARK
#   A_GREEN           close a carried ADJ leg if mark >= entry
#   A_UP50            close a carried ADJ leg if mark >= 1.5 x entry
#   A_RED             close a carried ADJ leg if mark <  entry (control)
#   M_<n>             Anbu's idea: open-position MTM at MARK >= n per lot ->
#                     close ALL open legs; n in --mtm-per-lot
#
# Pass bar (scoreboard order: yearly signs -> worst month -> consecutive
# losing months -> positive months -> DD -> net):
#   positive years >= BASE, worst month >= BASE, consecutive losing months
#   <= BASE, max DD <= BASE, delta-net > 0 in BOTH 2020-23 and 2024-26, and
#   delta-net > 0 in >= 5 of the 7 calendar years.
#
# Usage
#   python3 ic2_overnight_diag.py --csv ~/Downloads/backtest_IC_V2_..._ffcae7cd.csv
#   python3 ic2_overnight_diag.py --csv X.csv --mark 15:25
#   python3 ic2_overnight_diag.py --csv X.csv --db /path/backtest.db --lot-size 65

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

FENCE = "IC2_OVERNIGHT_DIAG_20261015"
IST = 5 * 3600 + 30 * 60
STALE_S = 900
DEFAULT_DB = os.path.expanduser("~/.scalp-app/backtest/backtest.db")


# ───────────────────────── export parsing ─────────────────────────
def _section(lines, start_label, stop_labels):
    i = lines.index(start_label)
    j = len(lines)
    for k in range(i + 1, len(lines)):
        if lines[k].split(",")[0] in stop_labels:
            j = k
            break
    return lines[i + 1:j]


def _parse_dm_hm(s):
    d, t = s.split(", ")
    dd, mm = (int(x) for x in d.split("/"))
    hh, mi = (int(x) for x in t.split(":"))
    return dd, mm, hh, mi


def load_export(path, start_year=None):
    raw = open(path, encoding="utf-8-sig").read().splitlines()
    cfg = {}
    for row in csv.reader(_section(raw, "CONFIG", {"TRADES"})):
        if len(row) >= 2 and row[0] != "Key":
            cfg[row[0]] = row[1]
    trade_lines = _section(raw, "TRADES", {"ENTRY CONDITION P&L", "DAILY P&L"})
    rows = [r for r in csv.DictReader(io.StringIO("\n".join(trade_lines)))
            if r.get("Entry Time")]
    yearly = {}
    try:
        for r in csv.reader(_section(raw, "YEARLY P&L", set())):
            if r and r[0].isdigit():
                yearly[int(r[0])] = float(r[1])
    except ValueError:
        pass

    if start_year is None:
        m = re.search(r"_(\d{4})-\d\d-\d\d_to_", os.path.basename(path))
        start_year = int(m.group(1)) if m else 2020
    # Year inference: rows are in entry order; a month drop of >6 = new year.
    yr, prev_m, legs = start_year, None, []
    for r in rows:
        dd, mm, hh, mi = _parse_dm_hm(r["Entry Time"])
        if prev_m is not None and mm < prev_m - 6:
            yr += 1
        prev_m = mm
        ent = datetime(yr, mm, dd, hh, mi)
        xd, xm, xh, xmi = _parse_dm_hm(r["Exit Time"])
        xy = yr + (1 if xm < mm - 6 else 0)
        ext = datetime(xy, xm, xd, xh, xmi)
        cond = r["Condition"]
        if "ADJ" in cond:
            kind, sign = "ADJ", +1
        elif cond in ("L3", "L4"):
            kind, sign = "WING", +1
        else:
            kind, sign = "SHORT", -1
        legs.append({
            "symbol": r["Symbol"], "cond": cond, "kind": kind, "sign": sign,
            "entry_dt": ent, "exit_dt": ext,
            "entry": float(r["Entry"]), "exit": float(r["Exit"]),
            "reason": r["Reason"], "gross": float(r["Gross"]),
            "net": float(r["Net"]), "qty": int(float(r["Qty"])),
        })
    return cfg, legs, yearly


# ───────────────────────── corpus access ─────────────────────────
class Corpus:
    def __init__(self, db):
        self.c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        self.underlying = "NIFTY"

    @staticmethod
    def day_start(d: date) -> int:
        return int((datetime(d.year, d.month, d.day)
                    - datetime(1970, 1, 1)).total_seconds()) - IST

    def close_before(self, sym, bound_ts):
        r = self.c.execute(
            "SELECT ts, close FROM backtest_candles_1m WHERE tradingsymbol=? "
            "AND ts<? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (sym, bound_ts, bound_ts - STALE_S)).fetchone()
        return (int(r[0]), float(r[1])) if r else None

    def spot_close_before(self, bound_ts):
        r = self.c.execute(
            "SELECT close FROM backtest_candles_1m WHERE underlying=? AND "
            "instrument_type='SPOT' AND ts<? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (self.underlying, bound_ts, bound_ts - STALE_S)).fetchone()
        return float(r[0]) if r else None

    def spot_open_at(self, ts):
        r = self.c.execute(
            "SELECT open FROM backtest_candles_1m WHERE underlying=? AND "
            "instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts ASC LIMIT 1",
            (self.underlying, ts, ts + 300)).fetchone()
        return float(r[0]) if r else None


def epoch_of(dt: datetime) -> int:
    return Corpus.day_start(dt.date()) + dt.hour * 3600 + dt.minute * 60


# ───────────────────────── scoring ─────────────────────────
def scoreboard(daily: dict):
    days = sorted(daily)
    eq = peak = dd = 0.0
    for d in days:
        eq += daily[d]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    months, years = defaultdict(float), defaultdict(float)
    for d in days:
        months[(d.year, d.month)] += daily[d]
        years[d.year] += daily[d]
    run = worst_run = 0
    for k in sorted(months):
        run = run + 1 if months[k] < 0 else 0
        worst_run = max(worst_run, run)
    net = sum(daily.values())
    return {
        "net": net, "dd": dd, "years": dict(years),
        "pos_years": sum(v > 0 for v in years.values()), "n_years": len(years),
        "worst_m": min(months.values()) if months else 0.0,
        "consec_neg": worst_run,
        "pos_m": sum(v > 0 for v in months.values()), "n_m": len(months),
        "h1": sum(v for y, v in years.items() if y <= 2023),
        "h2": sum(v for y, v in years.items() if y >= 2024),
    }


def book(legs, close_ids):
    """Daily P&L by booking date. Legs in close_ids are booked at their
    MARK value on the entry session instead of carried."""
    daily = defaultdict(float)
    for i, lg in enumerate(legs):
        if i in close_ids:
            daily[lg["entry_dt"].date()] += lg["net"] - lg["delta"]
        else:
            daily[lg["exit_dt"].date()] += lg["net"]
    return daily


# ───────────────────────── main ─────────────────────────
def lakh(x):
    return f"{x / 1e5:+.2f}L"


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--mark", default="15:15",
                    help="HH:MM; mark = close of last candle strictly before it")
    ap.add_argument("--lot-size", type=int, default=65)
    ap.add_argument("--mtm-per-lot", default="1000,2000,3000,4000,6000")
    ap.add_argument("--start-year", type=int, default=None)
    a = ap.parse_args()

    cfg, legs, yearly = load_export(a.csv, a.start_year)
    print(f"== {FENCE} ==")
    print(f"export : {os.path.basename(a.csv)}   legs={len(legs)}")
    print(f"config : exit_mode={cfg.get('exit_mode')} next_open={cfg.get('next_open_time')} "
          f"adj_tp L1/L2={cfg.get('adjust.L1.tp_val')}/{cfg.get('adjust.L2.tp_val')} "
          f"adj_sl={cfg.get('adjust.L1.sl_val')}")
    if cfg.get("exit_mode") != "NEXT_OPEN":
        sys.exit("ABORT: export is not a NEXT_OPEN run — nothing is carried.")

    # ── self-checks on the parse ──
    bad_sign = sum(1 for lg in legs
                   if abs(lg["sign"] * (lg["exit"] - lg["entry"]) * lg["qty"] - lg["gross"])
                   > max(5.0, 0.002 * abs(lg["gross"])))
    by_year = defaultdict(float)
    for lg in legs:
        by_year[lg["entry_dt"].year] += lg["net"]
    yr_ok = all(abs(by_year.get(y, 0) - v) < 50 for y, v in yearly.items()) if yearly else None
    print(f"checks : gross-sign mismatches={bad_sign}  "
          f"yearly totals match export={yr_ok}  "
          f"exit<entry={sum(lg['exit_dt'] < lg['entry_dt'] for lg in legs)}")
    if bad_sign > len(legs) * 0.01 or yr_ok is False:
        sys.exit("ABORT: export parse does not reconcile — fix before trusting anything below.")

    lots = max(1, round(legs[0]["qty"] / a.lot_size))
    mh, mm = (int(x) for x in a.mark.split(":"))
    corp = Corpus(a.db)

    # ── mark every carried leg ──
    cover = defaultdict(lambda: [0, 0])
    for lg in legs:
        lg["delta"] = 0.0
        lg["mark"] = None
        lg["carried"] = lg["exit_dt"].date() > lg["entry_dt"].date()
        d0 = lg["entry_dt"].date()
        bound = Corpus.day_start(d0) + (mh * 60 + mm) * 60
        # open at MARK = entered before it and still open at it: every carried
        # leg plus the few that stop out between MARK and the session close
        # (a 15:15 rule would have closed those too). Expiry-day EOD exits
        # are stamped 15:14 and fall outside, as they should.
        lg["open_at_mark"] = (epoch_of(lg["entry_dt"]) < bound
                              and epoch_of(lg["exit_dt"]) >= bound
                              and lg["reason"] not in ("EOD", "EOD_MTC", "EOR"))
        if lg["carried"]:
            cover[lg["kind"]][1] += 1
        if not lg["open_at_mark"] or lg["symbol"].startswith("SYN-"):
            continue
        got = corp.close_before(lg["symbol"], bound)
        if got is None:
            continue
        lg["mark"] = got[1]
        lg["delta"] = lg["sign"] * (lg["exit"] - got[1]) * lg["qty"]
        if not lg["carried"]:
            continue
        cover[lg["kind"]][0] += 1
        s0 = corp.spot_close_before(bound)
        s1 = corp.spot_open_at(epoch_of(lg["exit_dt"]))
        lg["gap_pct"] = (s1 / s0 - 1) * 100 if (s0 and s1) else None

    print(f"mark   : {a.mark} (close of last candle before it)   lots={lots}")
    for k in ("SHORT", "ADJ", "WING"):
        m, n = cover[k]
        print(f"  coverage {k:<5} {m:>5}/{n:<5} ({(m / n * 100 if n else 0):5.1f}%)")

    # ── 1) where the overnight goes, by leg kind ──
    print("\n[1] OVERNIGHT DELTA = carry result minus close-at-mark (marked legs only)")
    print(f"    {'kind':<6}{'n':>6}{'sum':>11}{'mean/leg':>11}{'%>0':>6}"
          f"{'20-23':>10}{'24-26':>10}  years(+)")
    for k in ("SHORT", "ADJ", "WING"):
        xs = [lg for lg in legs if lg["kind"] == k and lg["carried"]
              and lg["mark"] is not None]
        if not xs:
            continue
        s = sum(lg["delta"] for lg in xs)
        yrs = defaultdict(float)
        for lg in xs:
            yrs[lg["entry_dt"].year] += lg["delta"]
        print(f"    {k:<6}{len(xs):>6}{lakh(s):>11}{s / len(xs):>11.0f}"
              f"{sum(lg['delta'] > 0 for lg in xs) / len(xs) * 100:>6.0f}"
              f"{lakh(sum(v for y, v in yrs.items() if y <= 2023)):>10}"
              f"{lakh(sum(v for y, v in yrs.items() if y >= 2024)):>10}"
              f"  {sum(v > 0 for v in yrs.values())}/{len(yrs)}  "
              + " ".join(f"{y % 100}:{v / 1e5:+.1f}" for y, v in sorted(yrs.items())))

    # ── 2) ADJ anatomy: weekend, state at mark, and the gap ──
    adj = [lg for lg in legs if lg["kind"] == "ADJ" and lg["carried"]
           and lg["mark"] is not None]
    if adj:
        print("\n[2] CARRIED ADJ BUY legs — anatomy of the overnight delta")

        def row(lbl, xs):
            if not xs:
                print(f"    {lbl:<34}{0:>5}")
                return
            s = sum(lg["delta"] for lg in xs)
            print(f"    {lbl:<34}{len(xs):>5}{lakh(s):>10}{s / len(xs):>9.0f}"
                  f"{sum(lg['delta'] > 0 for lg in xs) / len(xs) * 100:>6.0f}%")
        wk = lambda lg: (lg["exit_dt"].date() - lg["entry_dt"].date()).days > 1
        row("weeknight", [lg for lg in adj if not wk(lg)])
        row("weekend / holiday", [lg for lg in adj if wk(lg)])
        row("green at mark (mark >= entry)", [lg for lg in adj if lg["mark"] >= lg["entry"]])
        row("  of which up >= 50%", [lg for lg in adj if lg["mark"] >= 1.5 * lg["entry"]])
        row("red at mark", [lg for lg in adj if lg["mark"] < lg["entry"]])
        g = [lg for lg in adj if lg.get("gap_pct") is not None]
        if g:
            fav = lambda lg: (lg["gap_pct"] if lg["symbol"].endswith("CE") else -lg["gap_pct"])
            row("gap FOR the leg (> +0.20%)", [lg for lg in g if fav(lg) > 0.20])
            row("flat gap (|gap| <= 0.20%)  = decay", [lg for lg in g if abs(lg["gap_pct"]) <= 0.20])
            row("gap AGAINST the leg (< -0.20%)", [lg for lg in g if fav(lg) < -0.20])
        else:
            print("    (no SPOT rows near mark/exit — gap split skipped)")

    # ── 3) open-position MTM at mark on carry days (for M_ thresholds) ──
    by_day = defaultdict(list)
    for i, lg in enumerate(legs):
        if lg["open_at_mark"]:
            by_day[lg["entry_dt"].date()].append(i)
    day_mtm = {}
    for d, ids in by_day.items():
        # unmarked legs (synthetic wings, late entries) contribute 0 — their
        # 15:15 value is unknown; wings are ~₹3 premium, bounded error.
        day_mtm[d] = sum(legs[i]["sign"] * (legs[i]["mark"] - legs[i]["entry"]) * legs[i]["qty"]
                         for i in ids if legs[i]["mark"] is not None)
    if day_mtm:
        v = sorted(day_mtm.values())
        q = lambda p: v[min(len(v) - 1, int(p * len(v)))]
        print(f"\n[3] open MTM at mark on {len(v)} carry days (₹, {lots} lots): "
              f"p10 {q(.1):,.0f}  p25 {q(.25):,.0f}  median {q(.5):,.0f}  "
              f"p75 {q(.75):,.0f}  p90 {q(.9):,.0f}")

    # ── 4) pre-registered rules ──
    marked = lambda i: legs[i]["mark"] is not None
    carried_ids = [i for i, lg in enumerate(legs) if lg["open_at_mark"]]
    rules = {
        "BASE": set(),
        "C_ALL": {i for i in carried_ids if marked(i)},
        "C_SHORTS": {i for i in carried_ids if marked(i) and legs[i]["kind"] == "SHORT"},
        "A_ALL": {i for i in carried_ids if marked(i) and legs[i]["kind"] == "ADJ"},
        "A_GREEN": {i for i in carried_ids if marked(i) and legs[i]["kind"] == "ADJ"
                    and legs[i]["mark"] >= legs[i]["entry"]},
        "A_UP50": {i for i in carried_ids if marked(i) and legs[i]["kind"] == "ADJ"
                   and legs[i]["mark"] >= 1.5 * legs[i]["entry"]},
        "A_RED": {i for i in carried_ids if marked(i) and legs[i]["kind"] == "ADJ"
                  and legs[i]["mark"] < legs[i]["entry"]},
    }
    for n in [int(x) for x in a.mtm_per_lot.split(",") if x.strip()]:
        thr = n * lots
        rules[f"M_{n}"] = {i for d, ids in by_day.items() if day_mtm[d] >= thr
                           for i in ids if marked(i)}

    base = scoreboard(book(legs, set()))
    print(f"\n[4] RULES (counterfactual on this run's legs; ₹ at {lots} lots)")
    hdr = (f"    {'rule':<9}{'legs':>5}{'net':>10}{'Δnet':>9}{'DD':>8}{'yrs+':>6}"
           f"{'worstM':>9}{'cLM':>4}{'+M':>7}{'Δ20-23':>9}{'Δ24-26':>9}{'Δyrs+':>6}  verdict")
    print(hdr)
    for name, ids in rules.items():
        sc = scoreboard(book(legs, ids))
        dy = {y: sc["years"].get(y, 0) - base["years"].get(y, 0) for y in base["years"]}
        dpos = sum(v > 0 for v in dy.values())
        if name == "BASE":
            verdict = "—"
        else:
            fails = []
            if sc["pos_years"] < base["pos_years"]: fails.append("years")
            if sc["worst_m"] < base["worst_m"] - 1: fails.append("worstM")
            if sc["consec_neg"] > base["consec_neg"]: fails.append("cLM")
            if sc["dd"] > base["dd"] + 1: fails.append("DD")
            if sc["h1"] - base["h1"] <= 0: fails.append("Δ20-23")
            if sc["h2"] - base["h2"] <= 0: fails.append("Δ24-26")
            if dpos < 5: fails.append("Δyrs")
            kinds = {legs[i]["kind"] for i in ids}
            # wings excluded: synthetic wings are unmarkable by design and
            # their ₹3 premium bounds the error; SHORT/ADJ must be covered.
            if any(cover[k][1] and cover[k][0] / cover[k][1] < 0.95
                   for k in kinds if k != "WING"):
                fails.append("coverage<95%")
            verdict = "PASS" if not fails else "fail: " + ",".join(fails)
        print(f"    {name:<9}{len(ids):>5}{lakh(sc['net']):>10}{lakh(sc['net'] - base['net']):>9}"
              f"{sc['dd'] / 1e5:>7.2f}L{sc['pos_years']:>3}/{sc['n_years']:<2}"
              f"{sc['worst_m'] / 1e5:>8.2f}L{sc['consec_neg']:>4}{sc['pos_m']:>4}/{sc['n_m']:<2}"
              f"{lakh(sc['h1'] - base['h1']):>9}{lakh(sc['h2'] - base['h2']):>9}"
              f"{dpos:>4}/{len(dy):<2} {verdict}")

    # ── per-leg dump ──
    out = os.path.splitext(a.csv)[0] + ".overnight_legs.csv"
    try:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "cond", "kind", "entry_dt", "exit_dt", "entry",
                        f"mark_{a.mark}", "exit", "reason", "net", "overnight_delta", "gap_pct"])
            for lg in legs:
                if lg["carried"]:
                    w.writerow([lg["symbol"], lg["cond"], lg["kind"], lg["entry_dt"], lg["exit_dt"],
                                lg["entry"], lg["mark"], lg["exit"], lg["reason"], lg["net"],
                                round(lg["delta"]) if lg["mark"] is not None else "",
                                round(lg["gap_pct"], 3) if lg.get("gap_pct") is not None else ""])
        print(f"\nper-leg rows -> {out}")
    except OSError as e:
        print(f"\n(per-leg CSV not written: {e})")


if __name__ == "__main__":
    main()

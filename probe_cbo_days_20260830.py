#!/usr/bin/env python3
# probe_cbo_days_20260830.py
#
# ── CBO_V1 DEAD-DAY PROBE ── read-only. Classifies EVERY business day in a
# range by the first gate in the CBO chain that would have killed it, and
# measures the two quantities the competing hypotheses disagree about:
#
#     H-threshold : entries die when ATM premium < the band MIN, because the
#                   band then selects ITM strikes whose 1m prints are sparse
#                   (fails at boundary sampling and/or at exact-minute fill).
#     H-coverage  : days die at covered=False (expected weekly expiry not in
#                   corpus, e.g. holiday-shifted expiries).
#     H-spot      : days die for lack of SPOT rows.
#
# For each day it reports:
#     spot_bars        SPOT 1m rows present
#     covered          selector coverage verdict + skip_reason
#     atm_prem         premium of the strike nearest spot at 3 sample
#                      boundaries (10:00:30 / 12:00:30 / 14:00:30) — the
#                      number H-threshold says decides everything
#     sel_CE/sel_PE    contracts selected per side at those boundaries
#     pick_printrate   the would-be pick's 1m prints ÷ session minutes —
#                      the liquidity number
#
# Nothing is written. Run from the repo root:
#     python3 probe_cbo_days_20260830.py --from 2023-01-01 --to 2023-12-31 \
#         --min 150 --max 200
# Add --csv probe_out.csv to get the full per-day table for upload.
#
# Runtime note: ~1-2 s/day (full-day preload per day). Probe a YEAR at a
# time, not the whole span at once.

from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path("backend").resolve()))

IST = 5 * 3600 + 30 * 60
DOW = "Mon Tue Wed Thu Fri".split()


def day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", required=True)
    ap.add_argument("--min", type=float, default=150.0)
    ap.add_argument("--max", type=float, default=200.0)
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    from app.utils.app_paths import APP_HOME
    from app.utils.market_hours import is_trading_day
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts)
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day

    db = str(APP_HOME / "backtest" / "backtest.db")
    src = CandleSource(db)
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    d0 = date.fromisoformat(args.dfrom)
    d1 = date.fromisoformat(args.dto)
    rows_out = []
    classes = Counter()
    atm_by_class = defaultdict(list)

    d = d0
    while d <= d1:
        if not is_trading_day(d):
            d += timedelta(days=1)
            continue
        ds = day_start_epoch(d)
        rec = {"date": d.isoformat(), "dow": DOW[d.weekday()],
               "expected_expiry": expected_expiry_for_day(d).isoformat()}

        n_spot = conn.execute(
            "SELECT COUNT(*) c FROM backtest_candles_1m WHERE underlying=? "
            "AND instrument_type='SPOT' AND ts>=? AND ts<?",
            (args.underlying, ds, ds + 86400)).fetchone()["c"]
        rec["spot_bars"] = n_spot
        if n_spot == 0:
            rec["class"] = "NO_SPOT"
            rows_out.append(rec); classes["NO_SPOT"] += 1
            d += timedelta(days=1); continue

        src.preload_day(args.underlying, ds)
        tl = build_selection_timeline(
            src=src, underlying=args.underlying, day_start_epoch=ds,
            cfg={"option_premium": {"min": args.min, "max": args.max},
                 "trade_side_mode": "BOTH"},
            strategy_id="CBO_PROBE", scope_to_expected_expiry=True)
        rec["covered"] = bool(tl.get("covered"))
        rec["skip_reason"] = tl.get("skip_reason") or ""
        if not tl.get("covered"):
            # what expiries DOES the corpus hold today? (holiday-shift check)
            have = sorted({r["expiry"] for r in conn.execute(
                "SELECT DISTINCT expiry FROM backtest_candles_1m WHERE "
                "underlying=? AND instrument_type IN ('CE','PE') AND ts>=? "
                "AND ts<? LIMIT 8", (args.underlying, ds, ds + 86400))
                if r["expiry"]})
            rec["corpus_expiries"] = "|".join(str(x) for x in have[:4])
            rec["class"] = "UNCOVERED"
            rows_out.append(rec); classes["UNCOVERED"] += 1
            d += timedelta(days=1); continue

        # sample three boundaries; measure ATM premium + selection + liquidity
        atm_prems, sel_ce, sel_pe, printrates = [], [], [], []
        for hh, mm in ((10, 0), (12, 0), (14, 0)):
            be = ds + hh * 3600 + mm * 60 + 30
            spot = src.spot_at(args.underlying, be)
            if spot is None:
                continue
            # ATM premium = premium of the CE at the strike nearest spot
            # (band-independent; the H-threshold decision variable)
            k = round(spot / 50.0) * 50
            r = conn.execute(
                "SELECT close FROM backtest_candles_1m WHERE underlying=? AND "
                "instrument_type='CE' AND strike=? AND expiry=? AND ts<=? "
                "AND ts>=? ORDER BY ts DESC LIMIT 1",
                (args.underlying, float(k), rec["expected_expiry"],
                 (be // 60) * 60, be - 1800)).fetchone()
            if r:
                atm_prems.append(float(r["close"]))
            snap = active_snapshot_for_ts(tl, be + 1)
            ce = [o for o in snap if o["type"] == "CE"]
            pe = [o for o in snap if o["type"] == "PE"]
            sel_ce.append(len(ce)); sel_pe.append(len(pe))
            for o in (ce[:1] + pe[:1]):
                nb = conn.execute(
                    "SELECT COUNT(*) c FROM backtest_candles_1m WHERE "
                    "tradingsymbol=? AND ts>=? AND ts<?",
                    (o["tradingsymbol"], ds + (9 * 60 + 15) * 60,
                     ds + (15 * 60 + 30) * 60)).fetchone()["c"]
                printrates.append(nb / 375.0)

        rec["atm_prem"] = round(st.median(atm_prems), 1) if atm_prems else None
        rec["sel_ce"] = max(sel_ce) if sel_ce else 0
        rec["sel_pe"] = max(sel_pe) if sel_pe else 0
        rec["pick_printrate"] = round(st.median(printrates), 2) if printrates else None

        if (rec["sel_ce"] == 0) or (rec["sel_pe"] == 0):
            rec["class"] = "SNAPSHOT_EMPTY_SIDE"
        elif rec["pick_printrate"] is not None and rec["pick_printrate"] < 0.9:
            rec["class"] = "PICK_ILLIQUID"       # fills will miss minutes
        else:
            rec["class"] = "TRADEABLE"
        classes[rec["class"]] += 1
        if rec["atm_prem"] is not None:
            atm_by_class[rec["class"]].append(rec["atm_prem"])
        rows_out.append(rec)
        d += timedelta(days=1)

    print(f"\n== CLASSIFICATION ({args.dfrom}..{args.dto}, band "
          f"{args.min}-{args.max}) ==")
    for k, v in classes.most_common():
        med = (f"  median ATM prem {st.median(atm_by_class[k]):.0f}"
               if atm_by_class.get(k) else "")
        print(f"  {k:22} {v:4}{med}")

    # the H-threshold headline: ATM premium on TRADEABLE vs dead days
    dead = [r["atm_prem"] for r in rows_out
            if r.get("atm_prem") is not None and r["class"] != "TRADEABLE"]
    live = atm_by_class.get("TRADEABLE", [])
    if dead and live:
        print(f"\n  H-threshold test: ATM premium median — TRADEABLE "
              f"{st.median(live):.0f} vs dead {st.median(dead):.0f} "
              f"(band min {args.min:.0f}). A clean separation at the band "
              f"min CONFIRMS the threshold mechanism; overlap falsifies it.")

    by_dow = defaultdict(Counter)
    for r in rows_out:
        by_dow[r["dow"]][r["class"]] += 1
    print("\n  by weekday:")
    for w in DOW:
        c = by_dow[w]
        tot = sum(c.values()) or 1
        print(f"    {w}: TRADEABLE {c['TRADEABLE']}/{tot}  "
              f"illiquid {c['PICK_ILLIQUID']}  empty {c['SNAPSHOT_EMPTY_SIDE']}  "
              f"uncovered {c['UNCOVERED']}  no_spot {c['NO_SPOT']}")

    if args.csv:
        cols = ["date", "dow", "class", "spot_bars", "covered", "skip_reason",
                "expected_expiry", "corpus_expiries", "atm_prem", "sel_ce",
                "sel_pe", "pick_printrate"]
        with open(args.csv, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows_out:
                w.writerow(r)
        print(f"\n  per-day table -> {args.csv}")

    src.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

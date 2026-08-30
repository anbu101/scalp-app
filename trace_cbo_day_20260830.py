#!/usr/bin/env python3
# trace_cbo_day_20260830.py
#
# ── CBO_V1 SINGLE-DAY TRACER ── read-only. Answers, for ONE day:
#   "the selection chain works (probe-verified), so where exactly do the
#    entries die?"
#
# Three independent measurements that must agree, in order of authority:
#
#   [1] GROUND TRUTH — the real run_cbo_backtest on just this day, with the
#       full diag_cbo printed. Every signal must be accounted for:
#           signals_raw == entries + Σ blocked_* (+ pessimistic-policy
#       suppressions). If the ledger does NOT balance, the runner has an
#       UNCOUNTED kill path, and [3] will name the line.
#   [2] ENGINE — cbo_signals() on the day's spot rows fetched with the
#       runner's verbatim SQL and parameters. If this is ~0, the mystery is
#       in the SIGNAL side (engine/spot interaction), not the gates.
#   [3] GATE WALK — the first N signals pushed through the runner's entry
#       gates one by one, each expression copied verbatim from
#       backtest_cbo_runner.py, printing PASS/KILL per gate per signal.
#
# Run from the repo root (AFTER the backend apply script — it imports the
# installed runner):
#     python3 trace_cbo_day_20260830.py --day 2023-05-10 --min 150 --max 200
#     python3 trace_cbo_day_20260830.py --day 2026-04-16 --min 150 --max 200   # control: an ACTIVE day
#
# Pass the SAME band and session settings as the run being investigated.

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path("backend").resolve()))

IST = 5 * 3600 + 30 * 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--min", type=float, default=150.0)
    ap.add_argument("--max", type=float, default=200.0)
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--leg", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--sess-start", default="09:20")
    ap.add_argument("--sess-end", default="15:00")
    ap.add_argument("--eod", default="15:15")
    ap.add_argument("--walk", type=int, default=10,
                    help="signals to gate-walk in section [3]")
    args = ap.parse_args()

    from app.utils.app_paths import APP_HOME
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts)
    from app.backtest.cbo.backtest_cbo_runner import (
        run_cbo_backtest, leg_side, _day_start_epoch, GRID_ANCHOR_MIN)
    from app.backtest.cbo.cbo_v1_engine import CboBar, cbo_signals

    day = date.fromisoformat(args.day)
    ds = _day_start_epoch(day)
    db = str(APP_HOME / "backtest" / "backtest.db")
    cfg = {"option_premium": {"min": args.min, "max": args.max},
           "leg_action": args.leg, "session_start": args.sess_start,
           "session_end": args.sess_end, "eod_square_off": args.eod}

    # ── [1] GROUND TRUTH: the real runner, one day ───────────────────────
    print(f"\n[1] REAL RUNNER on {day} (band {args.min}-{args.max}, "
          f"{args.leg})")
    res = run_cbo_backtest(
        db_path=db, strategy_id="CBO_V1", underlying=args.underlying,
        date_from=day, date_to=day, config_override=cfg)
    if res.get("aborted"):
        print(f"    ABORTED: {res.get('reason')}")
        return 1
    diag = res["summary"].get("diag_cbo", {})
    print(f"    trades: {len(res['trades'])}")
    nz = {k: v for k, v in diag.items()
          if isinstance(v, (int, float)) and v not in (0, 0.0)}
    for k in sorted(nz):
        print(f"    {k:28} {nz[k]}")
    sig_raw = int(diag.get("signals_raw", 0))
    accounted = int(diag.get("entries", 0)) + sum(
        int(v) for k, v in diag.items() if k.startswith("blocked_"))
    print(f"    LEDGER: signals_raw {sig_raw}  vs  entries+blocked "
          f"{accounted}  ->  {'BALANCED' if sig_raw == accounted else 'UNACCOUNTED ' + str(sig_raw - accounted)}")

    # ── [2] ENGINE, independently ────────────────────────────────────────
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    spot = [CboBar(r["ts"], r["open"], r["high"], r["low"], r["close"])
            for r in conn.execute(
                """SELECT ts, open, high, low, close
                   FROM backtest_candles_1m
                   WHERE underlying=? AND instrument_type='SPOT'
                     AND ts>=? AND ts<? ORDER BY ts""",
                (args.underlying, ds, ds + 86400))]
    anchor = ds + GRID_ANCHOR_MIN * 60
    sigs = cbo_signals(spot, anchor_ts=anchor, tf_minutes=5,
                       both_side_policy="up")   # runner's pessimistic mapping
    print(f"\n[2] ENGINE: {len(spot)} spot bars -> {len(sigs)} signals "
          f"(policy 'up', 5m)")
    if len(sigs) != sig_raw:
        print(f"    MISMATCH vs runner's signals_raw={sig_raw} — the runner "
              f"is not seeing the same bars/params this trace sees.")

    # ── [3] GATE WALK: runner expressions, verbatim ──────────────────────
    print(f"\n[3] GATE WALK (first {args.walk} signals)")
    src = CandleSource(db)
    src.preload_day(args.underlying, ds)
    timeline = build_selection_timeline(
        src=src, underlying=args.underlying, day_start_epoch=ds,
        cfg={"option_premium": {"min": args.min, "max": args.max},
             "trade_side_mode": "BOTH"},
        strategy_id="CBO_TRACE", scope_to_expected_expiry=True)
    print(f"    covered={timeline.get('covered')} "
          f"symbols={len(timeline.get('all_symbols', []))}")
    is_sell = args.leg == "SELL"

    def hhmm(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    start_min, end_min = hhmm(args.sess_start), hhmm(args.sess_end)
    opt_cache = {}

    def opt_bars(sym):
        if sym not in opt_cache:
            opt_cache[sym] = {c.ts: c
                              for c in src.candles_1m_for_symbol_day(sym, ds)}
        return opt_cache[sym]

    for s in sigs[:args.walk]:
        t = datetime.utcfromtimestamp(s.trigger_ts + IST).strftime("%H:%M")
        line = f"    {t} {s.direction:4}"
        sig_min = (s.trigger_ts - ds) // 60
        if not (start_min <= sig_min < end_min):
            print(line + f" KILL session (min {sig_min})")
            continue
        want = leg_side(s.direction, is_sell)
        snap = active_snapshot_for_ts(timeline, s.trigger_ts)
        cands = [o for o in snap if o["type"] == want]
        if not cands:
            print(line + f" KILL no_selection (snap has "
                  f"{sorted(set(o['type'] for o in snap))}, want {want})")
            continue
        pick = cands[0]
        fb = opt_bars(pick["tradingsymbol"]).get(s.fill_ts)
        if fb is None or not fb.open:
            nearby = sorted(ts for ts in opt_bars(pick["tradingsymbol"])
                            if abs(ts - s.fill_ts) <= 300)
            print(line + f" KILL no_fill  {pick['tradingsymbol']} has no bar "
                  f"at fill_ts; prints within ±5m: "
                  f"{[datetime.utcfromtimestamp(x + IST).strftime('%H:%M') for x in nearby]}")
            continue
        print(line + f" PASS -> {pick['tradingsymbol']} "
              f"strike {pick['strike']:.0f} entry~{fb.open:.2f}")

    src.close()
    conn.close()
    print("\nInterpretation:")
    print("  * [1] trades 0 + ledger UNBALANCED -> uncounted kill path in "
          "the runner; the walk shows which gate diverges.")
    print("  * [1] trades 0 + ledger balanced   -> the big blocked_* counter "
          "IS the mechanism; the walk shows it per-signal.")
    print("  * [2] ~0 signals                   -> signal-side problem; the "
          "gates are irrelevant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

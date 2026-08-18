#!/usr/bin/env python3
"""
gc_live_path_diag.py — why did GC take no live entries?

Replays TODAY (or --date) through the EXACT live data path
(_hist_minutes -> to_tf_candles -> simulate_gc_day) using the app's own
kite session, then prints the engine `diag` the runtime currently throws
away. Read-only: no orders, no state writes.

RUN (from the repo root, with the app's venv active):
    cd backend
    python3 ../gc_live_path_diag.py
    python3 ../gc_live_path_diag.py --date 2026-08-18

READING THE OUTPUT
  session_candles = 0      -> the live data path delivered nothing; the
                              engine could never signal (live bug).
  session_candles > 0 and
    trades > 0             -> engine WOULD have signalled; the gap is in
                              the manager/action layer, not the data.
    c1_range_skip = 1      -> the C1 volatility gate skipped the day.
    no_breakout = 1        -> genuine no-setup day.
"""
import argparse
import datetime as dt
import json
import sys

sys.path.insert(0, ".")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    args = ap.parse_args()

    day = (dt.date.fromisoformat(args.date) if args.date
           else dt.datetime.now(tz=IST).date())

    from app.brokers.zerodha_manager import ZerodhaManager
    from app.engine.gc import gc_runtime as R
    from app.engine.gc.gc_live_core import (
        norm_live_cfg, engine_cfg_for_day, to_tf_candles)
    from app.config.strategy_loader import load_strategy_config
    from app.backtest.gc.gc_v1_engine import simulate_gc_day

    bm = ZerodhaManager()
    kite = bm.get_data_kite()
    print(f"data kite: {'OK' if kite else 'NONE  <-- data path dead'}")
    if kite is None:
        sys.exit(1)

    # ---- 1. the live config the runtime would use ----
    raw = load_strategy_config("GC_V1") or {}
    cfg = norm_live_cfg(raw)
    print("\n--- LIVE CFG (compare against your backtest run parameters) ---")
    for k in ("mode", "exit_time", "entry_cutoff_time", "max_trades_per_day",
              "premium_max", "hedge_premium_max", "lots", "signal_mode",
              "sl_lookback", "c1_range_max_pct", "max_sl_pct"):
        print(f"  {k:22s} {cfg.get(k)}")

    # ---- 2. prev session tail (same call the runtime makes) ----
    prev_tail, prev_close = R._prev_session_tail(kite, day, cfg["sl_lookback"])
    print(f"\nprev_tail candles: {len(prev_tail)}   prev_close: {prev_close}")

    # ---- 3. today's minutes through the LIVE fetch ----
    rows = R._hist_minutes(kite, day)
    print(f"_hist_minutes rows: {len(rows)}")
    if rows:
        first, last = rows[0], rows[-1]
        print(f"  first ts {first['ts']} "
              f"({dt.datetime.fromtimestamp(first['ts'], IST):%H:%M}) "
              f"O={first['open']} C={first['close']}")
        print(f"  last  ts {last['ts']} "
              f"({dt.datetime.fromtimestamp(last['ts'], IST):%H:%M}) "
              f"O={last['open']} C={last['close']}")
    else:
        print("  !! ZERO ROWS — historical_data returned nothing for today.")

    now_epoch = int(dt.datetime.now(tz=IST).timestamp())
    closed = [r for r in rows if r["ts"] + 60 <= now_epoch]
    print(f"closed (fully elapsed) rows: {len(closed)}")

    candles = to_tf_candles(closed)
    print(f"to_tf_candles: {len(candles)}")

    # ---- 4. run the SAME engine the backtest ran ----
    day_start = R._day_start_epoch(day)
    ecfg = engine_cfg_for_day(cfg, day_start, prev_close)
    print("\n--- ENGINE CFG ---")
    print(f"  exit_epoch        {ecfg['exit_epoch']} "
          f"({dt.datetime.fromtimestamp(ecfg['exit_epoch'], IST):%H:%M})")
    print(f"  entry_cutoff      {ecfg['entry_cutoff_epoch']} "
          f"({dt.datetime.fromtimestamp(ecfg['entry_cutoff_epoch'], IST):%H:%M})")
    print(f"  c1_range_max_pct  {ecfg['c1_range_max_pct']} "
          f"(= {round((ecfg['c1_range_max_pct'] or 0)/100*(prev_close or 0), 2)} pts)")
    print(f"  sl_lookback       {ecfg['sl_lookback']}  "
          f"signal={ecfg['signal_mode']}  max_trades={ecfg['max_trades']}")

    sim = simulate_gc_day(candles, prev_tail, ecfg)
    print("\n--- ENGINE DIAG (what the runtime discards today) ---")
    print(json.dumps(sim.get("diag", {}), indent=2))
    trades = sim.get("trades", [])
    print(f"\nTRADES THE ENGINE WOULD HAVE TAKEN: {len(trades)}")
    for i, t in enumerate(trades):
        ets = dt.datetime.fromtimestamp(t.entry_ts, IST).strftime("%H:%M")
        xts = (dt.datetime.fromtimestamp(t.exit_ts, IST).strftime("%H:%M")
               if getattr(t, "exit_ts", None) else "-")
        print(f"  #{i} {t.signal_side} entry {ets} @{t.entry_spot} "
              f"sl={t.sl_level} exit {xts} reason={t.exit_reason}")

    print("\n--- VERDICT ---")
    if not rows:
        print("LIVE DATA PATH IS THE PROBLEM: historical_data returned no "
              "rows for today via the data kite.")
    elif not candles:
        print("LIVE DATA PATH IS THE PROBLEM: rows fetched but none passed "
              "the closed-candle filter.")
    elif trades:
        print("DATA IS FINE and the engine DOES signal -> the gap is in the "
              "manager/action layer (replay_and_diff / execution), not data.")
    else:
        print("Engine legitimately produced no trades on this data. Compare "
              "the LIVE CFG above with your backtest run parameters — a "
              "differing gate (c1_range_max_pct, sl_lookback, signal_mode, "
              "entry_cutoff_time) is the likely cause.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# vet_bar_probe.py — READ-ONLY. Velvet (VET_V1) 5m bars around a decision,
# computed exactly as the live engine does (the prior `warmup_sessions` spot
# sessions, each resampled on its own 09:15 anchor, + the day) from the
# RECORDED 1m spot data the parity check uses. Shows how close each bar was
# to flipping: a CE→PE flip needs the bar fully BELOW the channel (open AND
# close < bottom) and EMA fast1 < fast2.
#
#   python3 vet_bar_probe.py                       # 2026-11-02, 14:15–14:50
#   python3 vet_bar_probe.py --day 2026-11-02 --from 14:15 --to 14:50
#   (--repo /path/to/scalp-app if not /Users/anbu/dev/scalp-app)
# Writes nothing.

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-11-02")
    ap.add_argument("--from", dest="t0", default="14:15")
    ap.add_argument("--to", dest="t1", default="14:50")
    ap.add_argument("--repo", default="/Users/anbu/dev/scalp-app")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.repo).expanduser() / "backend"))
    from app.parity import parity_core as PC
    from app.backtest.gc.gc_v1_engine import resample_spot
    from app.backtest.vet.vet_v1_engine import vet_states
    from app.engine.vet.vet_live_signal_engine import session_start_epoch

    d = date.fromisoformat(a.day)
    cfg, notes = PC.config_for(d, "VET_V1")
    if not cfg:
        sys.exit(f"no saved VET_V1 config for {d}")
    tl, rl = int(cfg.get("trend_len") or 40), float(cfg.get("range_len") or 0.618)
    f1, f2 = int(cfg.get("ema_fast1") or 10), int(cfg.get("ema_fast2") or 20)
    tf, nw = int(cfg.get("signal_tf") or 5), int(cfg.get("warmup_sessions") or 10)
    pack, info = PC.build_pack(d)
    try:
        c = sqlite3.connect(pack)
        days = [r[0] for r in c.execute(
            "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') FROM backtest_candles_1m "
            "WHERE underlying='NIFTY' AND instrument_type='SPOT' ORDER BY 1")]
        if a.day not in days:
            sys.exit(f"no recorded spot for {a.day}")
        use = [x for x in days if x < a.day][-nw:] + [a.day]

        def spot(ds):
            s = PC.day_start(date.fromisoformat(ds))
            return [dict(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4]) for r in c.execute(
                "SELECT ts,open,high,low,close FROM backtest_candles_1m WHERE underlying='NIFTY' "
                "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts", (s, s + 86400))]
        bars = []
        today_rows = []
        for ds in use:
            rows = spot(ds)
            if ds == a.day:
                today_rows = rows
            bars += resample_spot(rows, tf, session_start_epoch(rows[0]["ts"]))
        st = vet_states(bars, ema_fast1=f1, ema_fast2=f2, trend_len=tl, range_len=rl)
    finally:
        try:
            os.unlink(pack)
        except Exception:
            pass

    print(f"VET_V1 {a.day} · config trend_len={tl} range_len={rl} ema {f1}/{f2} tf={tf}m · "
          f"warm-up {len(use) - 1} sessions ({use[0]}..{use[-2]}) · {len(today_rows)} 1m spot rows that day")
    for n in notes:
        print("  note:", n)
    lo = PC.day_start(d) + int(a.t0[:2]) * 3600 + int(a.t0[3:]) * 60
    hi = PC.day_start(d) + int(a.t1[:2]) * 3600 + int(a.t1[3:]) * 60
    print(f"\n{'bar':>5} {'decided':>7} {'open':>9} {'close':>9} {'ch_bot':>9} {'ch_top':>9} "
          f"{'O-bot':>7} {'C-bot':>7} {'e1-e2':>7} rng dir cond")
    for b, s in zip(bars, st):
        if not (lo <= b.ts <= hi) or not s.valid:
            continue
        t = datetime.fromtimestamp(b.ts, IST).strftime("%H:%M")
        dec = datetime.fromtimestamp(b.last1m_ts, IST).strftime("%H:%M")
        print(f"{t:>5} {dec:>7} {b.open:9.2f} {b.close:9.2f} {s.ch_bot:9.2f} {s.ch_top:9.2f} "
              f"{b.open - s.ch_bot:7.2f} {b.close - s.ch_bot:7.2f} {s.ema_f1 - s.ema_f2:7.2f} "
              f"{'Y' if s.in_range else '.':>3} {s.dir_trend:>3} {s.condition:>4}")
    missing = sorted(set(range(PC.day_start(d) + 555 * 60, PC.day_start(d) + 930 * 60, 60))
                     - {r["ts"] for r in today_rows})
    win = [m for m in missing if lo <= m <= hi]
    print("\nrecorded 1m spot minutes missing in the window: "
          + (", ".join(datetime.fromtimestamp(m, IST).strftime('%H:%M') for m in win) or "none"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# ── TSG_MTM_BASIS_20260821 ── are the worst-day gap-through candles REAL?
#
# For each of the four marquee loss days, prints around the exit minute:
#   1. NIFTY SPOT closes  (real crash => spot drops ~200-300 pts in the
#      same minute the options 5x; vendor glitch => spot barely moves)
#   2. the surviving short's option candles WITH VOLUME (a real move
#      trades huge volume on the jump candle; a glitch prints price
#      jumps on thin/zero volume)
#
# Run from repo root:  python3 tsg_gapday_spotcheck_20260821.py
# Read-only (mode=ro).

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

BT_DB = Path.home() / ".scalp-app" / "backtest" / "backtest.db"
IST = timezone(timedelta(hours=5, minutes=30))

# (date, exit HH:MM, surviving short tradingsymbol)
DAYS = [
    ((2024, 4, 18), (13, 27), "NIFTY2441822300PE"),
    ((2024, 8, 8),  (10, 7),  "NIFTY2480824300PE"),
    ((2024, 9, 12), (14, 2),  "NIFTY2491224950CE"),
    ((2025, 4, 25), (10, 1),  "NIFTY2550124200PE"),
]
WINDOW_MIN = 6   # minutes either side of the exit


def hm(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def main():
    conn = sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)

    # discover how spot rows are labeled (token 256265 per backfill)
    spot_syms = conn.execute(
        "SELECT DISTINCT tradingsymbol, instrument_type, instrument_token "
        "FROM backtest_candles_1m WHERE instrument_token = 256265 LIMIT 3"
    ).fetchall()
    print(f"spot rows labeled as: {spot_syms}")

    for (y, mo, d), (eh, em), sym in DAYS:
        day = datetime(y, mo, d, tzinfo=IST)
        ex = int(day.replace(hour=eh, minute=em).timestamp())
        lo, hi = ex - WINDOW_MIN * 60, ex + WINDOW_MIN * 60
        print()
        print("=" * 66)
        print(f"{day.date()}  exit minute {eh:02d}:{em:02d}  short = {sym}")
        print("=" * 66)

        spot = conn.execute(
            "SELECT ts, close FROM backtest_candles_1m "
            "WHERE instrument_token = 256265 AND ts BETWEEN ? AND ? "
            "ORDER BY ts", (lo, hi)).fetchall()
        if spot:
            base = spot[0][1]
            print("  SPOT:  " + "  ".join(
                f"{hm(t)}={c:.0f}({c - base:+.0f})" for t, c in spot))
            moves = [(b[1] - a[1], b[0]) for a, b in zip(spot, spot[1:])]
            worst = min(moves, key=lambda x: x[0]) if moves else (0, None)
            print(f"  SPOT max 1-min move in window: {worst[0]:+.0f} pts"
                  f" at {hm(worst[1]) if worst[1] else '-'}")
        else:
            print("  SPOT: NO ROWS for token 256265 in window "
                  "(check the label printed above; spot may use a "
                  "different token/symbol in this corpus)")

        opt = conn.execute(
            "SELECT ts, open, high, low, close, volume "
            "FROM backtest_candles_1m WHERE tradingsymbol = ? "
            "AND ts BETWEEN ? AND ? ORDER BY ts", (sym, lo, hi)).fetchall()
        print(f"  {sym}:")
        prev_close = None
        for t, o, h, l, c, v in opt:
            jump = ""
            if prev_close and prev_close > 0 and c / prev_close >= 2:
                jump = "   <== JUMP CANDLE"
            print(f"    {hm(t)}  O={o:<7} H={h:<7} L={l:<7} C={c:<7} "
                  f"vol={v}{jump}")
            prev_close = c

    print()
    print("READ IT LIKE THIS:")
    print("  Spot drops 200-300 pts in the same minute + heavy volume on the")
    print("  jump candle  => the moves are REAL. The tail days are genuine")
    print("  1-minute gap-throughs; no minute-close MTM stop can cap them,")
    print("  and only the hedge distance (H<8 finding) softens them.")
    print("  Spot moves only ~10-30 pts while options 5x, or jump candles")
    print("  print near-zero volume  => VENDOR/CORPUS GLITCH on those")
    print("  candles; the -65k/-88k tail days are artifacts and the real")
    print("  backtest tail is materially better than reported.")


if __name__ == "__main__":
    main()

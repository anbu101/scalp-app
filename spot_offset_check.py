#!/usr/bin/env python3
"""
spot_offset_check.py — is the corpus SPOT grid shifted vs Kite?

Fetches one day of NIFTY spot 1m from Kite (the LIVE source) and joins it
against backtest_candles_1m (instrument_type='SPOT', the BACKTEST source)
at offsets -60, 0, +60 seconds. Whichever offset yields the most exact
OHLC matches is the true relationship between the two grids.

Read-only. Run from backend/ with the app venv:
    python3 ../spot_offset_check.py                # today
    python3 ../spot_offset_check.py --date 2026-08-18
"""
import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DB = Path.home() / ".scalp-app" / "backtest" / "backtest.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--underlying", default="NIFTY")
    args = ap.parse_args()
    day = (dt.date.fromisoformat(args.date) if args.date
           else dt.datetime.now(tz=IST).date())

    from app.brokers.zerodha_manager import ZerodhaManager
    from app.engine.gc import gc_runtime as R

    kite = ZerodhaManager().get_data_kite()
    if kite is None:
        sys.exit("no data kite")

    kite_rows = R._hist_minutes(kite, day)
    if not kite_rows:
        sys.exit(f"kite returned no spot rows for {day}")
    kmap = {r["ts"]: r for r in kite_rows}
    print(f"kite rows: {len(kite_rows)}  "
          f"{dt.datetime.fromtimestamp(kite_rows[0]['ts'], IST):%H:%M}"
          f" -> {dt.datetime.fromtimestamp(kite_rows[-1]['ts'], IST):%H:%M}")

    ds = int(dt.datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    con = sqlite3.connect(str(DB))
    corpus = con.execute(
        "SELECT ts, open, high, low, close FROM backtest_candles_1m "
        "WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<? "
        "ORDER BY ts", (args.underlying, ds, ds + 86400)).fetchall()
    con.close()
    if not corpus:
        sys.exit(f"corpus has no SPOT rows for {day}")
    print(f"corpus rows: {len(corpus)}  "
          f"{dt.datetime.fromtimestamp(corpus[0][0], IST):%H:%M}"
          f" -> {dt.datetime.fromtimestamp(corpus[-1][0], IST):%H:%M}")

    def close_enough(a, b):
        return all(abs(float(x) - float(y)) < 0.051 for x, y in zip(a, b))

    print("\noffset  exact-OHLC matches   (corpus ts + offset == kite ts)")
    best, best_n = None, -1
    for off in (-60, 0, 60):
        n = 0
        for ts, o, h, l, c in corpus:
            k = kmap.get(ts + off)
            if k and close_enough((o, h, l, c),
                                  (k["open"], k["high"], k["low"], k["close"])):
                n += 1
        print(f"{off:+5d}s  {n:5d} / {len(corpus)}")
        if n > best_n:
            best, best_n = off, n

    print("\n--- VERDICT ---")
    pct = 100.0 * best_n / max(1, len(corpus))
    if best == 0 and pct > 90:
        print("Grids AGREE. The corpus and Kite stamp the same bars the same "
              "way; today's C1 difference is an extra opening print only.")
    elif best != 0 and pct > 90:
        print(f"CORPUS IS SHIFTED {best:+d}s vs Kite ({pct:.0f}% exact match "
              f"at that offset).\nEvery SPOT-based backtest (GC, IC, PST "
              f"Sell/Hedge, TMA V1/V2) has run on a shifted series.\n"
              f"Fix: correct the backfill writer, then one-time\n"
              f"  UPDATE backtest_candles_1m SET ts = ts {-best:+d} "
              f"WHERE instrument_type='SPOT';\n"
              f"and re-validate every affected strategy.")
    else:
        print(f"INCONCLUSIVE: best offset {best:+d}s matched only {pct:.0f}%. "
              f"Vendors differ on values, not just stamps — compare a few "
              f"rows by hand before changing anything.")

    # show the first few rows side by side at the winning offset
    print(f"\nfirst 5 rows at offset {best:+d}s:")
    for ts, o, h, l, c in corpus[:5]:
        k = kmap.get(ts + best)
        kt = (f"{dt.datetime.fromtimestamp(k['ts'], IST):%H:%M} "
              f"O={k['open']} H={k['high']} L={k['low']} C={k['close']}"
              if k else "— no kite bar —")
        print(f"  corpus {dt.datetime.fromtimestamp(ts, IST):%H:%M} "
              f"O={o} H={h} L={l} C={c}   |  kite {kt}")


if __name__ == "__main__":
    main()
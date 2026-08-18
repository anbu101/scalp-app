#!/usr/bin/env python3
"""
option_offset_check.py — are corpus OPTION candles stamp-shifted?

The spot shift is proven and fixed. This answers the remaining question
empirically, the same way: take a CURRENTLY-LIVE NIFTY option (so Kite
still lists its instrument_token), pull Kite's 1m history for a recent
trading day, and join it against backtest_candles_1m at -60/0/+60.

Whichever offset yields the most exact OHLC matches is the truth.

Read-only. Run from backend/ with the app venv:
    python3 ../option_offset_check.py                 # yesterday, auto-pick
    python3 ../option_offset_check.py --date 2026-08-14 --symbol NIFTY26AUG24500CE
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
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: last weekday)")
    ap.add_argument("--symbol", default=None, help="corpus tradingsymbol to test")
    args = ap.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else None
    if day is None:
        day = dt.datetime.now(tz=IST).date() - dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)

    from app.brokers.zerodha_manager import ZerodhaManager
    kite = ZerodhaManager().get_data_kite()
    if kite is None:
        sys.exit("no data kite")

    ds = int(dt.datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    con = sqlite3.connect(str(DB))

    # ---- pick a corpus option with a full day of rows ----
    if args.symbol:
        sym = args.symbol
    else:
        row = con.execute(
            "SELECT tradingsymbol, COUNT(*) n FROM backtest_candles_1m "
            "WHERE underlying='NIFTY' AND instrument_type IN ('CE','PE') "
            "AND ts>=? AND ts<? GROUP BY tradingsymbol "
            "ORDER BY n DESC LIMIT 1", (ds, ds + 86400)).fetchone()
        if not row:
            con.close()
            sys.exit(f"no corpus option rows for {day} — pick another --date")
        sym = row[0]
    print(f"day={day}  corpus symbol={sym}")

    corpus = con.execute(
        "SELECT ts, open, high, low, close FROM backtest_candles_1m "
        "WHERE tradingsymbol=? AND ts>=? AND ts<? ORDER BY ts",
        (sym, ds, ds + 86400)).fetchall()
    con.close()
    if not corpus:
        sys.exit(f"no corpus rows for {sym} on {day}")
    print(f"corpus rows: {len(corpus)}  "
          f"{dt.datetime.fromtimestamp(corpus[0][0], IST):%H:%M} -> "
          f"{dt.datetime.fromtimestamp(corpus[-1][0], IST):%H:%M}")

    # ---- resolve the Kite token for that symbol (must be UNEXPIRED) ----
    print("loading Kite NFO instruments…")
    inst = kite.instruments("NFO")
    tok = None
    for i in inst:
        if i.get("tradingsymbol") == sym:
            tok = i["instrument_token"]
            break
    if tok is None:
        sys.exit(f"{sym} not in Kite's live NFO list (expired contract).\n"
                 f"Re-run with --symbol set to a CURRENTLY-LIVE contract that "
                 f"also has corpus rows, e.g. a monthly-expiry strike.")
    print(f"kite token: {tok}")

    frm = dt.datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    to = dt.datetime(day.year, day.month, day.day, 15, 45, tzinfo=IST)
    raw = kite.historical_data(tok, frm, to, "minute") or []
    kmap = {int(r["date"].timestamp()): r for r in raw}
    print(f"kite rows: {len(raw)}  "
          + (f"{dt.datetime.fromtimestamp(min(kmap), IST):%H:%M} -> "
             f"{dt.datetime.fromtimestamp(max(kmap), IST):%H:%M}" if kmap else ""))
    if not kmap:
        sys.exit("kite returned no rows for that contract/day")

    def eq(a, b):
        return all(abs(float(x) - float(y)) < 0.051 for x, y in zip(a, b))

    # ── SCORING ── OPTIONS ARE NOT INDICES. Vendors legitimately differ on
    # the OPEN of a minute with no trade at the boundary (Kite carries the
    # prior close; Dhan reports the first actual trade), so exact-4-field
    # matching under-scores a perfectly aligned series. CLOSE is what every
    # strategy decides on, and H/L/C is the strict version — score both.
    print("\noffset   close-match      H/L/C-match   (corpus ts + off == kite ts)")
    best, best_n = None, -1
    for off in (-60, 0, 60):
        nc = nh = 0
        for ts, o, h, l, c in corpus:
            k = kmap.get(ts + off)
            if not k:
                continue
            if eq((c,), (k["close"],)):
                nc += 1
            if eq((h, l, c), (k["high"], k["low"], k["close"])):
                nh += 1
        print(f"{off:+5d}s  {nc:5d} ({100*nc/len(corpus):5.1f}%)   "
              f"{nh:5d} ({100*nh/len(corpus):5.1f}%)")
        if nc > best_n:
            best, best_n = off, nc

    pct = 100.0 * best_n / max(1, len(corpus))
    print("\n--- VERDICT ---")
    if best == 0 and pct > 70:
        print("OPTION candles are correctly stamped. The 1-minute shift was "
              "SPOT-ONLY (and stocks, same copied probe). No option repair "
              "needed.")
    elif best != 0 and pct > 70:
        print(f"OPTION candles are SHIFTED {best:+d}s vs Kite ({pct:.0f}% match).\n"
              f"This would affect EVERY strategy, not just the spot-based "
              f"six. Stop and report before any further re-validation.")
    else:
        print(f"INCONCLUSIVE: best {best:+d}s matched {pct:.0f}%. Dhan and "
              f"Kite may differ on values for this contract (illiquid strike, "
              f"different last-trade handling). Try a more liquid ATM strike.")

    print(f"\nfirst 3 rows at offset {best:+d}s:")
    for ts, o, h, l, c in corpus[:3]:
        k = kmap.get(ts + best)
        kt = (f"{dt.datetime.fromtimestamp(int(k['date'].timestamp()), IST):%H:%M} "
              f"O={k['open']} H={k['high']} L={k['low']} C={k['close']}"
              if k else "— none —")
        print(f"  corpus {dt.datetime.fromtimestamp(ts, IST):%H:%M} "
              f"O={o} H={h} L={l} C={c}  |  kite {kt}")


if __name__ == "__main__":
    main()

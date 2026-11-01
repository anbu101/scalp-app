#!/usr/bin/env python3
# fix_spot_restamp.py — ── SPOT_RESTAMP_20261031 ──
#
# The seam check (31 Oct) found the Dhan corpus' NIFTY spot ONE MINUTE EARLY on
# every sampled day (25–31 Aug): Kite ts = corpus ts + 60, identical OHLC,
# 374 common minutes + Kite's 09:15 bar with no partner. That is the SPOT_STAMP
# bug of 18 Aug coming back: spot_stamp_repair.py fixed the corpus once and set
# a marker (bt_meta 'spot_stamp_repaired'), so it never looked again — and
# spot days written after it (the late-August backfill) still landed early.
#
# This tool applies the same per-day, evidence-based repair, ignoring that
# marker:
#   * a day is touched ONLY if its first NIFTY SPOT bar is 09:14 IST
#     (days already starting 09:15 are left alone — re-running is harmless);
#   * each day: read → delete → insert at ts + 60, in ONE transaction
#     (the primary key is (instrument_token, ts), so an in-place shift could
#     collide);
#   * BEFORE changing anything, every row it will move is copied to
#     ~/.scalp-app/backtest/spot_restamp_backup_20261031.db (restore with --restore).
# Only SPOT rows move; option rows are untouched.
#
#   QUIT THE APP FIRST.
#   python3 fix_spot_restamp.py            # dry run: lists the affected days
#   python3 fix_spot_restamp.py --apply    # backup, then repair
#   python3 fix_spot_restamp.py --restore  # put the backed-up rows back
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
HOME = os.path.expanduser("~/.scalp-app/backtest")
DB = os.path.join(HOME, "backtest.db")
BACKUP = os.path.join(HOME, "spot_restamp_backup_20261031.db")
COLS = ("instrument_token, ts, underlying, tradingsymbol, instrument_type, strike, expiry, "
        "open, high, low, close, volume, oi")


def day_start(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


def affected_days(c):
    rows = c.execute("SELECT date(ts,'unixepoch','+5 hours','+30 minutes') AS d, MIN(ts) FROM backtest_candles_1m "
                     "WHERE underlying='NIFTY' AND instrument_type='SPOT' GROUP BY d ORDER BY d").fetchall()
    out = []
    for d, first in rows:
        t = datetime.fromtimestamp(int(first), IST)
        if (t.hour, t.minute) == (9, 14):
            out.append(datetime.strptime(d, "%Y-%m-%d").date())
    return out, len(rows)


def main():
    mode = "--apply" if "--apply" in sys.argv else "--restore" if "--restore" in sys.argv else "dry"
    if not os.path.exists(DB):
        sys.exit(f"✗ {DB} not found")
    c = sqlite3.connect(DB, timeout=60)
    if mode == "--restore":
        if not os.path.exists(BACKUP):
            sys.exit("✗ no backup to restore")
        c.execute("ATTACH DATABASE ? AS bk", (BACKUP,))
        days = [r[0] for r in c.execute("SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') FROM bk.backtest_candles_1m")]
        with c:
            for d in days:
                ds = day_start(datetime.strptime(d, "%Y-%m-%d").date())
                c.execute("DELETE FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='SPOT' AND ts>=? AND ts<?",
                          (ds, ds + 86400))
            c.execute(f"INSERT INTO backtest_candles_1m ({COLS}) SELECT {COLS} FROM bk.backtest_candles_1m")
        print(f"✓ restored {len(days)} day(s) from {BACKUP}")
        return
    days, total = affected_days(c)
    print(f"NIFTY spot days in the corpus: {total} · starting 09:14 (one minute early): {len(days)}")
    if days:
        print(f"  from {days[0]} to {days[-1]}")
        # contiguous ranges, for a quick read
        ranges, start, prev = [], days[0], days[0]
        for d in days[1:]:
            if (d - prev).days > 5:
                ranges.append((start, prev))
                start = d
            prev = d
        ranges.append((start, prev))
        for a, b in ranges:
            print(f"  · {a} … {b}")
    if mode == "dry" or not days:
        print("Dry run — nothing changed." if days else "Nothing to repair.")
        return
    b = sqlite3.connect(BACKUP)
    b.execute(f"CREATE TABLE IF NOT EXISTS backtest_candles_1m ({COLS.replace(', ', ' , ')} , PRIMARY KEY (instrument_token, ts))"
              .replace("instrument_token ,", "instrument_token INTEGER,").replace("ts ,", "ts INTEGER,"))
    b.commit()
    b.close()
    c.execute("ATTACH DATABASE ? AS bk", (BACKUP,))
    moved = 0
    for d in days:
        ds = day_start(d)
        with c:                                            # one transaction per day
            c.execute(f"INSERT OR IGNORE INTO bk.backtest_candles_1m ({COLS}) SELECT {COLS} FROM backtest_candles_1m "
                      "WHERE underlying='NIFTY' AND instrument_type='SPOT' AND ts>=? AND ts<?", (ds, ds + 86400))
            rows = c.execute(f"SELECT {COLS} FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='SPOT' "
                             "AND ts>=? AND ts<?", (ds, ds + 86400)).fetchall()
            c.execute("DELETE FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='SPOT' AND ts>=? AND ts<?",
                      (ds, ds + 86400))
            c.executemany(f"INSERT INTO backtest_candles_1m ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(r[0], r[1] + 60) + tuple(r[2:]) for r in rows])
            moved += len(rows)
    left, _ = affected_days(c)
    print(f"✓ moved {moved:,} spot rows on {len(days)} day(s) one minute later · backup: {BACKUP}")
    print(f"  days still starting 09:14: {len(left)} (expected 0)")
    print("  Next: Backtest → Backfill → Run seam check — it should now pass with 375 common minutes.")


if __name__ == "__main__":
    main()

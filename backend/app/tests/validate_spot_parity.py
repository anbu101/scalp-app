#!/usr/bin/env python3
# validate_spot_parity.py — mutual audit of TWO independent systems:
#   (a) the new Dhan SPOT backfill (are the candles right? are the STAMPS
#       right? a 1-minute stamp shift shows up as systematic drift here), and
#   (b) the put-call parity spot inference used by IC_V1's synthetic wings.
# They were built from different data paths; if they agree across 5 years,
# both are trustworthy. Standalone, read-only, no app imports.
#
# Method: sample trading days across the corpus; at the 09:17 bar (the IC
# entry bar) compute parity spot from the option chain
#   S = C - P + K·e^(-rτ)  at the strike with min |C-P|
# and compare with the SPOT row's close at the same bar.
#
# Usage: python3 validate_spot_parity.py [--db ~/.scalp-app/backtest/backtest.db]

import argparse
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
R = 0.065
CHECK_MIN = 9 * 60 + 17          # bar stamped 09:17 (the IC entry bar)
MAX_DAYS = 60


def day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - (5 * 3600 + 30 * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".scalp-app" / "backtest" / "backtest.db"))
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m WHERE instrument_type='SPOT' ORDER BY d""")]
    if not days:
        raise SystemExit("no SPOT rows — run the spot backfill first")
    step = max(1, len(days) // MAX_DAYS)
    sample = days[::step]
    print(f"corpus spot days: {len(days)} · sampling {len(sample)} across the span\n")

    diffs = []
    by_year = {}
    skipped = 0
    for d in sample:
        ds = day_start_epoch(d)
        bar = ds + CHECK_MIN * 60
        spot = cur.execute("""
            SELECT close FROM backtest_candles_1m
            WHERE instrument_type='SPOT' AND underlying='NIFTY' AND ts=?""",
            (bar,)).fetchone()
        if not spot:
            skipped += 1
            continue
        rows = cur.execute("""
            SELECT instrument_type t, strike k, expiry e, close c
            FROM backtest_candles_1m
            WHERE underlying='NIFTY' AND instrument_type IN ('CE','PE') AND ts=?""",
            (bar,)).fetchall()
        # dominant expiry that bar (near rollover two coexist)
        by_exp = {}
        for r in rows:
            by_exp.setdefault(r["e"], []).append(r)
        if not by_exp:
            skipped += 1
            continue
        exp, chain = max(by_exp.items(), key=lambda kv: len(kv[1]))
        pairs = {}
        for r in chain:
            slot = pairs.setdefault(float(r["k"]), [None, None])
            slot[0 if r["t"] == "CE" else 1] = float(r["c"])
        usable = {k: v for k, v in pairs.items() if v[0] and v[1]}
        if not usable:
            skipped += 1
            continue
        k = min(usable, key=lambda kk: abs(usable[kk][0] - usable[kk][1]))
        ce, pe = usable[k]
        try:
            exp_d = date.fromisoformat(exp)
        except Exception:
            skipped += 1
            continue
        tau = max(5.0, (day_start_epoch(exp_d) + (15 * 60 + 30) * 60 - bar) / 60.0) / (365 * 24 * 60)
        parity = ce - pe + k * math.exp(-R * tau)
        real = float(spot["close"])
        diff = parity - real
        diffs.append(diff)
        by_year.setdefault(d.year, []).append(diff)

    if not diffs:
        raise SystemExit("no comparable bars — check that spot and options overlap in time")
    absd = sorted(abs(x) for x in diffs)
    med = absd[len(absd) // 2]
    p95 = absd[int(len(absd) * 0.95)]
    mean_signed = sum(diffs) / len(diffs)
    print(f"samples: {len(diffs)} (skipped {skipped})")
    print(f"parity − spot:  median |err| {med:.1f} pts · p95 |err| {p95:.1f} pts · "
          f"mean SIGNED {mean_signed:+.1f} pts")
    print("\nper-year signed mean (a consistent sign here on trending opens = "
          "STAMP SHIFT between spot and option candles):")
    for y in sorted(by_year):
        v = by_year[y]
        print(f"  {y}: {sum(v)/len(v):+7.1f} pts over {len(v)} days")
    worst = sorted(zip(sample, diffs), key=lambda t: -abs(t[1]))[:5] if len(diffs) == len(sample) else []
    print("""
VERDICT GUIDE
  median ≤ ~10 pts, |signed mean| ≤ ~5   → both systems agree: spot backfill
      stamps are right AND the IC parity inference is sound. Green light.
  |signed mean| large & one-directional  → one dataset's stamps are shifted
      one minute vs the other — stop and report before building PST on it.
  p95 large but median small             → a few outlier days (rollovers,
      wild opens); acceptable, parity is per-minute noisy on gap days.""")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# analyze_tma_capture.py — did TMA's own socket starve?
#
# Reads ~/.scalp-app/tma_capture/YYYY-MM-DD.jsonl (written by the TMA tick
# engine, one line per finalized 1m candle) and reports per-minute coverage
# for SPOT and a chosen option symbol. Gap minutes on a near-ATM sold
# strike during market hours = the socket (or its subscription) starved —
# the market does not go silent on an ATM weekly for whole minutes.
#
# Usage:
#   python3 analyze_tma_capture.py                       # today, SPOT + busiest symbol
#   python3 analyze_tma_capture.py 2026-07-20 NIFTY2672124100CE
#
# Reading the output:
#   * SPOT gaps AND option gaps at the same minutes  → whole socket dropped
#     (look for [TMA_TICK][WS] closed/RECONNECTING lines in the audit log
#     at those times — 6 KiteTickers on one API key is the prime suspect).
#   * SPOT fine, option gapped                        → partial subscription
#     loss or per-token issue (rarer; send me the file).
#   * No gaps at all                                  → the socket was fine
#     and the SL lag was the inherent candle-close discipline (≤ ~75s),
#     not a bug.

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.now(IST).strftime("%Y-%m-%d")
    path = Path.home() / ".scalp-app" / "tma_capture" / f"{day}.jsonl"
    if not path.exists():
        print(f"NO CAPTURE FILE: {path}")
        print("→ the tick engine never finalized a single candle that day "
              "(socket never produced ticks), or the loop didn't start. "
              "Check the audit log for [TMA_TICK][WS] lines.")
        return
    per_sym = defaultdict(set)
    first, last = None, None
    for line in path.open():
        try:
            r = json.loads(line)
            ts = int(r["ts"])
            per_sym[r["sym"]].add(ts)
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        except Exception:
            continue
    if not per_sym:
        print(f"{path}: empty/unparseable")
        return

    print(f"{path.name}: {sum(len(v) for v in per_sym.values())} candles, "
          f"{len(per_sym)} symbols, span {hhmm(first)}–{hhmm(last)}")

    target = sys.argv[2] if len(sys.argv) > 2 else max(
        (s for s in per_sym if s != "SPOT"),
        key=lambda s: len(per_sym[s]), default=None)

    for sym in (["SPOT"] + ([target] if target else [])):
        mins = per_sym.get(sym, set())
        if not mins:
            print(f"\n{sym}: ZERO candles all day")
            continue
        lo, hi = min(mins), max(mins)
        expected = set(range(lo, hi + 60, 60))
        gaps = sorted(expected - mins)
        pct = 100.0 * len(mins) / len(expected)
        print(f"\n{sym}: {len(mins)}/{len(expected)} minutes "
              f"({pct:.1f}%) from {hhmm(lo)} to {hhmm(hi)}")
        if not gaps:
            print("  no gaps — stream was continuous")
            continue
        # collapse consecutive gap minutes into ranges
        runs, start, prev = [], gaps[0], gaps[0]
        for t in gaps[1:]:
            if t == prev + 60:
                prev = t
            else:
                runs.append((start, prev))
                start = prev = t
        runs.append((start, prev))
        print(f"  {len(gaps)} missing minutes in {len(runs)} gap(s):")
        for a, b in runs:
            n = (b - a) // 60 + 1
            print(f"    {hhmm(a)}–{hhmm(b + 60)}  ({n} min)")


if __name__ == "__main__":
    main()

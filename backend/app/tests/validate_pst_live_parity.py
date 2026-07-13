# backend/app/tests/validate_pst_live_parity.py
#
# ── PST LIVE-PARITY HARNESS ── (Phase 1 — D27)
#
# Proves the replay design's validity condition: feeding a day's 1m spot
# candles one-by-one through PSTLiveSignalEngine emits EXACTLY the signal
# set that build_signals produces over the full day in one shot (what the
# backtest sees), with no signal ever mutating after emission.
#
# TWO MODES:
#
#   --selftest [N]      Synthetic random-walk days (default 25 seeds), no
#                       DB needed. Run anywhere; proves the mechanism.
#
#   --corpus FROM TO    Replays REAL days from the backtest corpus
#                       (~/.scalp-app/backtest/backtest.db). Run on your
#                       machine; proves the mechanism on real data:
#                         python3 -m app.tests.validate_pst_live_parity \
#                             --corpus 2026-06-01 2026-06-30
#
# PASS = every day prints OK and the summary line says PARITY 100%.
# Any FAIL means the replay design cannot be trusted — stop the paper
# rollout and send me the failing day.

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

try:
    from app.engine.pst.pst_live_signal_engine import PSTLiveSignalEngine
except ImportError:
    sys.path.insert(0, ".")
    from pst_live_signal_engine import PSTLiveSignalEngine  # type: ignore

IST = 5 * 3600 + 30 * 60


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def _sig_key(s: dict):
    return (int(s["ts"]), s["side"], round(float(s["spot"]), 4))


def replay_one_day(spot_1m, day_start, warm_spot, warm_day_start, prev_hlc,
                   label: str) -> bool:
    eng = PSTLiveSignalEngine()
    if not eng.seed_warmup(warm_spot, warm_day_start, prev_hlc):
        print(f"  {label}: SKIP (warmup rejected)")
        return True
    eng.start_day(day_start)
    incremental = []
    for c in spot_1m:
        incremental.extend(eng.on_spot_candle(c))
        if eng.frozen:
            print(f"  {label}: FAIL — engine froze: {eng.diag['freeze_reason']}")
            return False
    full = eng.reference_full_day()
    a = [_sig_key(s) for s in incremental]
    b = [_sig_key(s) for s in full]
    if a != b:
        print(f"  {label}: FAIL — incremental {len(a)} != full-day {len(b)}")
        only_inc = set(a) - set(b); only_full = set(b) - set(a)
        if only_inc:  print(f"    only incremental: {sorted(only_inc)[:5]}")
        if only_full: print(f"    only full-day  : {sorted(only_full)[:5]}")
        return False
    stale = sum(1 for s in incremental if s.get("stale"))
    print(f"  {label}: OK — {len(a)} signals, byte-parity, "
          f"{eng.diag['candles']} candles, stale={stale}")
    return True


# ── synthetic self-test ─────────────────────────────────────────────
def _synth_day(seed: int, d: date):
    rng = random.Random(seed)
    ds = _day_start_epoch(d)
    open_ts = ds + (9 * 60 + 15) * 60
    px = 25000.0 + rng.uniform(-300, 300)
    out = []
    for i in range(375):                      # 09:15 → 15:29
        ts = open_ts + 60 * i
        drift = rng.uniform(-9, 9) + (2.5 if rng.random() < 0.06 else 0) \
                - (2.5 if rng.random() < 0.06 else 0)
        o = px
        c = max(100.0, px + drift)
        h = max(o, c) + rng.uniform(0, 4)
        l = min(o, c) - rng.uniform(0, 4)
        out.append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
        px = c
    return out, ds


def selftest(n: int) -> int:
    fails = 0
    for seed in range(n):
        warm, wds = _synth_day(seed * 1000 + 1, date(2026, 7, 9))
        day, ds = _synth_day(seed * 1000 + 2, date(2026, 7, 10))
        prev_hlc = {"high": max(c["high"] for c in warm),
                    "low": min(c["low"] for c in warm),
                    "close": warm[-1]["close"]}
        if not replay_one_day(day, ds, warm, wds, prev_hlc, f"seed {seed}"):
            fails += 1
    return fails


# ── corpus mode (run on the machine that has backtest.db) ───────────
def corpus(db_path: str, d_from: date, d_to: date) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    lo, hi = _day_start_epoch(d_from), _day_start_epoch(d_to) + 86400
    days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying='NIFTY' AND instrument_type='SPOT' AND ts>=? AND ts<?
        ORDER BY d""", (lo, hi))]

    def spot_for(d: date):
        ds = _day_start_epoch(d)
        return [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying='NIFTY' AND instrument_type='SPOT'
              AND ts>=? AND ts<? ORDER BY ts""", (ds, ds + 86400))], ds

    fails = 0
    prev = None
    for d in days:
        spot, ds = spot_for(d)
        if prev is not None and spot:
            wspot, wds = prev
            prev_hlc = {"high": max(c["high"] for c in wspot),
                        "low": min(c["low"] for c in wspot),
                        "close": wspot[-1]["close"]}
            if not replay_one_day(spot, ds, wspot, wds, prev_hlc, d.isoformat()):
                fails += 1
        if spot:
            prev = (spot, ds)
    conn.close()
    return fails


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", nargs="?", const=25, type=int, default=None)
    ap.add_argument("--corpus", nargs=2, metavar=("FROM", "TO"))
    ap.add_argument("--db", default=None, help="override backtest.db path")
    args = ap.parse_args()
    if args.selftest is not None:
        f = selftest(args.selftest)
        print(f"\nPARITY {'100%' if f == 0 else 'FAILED'} — "
              f"{args.selftest - f}/{args.selftest} synthetic days clean")
        sys.exit(1 if f else 0)
    if args.corpus:
        import os
        db = args.db or os.path.expanduser("~/.scalp-app/backtest/backtest.db")
        f = corpus(db, date.fromisoformat(args.corpus[0]),
                   date.fromisoformat(args.corpus[1]))
        print(f"\nPARITY {'100%' if f == 0 else 'FAILED'} on corpus range")
        sys.exit(1 if f else 0)
    ap.print_help()
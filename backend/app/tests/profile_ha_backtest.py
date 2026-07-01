#!/usr/bin/env python3
# profile_ha_backtest.py
# ============================================================================
# Standalone profiler for the HA_V1 backtest. Run it from the repo root so the
# `app` package is importable. It profiles a real backtest slice against your
# real corpus and prints the top hot-spots by cumulative time, plus a plain-
# English verdict on WHERE the time goes (DB vs selector vs replay).
#
# USAGE (from the repo root, e.g. ~/scalp-app/backend):
#     python3 profile_ha_backtest.py
#     python3 profile_ha_backtest.py --from 2024-01-01 --to 2024-01-31
#     python3 profile_ha_backtest.py --db /Users/anbu/.scalp-app/backtest/backtest.db
#
# It changes NOTHING — read-only profiling. No trades are persisted (it calls
# the runner directly, not the queue/route that saves runs).
# ============================================================================

import argparse
import cProfile
import io
import os
import pstats
import sys
import time
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def _default_db():
    # Common location; overridable via --db.
    return os.path.expanduser("~/.scalp-app/backtest/backtest.db")


def _iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _corpus_span(db_path):
    import sqlite3
    c = sqlite3.connect(db_path)
    try:
        row = c.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM backtest_candles_1m"
        ).fetchone()
    finally:
        c.close()
    if not row or row[0] is None:
        return None, None, 0
    lo = datetime.fromtimestamp(row[0], IST).date()
    hi = datetime.fromtimestamp(row[1], IST).date()
    return lo, hi, row[2]


def _bootstrap_syspath():
    """Make `import app...` work regardless of the cwd the user launched from.
    The script may live at <root>/app/tests/profile_ha_backtest.py or anywhere;
    walk upward from this file until we find a dir that contains an `app`
    package, and put that dir on sys.path[0]."""
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    for _ in range(8):  # walk up to 8 levels
        if os.path.isdir(os.path.join(d, "app")) and \
           os.path.isfile(os.path.join(d, "app", "__init__.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # Fallback: also add cwd, in case the layout is flat.
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return None


def _discover_runner_module():
    """Find the module that defines run_ha_backtest by scanning the `app` tree
    for a file named backtest_ha_runner.py, then converting its path to a
    dotted module name relative to the repo root on sys.path."""
    roots = [p for p in sys.path if p and os.path.isdir(os.path.join(p, "app"))]
    for root in roots:
        for dirpath, _dirs, files in os.walk(os.path.join(root, "app")):
            if "backtest_ha_runner.py" in files:
                full = os.path.join(dirpath, "backtest_ha_runner.py")
                rel = os.path.relpath(full, root)
                mod = rel[:-3].replace(os.sep, ".")  # strip .py, slashes→dots
                return mod
    return None


def _find_runner():
    """Import run_ha_backtest from wherever it lives in the tree."""
    candidates = []
    discovered = _discover_runner_module()
    if discovered:
        candidates.append(discovered)
    candidates += [
        "app.backtest.ha.backtest_ha_runner",
        "app.backtest.backtest_ha_runner",
        "app.tests.backtest_ha_runner",
        "backtest_ha_runner",
    ]
    seen = set()
    last_err = None
    for mod in candidates:
        if mod in seen:
            continue
        seen.add(mod)
        try:
            m = __import__(mod, fromlist=["run_ha_backtest"])
            return getattr(m, "run_ha_backtest"), mod
        except Exception as e:  # ImportError or attr missing
            last_err = e
            continue
    raise ImportError(
        "Could not import run_ha_backtest. Tried: "
        + ", ".join(candidates)
        + f"\nLast error: {last_err!r}\n"
        + "Run from the `backend` folder with:  PYTHONPATH=. python3 <path-to-this-script>"
    )


def _verdict(stats_obj):
    """Classify the bottleneck from the profile stats."""
    # Sum cumulative time by a few signature substrings.
    buckets = {
        "DB (sqlite fetch/execute)": 0.0,
        "Selector (_select_at_boundary / build_selection_timeline)": 0.0,
        "Candle source (candle_source.py)": 0.0,
        "HA replay (heikin_ashi / ema / signal_engine / on_bar)": 0.0,
    }
    # Use tottime (time IN the function itself, not cumulative) so buckets are
    # mutually exclusive and don't double-count nested calls.
    for (fn_file, _lineno, fn_name), (cc, nc, tt, ct, _callers) in stats_obj.stats.items():
        key = f"{fn_file}:{fn_name}".lower()
        if "sqlite3" in key or "fetchall" in fn_name or "fetchone" in fn_name or fn_name == "'execute'":
            buckets["DB (sqlite fetch/execute)"] += tt
        elif "backtest_selector" in key:
            buckets["Selector (_select_at_boundary / build_selection_timeline)"] += tt
        elif "candle_source" in key:
            buckets["Candle source (candle_source.py)"] += tt
        elif ("heikin_ashi" in key or "ha_signal_engine" in key
                or "on_bar" in key or "/ema.py" in key or "/indicators/" in key):
            buckets["HA replay (heikin_ashi / ema / signal_engine / on_bar)"] += tt
    return buckets


def main():
    _bootstrap_syspath()
    ap = argparse.ArgumentParser(description="Profile the HA_V1 backtest.")
    ap.add_argument("--db", default=_default_db(),
                    help="Path to backtest.db (default: ~/.scalp-app/backtest/backtest.db)")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--prem-min", type=float, default=150.0)
    ap.add_argument("--prem-max", type=float, default=200.0)
    ap.add_argument("--lots", type=int, default=10)
    ap.add_argument("--top", type=int, default=20, help="Top N rows to print")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"ERROR: corpus not found at {args.db}\n"
              f"Pass the right path with --db /path/to/backtest.db")
        sys.exit(1)

    lo, hi, nrows = _corpus_span(args.db)
    if nrows == 0:
        print(f"ERROR: {args.db} has no rows in backtest_candles_1m.")
        sys.exit(1)
    print(f"Corpus: {args.db}")
    print(f"  span : {lo} → {hi}   ({nrows:,} candles)")

    # Default to a 1-month slice at the START of the corpus if not given.
    d_from = _iso(args.date_from) if args.date_from else lo
    if args.date_to:
        d_to = _iso(args.date_to)
    else:
        d_to = d_from + timedelta(days=31)
        if d_to > hi:
            d_to = hi
    print(f"  slice: {d_from} → {d_to}\n")

    run_ha_backtest, mod = _find_runner()
    print(f"Runner: {mod}.run_ha_backtest\n")

    cfg = {
        "option_premium": {"min": args.prem_min, "max": args.prem_max},
        "risk_reward_ratio": 2.0,
        "session": {"primary": {"start": "09:15", "end": "15:20"}},
        "quantity": {"lots": args.lots},
    }

    kwargs = dict(
        db_path=args.db, strategy_id="HA_V1", underlying=args.underlying,
        date_from=d_from, date_to=d_to, config_override=cfg,
    )

    # ── wall-clock first (uninstrumented, honest timing) ──
    print("Running (wall-clock, uninstrumented)…")
    t0 = time.time()
    res = run_ha_backtest(**kwargs)
    wall = time.time() - t0
    summ = res.get("summary", {}) or {}
    diag = summ.get("diagnostics", {}) or {}
    dwd = diag.get("days_with_data", 0) or 0
    print(f"  wall time      : {wall:.2f}s")
    print(f"  days with data : {dwd}")
    print(f"  trades         : {summ.get('total_trades', '—')}")
    if dwd:
        per_day = wall / dwd
        print(f"  per day        : {per_day:.3f}s")
        print(f"  projected 5yr  : ~{per_day * 1250 / 60:.1f} min (≈1250 trading days)")
    print()

    # ── profiled pass ──
    print("Running (profiled)…")
    pr = cProfile.Profile()
    pr.enable()
    run_ha_backtest(**kwargs)
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(args.top)
    print("\n" + "=" * 78)
    print(f"TOP {args.top} BY CUMULATIVE TIME")
    print("=" * 78)
    print(s.getvalue())

    # ── verdict ──
    print("=" * 78)
    print("VERDICT — where the time goes")
    print("=" * 78)
    buckets = _verdict(ps)
    total = max(1e-9, sum(buckets.values()))
    for name, ct in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<58} {ct:7.2f}s")
    top_bucket = max(buckets.items(), key=lambda kv: kv[1])[0]
    print()
    print("Interpretation:")
    if "DB" in top_bucket:
        print("  → DB-bound. The per-day preload cache likely isn't priming. Fix:")
        print("    call  src.preload_day(underlying, lo)  at the top of the day loop,")
        print("    or confirm the same CandleSource instance is reused across days.")
    elif "Selector" in top_bucket:
        print("  → Selector-bound. _select_at_boundary re-scans the full universe at")
        print("    every 120s boundary (~186/day). Fix (results-identical): filter the")
        print("    universe to the expected weekly expiry + a strike window ONCE per day,")
        print("    before the boundary loop, so far fewer option_premium_at calls run.")
    elif "Candle source" in top_bucket:
        print("  → Candle-source-bound. Cache is serving, but volume of lookups is high;")
        print("    reducing selector calls (above) cuts this proportionally.")
    else:
        print("  → Replay-bound (HA/EMA/signal per bar). Hardest to cut; would need to")
        print("    trim per-bar work in the indicator path. Least likely to dominate.")
    print()
    print("Paste this whole output back and I'll write the exact patch for the")
    print("dominant bucket.")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# purge_banknifty_backtest.py
#
# One-shot maintenance: delete ALL BANKNIFTY data from the backtest corpus
# (we no longer run BB / BANKNIFTY backtests). NIFTY data is untouched.
#
# What it removes from ~/.scalp-app/backtest/backtest.db:
#   * backtest_candles_1m  WHERE underlying = 'BANKNIFTY'
#       (covers the continuous-front-month FUT series 'BANKNIFTYFUT' AND every
#        BANKNIFTY option contract — both carry underlying='BANKNIFTY')
#   * backtest_candles_1s  WHERE underlying = 'BANKNIFTY'  (if the table exists)
#   * BB backtest runs + their trades (strategy_id IN ('BB_V1','BB_V2'))
#       from backtest_runs / backtest_trades (if those tables exist)
#
# SAFETY:
#   * Prints a BEFORE/AFTER row count and asks for confirmation unless --yes.
#   * Wraps deletes in a single transaction; rolls back on any error.
#   * VACUUMs at the end to reclaim disk (the corpus was ~13M candles).
#   * Read-only on every NIFTY row; only underlying='BANKNIFTY' is touched.
#
# USAGE:
#   python3 purge_banknifty_backtest.py            # interactive (asks y/N)
#   python3 purge_banknifty_backtest.py --yes      # no prompt
#   python3 purge_banknifty_backtest.py --db /path/to/backtest.db
#
# After running, the BANKNIFTY rows are gone and the file is compacted.

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def _default_db() -> str:
    # Mirrors app.utils.app_paths APP_HOME = ~/.scalp-app
    home = os.path.expanduser("~")
    return os.path.join(home, ".scalp-app", "backtest", "backtest.db")


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    r = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def _count(c: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        return int(c.execute(sql, params).fetchone()[0])
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge BANKNIFTY data from the backtest corpus")
    ap.add_argument("--db", default=_default_db(), help="path to backtest.db")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    db = args.db
    if not os.path.exists(db):
        print(f"ERROR: backtest DB not found at {db}")
        print("Pass --db /path/to/backtest.db if it lives elsewhere.")
        return 1

    c = sqlite3.connect(db, timeout=30)
    c.row_factory = sqlite3.Row

    has_1m = _table_exists(c, "backtest_candles_1m")
    has_1s = _table_exists(c, "backtest_candles_1s")
    has_runs = _table_exists(c, "backtest_runs")
    has_trades = _table_exists(c, "backtest_trades")

    # ---- BEFORE counts ----
    n1m = _count(c, "SELECT COUNT(*) FROM backtest_candles_1m WHERE underlying='BANKNIFTY'") if has_1m else 0
    n1s = _count(c, "SELECT COUNT(*) FROM backtest_candles_1s WHERE underlying='BANKNIFTY'") if has_1s else 0
    nbb_runs = _count(c, "SELECT COUNT(*) FROM backtest_runs WHERE strategy_id IN ('BB_V1','BB_V2')") if has_runs else 0
    # BB trades: rows whose run is a BB run (covers schemas where trades have no strategy col)
    nbb_trades = 0
    if has_trades and has_runs:
        nbb_trades = _count(
            c,
            "SELECT COUNT(*) FROM backtest_trades WHERE run_id IN "
            "(SELECT run_id FROM backtest_runs WHERE strategy_id IN ('BB_V1','BB_V2'))",
        )

    nifty_1m = _count(c, "SELECT COUNT(*) FROM backtest_candles_1m WHERE underlying='NIFTY'") if has_1m else 0

    print(f"DB: {db}")
    print("--- BANKNIFTY rows to DELETE ---")
    print(f"  backtest_candles_1m  (BANKNIFTY): {n1m:,}")
    print(f"  backtest_candles_1s  (BANKNIFTY): {n1s:,}")
    print(f"  backtest_runs        (BB_V1/V2):  {nbb_runs:,}")
    print(f"  backtest_trades      (BB runs):   {nbb_trades:,}")
    print(f"--- NIFTY rows to KEEP (untouched): {nifty_1m:,} (1m) ---")

    if (n1m + n1s + nbb_runs + nbb_trades) == 0:
        print("\nNothing to delete — no BANKNIFTY/BB data found. Exiting.")
        c.close()
        return 0

    if not args.yes:
        ans = input("\nProceed with deletion? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted — nothing changed.")
            c.close()
            return 0

    # ---- DELETE in one transaction ----
    try:
        c.execute("BEGIN")
        if has_trades and has_runs:
            c.execute(
                "DELETE FROM backtest_trades WHERE run_id IN "
                "(SELECT run_id FROM backtest_runs WHERE strategy_id IN ('BB_V1','BB_V2'))"
            )
        if has_runs:
            c.execute("DELETE FROM backtest_runs WHERE strategy_id IN ('BB_V1','BB_V2')")
        if has_1s:
            c.execute("DELETE FROM backtest_candles_1s WHERE underlying='BANKNIFTY'")
        if has_1m:
            c.execute("DELETE FROM backtest_candles_1m WHERE underlying='BANKNIFTY'")
        c.execute("COMMIT")
    except Exception as e:
        c.execute("ROLLBACK")
        print(f"\nERROR during delete — rolled back, nothing changed: {e!r}")
        c.close()
        return 1

    # ---- AFTER counts + reclaim space ----
    after_1m = _count(c, "SELECT COUNT(*) FROM backtest_candles_1m WHERE underlying='BANKNIFTY'") if has_1m else 0
    after_nifty = _count(c, "SELECT COUNT(*) FROM backtest_candles_1m WHERE underlying='NIFTY'") if has_1m else 0
    print("\nDeleted. Reclaiming disk space (VACUUM)…")
    try:
        c.execute("VACUUM")
    except Exception as e:
        print(f"  (VACUUM skipped: {e!r})")
    c.close()

    print("\n--- DONE ---")
    print(f"  BANKNIFTY 1m remaining: {after_1m:,} (expected 0)")
    print(f"  NIFTY 1m remaining:     {after_nifty:,} (unchanged)")
    print("BANKNIFTY/BB data purged. NIFTY corpus intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
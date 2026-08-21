#!/usr/bin/env python3
# ── TSG_MTM_BASIS_20260821 ── diagnostic: why did the DAILY run reproduce
# the old POSITION-behaviour results byte-for-byte?
#
# PART A reads ~/.scalp-app/backtest/backtest.db for run 9efcbb28* and
# recent TSG runs, and prints:
#   - config_json.mtm_sl_basis   = what the FRONTEND sent
#   - summary.diag_tsg.mtm_sl_basis = what the RUNNER echoed (only the
#     NEW backend echoes this — its absence proves an old backend executed)
#
# PART B imports your REPO runner directly (no app, no bundle) and replays
# 2024-04-18 with the run's own stored config, once per basis. This tests
# the source code itself against real corpus data.
#
# Run from repo root:  python3 tsg_basis_diag_20260821.py
# Read-only: opens both DBs with mode=ro; never writes anything.

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

BT_DB = Path.home() / ".scalp-app" / "backtest" / "backtest.db"
RUN_PREFIX = "9efcbb28"
PROBE_DAY = date(2024, 4, 18)


def part_a():
    print("=" * 64)
    print("PART A — what was sent vs what ran (backtest_runs)")
    print("=" * 64)
    if not BT_DB.exists():
        print(f"[ABORT] {BT_DB} not found")
        return None
    conn = sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT run_id, strategy_id, created_at, config_json, summary_json "
        "FROM backtest_runs WHERE strategy_id='TSG_V1' "
        "ORDER BY created_at DESC LIMIT 12").fetchall()
    target_cfg = None
    for r in rows:
        cfg = json.loads(r["config_json"] or "{}")
        summ = json.loads(r["summary_json"] or "{}")
        diag = summ.get("diag_tsg") or {}
        sent = cfg.get("mtm_sl_basis", "<KEY ABSENT>")
        ran = diag.get("mtm_sl_basis", "<NOT ECHOED>")
        mark = " <== 9efcbb28" if r["run_id"].startswith(RUN_PREFIX) else ""
        print(f"  {r['run_id'][:8]}  sent={sent:<12}  runner_echo={ran:<12}"
              f"  net={summ.get('net_pnl')}{mark}")
        if r["run_id"].startswith(RUN_PREFIX):
            target_cfg = cfg
    conn.close()
    print()
    print("  READ IT LIKE THIS:")
    print("  sent=<KEY ABSENT>                → frontend never sent the key")
    print("    (frontend not rebuilt, or run re-queued from a pre-toggle config)")
    print("  sent=DAILY, echo=<NOT ECHOED>    → OLD backend executed the run")
    print("    (backend not rebuilt, or app not restarted after the rebuild)")
    print("  sent=DAILY, echo=DAILY, but results unchanged → source bug (Part B)")
    return target_cfg


def part_b(cfg):
    print()
    print("=" * 64)
    print(f"PART B — source-truth probe, repo code, {PROBE_DAY} only")
    print("=" * 64)
    sys.path.insert(0, str(Path("backend").resolve()))
    try:
        from app.backtest.tsg.backtest_tsg_runner import run_tsg_backtest
    except Exception as e:
        print(f"[ABORT] cannot import repo runner (run from repo root): {e!r}")
        return
    if not cfg:
        print("[WARN] run 9efcbb28 not found in DB — using its known shape")
        cfg = {"entry_time": "09:16", "exit_time": "15:26", "mtm_target": 0,
               "mtm_sl": 35000, "iv_sl_delta_pts": 4, "min_entry_iv": 0.1,
               "iv_sl_pct": 25}
    base = dict(cfg)
    base["parallel_workers"] = 1
    for basis in ("DAILY", "POSITION"):
        c = dict(base)
        c["mtm_sl_basis"] = basis
        res = run_tsg_backtest(
            db_path=str(BT_DB), strategy_id="TSG_V1", underlying="NIFTY",
            date_from=PROBE_DAY, date_to=PROBE_DAY, config_override=c)
        trades = res.get("trades") or []
        diag = (res.get("summary") or {}).get("diag_tsg") or {}
        net = sum(t.net_pnl for t in trades if t.exit_price is not None)
        print(f"\n  basis={basis}   runner_echo={diag.get('mtm_sl_basis')}"
              f"   day_net={net:,.0f}")
        for t in sorted(trades, key=lambda x: x.condition):
            print(f"    {t.condition:8s} {t.symbol:26s} exit@{t.exit_price}"
                  f"  {t.exit_reason:12s} net={t.net_pnl:>10,.0f}")
    print()
    print("  READ IT LIKE THIS:")
    print("  DAILY day_net ≈ -36k (early exit) & POSITION ≈ -65k")
    print("      → source is CORRECT; the sweep ran on a stale backend.")
    print("        Rebuild (./desktop/build-scalp.sh both), fully quit and")
    print("        relaunch the app, re-queue, and confirm runner_echo=DAILY.")
    print("  BOTH ≈ -65k with runner_echo printed")
    print("      → genuine source-level bug on real data (marks/minutes path,")
    print("        not the SL comparison) — send me this full output.")


if __name__ == "__main__":
    part_b(part_a())

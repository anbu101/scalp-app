#!/usr/bin/env python3
# apply_cbo_v1_20260829.py
#
# ── CBO_V1_INIT_20260829 ── wire the CBO_V1 backtest runner into the two
# hand-maintained dispatch chains, and copy the cbo package into BOTH trees.
#
# Conventions this follows (fleet standard):
#   * assert-anchored, replace-ONCE patches — every anchor is asserted to
#     appear exactly once before any write, so a drifted file aborts loudly
#     instead of patching the wrong place
#   * dated idempotency fence — a second run is a no-op, not a double-patch
#   * staged py_compile before anything is written back
#   * dual-tree: backend/app/ AND desktop/src-tauri/backend/app/
#
# Run from the repo root:
#     python3 apply_cbo_v1_20260829.py            # apply
#     python3 apply_cbo_v1_20260829.py --check    # report only, write nothing
#
# WHAT THIS DELIBERATELY DOES NOT DO
#   It does not touch frontend/src/pages/Backtest.jsx. That file carries
#   per-strategy state, a buildConfig useCallback dep array and a
#   describeConfig arm; a blind patch there is how stale-closure bugs ship.
#   The UI panel is a separate, reviewable change. Until it lands, CBO_V1
#   runs through the API / queue with a JSON config_override, which is
#   enough to sweep it.

from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_V1_INIT_20260829"

TREES = [
    Path("backend/app"),
    Path("desktop/src-tauri/backend/app"),
]

PKG_FILES = [
    "__init__.py",
    "cbo_v1_engine.py",
    "backtest_cbo_runner.py",
    "test_cbo_engine.py",
    "test_cbo_runner_sim.py",
]

ROUTES = "api/backtest_routes.py"
QUEUE = "backtest/queue_worker.py"


# ── patch 1: the strategy allow-list ─────────────────────────────────────
ALLOW_OLD = '"VAP_V1", "VET_V1", "BB_V1", "BB_V2"):'
ALLOW_NEW = '"VAP_V1", "VET_V1", "BB_V1", "BB_V2", "CBO_V1"):'

MSG_OLD = ('"Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, '
           'IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, '
           'VAP_V1, VET_V1, BB_V1, BB_V2"')
MSG_NEW = ('"Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, '
           'IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, '
           'VAP_V1, VET_V1, BB_V1, BB_V2, CBO_V1"')


# ── patch 2: the backtest_routes dispatch arm ────────────────────────────
ROUTES_ANCHOR = '                elif req.strategy_id == "VET_V1":'
ROUTES_ARM = f'''                elif req.strategy_id == "CBO_V1":
                    # ── {FENCE} BEGIN ── previous-candle breakout on
                    # index SPOT: a forming tf bar touching or crossing the
                    # PREVIOUS bar's high/low, detected on 1m sub-bars and
                    # filled at the next 1m open. Option BUY or SELL (SELL
                    # takes the opposite contract, VET convention). SL is a
                    # SPOT level (the reference bar's other extreme); TP is
                    # an OPTION premium move. Daily MTM caps flatten and
                    # halt. Keep this chain in sync with
                    # queue_worker._dispatch_run_impl — two hand-maintained
                    # copies (the IC omission happened twice).
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.cbo.backtest_cbo_runner import run_cbo_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    cbo = run_cbo_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {{}}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {{
                        "run_id": cbo["run_id"], "summary": cbo["summary"],
                        "config": cbo.get("config", (req.config_override or {{}})),
                        "trades": cbo["trades"], "strategy_id": req.strategy_id,
                        # ── ABORT_REASON_PASSTHROUGH ── see the TMA block
                        "aborted": cbo.get("aborted"), "reason": cbo.get("reason"),
                    }}
                    # ── {FENCE} END ──
'''


# ── patch 3: the queue_worker dispatch arm ───────────────────────────────
QUEUE_ANCHOR = '    if strategy_id == "VET_V1":'
QUEUE_ARM = f'''    if strategy_id == "CBO_V1":
        # ── {FENCE} ── previous-candle breakout on index SPOT; option
        # BUY or SELL, spot-level SL and premium TP, daily MTM caps.
        # Keep this chain in sync with backtest_routes — two
        # hand-maintained copies.
        from app.backtest.cbo.backtest_cbo_runner import run_cbo_backtest
        cbo = run_cbo_backtest(db_path=str(db), strategy_id=strategy_id,
                               underlying=underlying, date_from=df, date_to=dt,
                               config_override=(config or {{}}),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {{"run_id": cbo["run_id"], "summary": cbo["summary"],
                "config": cbo.get("config", (config or {{}})), "trades": cbo["trades"],
                "strategy_id": strategy_id,
                # ── ABORT_REASON_PASSTHROUGH ── same contract as the IC arm
                "aborted": cbo.get("aborted"), "reason": cbo.get("reason")}}

'''


class Abort(Exception):
    pass


def replace_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n} times, expected exactly 1. "
                    f"The file has drifted — patch NOT applied.")
    return text.replace(old, new, 1)


def stage_compile(path: Path, text: str) -> None:
    """Compile the NEW content in a temp file before it is ever written to
    the tree. A syntax error must never reach a file the app imports."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile FAILED — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def patch_file(path: Path, edits, check: bool) -> str:
    if not path.exists():
        raise Abort(f"missing: {path}")
    orig = path.read_text()
    if FENCE in orig:
        return "already fenced — skipped"
    text = orig
    for old, new, what in edits:
        text = replace_once(text, old, new, f"{path}:{what}")
    stage_compile(path, text)
    if not check:
        path.write_text(text)
    return "patched" if not check else "would patch (clean)"


def copy_pkg(src: Path, tree: Path, check: bool) -> str:
    dst = tree / "backtest" / "cbo"
    missing = [f for f in PKG_FILES if not (src / f).exists()]
    if missing:
        raise Abort(f"source package incomplete, missing: {missing}")
    for f in PKG_FILES:
        py_compile.compile(str(src / f), doraise=True)
    if check:
        return f"would copy {len(PKG_FILES)} files -> {dst}"
    dst.mkdir(parents=True, exist_ok=True)
    for f in PKG_FILES:
        shutil.copy2(src / f, dst / f)
    return f"copied {len(PKG_FILES)} files -> {dst}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report only; write nothing")
    ap.add_argument("--src", default="cbo_pkg",
                    help="directory holding the cbo package files")
    ap.add_argument("--allow-missing-tree", action="store_true",
                    help=("proceed when desktop/src-tauri/backend/app is "
                          "absent. That tree is a build-time copy and is not "
                          "in git, so it is missing from a fresh clone but "
                          "PRESENT in a working checkout. Skipping it there "
                          "would ship a backend change the packaged app does "
                          "not have — the dual-tree rule exists for exactly "
                          "that failure."))
    args = ap.parse_args()

    src = Path(args.src)
    results = []
    try:
        present = [t for t in TREES if t.exists()]
        if not present:
            raise Abort("no backend tree found — run from the repo root")
        missing = [t for t in TREES if not t.exists()]
        if missing and not args.allow_missing_tree:
            raise Abort(
                f"dual-tree requirement NOT satisfiable: {[str(m) for m in missing]} "
                f"absent.\n  A backend change that lands in only one tree ships "
                f"an app whose bundled\n  backend differs from source. Re-run "
                f"with --allow-missing-tree only if you\n  know this checkout "
                f"genuinely has no packaged backend.")
        for tree in present:
            results.append((str(tree / "backtest/cbo"),
                            copy_pkg(src, tree, args.check)))
            results.append((str(tree / ROUTES), patch_file(
                tree / ROUTES,
                [(ALLOW_OLD, ALLOW_NEW, "allow-list"),
                 (MSG_OLD, MSG_NEW, "error message"),
                 (ROUTES_ANCHOR, ROUTES_ARM + ROUTES_ANCHOR, "dispatch arm")],
                args.check)))
            results.append((str(tree / QUEUE), patch_file(
                tree / QUEUE,
                [(QUEUE_ANCHOR, QUEUE_ARM + QUEUE_ANCHOR, "dispatch arm")],
                args.check)))
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1

    for what, how in results:
        print(f"  {how:<48} {what}")
    for t in (t for t in TREES if not t.exists()):
        print(f"  {'SKIPPED (tree absent)':<48} {t}")
    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    if not args.check:
        print("\nNext:")
        print("  1. python3 backend/app/backtest/cbo/test_cbo_engine.py")
        print("  2. python3 backend/app/backtest/cbo/test_cbo_runner_sim.py .")
        print("  3. python3 check_undefined_names.py")
        print("  4. POST /backtest/run with strategy_id CBO_V1 and a JSON "
              "config_override (the UI panel is a separate change).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

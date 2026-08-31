#!/usr/bin/env python3
# apply_brk_v1_20260830.py
#
# ── BRK_V1_20260830 ── wire the BRK_V1 backtest runner (09:25 premium
# breakout scalp) into the two hand-maintained dispatch chains, the
# strategy allow-list, the report short-code map, and copy the brk package
# into BOTH trees.
#
# Conventions (fleet standard, same as apply_cbo_v1_20260829.py):
#   * assert-anchored, replace-ONCE patches — every anchor is asserted to
#     appear exactly once before any write; a drifted file aborts loudly
#   * dated idempotency fence — a second run is a no-op
#   * staged py_compile before anything is written back
#   * .bak-FENCE backups of every patched file
#   * dual-tree: backend/app/ AND desktop/src-tauri/backend/app/
#
# Run from the repo root:
#     python3 apply_brk_v1_20260830.py --check    # report only
#     python3 apply_brk_v1_20260830.py            # apply
#
# Does NOT touch the frontend — see apply_brk_v1_ui_20260830.py.

from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

FENCE = "BRK_V1_20260830"

TREES = [
    Path("backend/app"),
    Path("desktop/src-tauri/backend/app"),
]

PKG_FILES = [
    "__init__.py",
    "backtest_brk_runner.py",
    "test_brk_runner_sim.py",
]

ROUTES = "api/backtest_routes.py"
QUEUE = "backtest/queue_worker.py"
REPORT = "backtest/report/report_engine.py"


# ── patch 1: the strategy allow-list ─────────────────────────────────────
ALLOW_OLD = '"VAP_V1", "VET_V1", "BB_V1", "BB_V2", "CBO_V1"):'
ALLOW_NEW = '"VAP_V1", "VET_V1", "BB_V1", "BB_V2", "CBO_V1", "BRK_V1"):'

MSG_OLD = ('"Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, '
           'IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, '
           'VAP_V1, VET_V1, BB_V1, BB_V2, CBO_V1"')
MSG_NEW = ('"Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, '
           'IC_V2, TSG_V1, GC_V1, PST_SELL, PST_HEDGE, TMA_V1, TMA_V2, '
           'VAP_V1, VET_V1, BB_V1, BB_V2, CBO_V1, BRK_V1"')


# ── patch 2: the backtest_routes dispatch arm ────────────────────────────
ROUTES_ANCHOR = '                elif req.strategy_id == "CBO_V1":'
ROUTES_ARM = f'''                elif req.strategy_id == "BRK_V1":
                    # ── {FENCE} BEGIN ── 09:25 premium breakout scalp:
                    # pick the CE and PE nearest-below ₹180 at 09:25, buy
                    # whichever CLOSES above ₹180 first between 09:30 and
                    # 09:35, SL −20 / TP +40 in premium, optional trail,
                    # EOD square-off. One trade a day. Keep this chain in
                    # sync with queue_worker._dispatch_run_impl — two
                    # hand-maintained copies.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.brk.backtest_brk_runner import run_brk_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    brk = run_brk_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {{}}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {{
                        "run_id": brk["run_id"], "summary": brk["summary"],
                        "config": brk.get("config", (req.config_override or {{}})),
                        "trades": brk["trades"], "strategy_id": req.strategy_id,
                        # ── ABORT_REASON_PASSTHROUGH ── see the TMA block
                        "aborted": brk.get("aborted"), "reason": brk.get("reason"),
                    }}
                    # ── {FENCE} END ──
'''


# ── patch 3: the queue_worker dispatch arm ───────────────────────────────
QUEUE_ANCHOR = '    if strategy_id == "CBO_V1":'
QUEUE_ARM = f'''    if strategy_id == "BRK_V1":
        # ── {FENCE} ── 09:25 premium breakout scalp; option BUY only,
        # premium SL/TP, EOD square-off. Keep this chain in sync with
        # backtest_routes — two hand-maintained copies.
        from app.backtest.brk.backtest_brk_runner import run_brk_backtest
        brk = run_brk_backtest(db_path=str(db), strategy_id=strategy_id,
                               underlying=underlying, date_from=df, date_to=dt,
                               config_override=(config or {{}}),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {{"run_id": brk["run_id"], "summary": brk["summary"],
                "config": brk.get("config", (config or {{}})), "trades": brk["trades"],
                "strategy_id": strategy_id,
                # ── ABORT_REASON_PASSTHROUGH ── same contract as the IC arm
                "aborted": brk.get("aborted"), "reason": brk.get("reason")}}

'''


# ── patch 4: report short-code map (strategy_checklist 2.13) ─────────────
REPORT_OLD = '    "VET_V1": "VET",   # ── VET_V1 2026-08-29 ──\n'
REPORT_NEW = ('    "VET_V1": "VET",   # ── VET_V1 2026-08-29 ──\n'
              f'    "BRK_V1": "BRK",   # ── {FENCE} ──\n')


class Abort(Exception):
    pass


def replace_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n} times, expected exactly 1. "
                    f"The file has drifted — patch NOT applied.")
    return text.replace(old, new, 1)


def stage_compile(path: Path, text: str) -> None:
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
        shutil.copy2(path, path.with_name(path.name + f".bak-{FENCE}"))
        path.write_text(text)
    return "patched" if not check else "would patch (clean)"


def copy_pkg(src: Path, tree: Path, check: bool) -> str:
    dst = tree / "backtest" / "brk"
    missing = [f for f in PKG_FILES if not (src / f).exists()]
    if missing:
        raise Abort(f"source package incomplete, missing: {missing}")
    for f in PKG_FILES:
        py_compile.compile(str(src / f), doraise=True)
    if check:
        return f"would copy {len(PKG_FILES)} files -> {dst}"
    dst.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        # --src pointed at the in-tree package itself: nothing to copy.
        return f"source is the tree package — copy skipped ({dst})"
    for f in PKG_FILES:
        shutil.copy2(src / f, dst / f)
    return f"copied {len(PKG_FILES)} files -> {dst}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--src", default="brk_pkg",
                    help="directory holding the brk package files")
    ap.add_argument("--allow-missing-tree", action="store_true",
                    help=("proceed when desktop/src-tauri/backend/app is "
                          "absent (fresh clone). In a working checkout that "
                          "tree is PRESENT and must be patched too."))
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
                f"absent. Re-run with --allow-missing-tree only if this checkout "
                f"genuinely has no packaged backend.")
        for tree in present:
            results.append((str(tree / "backtest/brk"), copy_pkg(src, tree, args.check)))
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
            results.append((str(tree / REPORT), patch_file(
                tree / REPORT,
                [(REPORT_OLD, REPORT_NEW, "short-code map")],
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
        print("  1. python3 backend/app/backtest/brk/test_brk_runner_sim.py .")
        print("  2. python3 check_undefined_names.py")
        print("  3. python3 apply_brk_v1_ui_20260830.py --check && "
              "python3 apply_brk_v1_ui_20260830.py")
        print("  4. diff -r backend/app/backtest/brk desktop/src-tauri/backend/app/backtest/brk")
    return 0


if __name__ == "__main__":
    sys.exit(main())

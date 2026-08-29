#!/usr/bin/env python3
"""
apply_stock_frame_guard_20260828.py
────────────────────────────────────────────────────────────────────────────
Fence: STOCK_FRAME_GUARD_20260828

ROOT-CAUSE fix for the dual-price-frame corpus bug.

stock_backfill takes `strike` from Dhan's rolling-ATM response and COMPOSES
the tradingsymbol from it. After a split or bonus Dhan can return the same
(day, expiry) in two frames; the composed symbols then differ, so the
delete-then-insert on (tradingsymbol, ts) cannot collapse them and BOTH
ladders land in the corpus. Nothing downstream notices, because an ATM
selector keyed on spot just picks from whichever ladder sits near the
(back-adjusted) spot price.

This patch validates every option row against that IST day's spot BEFORE
writing it, rejects anything outside one sane frame, counts the rejects, and
raises an alarm in the report when the reject share is structural rather than
noise. corpus_health --scan stays as the after-the-fact detector for corpora
built before this landed.

EDITS  backend/app/backtest/dhan/stock_backfill.py   (+ desktop tree if present)
REQUIRES  apply_corpus_health_20260828.py first (FRAME_LO / FRAME_HI live there)

Run from the repo root:  python3 apply_stock_frame_guard_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill_guard_edits as E  # noqa: E402

FENCE = "STOCK_FRAME_GUARD_20260828"
ROOTS = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
TARGET = "backtest/dhan/stock_backfill.py"
TEST_SRC = Path("_frame_guard_payload/test_frame_guard.py")


def _ro(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{label}]: anchor matched {n} times, need 1. "
                         f"Nothing written.\n--- anchor ---\n{old}")
    return text.replace(old, new, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not TEST_SRC.exists():
        raise SystemExit(f"ABORT: missing ./{TEST_SRC}")
    trees = [r for r in ROOTS if r.exists()]
    if not trees:
        raise SystemExit("ABORT: run me from the repo root")
    print(f"trees: {', '.join(str(t) for t in trees)}")

    staged = {}
    for r in trees:
        if not (r / "backtest/util/corpus_health.py").exists():
            raise SystemExit(f"ABORT: {r}/backtest/util/corpus_health.py "
                             f"missing — run apply_corpus_health_20260828.py "
                             f"first (FRAME_LO/FRAME_HI live there)")
        p = r / TARGET
        if not p.exists():
            raise SystemExit(f"ABORT: missing {p}")
        t = p.read_text()
        if FENCE in t:
            continue
        if "from typing import" not in t:
            raise SystemExit(f"ABORT: {p} has no typing import to rely on")
        t = _ro(t, E.OLD_IMPORTS_ANCHOR, E.NEW_IMPORTS_ANCHOR, "consts")
        t = _ro(t, E.OLD_WRITE, E.NEW_WRITE, "write-guard")
        t = _ro(t, E.OLD_SETUP, E.NEW_SETUP, "spot-map")
        t = _ro(t, E.OLD_CALL, E.NEW_CALL, "call-site")
        t = _ro(t, E.OLD_REPORT, E.NEW_REPORT, "report")
        staged[p] = t
        tp = r / "backtest/dhan/test_frame_guard.py"
        body = TEST_SRC.read_text()
        if not tp.exists() or tp.read_text() != body:
            staged[tp] = body

    if not staged:
        print(f"already fenced ({FENCE}) in every tree — nothing to do")
        return

    with tempfile.TemporaryDirectory() as td:
        for path, text in staged.items():
            probe = Path(td) / path.name
            probe.write_text(text)
            try:
                py_compile.compile(str(probe), doraise=True)
            except py_compile.PyCompileError as e:
                raise SystemExit(f"ABORT: staged compile failed for {path}\n{e}")
    print(f"staged compile OK ({len(staged)} files)")

    if a.dry_run:
        for p in sorted(staged):
            print(f"  would write {p}")
        return

    import shutil
    for path, text in staged.items():
        if path.exists():
            shutil.copy2(path, str(path) + f".bak-{FENCE}")
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  cd backend && python3 -m pyflakes app/backtest/dhan/stock_backfill.py")
    print("  python3 app/backtest/dhan/test_frame_guard.py")


if __name__ == "__main__":
    sys.exit(main())

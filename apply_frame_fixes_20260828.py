#!/usr/bin/env python3
"""
apply_frame_fixes_20260828.py
────────────────────────────────────────────────────────────────────────────
Fences: CORPUS_FRAME_REPAIR_20260828 (corpus_health replacement)
        FRAME_BREAK_GUARD_20260828   (runner edits)

Closes the two outstanding items.

1. FRAME BAND FIX + --prune
   FRAME_LO/HI were 0.60/1.45. Those bands OVERLAP their own factor-2 copies,
   and narrowing to the geometric divider still left no GAP, so genuinely bad
   rows at ratio 1.42-1.44 were filed as a second frame instead of junk.
   Bands are now 0.75/1.30, giving a real gap of [1.30, 1.50] at factor 2.
   `--prune` deletes out-of-frame and orphan rows from an ALREADY single-frame
   corpus; it refuses (>5% share) if handed a dual-frame one.

2. FRAME BREAK GUARD
   repair_frame_split stamps frame_break_dates. GC and VET now refuse a run
   whose range crosses it. Override is an explicit corpus_meta edit.

WRITES  backend/app/backtest/util/corpus_health.py       (replacement)
        backend/app/backtest/util/test_corpus_health.py  (replacement)
EDITS   backend/app/backtest/gc/backtest_gc_runner.py
        backend/app/backtest/vet/backtest_vet_runner.py

NOTE the tightened band is also the WRITE-TIME guard band imported by
stock_backfill (STOCK_FRAME_GUARD_20260828). Re-run --scan on every stock
corpus after this: rows that were in-frame under 1.45 may now read as junk.

Run from the repo root:  python3 apply_frame_fixes_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_break_edits as E  # noqa: E402

FENCE = "FRAME_BREAK_GUARD_20260828"
ROOTS = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
PAYLOAD = Path("_frame_fixes_payload")
NEW_FILES = ["corpus_health.py", "test_corpus_health.py"]
GUARD_TEST = "test_frame_guard.py"      # re-shipped: asserted the old band
RUNNERS = ["backtest/gc/backtest_gc_runner.py", "backtest/vet/backtest_vet_runner.py"]


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

    for f in NEW_FILES:
        if not (PAYLOAD / f).exists():
            raise SystemExit(f"ABORT: missing ./{PAYLOAD}/{f}")

    trees = [r for r in ROOTS if r.exists()]
    if not trees:
        raise SystemExit("ABORT: run me from the repo root")
    print(f"trees: {', '.join(str(t) for t in trees)}")

    staged = {}
    for r in trees:
        for rel in RUNNERS:
            p = r / rel
            if not p.exists():
                raise SystemExit(f"ABORT: missing {p}")
            t = p.read_text()
            if "STOCK_LOT_AUTO_20260828" not in t:
                raise SystemExit(f"ABORT: {p} is missing the lot-resolver "
                                 f"fence — run apply_stock_lot_auto first")
            if FENCE in t:
                continue
            staged[p] = _ro(t, E.OLD, E.NEW, f"break-guard:{rel}")
        for f in NEW_FILES:
            body = (PAYLOAD / f).read_text()
            dst = r / "backtest/util" / f
            if not dst.exists() or dst.read_text() != body:
                staged[dst] = body
        gt = PAYLOAD / GUARD_TEST
        gdst = r / "backtest/dhan" / GUARD_TEST
        if gt.exists() and gdst.exists() and gdst.read_text() != gt.read_text():
            staged[gdst] = gt.read_text()

    if not staged:
        print("already applied in every tree — nothing to do")
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

    for path, text in staged.items():
        if path.exists():
            shutil.copy2(path, str(path) + f".bak-{FENCE}")
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  cd backend")
    print("  python3 app/backtest/util/test_corpus_health.py")
    print("  python3 app/backtest/dhan/test_frame_guard.py")
    print("  python3 -m app.backtest.util.corpus_health --scan HDFCBANK")
    print("  python3 -m app.backtest.util.corpus_health --prune HDFCBANK --dry-run")


if __name__ == "__main__":
    sys.exit(main())

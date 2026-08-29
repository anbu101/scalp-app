#!/usr/bin/env python3
"""
apply_corpus_health_20260828.py
────────────────────────────────────────────────────────────────────────────
Fence: CORPUS_FRAME_REPAIR_20260828

Adds corpus frame diagnosis + split/bonus repair. ADDITIVE ONLY — no existing
file is modified, so this cannot disturb any sealed strategy or running job.

WRITES (into every backend tree found)
  backend/app/backtest/util/corpus_health.py
  backend/app/backtest/util/test_corpus_health.py

Requires apply_stock_lot_auto_20260828.py to have run first (corpus_meta).

Run from the repo root:  python3 apply_corpus_health_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CORPUS_FRAME_REPAIR_20260828"
ROOTS = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
PAYLOAD = Path("_corpus_health_payload")
FILES = ["corpus_health.py", "test_corpus_health.py"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for f in FILES:
        if not (PAYLOAD / f).exists():
            raise SystemExit(f"ABORT: missing ./{PAYLOAD}/{f}")

    trees = [r for r in ROOTS if r.exists()]
    if not trees:
        raise SystemExit("ABORT: run me from the repo root (no backend/app found)")
    print(f"trees: {', '.join(str(t) for t in trees)}")

    for r in trees:
        if not (r / "backtest/util/lot_sizes.py").exists():
            raise SystemExit(f"ABORT: {r}/backtest/util/lot_sizes.py missing — "
                             f"run apply_stock_lot_auto_20260828.py first "
                             f"(corpus_meta lives there)")

    staged = {}
    for r in trees:
        for f in FILES:
            body = (PAYLOAD / f).read_text()
            dst = r / "backtest/util" / f
            if not dst.exists() or dst.read_text() != body:
                staged[dst] = body

    if not staged:
        print(f"already applied ({FENCE}) in every tree — nothing to do")
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
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  cd backend")
    print("  python3 app/backtest/util/test_corpus_health.py")
    print("  python3 -m app.backtest.util.corpus_health --scan HDFCBANK")


if __name__ == "__main__":
    sys.exit(main())

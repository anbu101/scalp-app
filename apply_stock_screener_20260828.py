#!/usr/bin/env python3
"""
apply_stock_screener_20260828.py
────────────────────────────────────────────────────────────────────────────
Fence: STOCK_SCREENER_20260828

Adds an OPTIONAL daily equity-screener entry gate to VET (default OFF).

WRITES   backend/app/backtest/util/screener.py
         backend/app/backtest/util/test_screener.py
EDITS    backend/app/backtest/vet/backtest_vet_runner.py
         (defaults, coercion, diag counter, entry gate, summary line)

The gate blocks ENTRIES only. Exits, rolls, SL/TP and EOD square are
untouched — a position opened on a selected day is managed to its own exit.

Run from the repo root:  python3 apply_stock_screener_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screener_edits as E  # noqa: E402

FENCE = "STOCK_SCREENER_20260828"
ROOTS = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
PAYLOAD = Path("_screener_payload")
NEW_FILES = ["screener.py", "test_screener.py"]
TARGET = "backtest/vet/backtest_vet_runner.py"


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
        p = r / TARGET
        if not p.exists():
            raise SystemExit(f"ABORT: missing {p}")
        t = p.read_text()
        if FENCE not in t:
            t = _ro(t, E.OLD_DEFAULTS, E.NEW_DEFAULTS, "defaults")
            t = _ro(t, E.OLD_COERCE, E.NEW_COERCE, "coerce")
            t = _ro(t, E.OLD_DIAG, E.NEW_DIAG, "diag")
            t = _ro(t, E.OLD_BUILD_ANCHOR, E.NEW_BUILD_ANCHOR, "build-gate")
            t = _ro(t, E.OLD_GATE_ANCHOR, E.NEW_GATE_ANCHOR, "entry-gate")
            t = _ro(t, E.OLD_SUMMARY, E.NEW_SUMMARY, "summary")
            staged[p] = t
        for f in NEW_FILES:
            body = (PAYLOAD / f).read_text()
            dst = r / "backtest/util" / f
            if not dst.exists() or dst.read_text() != body:
                staged[dst] = body

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

    for path, text in staged.items():
        if path.exists():
            shutil.copy2(path, str(path) + f".bak-{FENCE}")
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  cd backend")
    print("  python3 app/backtest/util/test_screener.py")
    print("  python3 -m pyflakes app/backtest/vet/backtest_vet_runner.py "
          "app/backtest/util/screener.py")


if __name__ == "__main__":
    sys.exit(main())

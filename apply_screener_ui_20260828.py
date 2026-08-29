#!/usr/bin/env python3
"""
apply_screener_ui_20260828.py
────────────────────────────────────────────────────────────────────────────
Fence: STOCK_SCREENER_UI_20260828

Front-end for the daily screener gate (backend fence STOCK_SCREENER_20260828).

EDITS  frontend/src/pages/Backtest.jsx
       state (7) + localStorage payload + BOTH dep arrays + buildConfig keys
       + the field row itself

STALE-CLOSURE RULE: buildConfig and the localStorage effect both read the new
state, so both dep arrays are extended in this same patch. Verified by an
assert, not by eye.

Run from the repo root:  python3 apply_screener_ui_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screener_ui_edits as E  # noqa: E402

FENCE = "STOCK_SCREENER_UI_20260828"
ROOTS = [Path("frontend/src"), Path("desktop/src-tauri/frontend/src")]
TARGET = "pages/Backtest.jsx"


def _ro(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{label}]: anchor matched {n} times, need 1. "
                         f"Nothing written.\n--- anchor ---\n{old[:200]}")
    return text.replace(old, new, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    trees = [r for r in ROOTS if (r / TARGET).exists()]
    if not trees:
        raise SystemExit(f"ABORT: no {TARGET} found — run me from the repo root")
    print(f"trees: {', '.join(str(t) for t in trees)}")

    staged = {}
    for r in trees:
        p = r / TARGET
        t = p.read_text()
        if FENCE in t:
            continue
        t = _ro(t, E.OLD_STATE, E.NEW_STATE, "state")
        t = _ro(t, E.OLD_LS, E.NEW_LS, "localStorage")
        t = _ro(t, E.OLD_DEP_LS, E.NEW_DEP_LS, "dep-localStorage")
        t = _ro(t, E.OLD_DEP_CFG, E.NEW_DEP_CFG, "dep-buildConfig")
        t = _ro(t, E.OLD_CFG, E.NEW_CFG, "buildConfig")
        t = _ro(t, E.OLD_UI, E.NEW_UI, "fields")
        staged[p] = t

    if not staged:
        print(f"already fenced ({FENCE}) in every tree — nothing to do")
        return

    # every new state name must appear in BOTH dep arrays before we write
    names = ["vetScrOn", "vetScrEmaFast", "vetScrEmaSlow", "vetScrSmaTrend",
             "vetScrVolSma", "vetScrMinVolume", "vetScrWindow"]
    for path, text in staged.items():
        lines = text.splitlines()
        dep_ls = [l for l in lines if l.rstrip().endswith("vetScrWindow]);")]
        dep_cfg = [l for l in lines
                   if l.strip().startswith("vetScrOn, vetScrEmaFast")]
        if len(dep_ls) != 1:
            raise SystemExit(f"ABORT: localStorage dep array not extended once "
                             f"in {path} (found {len(dep_ls)})")
        if len(dep_cfg) != 1:
            raise SystemExit(f"ABORT: buildConfig dep array line not unique "
                             f"in {path} (found {len(dep_cfg)})")
        for n in names:
            if n not in dep_ls[0]:
                raise SystemExit(f"ABORT: {n} missing from the localStorage dep "
                                 f"array in {path} (stale-closure rule)")
            if n not in dep_cfg[0]:
                raise SystemExit(f"ABORT: {n} missing from the buildConfig dep "
                                 f"array in {path} (stale-closure rule)")
        if text.count("screener_enabled") != 1:
            raise SystemExit(f"ABORT: buildConfig key not written once in {path}")
        for n in names:                      # declared exactly once as state
            if text.count(f"const [{n}, set") != 1:
                raise SystemExit(f"ABORT: {n} not declared exactly once in {path}")
    print("stale-closure assert OK (both dep arrays extended)")

    if a.dry_run:
        for p in sorted(staged):
            print(f"  would write {p}")
        return

    for path, text in staged.items():
        shutil.copy2(path, str(path) + f".bak-{FENCE}")
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  npx esbuild frontend/src/pages/Backtest.jsx --loader:.jsx=jsx "
          "--outfile=/dev/null")
    print("  then rebuild:  ./desktop/build-scalp.sh")


if __name__ == "__main__":
    sys.exit(main())

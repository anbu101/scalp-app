#!/usr/bin/env python3
# apply_scalp_v1_sizing_floatfix_20260824.py
#
# Fix — fence: SCALP_V1_SIZING_FLOATFIX_20260824
#
# BUG (found in run c4aa3705): risk sizing computed lots from the raw float
# stop distance. A distance of 20.000000000000004 (float dust from
# entry/sl arithmetic on paise-quantized prices) makes
#   int(13000 // (risk_pts * 65))
# floor to 9 lots instead of 10. Observed: 16/38,388 trades at 585 qty where
# 650 was correct (~Rs 17K impact). Prices are paise-quantized, so the stop
# distance is quantized to 2 decimals before the division. Deterministic and
# behavior-identical except on exact float-boundary trades, where it is now
# CORRECT.
#
# PREREQ: SCALP_V1_ENTRY_SIZING_20260823 applied. Idempotent. Run from root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_SIZING_FLOATFIX_20260824"
PREREQ = "SCALP_V1_ENTRY_SIZING_20260823"
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / RN_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


A_OLD = "                    _risk_pts = float(signal.sl) - float(signal.entry_price)"
A_NEW = ("                    # ── SCALP_V1_SIZING_FLOATFIX_20260824 ── prices are\n"
         "                    # paise-quantized; quantize the distance before the\n"
         "                    # floor division so 20.000000000000004 sizes as 20.0.\n"
         "                    _risk_pts = round(float(signal.sl) - float(signal.entry_price), 2)")


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / RN_REL
        t = p.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        if PREREQ not in t:
            _die(f"prerequisite fence {PREREQ} MISSING in {p}")
        n = t.count(A_OLD)
        if n != 1:
            _die(f"anchor matched {n} times (want 1) in {p} — NOTHING written")
        t = t.replace(A_OLD, A_NEW, 1)
        staged.append((p, t))
    for p, t in staged:
        try:
            compile(t, str(p), "exec")
        except SyntaxError as e:
            _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. Affects only exact float-boundary")
    print("trades (16 in the observed run); all other results byte-identical.")


if __name__ == "__main__":
    main()

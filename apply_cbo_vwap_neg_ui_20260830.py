#!/usr/bin/env python3
# apply_cbo_vwap_neg_ui_20260830.py
#
# ── CBO_VWAP_NEG_UI_20260830 ── "VWAP min (pt)" accepts NEGATIVE values
# (Anbu 2026-08-30). Negative = tolerance band: UP entries are allowed with
# the close up to |x| pts BELOW the session VWAP (DOWN mirrored) — the
# SCALP V1 Config B "within +10" semantics. The runner's gate math
# (close − vwap >= min_pts) already handles negatives end-to-end; what
# blocked entry was the controlled input's onChange doing
# Number(e.target.value): typing the leading "-" yields Number("-") = NaN,
# the input breaks, and negatives can't be typed. FIX: the field holds the
# RAW STRING; buildConfig already coerces with Number(...) || 0, which also
# neutralises the transient "-"/"" states. Tooltip documents the negative
# semantics at the point of use.
#
#     python3 apply_cbo_vwap_neg_ui_20260830.py --check
#     python3 apply_cbo_vwap_neg_ui_20260830.py

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_VWAP_NEG_UI_20260830"
NEEDS = "CBO_D10_FILTERS_UI_20260830"
TARGET = Path("frontend/src/pages/Backtest.jsx")

E1_OLD = ('<Field label="VWAP min (pt)"><input type="number" style={inputStyle} '
          'value={cboVwapMin} onChange={(e) => setCboVwapMin(Number(e.target.value))} /></Field>')
E1_NEW = ('''<Field label="VWAP min (pt)"><input type="number" step="0.5" style={inputStyle}
                    value={cboVwapMin} onChange={(e) => setCboVwapMin(e.target.value)}
                    title="Signed. Positive x: UP needs close ≥ VWAP + x (DOWN mirrored). NEGATIVE x = tolerance band: UP allowed with close up to |x| pts BELOW VWAP — the Config B 'within' semantics. 0 = simple above/below." /></Field>'''
          f'{{/* ── {FENCE} ── raw-string state: Number() in onChange turned the '
          'transient "-" into NaN and made negatives untypable; buildConfig '
          'coerces with Number(...) || 0 which absorbs "-"/"" safely */}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    args = ap.parse_args()
    if not TARGET.exists():
        print(f"ABORTED: {TARGET} not found — run from the repo root",
              file=sys.stderr)
        return 1
    orig = TARGET.read_text()
    if FENCE in orig:
        print(f"  already fenced — skipped   {TARGET}")
        return 0
    if NEEDS not in orig:
        print(f"ABORTED: {TARGET} lacks {NEEDS} — apply the D10-filters UI "
              f"patch first.", file=sys.stderr)
        return 1
    n = orig.count(E1_OLD)
    if n != 1:
        print(f"\nABORTED: field anchor found {n}x, expected 1 — drifted; "
              f"nothing written.", file=sys.stderr)
        return 1
    t = orig.replace(E1_OLD, E1_NEW, 1)

    if not args.no_esbuild:
        tmp = TARGET.parent / "_cbo_vn_stage.jsx"
        tmp.write_text(t)
        try:
            r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                                "--loader:.jsx=jsx", "--outfile=/dev/null"],
                               capture_output=True, text=True, cwd=".")
            if r.returncode != 0:
                print(f"\nABORTED: esbuild rejected the patched file:\n"
                      f"{r.stderr[:1200]}", file=sys.stderr)
                return 1
        except FileNotFoundError:
            print("  WARNING: npx not found — JSX gate SKIPPED",
                  file=sys.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    if args.check:
        print(f"  would patch (clean, esbuild OK)   {TARGET}")
        return 0
    shutil.copy2(TARGET, TARGET.with_suffix(f".jsx.bak-{FENCE}"))
    TARGET.write_text(t)
    print(f"  patched (backup .bak-{FENCE})   {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

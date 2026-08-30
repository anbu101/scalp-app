#!/usr/bin/env python3
# apply_cbo_prem_sl_ui_20260830.py
#
# ── CBO_PREM_SL_UI_20260830 ── two fields on the CBO panel for the premium
# stop (D9): "Premium SL" mode select (off / abs ₹ / % of entry) + value.
# Companion to apply_cbo_prem_sl_20260830.py (backend) — run that FIRST.
#
# Seven assert-anchored insertions into frontend/src/pages/Backtest.jsx
# (which must already carry CBO_V1_UI_20260829): state, persistence JSON,
# persistence deps, buildConfig emit, buildConfig deps (STALE-CLOSURE RULE:
# both new state names land in the dep array in this same patch, verified
# mechanically below), describeConfig, panel. esbuild parse gate before
# any write.
#
#     python3 apply_cbo_prem_sl_ui_20260830.py --check
#     python3 apply_cbo_prem_sl_ui_20260830.py

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_PREM_SL_UI_20260830"
NEEDS = "CBO_V1_UI_20260829"
TARGET = Path("frontend/src/pages/Backtest.jsx")

E1_OLD = '  const [cboTargetValue, setCboTargetValue] = useState(cboSaved.targetValue ?? 10);'
E1_NEW = E1_OLD + f'''
  // ── {FENCE} ── premium stop, ADDITIVE to the spot-level stop
  // (tighter-wins). off = spot stop only, byte-identical baseline.
  const [cboSlMode, setCboSlMode] = useState(cboSaved.slMode ?? "off");
  const [cboSlValue, setCboSlValue] = useState(cboSaved.slValue ?? 0);'''

E2_OLD = 'targetMode: cboTargetMode, targetValue: cboTargetValue, sessStart: cboSessStart'
E2_NEW = 'targetMode: cboTargetMode, targetValue: cboTargetValue, slMode: cboSlMode, slValue: cboSlValue, sessStart: cboSessStart'

E3_OLD = ('}, [cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSessStart')
E3_NEW = ('}, [cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, cboSlValue, cboSessStart')

E4_OLD = '        target_value: Number(cboTargetValue) || 0,'
E4_NEW = E4_OLD + f'''
        sl_prem_mode: cboSlMode,               // ── {FENCE} ──
        sl_prem_value: Number(cboSlValue) || 0,'''

E5_OLD = ('    cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSessStart, '
          'cboSessEnd, cboEodTime, cboMaxTrades, cboMtmLoss, cboMtmProfit, '
          'cboMtmIncludeOpen, cboCooldown, cboSkipExpiry, cboSkewOn, cboSkewMin, '
          'cboSkewInvert, cboSkewParity, cboSkewCarry,')
E5_NEW = ('    cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, cboSlValue, cboSessStart, '
          'cboSessEnd, cboEodTime, cboMaxTrades, cboMtmLoss, cboMtmProfit, '
          'cboMtmIncludeOpen, cboCooldown, cboSkipExpiry, cboSkewOn, cboSkewMin, '
          'cboSkewInvert, cboSkewParity, cboSkewCarry,   '
          f'// ── {FENCE} ── stale-closure rule: buildConfig reads the two '
          'new fields, so they land here in the SAME commit')

E6_OLD = '    add("SL", "prev-candle spot level");'
E6_NEW = f'''    add("SL", cfg.sl_prem_mode && cfg.sl_prem_mode !== "off"
      ? `prev-candle spot + prem ${{cfg.sl_prem_mode === "pct" ? `${{cfg.sl_prem_value}}%` : `₹${{cfg.sl_prem_value}}`}} (tighter wins)`
      : "prev-candle spot level");   // ── {FENCE} ──'''

E7_OLD = ('<Field label={cboTargetMode === "pct" ? "Target %" : "Target ₹"}>'
          '<input type="number" style={inputStyle} value={cboTargetValue} '
          'onChange={(e) => setCboTargetValue(Number(e.target.value))} /></Field>')
E7_NEW = E7_OLD + """
                <Field label="Premium SL">
                  <select style={inputStyle} value={cboSlMode} onChange={(e) => setCboSlMode(e.target.value)}
                    title="ADDITIVE premium stop — the trade exits on whichever of premium-SL / spot-SL / TP triggers first. off = spot stop only (baseline). Fill is at the stop level; in the same minute any SL beats TP, and if both stops trigger the WORSE fill is booked. NOTE a tighter stop also converts some would-be winners into losses — win rate is EXPECTED to drop; read sl_prem vs sl_spot shares in the run diag before judging.">
                    <option value="off">off (spot only)</option>
                    <option value="abs">absolute ₹</option>
                    <option value="pct">% of entry</option>
                  </select>
                </Field>
                {cboSlMode !== "off" && (
                  <Field label={cboSlMode === "pct" ? "Prem SL %" : "Prem SL ₹"}><input type="number" style={inputStyle} value={cboSlValue} onChange={(e) => setCboSlValue(Number(e.target.value))} /></Field>
                )}"""

EDITS = [(E1_OLD, E1_NEW, "1 state"), (E2_OLD, E2_NEW, "2 persist json"),
         (E3_OLD, E3_NEW, "3 persist deps"), (E4_OLD, E4_NEW, "4 buildConfig"),
         (E5_OLD, E5_NEW, "5 buildConfig deps"), (E6_OLD, E6_NEW, "6 describe"),
         (E7_OLD, E7_NEW, "7 panel")]


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
        print(f"ABORTED: {TARGET} lacks {NEEDS} — apply the CBO UI patch "
              f"first.", file=sys.stderr)
        return 1
    t = orig
    for old, new, what in EDITS:
        n = t.count(old)
        if n != 1:
            print(f"\nABORTED: {what}: anchor found {n}x, expected 1 — "
                  f"drifted; nothing written.", file=sys.stderr)
            return 1
        t = t.replace(old, new, 1)

    # stale-closure assert: both new names must appear in BOTH dep arrays
    # and in buildConfig's CBO arm.
    arm = t[t.find('if (sid === "CBO_V1")'): t.find('if (sid === "VET_V1")')]
    for name in ("cboSlMode", "cboSlValue"):
        reads = name in arm
        deps = len(re.findall(rf"\b{name}\b", t)) >= 4  # state+persist+2 deps+arm+panel
        if not (reads and deps):
            print(f"\nABORTED: stale-closure check failed for {name} "
                  f"(in arm: {reads}).", file=sys.stderr)
            return 1

    if not args.no_esbuild:
        tmp = TARGET.parent / "_cbo_slui_stage.jsx"
        tmp.write_text(t)
        try:
            r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                                "--loader:.jsx=jsx", "--outfile=/dev/null"],
                               capture_output=True, text=True, cwd=".")
            if r.returncode != 0:
                print(f"\nABORTED: esbuild rejected the patched file:\n"
                      f"{r.stderr[:1500]}", file=sys.stderr)
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
    print(f"\n{FENCE} applied. npm start and eyeball the panel before "
          f"any Tauri rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

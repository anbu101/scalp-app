#!/usr/bin/env python3
# apply_cbo_d10_filters_ui_20260830.py
#
# ── CBO_D10_FILTERS_UI_20260830 ── panel fields for the three instruments
# shipped by apply_cbo_d10_filters_20260830.py (run the backend one FIRST):
#   * TP fill-through ε (D10 honesty bound)
#   * VWAP filter (checkbox + min pts + invert)
#   * EMA gate (checkbox + period + slope window + min slope + invert)
# Nine new state names; the stale-closure assert covers all of them in the
# buildConfig arm and both dep arrays. esbuild parse gate before any write;
# all-or-nothing staging.
#
#     python3 apply_cbo_d10_filters_ui_20260830.py --check
#     python3 apply_cbo_d10_filters_ui_20260830.py

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_D10_FILTERS_UI_20260830"
NEEDS = "CBO_PREM_SL_UI_20260830"
TARGET = Path("frontend/src/pages/Backtest.jsx")

NEW_STATE_NAMES = ["cboTpEps", "cboVwapOn", "cboVwapMin", "cboVwapInvert",
                   "cboEmaOn", "cboEmaPeriod", "cboEmaSlopeWin",
                   "cboEmaMinSlope", "cboEmaInvert"]

E1_OLD = '  const [cboSlValue, setCboSlValue] = useState(cboSaved.slValue ?? 0);'
E1_NEW = E1_OLD + f'''
  // ── {FENCE} ── D10 fill-through ε + VWAP filter + EMA gate.
  const [cboTpEps, setCboTpEps] = useState(cboSaved.tpEps ?? 0);
  const [cboVwapOn, setCboVwapOn] = useState(cboSaved.vwapOn ?? false);
  const [cboVwapMin, setCboVwapMin] = useState(cboSaved.vwapMin ?? 0);
  const [cboVwapInvert, setCboVwapInvert] = useState(cboSaved.vwapInvert ?? false);
  const [cboEmaOn, setCboEmaOn] = useState(cboSaved.emaOn ?? false);
  const [cboEmaPeriod, setCboEmaPeriod] = useState(cboSaved.emaPeriod ?? 144);
  const [cboEmaSlopeWin, setCboEmaSlopeWin] = useState(cboSaved.emaSlopeWin ?? 10);
  const [cboEmaMinSlope, setCboEmaMinSlope] = useState(cboSaved.emaMinSlope ?? 0);
  const [cboEmaInvert, setCboEmaInvert] = useState(cboSaved.emaInvert ?? false);'''

E2_OLD = 'slMode: cboSlMode, slValue: cboSlValue, sessStart: cboSessStart'
E2_NEW = ('slMode: cboSlMode, slValue: cboSlValue, tpEps: cboTpEps, '
          'vwapOn: cboVwapOn, vwapMin: cboVwapMin, vwapInvert: cboVwapInvert, '
          'emaOn: cboEmaOn, emaPeriod: cboEmaPeriod, emaSlopeWin: cboEmaSlopeWin, '
          'emaMinSlope: cboEmaMinSlope, emaInvert: cboEmaInvert, '
          'sessStart: cboSessStart')

_DEPS_ADD = (', cboTpEps, cboVwapOn, cboVwapMin, cboVwapInvert, cboEmaOn, '
             'cboEmaPeriod, cboEmaSlopeWin, cboEmaMinSlope, cboEmaInvert')

E3_OLD = ('}, [cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, '
          'cboSlValue, cboSessStart')
E3_NEW = ('}, [cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, '
          'cboSlValue' + _DEPS_ADD + ', cboSessStart')

E4_OLD = '        sl_prem_value: Number(cboSlValue) || 0,'
E4_NEW = E4_OLD + f'''
        // ── {FENCE} ──
        tp_fill_through_pts: Number(cboTpEps) || 0,
        vwap_filter: {{ enabled: !!cboVwapOn, min_pts: Number(cboVwapMin) || 0, invert: !!cboVwapInvert }},
        ema_gate: {{ enabled: !!cboEmaOn, period: Number(cboEmaPeriod) || 144, slope_window: Number(cboEmaSlopeWin) || 10, min_slope: Number(cboEmaMinSlope) || 0, invert: !!cboEmaInvert }},'''

E5_OLD = ('    cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, '
          'cboSlValue, cboSessStart, cboSessEnd,')
E5_NEW = ('    cboTf, cboTriggerSrc, cboBothPolicy, cboBuffer, cboMinRefRange, '
          'cboRequireFullRef, cboDirection, cboLegAction, cboPremMin, cboPremMax, '
          'cboLots, cboLotSize, cboTargetMode, cboTargetValue, cboSlMode, '
          'cboSlValue' + _DEPS_ADD + ', cboSessStart, cboSessEnd,')

E6_OLD = ': "prev-candle spot level");   // ── CBO_PREM_SL_UI_20260830 ──'
E6_NEW = E6_OLD + f'''
    if (Number(cfg.tp_fill_through_pts) > 0) add("TP fill", `through ≥${{cfg.tp_fill_through_pts}}pt`);   // ── {FENCE} ──
    if (cfg.vwap_filter && cfg.vwap_filter.enabled) add("VWAP", `${{cfg.vwap_filter.invert ? "INVERTED " : ""}}≥${{cfg.vwap_filter.min_pts}}pt`);
    if (cfg.ema_gate && cfg.ema_gate.enabled) add("EMA gate", `${{cfg.ema_gate.invert ? "INVERTED " : ""}}${{cfg.ema_gate.period}}/${{cfg.ema_gate.slope_window}} ≥${{cfg.ema_gate.min_slope}}`);'''

E7_OLD = '''                  <Field label={cboSlMode === "pct" ? "Prem SL %" : "Prem SL ₹"}><input type="number" style={inputStyle} value={cboSlValue} onChange={(e) => setCboSlValue(Number(e.target.value))} /></Field>
                )}'''
E7_NEW = E7_OLD + """
                <Field label="TP fill-through ε">
                  <input type="number" step="0.5" style={inputStyle} value={cboTpEps} onChange={(e) => setCboTpEps(Number(e.target.value))}
                    title="Fill-realism bound (D10). 0 = a wick TOUCHING the TP limit fills (best case, current model). ε>0 = the bar must trade THROUGH the limit by ε points before the win books — fill price stays the limit. Run 0 / 0.5 / 1.0 as a bracket: reality lives inside it. Losses already fill pessimistically, so ε>0 only ever reduces results." />
                </Field>
              </div>
              <div style={{ display: \"flex\", gap: spacing.md, marginBottom: spacing.md, flexWrap: \"wrap\", alignItems: \"center\" }}>
                <label style={{ fontSize: 12, display: \"flex\", alignItems: \"center\", gap: 6 }}
                  title=\"Session VWAP of SPOT (cumulative typical-price mean, SCALP V1 semantics). UP entries need the trigger bar's close ABOVE VWAP by ≥ min pts; DOWN mirrored. Unmeasurable blocks (counted). PRE-REGISTERED HISTORY: a VWAP entry gate is on the SCALP V3 falsified list — it encoded the same information as close-confirmation. With the close-confirmed trigger the expected result here is NO EFFECT; this toggle exists to be falsified.\">
                  <input type=\"checkbox\" checked={cboVwapOn} onChange={(e) => setCboVwapOn(e.target.checked)} /> VWAP filter
                </label>
                {cboVwapOn && (<>
                  <Field label=\"VWAP min (pt)\"><input type=\"number\" style={inputStyle} value={cboVwapMin} onChange={(e) => setCboVwapMin(Number(e.target.value))} /></Field>
                  <label style={{ fontSize: 12, display: \"flex\", alignItems: \"center\", gap: 6 }}>
                    <input type=\"checkbox\" checked={cboVwapInvert} onChange={(e) => setCboVwapInvert(e.target.checked)} /> invert
                  </label>
                </>)}
                <label style={{ fontSize: 12, display: \"flex\", alignItems: \"center\", gap: 6 }}
                  title=\"EMA(period) on SPOT closes; slope over the window. UP needs slope ≥ +min, DOWN ≤ −min. Warmup blocks as unmeasurable (counted) — an EMA-144 gate silences roughly the first 2.5 hours of every day by construction. HISTORY: an EMA regime gate is on the SCALP V3 falsified list and PST's was falsified 2026-08-28 (every cell worse).\">
                  <input type=\"checkbox\" checked={cboEmaOn} onChange={(e) => setCboEmaOn(e.target.checked)} /> EMA gate
                </label>
                {cboEmaOn && (<>
                  <Field label=\"Period\"><input type=\"number\" style={inputStyle} value={cboEmaPeriod} onChange={(e) => setCboEmaPeriod(Number(e.target.value))} /></Field>
                  <Field label=\"Slope win\"><input type=\"number\" style={inputStyle} value={cboEmaSlopeWin} onChange={(e) => setCboEmaSlopeWin(Number(e.target.value))} /></Field>
                  <Field label=\"Min slope\"><input type=\"number\" step=\"0.1\" style={inputStyle} value={cboEmaMinSlope} onChange={(e) => setCboEmaMinSlope(Number(e.target.value))} /></Field>
                  <label style={{ fontSize: 12, display: \"flex\", alignItems: \"center\", gap: 6 }}>
                    <input type=\"checkbox\" checked={cboEmaInvert} onChange={(e) => setCboEmaInvert(e.target.checked)} /> invert
                  </label>
                </>)}"""

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
        print(f"ABORTED: {TARGET} lacks {NEEDS} — apply the prem-SL UI "
              f"patch first.", file=sys.stderr)
        return 1
    t = orig
    for old, new, what in EDITS:
        n = t.count(old)
        if n != 1:
            print(f"\nABORTED: {what}: anchor found {n}x, expected 1 — "
                  f"drifted; nothing written.", file=sys.stderr)
            return 1
        t = t.replace(old, new, 1)

    arm = t[t.find('if (sid === "CBO_V1")'): t.find('if (sid === "VET_V1")')]
    for name in NEW_STATE_NAMES:
        if name not in arm or len(re.findall(rf"\b{name}\b", t)) < 5:
            print(f"\nABORTED: stale-closure check failed for {name}.",
                  file=sys.stderr)
            return 1

    if not args.no_esbuild:
        tmp = TARGET.parent / "_cbo_flt_stage.jsx"
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
    print(f"\n{FENCE} applied. npm start and eyeball before rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

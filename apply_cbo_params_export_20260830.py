#!/usr/bin/env python3
# apply_cbo_params_export_20260830.py
#
# ── CBO_PARAMS_EXPORT_20260830 ── two observability fixes (Anbu 2026-08-30):
#
# 1) KEY PARAMS mis-render: RunComparison's paramSummary walks a generic
#    PARAM_DEFS union built for the older strategies. Against a CBO config
#    its getters read other strategies' field names — vwap_filter.
#    min_below_pts (CBO: min_pts) -> "below ≥0"; an EMA getter -> 
#    "9/undefinedb ≥undefined"; and the EOD column reads eod_squareoff_time
#    (CBO: eod_square_off) -> "day end (legacy)" on every row. Every CBO run
#    therefore rendered near-identically. FIX: one cboParamSummary() in the
#    SHARED paramFormat.js (the four-copies debt says new formatting lands
#    there), branched into RunComparison AND BacktestQueue's local copy; the
#    EOD getter learns CBO's key.
#
# 2) CSV self-description: the export had SUMMARY + TRADES (+ DAILY P&L) but
#    NOT the config — a run's CSV could not say what produced it, which is
#    how two identical-looking files needed forensics to tell apart. FIX:
#    buildCsv gains CONFIG (flattened key,value — strategy-agnostic, whole
#    fleet benefits) and DIAG (diag_cbo when present, i.e. the falsification
#    counters travel WITH the results).
#
# Files: frontend/src/pages/backtest/paramFormat.js, RunComparison.jsx,
#        BacktestQueue.jsx, frontend/src/pages/Backtest.jsx.
# All-or-nothing staging; esbuild parse gate per file.
#
#     python3 apply_cbo_params_export_20260830.py --check
#     python3 apply_cbo_params_export_20260830.py

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "CBO_PARAMS_EXPORT_20260830"

PF = Path("frontend/src/pages/backtest/paramFormat.js")
RC = Path("frontend/src/pages/backtest/RunComparison.jsx")
BQ = Path("frontend/src/pages/backtest/BacktestQueue.jsx")
BT = Path("frontend/src/pages/Backtest.jsx")

# ── paramFormat.js: the shared CBO summary ───────────────────────────────
PF_OLD = "export default { fmtIcSl };"
PF_NEW = '''// ── CBO_PARAMS_EXPORT_20260830 ── compact, DISTINGUISHING one-liner for a
// CBO_V1 config. Every knob that can differ between two runs appears when it
// is engaged; pure defaults are suppressed to keep the line scannable. This
// lives in the SHARED module so RunComparison and BacktestQueue render the
// same string (the four-formatter-copies debt: new formatting lands here).
export function cboParamSummary(cfg) {
  if (!cfg) return "—";
  const p = [];
  const trig = cfg.trigger_source === "tf_close" ? "5mC"
    : cfg.trigger_source === "close" ? "1mC" : "wick";
  p.push(`trig ${trig}`);
  if (cfg.direction && cfg.direction !== "BOTH") p.push(cfg.direction);
  if (cfg.leg_action === "SELL") p.push("SELL-opp");
  if (cfg.option_premium) p.push(`prem ${cfg.option_premium.min}-${cfg.option_premium.max}`);
  if (Number(cfg.timeframe_minutes) && Number(cfg.timeframe_minutes) !== 5) p.push(`tf${cfg.timeframe_minutes}`);
  p.push(`tgt ${cfg.target_value}${cfg.target_mode === "pct" ? "%" : "₹"}`);
  if (cfg.sl_prem_mode && cfg.sl_prem_mode !== "off") p.push(`slP ${cfg.sl_prem_value}${cfg.sl_prem_mode === "pct" ? "%" : "₹"}`);
  if (Number(cfg.tp_fill_through_pts) > 0) p.push(`ε${cfg.tp_fill_through_pts}`);
  if (cfg.vwap_filter?.enabled) p.push(`vwap${cfg.vwap_filter.invert ? "INV" : ""}≥${Number(cfg.vwap_filter.min_pts) || 0}`);
  if (cfg.ema_gate?.enabled) p.push(`ema${cfg.ema_gate.period}/${cfg.ema_gate.slope_window}${cfg.ema_gate.invert ? "INV" : ""}≥${Number(cfg.ema_gate.min_slope) || 0}`);
  if (cfg.atm_skew_filter?.enabled) p.push(`skew ${cfg.atm_skew_filter.parity_adjust ? "par" : "raw"}${cfg.atm_skew_filter.invert ? "INV" : ""}≥${Number(cfg.atm_skew_filter.min_diff_pts) || 0}`);
  if (Number(cfg.breakout_buffer_pts) > 0) p.push(`buf${cfg.breakout_buffer_pts}`);
  if (Number(cfg.min_ref_range_pts) > 0) p.push(`minRef${cfg.min_ref_range_pts}`);
  if (cfg.require_full_ref) p.push("fullRef");
  if (cfg.both_side_policy && cfg.both_side_policy !== "pessimistic") p.push(`amb:${cfg.both_side_policy}`);
  if (Number(cfg.mtm_loss_cap) > 0) p.push(`mtmL${Math.round(cfg.mtm_loss_cap / 1000)}k`);
  if (Number(cfg.mtm_profit_cap) > 0) p.push(`mtmP${Math.round(cfg.mtm_profit_cap / 1000)}k`);
  if (cfg.mtm_include_open === false) p.push("mtm:realised");
  if (Number(cfg.max_trades_per_day) > 0) p.push(`capD${cfg.max_trades_per_day}`);
  if (Number(cfg.cooldown_minutes) > 0) p.push(`cd${cfg.cooldown_minutes}m`);
  if (cfg.skip_expiry_day) p.push("noExp");
  if (cfg.session_start && cfg.session_end) p.push(`${cfg.session_start}-${cfg.session_end}`);
  if (cfg.eod_square_off) p.push(`eod ${cfg.eod_square_off}`);
  if (Number(cfg.lots)) p.push(`${cfg.lots}L`);
  return p.join(" · ");
}

export default { fmtIcSl, cboParamSummary };'''

# ── RunComparison.jsx ────────────────────────────────────────────────────
RC_IMP_OLD = 'import { fmtIcSl } from "./paramFormat";   // ── IC_IV_SL ──'
RC_IMP_NEW = ('import { fmtIcSl, cboParamSummary } from "./paramFormat";   '
              '// ── IC_IV_SL ── ── CBO_PARAMS_EXPORT_20260830 ──')

RC_SUM_OLD = '''function paramSummary(run) {
  const cfg = run.config || {};
  const parts = [];'''
RC_SUM_NEW = '''function paramSummary(run) {
  const cfg = run.config || {};
  // ── CBO_PARAMS_EXPORT_20260830 ── CBO configs use their own field names;
  // walking the generic PARAM_DEFS against them interpolated other
  // strategies' keys ("below ≥0", "9/undefinedb ≥undefined") and every run
  // rendered alike. Detection key: both_side_policy + breakout_buffer_pts
  // (unique to CBO shapes on this page).
  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {
    return cboParamSummary(cfg);
  }
  const parts = [];'''

RC_EOD_OLD = ('get: (r) => (r.config?.eod_squareoff_time ? '
              'String(r.config.eod_squareoff_time) : "day end (legacy)") }')
RC_EOD_NEW = ('get: (r) => ((r.config?.eod_squareoff_time || r.config?.eod_square_off) ? '
              'String(r.config.eod_squareoff_time || r.config.eod_square_off) : '
              '"day end (legacy)") }   /* ── CBO_PARAMS_EXPORT_20260830 ── CBO\'s key is eod_square_off */')

# ── BacktestQueue.jsx ────────────────────────────────────────────────────
BQ_IMP_OLD = 'import { fmtIcSl } from "./paramFormat";   // ── IC_IV_SL ──'
BQ_IMP_NEW = ('import { fmtIcSl, cboParamSummary } from "./paramFormat";   '
              '// ── IC_IV_SL ── ── CBO_PARAMS_EXPORT_20260830 ──')

BQ_LINE_OLD = '''function paramLine(cfg) {
  if (!cfg) return "—";
  const p = [];'''
BQ_LINE_NEW = '''function paramLine(cfg) {
  if (!cfg) return "—";
  // ── CBO_PARAMS_EXPORT_20260830 ── same branch as RunComparison, same
  // shared formatter, so a staged job and its finished run read identically.
  if (cfg.both_side_policy != null && cfg.breakout_buffer_pts != null) {
    return cboParamSummary(cfg);
  }
  const p = [];'''

# ── Backtest.jsx: CONFIG + DIAG sections in the export ───────────────────
BT_SIG_OLD = 'function buildCsv(trades, summary, metrics, strategyId) {'
BT_SIG_NEW = ('function buildCsv(trades, summary, metrics, strategyId, '
              'config, diag) {   // ── CBO_PARAMS_EXPORT_20260830 ── config+diag')

BT_SEC_OLD = '''  lines.push("TRADES");'''
BT_SEC_NEW = '''  // ── CBO_PARAMS_EXPORT_20260830 ── the file must SAY what produced it:
  // full flattened config (strategy-agnostic; nested objects become dotted
  // keys) and, when present, the run's diag counters — the falsification
  // ledger travels WITH the results instead of living only in the UI.
  if (config && typeof config === "object") {
    lines.push("CONFIG");
    lines.push("Key,Value");
    const flat = [];
    const walk = (obj, prefix) => {
      for (const k of Object.keys(obj).sort()) {
        const v = obj[k];
        if (v != null && typeof v === "object" && !Array.isArray(v)) walk(v, `${prefix}${k}.`);
        else flat.push([`${prefix}${k}`, Array.isArray(v) ? v.join("|") : v]);
      }
    };
    walk(config, "");
    for (const [k, v] of flat) lines.push(`${csvEscape(k)},${csvEscape(v)}`);
    lines.push("");
  }
  if (diag && typeof diag === "object") {
    lines.push("DIAG");
    lines.push("Counter,Value");
    for (const k of Object.keys(diag).sort()) {
      const v = diag[k];
      if (typeof v === "number" || typeof v === "string") lines.push(`${csvEscape(k)},${csvEscape(v)}`);
    }
    lines.push("");
  }
  lines.push("TRADES");'''

BT_CALL_OLD = 'const csv = buildCsv(trades, summary, metrics, resultStrategy);'
BT_CALL_NEW = ('const csv = buildCsv(trades, summary, metrics, resultStrategy, '
               'resultConfig, summary?.diag_cbo);   // ── CBO_PARAMS_EXPORT_20260830 ──')


class Abort(Exception):
    pass


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n}x, expected 1 — drifted; "
                    f"nothing written.")
    return text.replace(old, new, 1)


def jsx_gate(path, text):
    tmp = path.parent / f"_stage_{path.stem}.jsx"
    tmp.write_text(text)
    try:
        r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                            "--loader:.jsx=jsx", "--loader:.js=jsx",
                            "--outfile=/dev/null"],
                           capture_output=True, text=True, cwd=".")
        if r.returncode != 0:
            raise Abort(f"esbuild rejected {path}:\n{r.stderr[:1200]}")
    except FileNotFoundError:
        print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)


def stage(path, edits):
    if not path.exists():
        raise Abort(f"missing: {path} — run from the repo root")
    text = path.read_text()
    if FENCE in text:
        print(f"  already fenced — skipped   {path}")
        return None
    for old, new, what in edits:
        text = replace_once(text, old, new, f"{path.name}:{what}")
    jsx_gate(path, text)
    return (path, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    plan = [
        (PF, [(PF_OLD, PF_NEW, "shared formatter")]),
        (RC, [(RC_IMP_OLD, RC_IMP_NEW, "import"),
              (RC_SUM_OLD, RC_SUM_NEW, "summary branch"),
              (RC_EOD_OLD, RC_EOD_NEW, "eod key")]),
        (BQ, [(BQ_IMP_OLD, BQ_IMP_NEW, "import"),
              (BQ_LINE_OLD, BQ_LINE_NEW, "paramLine branch")]),
        (BT, [(BT_SIG_OLD, BT_SIG_NEW, "buildCsv signature"),
              (BT_SEC_OLD, BT_SEC_NEW, "config+diag sections"),
              (BT_CALL_OLD, BT_CALL_NEW, "call site")]),
    ]
    staged = []
    try:
        for path, edits in plan:
            staged.append(stage(path, edits))
    except Abort as e:
        print(f"\nABORTED: {e}\nNothing written (all-or-nothing staging).",
              file=sys.stderr)
        return 1
    for item in staged:
        if item is None:
            continue
        path, text = item
        if args.check:
            print(f"  would patch (clean, esbuild OK)   {path}")
        else:
            shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{FENCE}"))
            path.write_text(text)
            print(f"  patched                 {path}")
    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}.")
    if not args.check:
        print("\nSmoke test: npm start -> Compare Runs must show distinct "
              "one-liners per CBO run; Download CSV must contain CONFIG and "
              "DIAG sections above TRADES.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

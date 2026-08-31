// frontend/src/pages/backtest/paramFormat.js
//
// ── IC_IV_SL ── SHARED param formatters for backtest config display.
//
// WHY THIS FILE EXISTS: the IC leg SL string is rendered in FOUR places —
// Backtest.jsx (describeConfig, results header chips), Portfolio.jsx (via
// the describeConfig prop), RunComparison.jsx (PARAM_KEYS.ic_legs, the
// config-diff matrix) and BacktestQueue.jsx (paramLine, job labels). They
// were four independent copies of the same template literal.
//
// That was survivable while every mode was a premium number. It stopped
// being survivable with IV modes, because the failure is SILENT AND
// WRONG rather than merely ugly: a leg with sl_mode "iv" and sl_val 30
// rendered through the old template as "SL30%" — IDENTICAL to a leg with
// sl_mode "pct" and sl_val 30. Two runs that stop on completely different
// things would compare as having identical parameters in RunComparison,
// and two queued sweep jobs would carry the same label. A comparison view
// that cannot distinguish the runs it is comparing is worse than no view.
//
// SCOPE: deliberately ONE function. This is the seed of the pending
// paramFormat consolidation, not the consolidation itself — the remaining
// duplicated formatters (premium caps, TP, adjust blocks, PST/TMA leg
// strings) are untouched and still live in their own files. Move them
// here one at a time, with the run-params chips as the tripwire.
//
// Pure module: no React, no imports, no app coupling.

/** IC leg / adjustment SL suffix, by sl_mode.
 *
 *   "pct"       premium stop, percent of entry     → SL42%
 *   "pts"       premium stop, absolute points      → SL30p
 *   "iv"        vol stop, ABSOLUTE IV level (%)    → IV30%
 *   "iv_delta"  vol stop, vol points above entry IV → IV+8p
 *
 * The IV forms deliberately drop the "SL" prefix: a vol stop must never
 * read like a premium number anywhere in the UI. */
export function fmtIcSl(v, mode) {
  if (mode === "iv") return `IV${v}%`;
  if (mode === "iv_delta") return `IV+${v}p`;
  return `SL${v}${mode === "pts" ? "p" : "%"}`;
}

// ── CBO_PARAMS_EXPORT_20260830 ── compact, DISTINGUISHING one-liner for a
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

export default { fmtIcSl, cboParamSummary };
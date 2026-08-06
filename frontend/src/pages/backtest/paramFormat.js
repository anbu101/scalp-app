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

export default { fmtIcSl };
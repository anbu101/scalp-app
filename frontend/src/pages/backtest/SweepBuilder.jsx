// frontend/src/pages/backtest/SweepBuilder.jsx
//
// ── SWEEP_BUILDER ── stage a parameter sweep through the EXISTING queue.
//
// Two modes, and only two, on purpose:
//   OAT  — baseline + ONE axis varied across its values (max 12). This is the
//          sensitivity pass: find which axes are alive before gridding.
//   GRID — exactly TWO axes, cartesian, hard-capped at 30 combos. Interactions
//          between two parameters are worth mapping; among six they never are.
//
// The baseline for every combo is buildConfig(strategyId) — the SAME builder
// the Run/Queue paths use — so a sweep run differs from a manual run in
// exactly the swept values and nothing else.
//
// Deliberately NOT axes (the discipline, encoded):
//   · Session window — one full-session run + the Time of Day tab answers
//     every window analytically; sweep runs would waste the corpus.
//   · Lots — scales P&L linearly; sweep at a fixed size, size later.
//   · Max Loss / Max Profit — distribution truncation; tune once at the end.
//
// Labels follow the SWEEP:<name> · <axis> <value> convention, so the Queue
// shows a shared colored badge per sweep (see groupInfo in BacktestQueue.jsx)
// and Compare Runs' parameter matrix does the analysis after the runs land.
//
// No backend changes: combos go through POST /api/backtest/queue/enqueue.

import React, { useEffect, useState, useMemo, useCallback } from "react";

const OAT_MAX = 12;
const GRID_MAX = 30;
const SWEEP_PREFIX = "SWEEP:";

const V1 = "SCALP_V1", V3 = "SCALP_V3", V5 = "SCALP_V5";
// ── WICK_PST_V1_REMOVAL ── WICK_V1 and PST_V1 removed. SweepBuilder is a
// LAUNCHER (every axis here enqueues a real run), so unlike the display-only
// label/colour maps elsewhere, nothing about them is retained.
const HA = "HA_V1", HAS = "HA_SELL", IC = "IC_V1", PSTS = "PST_SELL", PSTH = "PST_HEDGE", TMA = "TMA_V1", TMA2 = "TMA_V2", TSG = "TSG_V1", GC = "GC_V1", VAP = "VAP_V1";
const _hm = (t) => (/^\d{1,2}:\d{2}$/.test(t.trim()) ? { v: t.trim() } : { err: `"${t}" must be HH:MM` });

/* ── SWEEP_AXES BEGIN ── the sweepable parameter axes. Each axis knows which
   strategies it applies to, how to parse a typed value, how to write it into
   a config object, and how to render it in the job label. `hint` doubles as
   the placeholder AND the recommended starting values. */
const _num = (tok) => {
  const v = Number(tok);
  return Number.isFinite(v) ? { v } : { err: `"${tok}" is not a number` };
};
const _band = (tok) => {
  const m = tok.match(/^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$/);
  if (!m) return { err: `"${tok}" must look like 150-200` };
  const lo = Number(m[1]), hi = Number(m[2]);
  if (!(lo < hi)) return { err: `"${tok}": min must be below max` };
  return { v: [lo, hi] };
};
const _conds = (tok) => {
  const parts = [...new Set(tok.split("+").map((s) => s.trim().toUpperCase()))];
  if (!parts.length || parts.some((p) => !["C1", "C2", "C3"].includes(p))) {
    return { err: `"${tok}" must be C1/C2/C3 joined by +, e.g. C1+C3` };
  }
  // canonical order, mapped to the runner's names
  return { v: ["C1", "C2", "C3"].filter((c) => parts.includes(c)).map((c) => c.replace("C", "COND")) };
};
const AXES = [
  { key: "premium", label: "Premium band", strategies: [V1, V3, V5, HA, HAS],
    hint: "150-200, 200-250", parse: _band,
    apply: (c, v) => { c.option_premium = { min: v[0], max: v[1] }; },
    fmt: (v) => `prem ${v[0]}-${v[1]}` },
  { key: "rr", label: "Risk:Reward", strategies: [V1, V3, HA, HAS],
    hint: "1.0, 1.3, 1.7, 2.0, 2.5", parse: _num,
    apply: (c, v) => { c.risk_reward_ratio = v; }, fmt: (v) => `RR ${v}` },
  { key: "min_sl", label: "Min SL pts", strategies: [V1, V3, HA, HAS],
    hint: "1, 3, 5, 8", parse: _num,
    apply: (c, v) => { c.min_sl_points = v; }, fmt: (v) => `minSL ${v}` },
  { key: "max_sl", label: "Max SL cap", strategies: [V1, V3, HA, HAS],
    hint: "0, 10, 20", parse: _num,
    apply: (c, v) => { c.max_sl_points = v; }, fmt: (v) => `maxSL ${v}` },
  { key: "risk_max_sl", label: "Risk Max SL", strategies: [V1, V3],
    hint: "0, 10, 15", parse: _num,
    apply: (c, v) => { c.risk_max_sl_points = v; }, fmt: (v) => `rMaxSL ${v}` },
  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── V1 entry-filter axes. Blackout is
  // a 0/1 axis (IV12keep pattern) over the validated 12:00–14:00 window;
  // cap 0 = off, matching runner semantics. Omit-when-off both.
  { key: "v1_blackout", label: "V1 blackout 12–14 (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.entry_blackout = { enabled: true, start: "12:00", end: "14:00" }; }, fmt: (v) => (v ? "bo12-14" : "no-bo") },
  { key: "v1_cap", label: "V1 max trades/day", strategies: [V1],
    hint: "0, 10, 12, 15, 20", parse: _num,
    apply: (c, v) => { if (v > 0) c.max_trades_per_day = v; }, fmt: (v) => (v > 0 ? `cap ${v}` : "no cap") },
  // ── SCALP_V1_ENTRY_SIZING_20260823 ── D8.2/D8.3 axes. rupee_risk 0 = off
  // (fixed lots); pair a sizing sweep with max_sl {0, 25, 30} for the full
  // skip-vs-widen-vs-size picture. Spread 0 = gate off.
  { key: "v1_rupee_risk", label: "V1 ₹ risk/trade (0=fixed lots)", strategies: [V1],
    hint: "0, 10000, 13000, 16000", parse: _num,
    apply: (c, v) => { if (v > 0) c.risk_sizing = { enabled: true, rupee_risk: v }; }, fmt: (v) => (v > 0 ? `rsz ${v}` : "fixed lots") },
  { key: "v1_max_spread", label: "V1 max spread pts (0=off)", strategies: [V1],
    hint: "0, 12, 17, 22", parse: _num,
    apply: (c, v) => { if (v > 0) c.entry_max_spread_points = v; }, fmt: (v) => (v > 0 ? `sprd<${v}` : "no sprd gate") },
  // ── SCALP_V1_EMA_GATE_20260824 ── D10 axes. Gate period 0 = gate off;
  // lookback/min-slope ride the Backtest-page values via base config.
  // ── SCALP_V3_SWEEP_AXES_20260826 ── axes extended to V3: apply() is
  // config-key-generic and the V3 runner/engine now read these keys.
  { key: "v1_ema_gate_period", label: "EMA gate period (0=off)", strategies: [V1, V3],
    hint: "0, 89, 144, 200", parse: _num,
    apply: (c, v) => { if (v > 0) c.ema_gate = { ...(c.ema_gate || {}), enabled: true, period: v, slope_lookback: (c.ema_gate?.slope_lookback ?? 30), min_slope_pts: (c.ema_gate?.min_slope_pts ?? 0) }; }, fmt: (v) => (v > 0 ? `eGate${v}` : "no eGate") },
  { key: "v1_tp_mult", label: "TP multiplier", strategies: [V1, V3],   // ── SCALP_V3_SWEEP_AXES_20260826 ──
    hint: "1, 1.5, 2, 2.5", parse: _num,
    apply: (c, v) => { if (v > 0 && v !== 1) c.tp_multiplier = v; }, fmt: (v) => (v !== 1 ? `tpX${v}` : "tpX1") },
  // ── SCALP_V1_MTM_STOP_20260824 ── rupees; 0 = off.
  { key: "v1_mtm_stop", label: "V1 daily MTM stop ₹ (0=off)", strategies: [V1],
    hint: "0, 50000, 75000, 100000, 125000", parse: _num,
    apply: (c, v) => { if (v > 0) c.daily_max_mtm_loss = v; }, fmt: (v) => (v > 0 ? `mtm${v/1000}k` : "no mtm") },
  // ── SCALP_V1_HEDGE_LEG_20260824 ── 0 = no hedge; otherwise max premium ₹.
  { key: "v1_hedge", label: "V1 hedge max ₹ (0=off)", strategies: [V1],
    hint: "0, 5, 8, 12", parse: _num,
    apply: (c, v) => { if (v > 0) c.hedge_leg = { enabled: true, max_premium: v }; }, fmt: (v) => (v > 0 ? `hdg${v}` : "no hedge") },
  // ── SCALP_V1_VWAP_20260825 ── -1 = off; 0 = below by any amount; >0 = min pts below.
  { key: "v1_vwap", label: "V1 VWAP min below (-1=off)", strategies: [V1],
    hint: "-1, 0, 2, 5", parse: _num,
    apply: (c, v) => { if (v >= 0) c.vwap_filter = { enabled: true, min_below_pts: v }; }, fmt: (v) => (v >= 0 ? `vwap≥${v}` : "no vwap") },
  { key: "hedge_sl", label: "Hedge SL pts", strategies: [V3],
    hint: "10, 15, 20, 25", parse: _num,
    apply: (c, v) => { c.hedge_sl_points = v; }, fmt: (v) => `hSL ${v}` },
  // ── SCALP_V3_CONFIRM_20260826 ── D4 on/off axis (0 = off, 1 = on).
  { key: "v3_confirm", label: "Entry confirm (0/1)", strategies: [V3],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v > 0) c.entry_confirmation = { enabled: true }; },
    fmt: (v) => (v > 0 ? "confirm" : "no confirm") },
  { key: "sl_points", label: "SL pts", strategies: [V5],
    hint: "10, 13, 16, 20", parse: _num,
    apply: (c, v) => { c.sl_points = v; }, fmt: (v) => `SL ${v}` },
  { key: "tp_points", label: "TP pts", strategies: [V5],
    hint: "0, 12, 16, 20", parse: _num,
    apply: (c, v) => { c.tp_points = v; }, fmt: (v) => `TP ${v}` },
  { key: "target_pts", label: "Fixed target pts", strategies: [HA, HAS],
    hint: "10, 16, 20, 25", parse: _num,
    apply: (c, v) => { c.target_override = { enabled: true, points: v }; },
    fmt: (v) => `tgt ${v}`,
    note: "each run forces Fixed target ON (the R:R-exit family is a separate branch)" },
  { key: "tp_hold", label: "TP hold candles", strategies: [HA, HAS],
    hint: "0, 1, 2", parse: _num,
    apply: (c, v) => { c.tp_hold_extra_candles = v; }, fmt: (v) => `hold ${v}` },
  { key: "max_trades_side", label: "Max trades/side", strategies: [HA, HAS],
    hint: "3, 5, 10, 50", parse: _num,
    apply: (c, v) => { c.max_trades_per_side = v; }, fmt: (v) => `cap ${v}` },
  { key: "entry_conds", label: "Entry conditions", strategies: [HA, HAS],
    hint: "C1, C2, C3, C1+C2+C3", parse: _conds,
    apply: (c, v) => { c.entry_conditions = v; },
    fmt: (v) => v.map((x) => x.replace("COND", "C")).join("+"),
    note: "the condition-isolation workflow as one sweep" },
  // ── HA_COND1_RETRACE BEGIN ── HA_V1 ONLY (sell runner doesn't read the
  // key). Both axes force enabled:true (same convention as target_pts) and
  // SPREAD the existing object so a combined frac×ttl sweep composes
  // correctly whichever axis applies second.
  { key: "c1r_frac", label: "C1 retrace frac", strategies: [HA],
    hint: "0.35, 0.5, 0.65", parse: _num,
    apply: (c, v) => { c.cond1_retrace = { ttl_bars: 5, ...(c.cond1_retrace || {}), enabled: true, frac: v }; },
    fmt: (v) => `c1rF ${v}`,
    note: "each run forces C1 retrace ON (COND1-only limit entry; C2/C3 unaffected)" },
  { key: "c1r_ttl", label: "C1 retrace TTL bars", strategies: [HA],
    hint: "3, 5, 8", parse: _num,
    apply: (c, v) => { c.cond1_retrace = { frac: 0.5, ...(c.cond1_retrace || {}), enabled: true, ttl_bars: v }; },
    fmt: (v) => `c1rT ${v}b` },
  // ── HA_COND1_RETRACE END ──
  // ── HA_COND1_FLIP ── 0/1 axis (IV12keep pattern) — lets flip vs signal-side
  // A/B run as ONE sweep, alone or crossed with the retrace axes.
  { key: "c1r_flip", label: "C1 flip side (0/1)", strategies: [HA],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.cond1_flip_side = true; else delete c.cond1_flip_side; },
    fmt: (v) => (v ? "c1flip" : "c1 signal-side") },
  // ── GC_V1 — timeframe / premium cap / trade cap / mode / signal mode are
  // the interesting axes; exit time and day caps ride along for robustness
  // sweeps. mode + signal_mode are 0/1 axes (IV12keep pattern).
  { key: "gc_tf", label: "GC timeframe (min)", strategies: [GC],
    hint: "1, 3, 5, 10, 15", parse: _num,
    apply: (c, v) => { c.timeframe_minutes = v; }, fmt: (v) => `${v}m` },
  { key: "gc_prem", label: "GC premium <", strategies: [GC],
    hint: "100, 150, 200, 300", parse: _num,
    apply: (c, v) => { c.premium_max = v; }, fmt: (v) => `<${v}` },
  { key: "gc_cap", label: "GC max trades/day", strategies: [GC],
    hint: "1, 2, 4, 6", parse: _num,
    apply: (c, v) => { c.max_trades_per_day = v; }, fmt: (v) => `cap ${v}` },
  { key: "gc_exit", label: "GC exit (EOD) time", strategies: [GC],
    hint: "14:30, 15:00, 15:15", parse: _hm,
    apply: (c, v) => { c.exit_time = v; }, fmt: (v) => `EOD ${v}` },
  { key: "gc_mode", label: "GC sell mode (0/1)", strategies: [GC],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.mode = v ? "SELL" : "BUY"; }, fmt: (v) => (v ? "SELL-opp" : "BUY") },
  { key: "gc_sig", label: "GC signal mode (0/1)", strategies: [GC],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.signal_mode = v ? "first" : "latest"; }, fmt: (v) => (v ? "sigFIRST" : "sigLATEST") },   // ── GC_SIGNAL_MODE (D4) ──
  { key: "gc_lb", label: "GC SL lookback", strategies: [GC],
    hint: "5, 10, 15, 20", parse: _num,
    apply: (c, v) => { c.sl_lookback = v; }, fmt: (v) => `lb ${v}` },
  { key: "gc_c1_gate", label: "GC C1 range gate %", strategies: [GC],
    hint: "0, 0.2, 0.3, 0.5", parse: _num,
    apply: (c, v) => { c.c1_range_max_pct = Math.abs(v); }, fmt: (v) => (v > 0 ? `c1≤${Math.abs(v)}%` : "c1 gate off") },   // ── GC_C1_RANGE_GATE ──
  { key: "gc_c1_skip", label: "GC C1 skip candles", strategies: [GC],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.c1_skip_candles = Math.max(0, Math.round(v)); },
    fmt: (v) => (v > 0 ? `skip ${Math.round(v)}c` : "no skip") },   // ── GC_C1_SKIP ──
  { key: "gc_sl_cap", label: "GC max SL % (gap guard)", strategies: [GC],
    hint: "0, 0.2, 0.3, 0.5", parse: _num,
    apply: (c, v) => { c.max_sl_pct = Math.abs(v); }, fmt: (v) => (v > 0 ? `slcap${Math.abs(v)}%` : "sl cap off") },   // ── GC_SL_CAP ──
  { key: "gc_cutoff", label: "GC entry cutoff", strategies: [GC],
    hint: "11:00, 12:00, 13:00, 14:00", parse: _hm,
    apply: (c, v) => { c.entry_cutoff_time = v; }, fmt: (v) => `≤${v}` },   // ── GC_ENTRY_CUTOFF ──
  { key: "gc_hedge", label: "GC hedge BUY ≤ ₹", strategies: [GC],
    hint: "0, 3, 5, 10", parse: _num,
    apply: (c, v) => { c.hedge_premium_max = Math.abs(v); }, fmt: (v) => (v > 0 ? `hdg≤${Math.abs(v)}` : "no hedge") },   // ── GC_HEDGE ──
  { key: "gc_trade_loss", label: "GC max loss/trade ₹", strategies: [GC],
    hint: "0, 1500, 2500", parse: _num,
    apply: (c, v) => { c.max_loss_per_trade = Math.abs(v); }, fmt: (v) => (v > 0 ? `t-₹${Math.abs(v)}` : "trade -cap off") },   // ── GC_TRADE_CAPS ──
  { key: "gc_trade_profit", label: "GC max profit/trade ₹", strategies: [GC],
    hint: "0, 2500, 5000", parse: _num,
    apply: (c, v) => { c.max_profit_per_trade = Math.abs(v); }, fmt: (v) => (v > 0 ? `t+₹${Math.abs(v)}` : "trade +cap off") },
  { key: "gc_month_loss", label: "GC max loss/month ₹", strategies: [GC],
    hint: "0, 15000, 25000, 50000", parse: _num,
    apply: (c, v) => { c.max_loss_month = Math.abs(v); }, fmt: (v) => (v > 0 ? `m-₹${Math.abs(v)}` : "month cap off") },   // ── GC_MONTH_CAP ──
  { key: "gc_day_loss", label: "GC max loss/day ₹", strategies: [GC],
    hint: "0, 3000, 5000", parse: _num,
    apply: (c, v) => { c.max_loss_day = Math.abs(v); }, fmt: (v) => (v > 0 ? `day-₹${Math.abs(v)}` : "day-cap off") },
  { key: "gc_day_profit", label: "GC max profit/day ₹", strategies: [GC],
    hint: "0, 5000, 10000", parse: _num,
    apply: (c, v) => { c.max_profit_day = Math.abs(v); }, fmt: (v) => (v > 0 ? `day+₹${Math.abs(v)}` : "day+cap off") },
  // ── TSG_V1 — entry time and the MTM target ARE the strategy; caps apply
  // per action across both legs of that action (config legs carry action).
  { key: "tsg_entry", label: "Entry time", strategies: [TSG],
    hint: "09:16, 09:30, 10:00, 10:30", parse: _hm,
    apply: (c, v) => { c.entry_time = v; }, fmt: (v) => `entry ${v}` },
  { key: "tsg_exit", label: "Exit (EOD) time", strategies: [TSG],
    hint: "15:00, 15:15, 15:25", parse: _hm,
    apply: (c, v) => { c.exit_time = v; }, fmt: (v) => `EOD ${v}` },
  { key: "tsg_mtm", label: "MTM target ₹", strategies: [TSG],
    hint: "3000, 5000, 8000, 12000", parse: _num,
    apply: (c, v) => { c.mtm_target = v; }, fmt: (v) => `MTM≥${v}` },
  { key: "tsg_mtm_sl", label: "MTM SL ₹", strategies: [TSG],
    hint: "1500, 2500, 4000, 6000", parse: _num,
    apply: (c, v) => { c.mtm_sl = Math.abs(v); }, fmt: (v) => `MTMSL ${Math.abs(v)}` },   // ── TSG_MTM_SL ──
  { key: "tsg_mtm_sl_basis", label: "MTM SL basis", strategies: [TSG],
    hint: "DAILY, POSITION", parse: (s) => String(s).trim().toUpperCase(),
    apply: (c, v) => { c.mtm_sl_basis = (v === "POSITION" ? "POSITION" : "DAILY"); }, fmt: (v) => `SLbasis ${v === "POSITION" ? "pos" : "day"}` },   // ── TSG_MTM_BASIS_20260821 ──
  { key: "tsg_iv_sl", label: "IV SL %", strategies: [TSG],
    hint: "30, 35, 40, 45", parse: _num,
    apply: (c, v) => { c.iv_sl_pct = Math.abs(v); }, fmt: (v) => `IVSL ${Math.abs(v)}%` },   // ── TSG_IV_SL ──
  // ── IC_MIN_ENTRY_IV ── same config key, same DECIMAL unit, so the axis
  // serves IC as well as TSG. Key left as tsg_iv13 on purpose: renaming it
  // would orphan every saved sweep that already references it.
  { key: "tsg_iv13", label: "Min entry IV", strategies: [TSG, IC],
    hint: "0, 0.08, 0.10, 0.12", parse: _num,
    apply: (c, v) => { c.min_entry_iv = Math.abs(v); }, fmt: (v) => (v > 0 ? `IVfloor ${Math.abs(v)}` : "IVfloor off") },   // ── TSG_IV13 / IC_MIN_ENTRY_IV ──
  { key: "tsg_iv12", label: "IV keep hedge (0/1)", strategies: [TSG],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { c.iv_keep_hedge = !!v; }, fmt: (v) => (v ? "IV12keep" : "IV12pair") },   // ── TSG_IV12 ──
  { key: "tsg_trail_arm", label: "Trail arm ₹", strategies: [TSG],
    hint: "15000, 20000, 25000", parse: _num,
    apply: (c, v) => { c.mtm_trail_arm = Math.abs(v); }, fmt: (v) => `Tarm ${Math.abs(v)}` },   // ── TSG_TRAIL ──
  { key: "tsg_trail_gb", label: "Trail giveback ₹", strategies: [TSG],
    hint: "6000, 8000, 10000", parse: _num,
    apply: (c, v) => { c.mtm_trail_giveback = Math.abs(v); }, fmt: (v) => `Tgb ${Math.abs(v)}` },   // ── TSG_TRAIL ──
  { key: "tsg_iv_delta", label: "IV SL Δ pts", strategies: [TSG],
    hint: "5, 8, 12, 15", parse: _num,
    apply: (c, v) => { c.iv_sl_delta_pts = Math.abs(v); c.iv_sl_pct = 0; }, fmt: (v) => `IVSL +${Math.abs(v)}pts` },   // ── TSG_IV_SL_DELTA ──
  { key: "tsg_short_prem", label: "Sell premium <", strategies: [TSG],
    hint: "60, 85, 110", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") l.premium_max = v; }); },
    fmt: (v) => `sPrem<${v}` },
  { key: "tsg_hedge_prem", label: "Hedge premium <", strategies: [TSG],
    hint: "3, 5, 8, 12", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "BUY") l.premium_max = v; }); },
    fmt: (v) => `hPrem<${v}` },
  // ── IC_V1 — entry time IS the strategy; short cap/SL apply to both shorts
  { key: "ic_entry", label: "Entry time", strategies: [IC],
    hint: "09:18, 09:30, 09:45, 10:15", parse: _hm,
    apply: (c, v) => { c.entry_time = v; }, fmt: (v) => `entry ${v}` },
  { key: "ic_short_prem", label: "Short premium <", strategies: [IC],
    hint: "60, 85, 110", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") l.premium_max = v; }); },
    fmt: (v) => `sPrem<${v}` },
  // NOTE: this axis FORCES sl_mode="pct" on both shorts — sweeping a
  // percentage over a leg left in points (or, since IC_IV_SL, in a vol
  // mode) would otherwise reinterpret the number silently. If the base
  // config uses a vol stop, this axis OVERWRITES it back to a premium
  // stop; use the IV axes below instead of mixing them in one sweep.
  { key: "ic_short_sl", label: "Short SL %", strategies: [IC],
    hint: "30, 42, 55, 70", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") { l.sl_val = v; l.sl_mode = "pct"; } }); },
    fmt: (v) => `sSL ${v}%` },
  // ── IC_IV_SL BEGIN ── vol stops on the shorts. Each axis sets BOTH the
  // value and the mode, exactly as ic_short_sl does, so an axis can never
  // half-apply and leave a number being read in the wrong unit. The two IV
  // axes are mutually exclusive by construction (each overwrites sl_mode);
  // to compare them, run two sweeps, not one grid.
  { key: "ic_short_iv_sl", label: "Short IV SL %", strategies: [IC],
    hint: "25, 30, 35, 40", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") { l.sl_val = Math.abs(v); l.sl_mode = "iv"; } }); },
    fmt: (v) => `sIV ${Math.abs(v)}%` },
  { key: "ic_short_iv_delta", label: "Short IV SL Δ pts", strategies: [IC],
    hint: "5, 8, 12, 15", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") { l.sl_val = Math.abs(v); l.sl_mode = "iv_delta"; } }); },
    fmt: (v) => `sIV +${Math.abs(v)}pts` },
  // ── IC_IV_SL END ──
  // ── IC_V2 BEGIN ── adjustment axes. Base config must have adjust_on_sl +
  // adjust{} present (IC_V2 defaults do); apply guards keep V1-shaped
  // configs untouched rather than conjuring an adjust block mid-sweep.
  { key: "ic_adj_prem", label: "Adjust premium <", strategies: [IC],
    hint: "60, 85, 110", parse: _num,
    apply: (c, v) => { ["L1", "L2"].forEach((k) => { if (c.adjust?.[k]) c.adjust[k].premium_max = v; }); },
    fmt: (v) => `adj<${v}` },
  { key: "ic_adj_sl", label: "Adjust SL %", strategies: [IC],
    hint: "15, 25, 35", parse: _num,
    apply: (c, v) => { ["L1", "L2"].forEach((k) => { if (c.adjust?.[k]) { c.adjust[k].sl_val = v; c.adjust[k].sl_mode = "pct"; } }); },
    fmt: (v) => `adjSL ${v}%` },
  { key: "ic_adj_delay", label: "Adjust delay (s)", strategies: [IC],
    hint: "60, 120, 300", parse: _num,
    apply: (c, v) => { c.adjust_delay_s = v; },
    fmt: (v) => `adj+${v}s` },
  // ── IC_V2 END ──
  // ── PST ──
  { key: "pst_prem", label: "Premium <", strategies: [PSTS, PSTH],
    hint: "100, 150, 200", parse: _num,
    apply: (c, v) => { c.premium_max = v; }, fmt: (v) => `prem<${v}` },
  { key: "pst_sl", label: "Leg SL %", strategies: [PSTS, PSTH],
    hint: "10, 15, 20, 25", parse: _num,
    apply: (c, v) => { (c.legs || []).forEach((l) => { l.sl_pct = v; }); }, fmt: (v) => `SL ${v}%` },
  { key: "pst_tg1", label: "L1 spot target", strategies: [PSTS, PSTH],
    hint: "15, 20, 30", parse: _num,
    apply: (c, v) => { const l = (c.legs || [])[0]; if (l) l.spot_tg_points = v; }, fmt: (v) => `TG1 ${v}p` },
  { key: "pst_tg2", label: "L2 spot target", strategies: [PSTS, PSTH],
    hint: "40, 50, 70, 100", parse: _num,
    apply: (c, v) => { const l = (c.legs || [])[1]; if (l) l.spot_tg_points = v; }, fmt: (v) => `TG2 ${v}p` },
  // ── VAP_V1 ── nested v1.main/v1.hedge config; guards keep a sweep from
  // minting keys on a foreign config shape. The signal band and the traded
  // band are SEPARATE axes on purpose — in SELL mode they select different
  // contracts, and sweeping them together would confound the two effects.
  { key: "vap_sig_prem", label: "Signal premium <", strategies: [VAP],
    hint: "150, 200, 250", parse: _num,
    apply: (c, v) => { if (c.vwap) c.signal_premium_max = v; }, fmt: (v) => `SIG<${v}` },
  { key: "vap_min_prem", label: "Signal premium ≥", strategies: [VAP],
    hint: "40, 60, 80", parse: _num,
    apply: (c, v) => { if (c.vwap) c.min_premium = v; }, fmt: (v) => `SIG≥${v}` },
  { key: "vap_main_prem", label: "Short leg premium <", strategies: [VAP],
    hint: "100, 150, 200", parse: _num,
    apply: (c, v) => { if (c.v1?.main) c.v1.main.premium_max = v; }, fmt: (v) => `M<${v}` },
  { key: "vap_hedge_prem", label: "Hedge premium <", strategies: [VAP],
    hint: "2, 3, 5", parse: _num,
    apply: (c, v) => { if (c.v1?.hedge) c.v1.hedge.premium_max = v; }, fmt: (v) => `H<${v}` },
  { key: "vap_sl_pct", label: "SL % (sl_mode=PCT)", strategies: [VAP],
    hint: "20, 25, 30, 40", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "PCT"; c.sl_pct = v; } }, fmt: (v) => `SL${v}%` },
  { key: "vap_atr_mult", label: "ATR multiplier (sl_mode=ATR)", strategies: [VAP],
    hint: "1.0, 1.5, 2.0", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "ATR"; c.atr_mult = v; } }, fmt: (v) => `ATRx${v}` },
  { key: "vap_atr_period", label: "ATR period (sl_mode=ATR)", strategies: [VAP],
    hint: "4, 6, 10, 14", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.sl_mode = "ATR"; c.atr_period = v; } }, fmt: (v) => `ATR${v}` },
  { key: "vap_rr", label: "Reward:risk (tp_mode=RR)", strategies: [VAP],
    hint: "1.0, 1.5, 2.0, 3.0", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.tp_mode = "RR"; c.rr = v; } }, fmt: (v) => `RR${v}` },
  { key: "vap_tp_pct", label: "TP % (tp_mode=PCT)", strategies: [VAP],
    hint: "30, 40, 60", parse: _num,
    apply: (c, v) => { if (c.vwap) { c.tp_mode = "PCT"; c.tp_pct = v; } }, fmt: (v) => `TP${v}%` },
  { key: "vap_buffer", label: "VWAP buffer %", strategies: [VAP],
    hint: "0, 0.5, 1, 2", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vwap_buffer_pct = v; }, fmt: (v) => `buf${v}%` },
  { key: "vap_max_day", label: "Max entries/day per leg", strategies: [VAP],
    hint: "1, 2, 3, 0", parse: _num,
    apply: (c, v) => { if (c.v1) c.v1.max_trades_per_day = v; }, fmt: (v) => `cap${v}/leg` },
  { key: "vap_both", label: "Both sides", strategies: [VAP],
    hint: "ON, OFF", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["ON", "OFF"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be ON or OFF` };
    },
    apply: (c, v) => { if (c.vwap) c.allow_both_sides = v; },
    fmt: (v) => (v ? "CE+PE" : "1slot") },
  { key: "vap_ema_period", label: "Option EMA period (0=off)", strategies: [VAP],
    hint: "0, 9, 20, 50", parse: _num,
    apply: (c, v) => { if (c.vwap) c.ema_period = v; }, fmt: (v) => (v ? `EMA${v}` : "noEMA") },
  { key: "vap_ema_basis", label: "EMA basis (minutes)", strategies: [VAP],
    hint: "1, 5", parse: (tok) => {
      const v = Number(tok.trim());
      return [1, 5].includes(v) ? { v } : { err: `"${tok}" must be 1 or 5` };
    },
    apply: (c, v) => { if (c.vwap) c.ema_basis_minutes = v; }, fmt: (v) => `@${v}m` },
  { key: "vap_vol_mult", label: "Break-bar volume multiple (0=off)", strategies: [VAP],
    hint: "0, 1.5, 2, 3", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vol_mult = v; }, fmt: (v) => (v ? `vol${v}x` : "noVol") },
  { key: "vap_vol_lookback", label: "Volume lookback (bars)", strategies: [VAP],
    hint: "6, 12, 24", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vol_lookback = v; }, fmt: (v) => `lb${v}` },
  { key: "vap_sl_grace", label: "SL grace (min)", strategies: [VAP],
    hint: "0, 10, 15, 30, 45", parse: _num,
    apply: (c, v) => { if (c.vwap) c.sl_grace_min = v; }, fmt: (v) => `grace${v}m` },
  { key: "vap_grace_disaster", label: "Disaster SL % in grace", strategies: [VAP],
    hint: "0, 50, 75", parse: _num,
    apply: (c, v) => { if (c.vwap) c.sl_grace_disaster_pct = v; }, fmt: (v) => `dis${v}%` },
  { key: "vap_arm_first", label: "First entry needs arming", strategies: [VAP],
    hint: "ON, OFF", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["ON", "OFF"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be ON or OFF` };
    },
    apply: (c, v) => { if (c.vwap) c.require_arm_first = v; },
    fmt: (v) => (v ? "armFirst" : "noArm") },
  { key: "vap_sess_end", label: "Entry cutoff", strategies: [VAP],
    hint: "11:00, 12:30, 14:45", parse: (tok) => {
      const v = tok.trim();
      return /^\d{2}:\d{2}$/.test(v) ? { v } : { err: `"${tok}" must be HH:MM` };
    },
    apply: (c, v) => { c.session_end = v; }, fmt: (v) => `cut${v}` },
  { key: "vap_mode", label: "Execution mode", strategies: [VAP],
    hint: "BUY, SELL", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["BUY", "SELL"].includes(v) ? { v } : { err: `"${tok}" must be BUY or SELL` };
    },
    apply: (c, v) => { c.mode = v; },
    fmt: (v) => (v === "SELL" ? "SELL-opposite" : "BUY-signal") },
  // ── TMA_V2 ── nested s1.main/s1.hedge config; guards keep a sweep from
  // minting keys on a foreign config shape.
  { key: "tma2_main_prem", label: "Main premium <", strategies: [TMA2],
    hint: "80, 100, 120", parse: _num,
    apply: (c, v) => { if (c.s1?.main) c.s1.main.premium_max = v; }, fmt: (v) => `M<${v}` },
  { key: "tma2_hedge_prem", label: "Hedge premium <", strategies: [TMA2],
    hint: "2, 3, 5", parse: _num,
    apply: (c, v) => { if (c.s1?.hedge) c.s1.hedge.premium_max = v; }, fmt: (v) => `H<${v}` },
  { key: "tma2_sl", label: "Main SL", strategies: [TMA2],
    hint: "20, 30, 50", parse: _num,
    apply: (c, v) => { if (c.s1?.main) c.s1.main.sl_pct = v; }, fmt: (v) => `SL${v}` },
  { key: "tma2_tp", label: "Main TP", strategies: [TMA2],
    hint: "40, 50, 70", parse: _num,
    apply: (c, v) => { if (c.s1?.main) c.s1.main.tp_pct = v; }, fmt: (v) => `TP${v}` },
  { key: "tma2_mode", label: "Execution mode", strategies: [TMA2],
    hint: "BUY, SELL", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["BUY", "SELL"].includes(v) ? { v } : { err: `"${tok}" must be BUY or SELL` };
    },
    apply: (c, v) => { c.mode = v; },
    fmt: (v) => (v === "SELL" ? "SELL-spread" : "BUY-trend") },
  // ── 2026-CHOP ── exit reference, extension gate, slope gate
  // ── EXIT_REF_CUSTOM ── any EMA period 14-250 (55/89 are the presets)
  { key: "tma2_xover_ref", label: "Xover ref EMA", strategies: [TMA2],
    hint: "55, 70, 89", parse: (tok) => {
      const v = Number(tok.trim());
      return Number.isInteger(v) && v >= 14 && v <= 250
        ? { v }
        : { err: `"${tok}" must be a whole EMA period between 14 and 250` };
    },
    apply: (c, v) => { c.xover_exit_ref = v; },
    fmt: (v) => `ref${v}` },
  // ── EXT_BAND ── floor on the fan width (pairs with tma2_max_ext)
  { key: "tma2_min_ext", label: "Min extension %", strategies: [TMA2],
    hint: "0, 0.1, 0.15, 0.2", parse: _num,
    apply: (c, v) => { c.min_extension_pct = v; },
    fmt: (v) => (v > 0 ? `ext≥${v}%` : "minOFF") },
  { key: "tma2_max_ext", label: "Max extension %", strategies: [TMA2],
    hint: "0, 0.3, 0.5, 0.8", parse: _num,
    apply: (c, v) => { c.max_extension_pct = v; },
    fmt: (v) => (v > 0 ? `ext≤${v}%` : "extOFF") },
  { key: "tma2_slope", label: "EMA144 slope gate", strategies: [TMA2],
    hint: "OFF, ON", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["OFF", "ON"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be OFF or ON` };
    },
    apply: (c, v) => { c.ema144_slope_gate = v; },
    fmt: (v) => (v ? "144slope" : "noSlope") },
  // ── MAX_LOSS_PER_TRADE ── ₹ cap → tighter intraday SL level
  { key: "tma2_max_loss", label: "Max loss/trade ₹", strategies: [TMA2],
    hint: "0, 20000, 30000, 50000", parse: _num,
    apply: (c, v) => { c.max_loss_per_trade = v; },
    fmt: (v) => (v > 0 ? `cap₹${v >= 1000 ? v / 1000 + "k" : v}` : "capOFF") },
  // ── SL_STREAK_COOLDOWN ── combined K/N axis ("OFF, 4/3, 5/5"):
  // a single token sets both keys, so OFF doesn't multiply with days
  { key: "tma2_brake", label: "SL-streak brake", strategies: [TMA2],
    hint: "OFF, 4/3, 5/5", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      if (v === "OFF") return { v: { k: 0, d: 5 } };
      const m = v.match(/^(\d+)\s*\/\s*(\d+)$/);
      return m ? { v: { k: Number(m[1]), d: Number(m[2]) } }
               : { err: `"${tok}" must be OFF or K/DAYS like 4/3` };
    },
    apply: (c, v) => { c.sl_streak_count = v.k; c.sl_streak_cooldown_days = v.d; },
    fmt: (v) => (v.k > 0 ? `${v.k}SL→${v.d}d` : "brakeOFF") },
  // ── XOVER_TOGGLE ── 13/89 crossover exit on/off
  { key: "tma2_xover", label: "13/89 exit", strategies: [TMA2],
    hint: "ON, OFF", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["ON", "OFF"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be ON or OFF` };
    },
    apply: (c, v) => { c.xover_exit_enabled = v; },
    fmt: (v) => (v ? "XoverON" : "XoverOFF") },
  { key: "tma2_trade_mode", label: "Trade mode", strategies: [TMA2],
    hint: "INTRADAY, POSITIONAL", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["INTRADAY", "POSITIONAL"].includes(v) ? { v } : { err: `"${tok}" must be INTRADAY or POSITIONAL` };
    },
    apply: (c, v) => { c.trade_mode = v; },
    fmt: (v) => (v === "POSITIONAL" ? "Positional" : "Intraday") },
  // ── NEG_MTM_EOD_CUT ── only meaningful with trade mode POSITIONAL
  { key: "tma2_mtm_cut", label: "EOD loss cut", strategies: [TMA2],
    hint: "OFF, ON", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["OFF", "ON"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be OFF or ON` };
    },
    apply: (c, v) => { c.cut_neg_mtm_eod = v; },
    fmt: (v) => (v ? "CutLosers" : "CarryAll") },
  // ── TMA_V1 ── nested per-condition config (c1/c2); guards keep a sweep
  // from minting keys on a foreign config shape.
  // ── SPREAD_V2 ── sell-leg and hedge axes (C2 removed)
  { key: "tma_warmup", label: "Warmup sessions", strategies: [TMA],
    hint: "3, 4, 5, 6", parse: _num,
    apply: (c, v) => { c.warmup_days = Math.max(1, Math.round(v)); },
    fmt: (v) => `warm ${Math.round(v)}d` },   // ── TMA1_WARMUP_CFG ──
  { key: "tma_sell_prem", label: "Sell premium <", strategies: [TMA],
    hint: "80, 100, 120", parse: _num,
    apply: (c, v) => { if (c.c1?.sell) c.c1.sell.premium_max = v; }, fmt: (v) => `S<${v}` },
  { key: "tma_buy_prem", label: "Hedge premium <", strategies: [TMA],
    hint: "2, 3, 5", parse: _num,
    apply: (c, v) => { if (c.c1?.buy) c.c1.buy.premium_max = v; }, fmt: (v) => `H<${v}` },
  { key: "tma_sell_sl", label: "Sell SL %", strategies: [TMA],
    hint: "20, 30, 50", parse: _num,
    apply: (c, v) => { if (c.c1?.sell) c.c1.sell.sl_pct = v; }, fmt: (v) => `SL${v}%` },
  { key: "tma_sell_tp", label: "Sell TP %", strategies: [TMA],
    hint: "40, 50, 70", parse: _num,
    apply: (c, v) => { if (c.c1?.sell) c.c1.sell.tp_pct = v; }, fmt: (v) => `TP${v}%` },
  { key: "tma_trade_mode", label: "Trade mode", strategies: [TMA],
    hint: "INTRADAY, POSITIONAL", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["INTRADAY", "POSITIONAL"].includes(v) ? { v } : { err: `"${tok}" must be INTRADAY or POSITIONAL` };
    },
    apply: (c, v) => { c.trade_mode = v; },
    fmt: (v) => (v === "POSITIONAL" ? "Positional" : "Intraday") },
  // ── NEG_MTM_EOD_CUT ── only meaningful with trade mode POSITIONAL
  { key: "tma_mtm_cut", label: "EOD loss cut", strategies: [TMA],
    hint: "OFF, ON", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["OFF", "ON"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be OFF or ON` };
    },
    apply: (c, v) => { c.cut_neg_mtm_eod = v; },
    fmt: (v) => (v ? "CutLosers" : "CarryAll") },
];
/* ── SWEEP_AXES END ── */

function parseAxisValues(axis, text) {
  const toks = String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!toks.length) return { values: [], error: null };
  const values = [];
  const seen = new Set();
  for (const t of toks) {
    const r = axis.parse(t);
    if (r.err) return { values: [], error: r.err };
    const k = JSON.stringify(r.v);
    if (!seen.has(k)) { seen.add(k); values.push(r.v); }
  }
  return { values, error: null };
}

/* ── SWEEP_FIELD BEGIN ── module scope ON PURPOSE. Defining this inside the
   component body creates a NEW component type on every render, so React
   remounts the whole <label><input> subtree per keystroke: the input loses
   focus, typed characters vanish, and the next keypress lands on <body>
   where the app's single-letter nav shortcuts fire ("c" → Connections
   mid-word). Same silent-corruption family as stale useCallback deps. */
function SweepField({ label, c, typography, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ ...typography.label, color: c.text.muted, fontSize: 11 }}>{label}</span>
      {children}
    </label>
  );
}
/* ── SWEEP_FIELD END ── */

export default function SweepBuilder({
  colors, spacing, typography, Card,
  apiCall,
  strategyId, dateFrom, dateTo,
  buildConfig,
  queueActive,
  onStaged,              // () => void — refresh the queue list
}) {
  const c = colors;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("oat");           // "oat" | "grid"
  const [axis1Key, setAxis1Key] = useState(null);
  const [axis2Key, setAxis2Key] = useState(null);
  const [values1, setValues1] = useState("");
  const [values2, setValues2] = useState("");
  const [enqueueing, setEnqueueing] = useState(false);
  const [msg, setMsg] = useState(null);

  const axes = useMemo(() => AXES.filter((a) => a.strategies.includes(strategyId)), [strategyId]);

  // strategy switch invalidates axis picks that don't apply anymore
  useEffect(() => {
    const valid = new Set(axes.map((a) => a.key));
    if (axis1Key && !valid.has(axis1Key)) { setAxis1Key(null); setValues1(""); }
    if (axis2Key && !valid.has(axis2Key)) { setAxis2Key(null); setValues2(""); }
  }, [axes, axis1Key, axis2Key]);

  const axis1 = axes.find((a) => a.key === axis1Key) || null;
  const axis2 = axes.find((a) => a.key === axis2Key) || null;
  const p1 = useMemo(() => (axis1 ? parseAxisValues(axis1, values1) : { values: [], error: null }), [axis1, values1]);
  const p2 = useMemo(() => (axis2 ? parseAxisValues(axis2, values2) : { values: [], error: null }), [axis2, values2]);

  // baseline snapshot of the current Run-form params (recomputed per render;
  // it's a cheap pure object build) — used for footgun warnings + preview
  const baseline = useMemo(() => {
    try { return buildConfig(strategyId) || {}; } catch { return {}; }
  }, [buildConfig, strategyId]);

  /* ── SWEEP_VALIDATE BEGIN ── errors block staging; warnings don't. */
  const { combos, errors, warnings } = useMemo(() => {
    const errors = [], warnings = [];
    if (p1.error) errors.push(`${axis1?.label}: ${p1.error}`);
    if (p2.error) errors.push(`${axis2?.label}: ${p2.error}`);

    let combos = [];
    if (mode === "oat") {
      if (axis1 && p1.values.length) {
        if (p1.values.length > OAT_MAX) errors.push(`OAT is capped at ${OAT_MAX} values (${p1.values.length} given) — coarser first, refine later.`);
        combos = p1.values.map((v) => [[axis1, v]]);
      }
    } else {
      if (axis1 && axis2 && axis1.key === axis2.key) errors.push("Pick two different axes for a grid.");
      else if (axis1 && axis2 && p1.values.length && p2.values.length) {
        const total = p1.values.length * p2.values.length;
        if (total > GRID_MAX) errors.push(`Grid is capped at ${GRID_MAX} combos (${p1.values.length} × ${p2.values.length} = ${total}). Trim a list — the cap is the discipline, not a limitation.`);
        for (const v1 of p1.values) for (const v2 of p2.values) combos.push([[axis1, v1], [axis2, v2]]);
      }
    }

    // interaction footguns
    const usingRR = [axis1, axis2].some((a) => a?.key === "rr");
    if (usingRR && (strategyId === HA || strategyId === HAS) && baseline.target_override?.enabled) {
      warnings.push("Fixed target is ON in the Run form — sweeping R:R will have no effect (the target overrides it). Turn Fixed target off first.");
    }
    const usingTgt = [axis1, axis2].some((a) => a?.key === "target_pts");
    if (usingTgt) warnings.push("Fixed-target sweeps force target ON per run, regardless of the form toggle.");
    [axis1, axis2].forEach((a) => { if (a?.note && a.key !== "target_pts") warnings.push(`${a.label}: ${a.note}`); });

    if (!dateFrom || !dateTo) errors.push("Set the date range in the Run form first.");
    return { combos: errors.length ? [] : combos, errors, warnings };
  }, [mode, axis1, axis2, p1, p2, baseline, strategyId, dateFrom, dateTo]);
  /* ── SWEEP_VALIDATE END ── */

  const comboLabel = useCallback((overrides) =>
    overrides.map(([a, v]) => a.fmt(v)).join(" · "), []);

  /* ── SWEEP_ENQUEUE BEGIN ── one queue job per combo. Baseline is cloned per
     combo (configs are plain JSON) so an axis apply() can never bleed across
     combos. Label: SWEEP:<name> · <varied values> — the Queue's group badge
     and later report tooling both key off this. */
  const enqueueSweep = useCallback(async (alsoStart) => {
    if (!combos.length) return;
    setEnqueueing(true);
    const sweepName = (name || `${strategyId.toLowerCase()}-sweep`).trim();
    setMsg({ kind: "info", text: `Staging ${combos.length} runs…` });
    const base = buildConfig(strategyId);
    let ok = 0; const failed = [];
    for (const overrides of combos) {
      const cfg = JSON.parse(JSON.stringify(base));
      overrides.forEach(([a, v]) => a.apply(cfg, v));
      try {
        await apiCall("/api/backtest/queue/enqueue", {
          method: "POST",
          body: JSON.stringify({
            strategy_id: strategyId, underlying: "NIFTY",
            date_from: dateFrom, date_to: dateTo,
            config_override: cfg,
            label: `${SWEEP_PREFIX}${sweepName} · ${comboLabel(overrides)}`,
          }),
        });
        ok++;
      } catch (e) { failed.push(String(e.message || e)); }
    }
    let started = false;
    if (alsoStart && ok && !failed.length) {
      try { await apiCall("/api/backtest/queue/start", { method: "POST" }); started = true; }
      catch { /* already running — fine, jobs are queued behind it */ }
    }
    setEnqueueing(false);
    if (failed.length) setMsg({ kind: "err", text: `Staged ${ok}, failed ${failed.length}. First error: ${failed[0]}` });
    else setMsg({ kind: "ok", text: `Staged ${ok} runs as "${sweepName}"${started ? " and started the queue" : queueActive ? " — the running queue will pick them up" : " — press Start when ready"}. Analyse in Compare Runs when done.` });
    onStaged?.();
  }, [combos, name, strategyId, buildConfig, apiCall, dateFrom, dateTo, comboLabel, queueActive, onStaged]);
  /* ── SWEEP_ENQUEUE END ── */

  useEffect(() => {
    if (msg && msg.kind === "ok") {
      const t = setTimeout(() => setMsg(null), 6000);
      return () => clearTimeout(t);
    }
  }, [msg]);

  const inputStyle = {
    padding: "7px 10px", borderRadius: 6, border: `1px solid ${c.border.light}`,
    background: c.bg.secondary, color: c.text.primary, fontSize: 13, outline: "none",
    fontFamily: "'Inter', sans-serif",
  };
  const chip = (active, disabled) => ({
    padding: "6px 12px", borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 12, fontWeight: 700, opacity: disabled ? 0.5 : 1,
    border: `1px solid ${active ? c.primary : c.border.light}`,
    background: active ? c.primaryBg : c.bg.secondary,
    color: active ? c.primary : c.text.secondary,
  });
  const btn = (variant) => ({
    padding: "8px 16px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600,
    background: variant === "primary" ? c.primary : c.bg.tertiary,
    color: variant === "primary" ? "#fff" : c.text.primary,
  });

  const axisPicker = (which) => {
    const cur = which === 1 ? axis1Key : axis2Key;
    const other = which === 1 ? axis2Key : axis1Key;
    return (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {axes.map((a) => {
          const taken = mode === "grid" && a.key === other;
          return (
            <button key={a.key} style={chip(cur === a.key, taken)} disabled={taken}
              onClick={() => {
                if (which === 1) { setAxis1Key(a.key); setValues1(""); }
                else { setAxis2Key(a.key); setValues2(""); }
              }}
              title={a.note || a.label}>
              {a.label}
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
        <button style={btn("default")} onClick={() => setOpen((v) => !v)}>
          {open ? "▾" : "▸"} Stage a sweep
        </button>
        <span style={{ fontSize: 12, color: c.text.muted }}>
          Vary one axis (sensitivity) or two (focused grid) around the current <b>{strategyId}</b> form params. Session and lots are deliberately not axes — the Time of Day tab answers windows from one run, and lots just scale.
        </span>
        {msg && (
          <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600,
            color: msg.kind === "ok" ? c.profit : msg.kind === "err" ? c.loss : c.text.muted }}>
            {msg.text}
          </span>
        )}
      </div>

      {open && (
        <div style={{ marginTop: spacing.md, display: "flex", flexDirection: "column", gap: spacing.md }}>
          <div style={{ display: "flex", gap: spacing.md, alignItems: "flex-end", flexWrap: "wrap" }}>
            <SweepField c={c} typography={typography} label="Sweep name">
              <input style={{ ...inputStyle, minWidth: 170 }} placeholder={`${strategyId.toLowerCase()}-sweep`}
                value={name} onChange={(e) => setName(e.target.value)} />
            </SweepField>
            <SweepField c={c} typography={typography} label="Mode">
              <div style={{ display: "flex", gap: 6 }}>
                <button style={chip(mode === "oat", false)} onClick={() => setMode("oat")}
                  title="One axis varied — the sensitivity pass; do this first">1 axis (OAT)</button>
                <button style={chip(mode === "grid", false)} onClick={() => setMode("grid")}
                  title="Two axes, cartesian, capped — for the axes OAT showed to be alive">2-axis grid</button>
              </div>
            </SweepField>
            <div style={{ fontSize: 11, color: c.text.tertiary, paddingBottom: 8 }}>
              Period: <b style={{ color: c.text.secondary }}>{dateFrom || "—"} → {dateTo || "—"}</b> (from the Run form) · baseline = current {strategyId} params
            </div>
          </div>

          <SweepField c={c} typography={typography} label={mode === "grid" ? "Axis 1" : "Axis"}>{axisPicker(1)}</SweepField>
          {axis1 && (
            <SweepField c={c} typography={typography} label={`${axis1.label} values (comma-separated)`}>
              <input style={{ ...inputStyle, maxWidth: 420, fontFamily: "monospace" }}
                placeholder={axis1.hint} value={values1} onChange={(e) => setValues1(e.target.value)} />
            </SweepField>
          )}
          {mode === "grid" && (
            <>
              <SweepField c={c} typography={typography} label="Axis 2">{axisPicker(2)}</SweepField>
              {axis2 && (
                <SweepField c={c} typography={typography} label={`${axis2.label} values (comma-separated)`}>
                  <input style={{ ...inputStyle, maxWidth: 420, fontFamily: "monospace" }}
                    placeholder={axis2.hint} value={values2} onChange={(e) => setValues2(e.target.value)} />
                </SweepField>
              )}
            </>
          )}

          {errors.length > 0 && errors.map((e, i) => (
            <div key={i} style={{ fontSize: 12, color: c.loss, fontWeight: 600 }}>{e}</div>
          ))}
          {warnings.length > 0 && warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 12, color: c.warning }}>⚠ {w}</div>
          ))}

          {combos.length > 0 && (
            <div>
              <div style={{ fontSize: 12, color: c.text.secondary, marginBottom: 6 }}>
                <b>{combos.length}</b> run{combos.length === 1 ? "" : "s"} will be staged:
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {combos.map((ov, i) => (
                  <span key={i} style={{ fontSize: 11, fontFamily: "monospace", padding: "3px 8px",
                    borderRadius: 5, background: c.bg.secondary, border: `1px solid ${c.border.light}`,
                    color: c.text.secondary }}>
                    {comboLabel(ov)}
                  </span>
                ))}
              </div>
              <div style={{ display: "flex", gap: spacing.sm, marginTop: spacing.md }}>
                <button style={btn("default")} disabled={enqueueing} onClick={() => enqueueSweep(false)}>
                  Enqueue {combos.length}
                </button>
                <button style={btn("primary")} disabled={enqueueing} onClick={() => enqueueSweep(true)}>
                  {enqueueing ? "Staging…" : `Enqueue ${combos.length} & start`}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
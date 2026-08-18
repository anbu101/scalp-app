// frontend/src/pages/backtest/RunComparison.jsx
//
// Backtest Run Comparison — an analytics tool (not just a viewer) for the
// Backtest page. Lists every persisted run with the PARAMETERS used and an
// EXHAUSTIVE KPI set, and lets you filter, sort, multi-select to compare
// side-by-side (with deltas + overlaid equity curves), inspect one run, and
// delete unwanted runs.
//
// DATA SOURCES (existing API, plus two small backend additions):
//   GET    /api/backtest/runs?limit=N      → [{run_id, strategy_id, underlying,
//            date_from, date_to, status, created_at, finished_at, summary, config}]
//          (config now included — see backend additions doc)
//   GET    /api/backtest/runs/{run_id}      → full run incl. trades (lazy-loaded
//            only when a run is opened/compared, so KPIs match Backtest.jsx exactly)
//   DELETE /api/backtest/runs/{run_id}      → delete a run (backend addition)
//
// KPI PARITY: this file imports `computeMetrics` from Backtest.jsx so every KPI
// (profit factor, expectancy, return/DD, hold times, streaks, exit-reason
// splits, day/instrument/side breakdowns, equity curve) is byte-for-byte what
// the run's own Summary/Advanced tabs show. No KPI is re-implemented here.
//
// This component is self-contained and takes the shared design primitives +
// helpers as props from the host page, so it never duplicates tokens or math.

import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import AiPanel from "./AiPanel";   // ── AI_PANEL ──
import ReportView, { buildReportHtml } from "./ReportView";   // ── REPORT_VIEW ──
import { fmtIcSl } from "./paramFormat";   // ── IC_IV_SL ──

// Persisted column (metric) selection — survives navigation + app restart.
// Bump the version suffix if the default set changes meaningfully.
const COLS_LS_KEY = "scalp_compare_metric_cols_v1";

function loadSelectedKeys(allDefs) {
  try {
    const raw = localStorage.getItem(COLS_LS_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) {
        // keep only keys that still exist (defs can change across versions)
        const valid = new Set(allDefs.map((d) => d.key));
        const filtered = arr.filter((k) => valid.has(k));
        if (filtered.length) return new Set(filtered);
      }
    }
  } catch { /* ignore */ }
  return new Set(allDefs.filter((d) => d.def).map((d) => d.key));  // defaults
}

function saveSelectedKeys(set) {
  try { localStorage.setItem(COLS_LS_KEY, JSON.stringify([...set])); } catch { /* ignore */ }
}

/* ============================================================================
   ── PARAMS_FULL BEGIN ──
   Parameter model — the FULL union of config keys across every strategy the
   backtest supports (SCALP_V1/V3/V4/V5, HA_V1, HA_SELL), each with a
   short human label and a getter. Drives the params columns + compare rows.
   Rows where NO selected run sets the param are hidden automatically, so each
   comparison only shows the knobs that actually apply.
   ========================================================================== */
const _fmtConds = (arr) =>
  Array.isArray(arr) && arr.length
    ? arr.map((c) => String(c).replace("COND", "C")).join("+")
    : null;

const PARAM_DEFS = [
  { key: "date_from",        label: "From",           get: (r) => r.date_from },
  { key: "date_to",          label: "To",             get: (r) => r.date_to },
  { key: "premium_min",      label: "Prem min",       get: (r) => r.config?.option_premium?.min },
  { key: "premium_max",      label: "Prem max",       get: (r) => r.config?.option_premium?.max },
  // WICK_V1 (retired) — kept so archived runs still render their params
  { key: "timeframe",        label: "Timeframe (m)",  get: (r) => r.config?.timeframe_minutes },
  { key: "top_wick_min",     label: "Top wick min",   get: (r) => r.config?.top_wick_min },
  { key: "dual_side",        label: "1 CE + 1 PE",    get: (r) => (r.config?.dual_side_mode ? "ON" : null) },
  // V1 / hedge / HA
  { key: "rr",               label: "R:R",            get: (r) => r.config?.risk_reward_ratio },
  { key: "min_sl",           label: "Min SL",         get: (r) => r.config?.min_sl_points },
  { key: "max_sl",           label: "Max SL cap",     get: (r) => r.config?.max_sl_points },
  { key: "risk_max_sl",      label: "Risk Max SL",    get: (r) => r.config?.risk_max_sl_points },
  { key: "hedge_sl",         label: "Hedge SL",       get: (r) => r.config?.hedge_sl_points },
  // V5 (and retired WICK) absolute points
  { key: "sl_points",        label: "SL pts",         get: (r) => r.config?.sl_points },
  { key: "tp_points",        label: "TP pts",         get: (r) => r.config?.tp_points },
  // HA-specific
  { key: "fixed_target",     label: "Fixed target",   get: (r) => (r.config?.target_override?.enabled ? `${r.config.target_override.points} pts` : null) },
  { key: "entry_conds",      label: "Entry conds",    get: (r) => _fmtConds(r.config?.entry_conditions) },
  // ── HA_COND1_RETRACE ── the config-diff matrix decides "same params or not"
  // by string equality on this value; a retrace run and a market-entry run
  // with otherwise identical params differ ONLY here.
  { key: "c1_retrace",       label: "C1 retrace",     get: (r) => (r.config?.cond1_retrace?.enabled ? `${r.config.cond1_retrace.frac ?? 0.5}× / ${r.config.cond1_retrace.ttl_bars ?? 5}b` : null) },
  // ── HA_COND1_FLIP ── a flipped run and a signal-side run must never
  // compare as identical params in the diff matrix.
  { key: "c1_flip",          label: "C1 flip side",   get: (r) => (r.config?.cond1_flip_side ? "CE↔PE" : null) },
  // ── HA_COND_WINDOWS / HA_DAILY_CAP ── partitioned and capped runs must
  // never diff as identical params against unrestricted ones.
  { key: "cond_windows",     label: "Cond windows",   get: (r) => { const w = r.config?.condition_windows; return (w && Object.keys(w).length) ? Object.entries(w).map(([c, v]) => `${String(c).replace("COND", "C")} ${v.start}–${v.end}`).join(" · ") : null; } },
  { key: "day_cap",          label: "Max trades/day", get: (r) => (Number(r.config?.max_trades_per_day) > 0 ? String(r.config.max_trades_per_day) : null) },
  { key: "max_trades_side",  label: "Max trades/side",get: (r) => r.config?.max_trades_per_side },
  { key: "tp_hold",          label: "TP hold candles",get: (r) => r.config?.tp_hold_extra_candles || null },
  // IC_V1
  { key: "ic_entry",         label: "Entry time",     get: (r) => r.config?.entry_time },
  { key: "ic_exit",          label: "EOD time",       get: (r) => r.config?.exit_time },
  // ── IC_IV_SL ── fmtIcSl, NOT an inline template. The config-diff matrix
  // decides "same params or not" by STRING EQUALITY on this value, so a
  // premium stop of 30% and a vol stop of 30% IV rendering alike would make
  // two structurally different runs compare as identical.
  { key: "ic_legs",          label: "Legs",           get: (r) => Array.isArray(r.config?.legs) ? r.config.legs.filter((l) => Number(l.lots) > 0).map((l) => `${l.id}:${l.action === "SELL" ? "S" : "B"}${l.opt_type}<${l.premium_max}${l.sl_val ? ` ${fmtIcSl(l.sl_val, l.sl_mode)}` : ""}${l.mtc_other_on_sl ? "·MTC" : ""}`).join(" ") : null },
  // ── IC_V2 BEGIN ── carry + adjustment params as first-class compare rows
  { key: "ic_hold",          label: "IC hold",        get: (r) => r.config?.exit_mode === "NEXT_OPEN" ? `→ ${r.config.next_open_time || "09:16"} open` : (Array.isArray(r.config?.legs) && r.config.legs.some((l) => l.action && l.opt_type) ? "Daily EOD" : null) },
  { key: "ic_expiry_exit",   label: "IC expiry EOD",  get: (r) => r.config?.exit_mode === "NEXT_OPEN" ? (r.config.expiry_exit_time || "15:28") : null },
  { key: "ic_adj_delay",     label: "IC adj delay",   get: (r) => r.config?.adjust_on_sl ? `${r.config.adjust_delay_s ?? 60}s` : null },
  { key: "ic_adj_ce",        label: "IC adj CE (L1)", get: (r) => { const a = r.config?.adjust_on_sl ? r.config?.adjust?.L1 : null; return (a && a.enabled !== false && Number(a.lots) > 0) ? `<${a.premium_max} ${a.lots}L SL${a.sl_val}${a.sl_mode === "pts" ? "p" : "%"}${Number(a.tp_val) ? ` TP${a.tp_val}${a.tp_mode === "pts" ? "p" : "%"}` : ""}` : null; } },
  { key: "ic_adj_pe",        label: "IC adj PE (L2)", get: (r) => { const a = r.config?.adjust_on_sl ? r.config?.adjust?.L2 : null; return (a && a.enabled !== false && Number(a.lots) > 0) ? `<${a.premium_max} ${a.lots}L SL${a.sl_val}${a.sl_mode === "pts" ? "p" : "%"}${Number(a.tp_val) ? ` TP${a.tp_val}${a.tp_mode === "pts" ? "p" : "%"}` : ""}` : null; } },
  // ── ADJ_ONLY ── execution mode as its own row: full-condor vs adjust-only
  // runs with identical legs differ ONLY here; without this row the compare
  // matrix hides the one knob that changed.
  { key: "ic_exec",          label: "IC execution",   get: (r) => r.config?.adjust_only ? "ADJ-only" : (r.config?.adjust_on_sl ? "Full condor" : null) },
  // ── IC_V2 END ──
  // ── GC_V1 ── sl_lookback + signal_mode is unique to GC configs; exit_time
  // renders via the shared EOD row.
  { key: "gc_mode",       label: "GC mode",       get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? (r.config.mode === "SELL" ? "SELL (opp)" : "BUY") : null },
  { key: "gc_tf",         label: "GC timeframe",  get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && r.config?.timeframe_minutes) ? `${r.config.timeframe_minutes}m` : null },
  { key: "gc_prem",       label: "GC premium <",  get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? r.config?.premium_max : null },
  { key: "gc_lots",       label: "GC lots",       get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? r.config?.lots : null },
  { key: "gc_cap",        label: "GC trades/day", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? r.config?.max_trades_per_day : null },
  { key: "gc_sig",        label: "GC signal mode", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? r.config.signal_mode : null },
  { key: "gc_lb",         label: "GC SL lookback", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? r.config.sl_lookback : null },
  { key: "gc_c1_gate",    label: "GC C1 gate %",   get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && Number(r.config?.c1_range_max_pct) > 0) ? `${r.config.c1_range_max_pct}%` : null },   // ── GC_C1_RANGE_GATE ──
  { key: "gc_c1_skip",    label: "GC C1 skip",     get: (r) => (Number(r.config?.c1_skip_candles) > 0 ? `${r.config.c1_skip_candles}c` : null) },   // ── GC_C1_SKIP ──
  { key: "gc_sl_cap",     label: "GC SL cap %",    get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && Number(r.config?.max_sl_pct) > 0) ? `${r.config.max_sl_pct}%` : null },   // ── GC_SL_CAP ──
  { key: "gc_cutoff",     label: "GC entry cutoff", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode) ? (r.config?.entry_cutoff_time || null) : null },   // ── GC_ENTRY_CUTOFF ──
  { key: "gc_hedge",      label: "GC hedge ≤ ₹",   get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && r.config?.mode === "SELL" && Number(r.config?.hedge_premium_max) > 0) ? r.config.hedge_premium_max : null },   // ── GC_HEDGE ──
  { key: "gc_trade_caps", label: "GC trade ±cap ₹", get: (r) => { const c = r.config || {}; if (c.sl_lookback == null || !c.signal_mode) return null; const pp = Number(c.max_profit_per_trade) > 0 ? `+${c.max_profit_per_trade}` : null; const ll = Number(c.max_loss_per_trade) > 0 ? `-${c.max_loss_per_trade}` : null; return (pp || ll) ? [pp, ll].filter(Boolean).join(" / ") : null; } },   // ── GC_TRADE_CAPS ──
  { key: "gc_month_cap",  label: "GC month -cap ₹", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && Number(r.config?.max_loss_month) > 0) ? r.config.max_loss_month : null },   // ── GC_MONTH_CAP ──
  { key: "gc_underlying", label: "GC underlying",   get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && r.config?.underlying && r.config.underlying !== "NIFTY") ? r.config.underlying : null },   // ── GC_STOCK_MODE ──
  { key: "gc_min_vol",    label: "GC min volume",   get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && Number(r.config?.min_entry_volume) > 0) ? r.config.min_entry_volume : null },   // ── GC_LIQ_GATE ──
  { key: "gc_prem_pct",   label: "GC premium <% spot", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && Number(r.config?.premium_max_pct) > 0) ? `${r.config.premium_max_pct}%` : null },   // ── GC_PREM_PCT ──
  { key: "gc_strike_sel", label: "GC strike",       get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && r.config?.strike_selection === "atm") ? `ATM${Number(r.config.atm_offset) > 0 ? `+${r.config.atm_offset}` : (Number(r.config.atm_offset) < 0 ? r.config.atm_offset : "")}` : null },   // ── GC_ATM_SELECT ──
  { key: "gc_hedge_off",  label: "GC hedge offset", get: (r) => (r.config?.sl_lookback != null && r.config?.signal_mode && r.config?.mode === "SELL" && r.config?.strike_selection === "atm" && Number(r.config?.hedge_offset) >= 1) ? `+${r.config.hedge_offset}` : null },   // ── GC_HEDGE_V2 ──
  { key: "gc_day_caps",   label: "GC day ±cap ₹", get: (r) => { const c = r.config || {}; if (c.sl_lookback == null || !c.signal_mode) return null; const pp = Number(c.max_profit_day) > 0 ? `+${c.max_profit_day}` : null; const ll = Number(c.max_loss_day) > 0 ? `-${c.max_loss_day}` : null; return (pp || ll) ? [pp, ll].filter(Boolean).join(" / ") : null; } },
  // ── TSG_V1 ── mtm_target is unique to TSG configs; entry/exit/legs render
  // via the ic_* rows above (identical config keys by design).
  { key: "tsg_mtm",          label: "MTM target ₹", get: (r) => (r.config?.mtm_target != null && Number(r.config.mtm_target) > 0) ? r.config.mtm_target : null },
  { key: "tsg_mtm_sl",       label: "MTM SL ₹",     get: (r) => Number(r.config?.mtm_sl) > 0 ? `-${r.config.mtm_sl}` : null },   // ── TSG_MTM_SL ──
  { key: "tsg_iv_sl",        label: "IV SL %",      get: (r) => Number(r.config?.iv_sl_pct) > 0 ? r.config.iv_sl_pct : null },   // ── TSG_IV_SL ──
  { key: "tsg_iv_delta",     label: "IV SL Δpts",   get: (r) => Number(r.config?.iv_sl_delta_pts) > 0 ? `+${r.config.iv_sl_delta_pts}` : null },   // ── TSG_IV_SL_DELTA ──
  { key: "tsg_trail",        label: "Trail arm/gb", get: (r) => (Number(r.config?.mtm_trail_arm) > 0 && Number(r.config?.mtm_trail_giveback) > 0) ? `${r.config.mtm_trail_arm}/${r.config.mtm_trail_giveback}` : null },   // ── TSG_TRAIL ──
  { key: "tsg_iv12",         label: "IV keep hedge", get: (r) => r.config?.iv_keep_hedge ? "yes" : null },   // ── TSG_IV12 ──
  { key: "tsg_iv13",         label: "Min entry IV", get: (r) => Number(r.config?.min_entry_iv) > 0 ? `${r.config.min_entry_iv}` : null },   // ── TSG_IV13 ──
  { key: "tsg_short_skew",   label: "Short skew",   get: (r) => (r.config?.short_skew_mult != null && Number(r.config.short_skew_mult) !== 1) ? r.config.short_skew_mult : null },
  // shared risk / session / size
  { key: "max_loss",         label: "Max Loss ₹",     get: (r) => r.config?.max_loss },
  { key: "max_profit",       label: "Max Profit ₹",   get: (r) => r.config?.max_profit },
  // ── V3_RISK_LIMITS ──
  { key: "daily_max_loss",     label: "Day Max Loss ₹",   get: (r) => r.config?.daily_max_loss },
  { key: "daily_max_profit",   label: "Day Max Profit ₹", get: (r) => r.config?.daily_max_profit },
  { key: "monthly_max_loss",   label: "Mon Max Loss ₹",   get: (r) => r.config?.monthly_max_loss },
  { key: "monthly_max_profit", label: "Mon Max Profit ₹", get: (r) => r.config?.monthly_max_profit },
  // ── V3_TRADE_COUNT_LIMITS ──
  { key: "max_trades_day",       label: "Max trades/day",      get: (r) => r.config?.max_trades_per_day },
  { key: "max_trades_side_day",  label: "Max trades/side/day", get: (r) => r.config?.max_trades_per_side_per_day },
  { key: "side",             label: "Side",           get: (r) => r.config?.trade_side_mode },
  { key: "sess_start",       label: "Sess start",     get: (r) => r.config?.session?.primary?.start },
  { key: "sess_end",         label: "Sess end",       get: (r) => r.config?.session?.primary?.end },
  { key: "lots",             label: "Lots",           get: (r) => r.config?.quantity?.lots },
  // ── TMA_V2 ── (ema4 + s1 is unique to TMA_V2 configs)
  { key: "tma2_mode",  label: "TMA2 mode",  get: (r) => (r.config?.ema4 && r.config?.s1) ? (r.config.mode === "SELL" ? "SELL (spread)" : "BUY") : null },
  { key: "tma2_xover", label: "TMA2 xover exit", get: (r) => (r.config?.ema4 && r.config?.s1) ? (r.config.xover_exit_enabled === false ? "OFF" : `ON (13/${Number(r.config.xover_exit_ref) === 55 ? 55 : 89})`) : null },   // ── XOVER_TOGGLE / 2026-CHOP ──
  { key: "tma2_ext",   label: "TMA2 max ext %", get: (r) => (r.config?.ema4 && r.config?.s1 && Number(r.config.max_extension_pct) > 0) ? `${r.config.max_extension_pct}%` : null },   // ── 2026-CHOP ──
  { key: "tma2_slope", label: "TMA2 144 slope", get: (r) => (r.config?.ema4 && r.config?.s1 && r.config.ema144_slope_gate) ? "ON" : null },   // ── 2026-CHOP ──
  { key: "tma2_brake", label: "TMA2 SL brake", get: (r) => (r.config?.ema4 && r.config?.s1 && Number(r.config.sl_streak_count) > 0) ? `${r.config.sl_streak_count}SL/${r.config.sl_streak_cooldown_days || 5}d` : null },   // ── SL_STREAK_COOLDOWN ──
  { key: "tma2_maxloss", label: "TMA2 cap/trade", get: (r) => (r.config?.ema4 && r.config?.s1 && Number(r.config.max_loss_per_trade) > 0) ? `₹${r.config.max_loss_per_trade}` : null },   // ── MAX_LOSS_PER_TRADE ──
  { key: "tma2_hold",  label: "TMA2 hold",  get: (r) => (r.config?.ema4 && r.config?.s1) ? (r.config.trade_mode === "POSITIONAL" ? "Positional" : "Intraday") : null },   // ── POSITIONAL ──
  { key: "tma2_main",  label: "TMA2 main leg", get: (r) => { const c = r.config?.ema4 ? r.config?.s1?.main : null; if (!c) return null; const f = (v, x) => { const m = x === "PTS" ? "p" : x === "ABS" ? "@" : "%"; return m === "@" ? `@${v}` : `${v}${m}`; }; return `<${c.premium_max} ${c.lots}L SL${f(c.sl_pct, c.sl_unit)} TP${f(c.tp_pct, c.tp_unit)}`; } },   // ── SLTP_UNITS ──
  { key: "tma2_hedge", label: "TMA2 hedge", get: (r) => { const c = (r.config?.ema4 && r.config?.mode === "SELL") ? r.config?.s1?.hedge : null; return c ? `<${c.premium_max} ${c.lots}L${r.config.wing_mode && r.config.wing_mode !== "synthetic" ? ` (${r.config.wing_mode})` : ""}` : null; } },
  { key: "tma2_sess",  label: "TMA2 session", get: (r) => (r.config?.ema4 && r.config?.s1 && r.config?.session_start) ? `${r.config.session_start}–${r.config.session_end}` : null },
  // ── TMA_V1 ── (ema + c1/c2 is unique to TMA configs)
  { key: "tma_hold",  label: "TMA hold",   get: (r) => (r.config?.ema && r.config?.c1) ? (r.config.trade_mode === "POSITIONAL" ? "Positional" : "Intraday") : null },   // ── POSITIONAL ──
  { key: "tma_mtm",   label: "TMA EOD cut", get: (r) => (r.config?.ema && r.config?.c1 && r.config.trade_mode === "POSITIONAL") ? (r.config.cut_neg_mtm_eod ? "Cut losers" : "Carry all") : null },   // ── NEG_MTM_EOD_CUT ──
  { key: "tma_sell",  label: "TMA sell leg", get: (r) => { const c = r.config?.ema ? r.config?.c1?.sell : null; if (!c) return null; const lg = c.sl_tp_unit === "PTS" ? "p" : "%"; const f = (v, x) => { const m = !x ? lg : x === "PTS" ? "p" : x === "ABS" ? "@" : "%"; return m === "@" ? `@${v}` : `${v}${m}`; }; return `<${c.premium_max} ${c.lots}L SL${f(c.sl_pct, c.sl_unit)} TP${f(c.tp_pct, c.tp_unit)}`; } },   // ── SPREAD_V2 / SLTP_UNITS ──
  { key: "tma_buy",   label: "TMA hedge",   get: (r) => { const c = r.config?.ema ? r.config?.c1?.buy : null; return c ? `<${c.premium_max} ${c.lots}L${r.config.wing_mode && r.config.wing_mode !== "synthetic" ? ` (${r.config.wing_mode})` : ""}` : null; } },
  { key: "tma_c2_diff", label: "C2 diff ≥",  get: (r) => (r.config?.ema && r.config?.c1 && r.config?.c2 && Number(r.config.c2.min_diff)) ? `${r.config.c2.min_diff} pts` : null },   // ── C2_DIFF_FILTER ── own row so diff sweeps compare at a glance
  { key: "tma_sess",  label: "TMA session", get: (r) => (r.config?.ema && r.config?.c1 && r.config?.c2 && r.config?.session_start) ? `${r.config.session_start}–${r.config.session_end}` : null },
  // PST
  { key: "pst_prem",  label: "Premium <",  get: (r) => r.config?.signal_tf ? r.config?.premium_max : null },
  { key: "pst_legs",  label: "PST legs",   get: (r) => r.config?.signal_tf && Array.isArray(r.config?.legs) ? r.config.legs.filter((l) => Number(l.lots) > 0).map((l) => `${l.id}:${l.lots}L SL${l.sl_pct}% TG${l.spot_tg_points}p`).join(" ") : null },
  { key: "pst_side",  label: "Side",       get: (r) => r.config?.signal_tf ? r.config?.side_mode : null },
];
/* ── PARAMS_FULL END ── */

/* ============================================================================
   KPI model — the exhaustive list. Each KPI knows how to read its value from a
   computeMetrics() result (m) and the run summary (s), how to format it, and
   which direction is "good" (for delta coloring in compare view).
   dir:  +1 → higher is better, -1 → lower is better, 0 → neutral
   ========================================================================== */
// Derived period stats from computeMetrics' daily/weekly/monthly/yearly
// aggregates. Each aggregate row is {key,label,pnl,trades,wins}; a "win
// period" is pnl>0, a "loss period" is pnl<0.
function periodStats(m) {
  const count = (arr, pred) => (arr || []).reduce((n, r) => n + (pred(r) ? 1 : 0), 0);
  return {
    winDays:    count(m?.daily,   (r) => r.pnl > 0),
    lossDays:   count(m?.daily,   (r) => r.pnl < 0),
    winWeeks:   count(m?.weekly,  (r) => r.pnl > 0),
    lossWeeks:  count(m?.weekly,  (r) => r.pnl < 0),
    winMonths:  count(m?.monthly, (r) => r.pnl > 0),
    lossMonths: count(m?.monthly, (r) => r.pnl < 0),
    // ── YEARLY ── (m.yearly exists once Backtest.jsx ships the yearly aggregate)
    winYears:   count(m?.yearly,  (r) => r.pnl > 0),
    lossYears:  count(m?.yearly,  (r) => r.pnl < 0),
  };
}

// Exit-reason COUNT for a given reason from computeMetrics' exitReasons.
function exitCount(m, reason) {
  const er = (m?.exitReasons || []).find((x) => x.reason === reason);
  return er ? er.trades : 0;
}

// The exit reasons we surface as toggleable count rows (union of the common
// ones across strategies). Unknown reasons still appear in the Exit-reason
// matrix section below; these are just the quick-add count columns.
// ── V3_RISK_LIMITS ── period-guard reasons added to the quick-add columns
const EXIT_REASON_KEYS = ["TP", "SL", "SL_AFTER_TP", "EOD", "SPOT_TG", "SPOT_SL", "EMA_EXIT", "SIG_TP", "SIG_SL", "MAX_LOSS", "MAX_PROFIT",
  "DAILY_MAX_LOSS", "DAILY_MAX_PROFIT", "MONTHLY_MAX_LOSS", "MONTHLY_MAX_PROFIT",
  // ── IC_V1 / IC_V2 ── condor exits: MTC scratch, carry close, range end
  "MTC_COST", "EOD_MTC", "NEXT_OPEN", "NEXT_OPEN_MTC", "EOR",
  // ── TSG_V1 ── combined-MTM basket exits + per-leg IV SL + trailing lock
  "MTM_TARGET", "MTM_SL", "MTM_TRAIL", "IV_SL", "IV_SL_HEDGE"];

// ── MARGIN_COLUMNS ── capital spec per run. Three kinds:
//   api   → structure priced by Zerodha's basket API (shorts & spreads);
//           sig keys the per-day cache so identical configs share one quote
//   local → BUY-only strategies: capital = premium cap × qty (no API —
//           buying blocks the premium, not SPAN)
//   null  → unknown config shape: show — rather than a wrong number
// ── WICK_PST_V1_REMOVAL ── WICK_V1 / PST_V1 retained here on purpose: this
// set drives the capital calc for ARCHIVED runs, which still exist in
// backtest.db. Dropping them would make old runs price as unknown-shape.
const BUY_ONLY = new Set(["SCALP_V3", "SCALP_V5", "HA_V1",
  "WICK_V1", "PST_V1", "PST_HEDGE", "BB_V1", "BB_V2"]);
const SHORT_ONE_LEG = new Set(["SCALP_V1", "SCALP_V2", "PST_SELL"]);
function capitalSpecOf(run) {
  const c = run?.config || {};
  const lot = String(run?.strategy_id || "").startsWith("BB") ? 30 : 65;
  const cap = c.option_premium?.max ?? c.premium_max;
  const lots = c.quantity?.lots
    ?? (Array.isArray(c.legs) ? c.legs.reduce((a, l) => a + (Number(l.lots) || 0), 0) : null)
    ?? c.lots;
  // TMA v2 spread
  if (c.ema && c.c1?.sell) {
    const sl = c.c1.sell, bl = c.c1.buy || {};
    const legs = [
      { side: "PE", action: "SELL", premium_max: sl.premium_max, lots: sl.lots },
      { side: "PE", action: "BUY", premium_max: bl.premium_max, lots: bl.lots }];
    return { kind: "api", legs, sig: JSON.stringify(legs) };
  }
  // IC-style explicit legs (action + opt_type per leg)
  if (Array.isArray(c.legs) && c.legs.some((l) => l.action && l.opt_type)) {
    const legs = c.legs.filter((l) => Number(l.lots) > 0)
      .map((l) => ({ side: l.opt_type, action: l.action, premium_max: l.premium_max, lots: l.lots }));
    if (!legs.length) return null;
    // ── IC_V2 ── adjustment legs are bought only AFTER a short stops out,
    // so they never appear in cfg.legs — but on a double-SL day BOTH are
    // open and carried overnight. Quoting the entry basket alone understates
    // the peak requirement by two near-ATM longs. Include them in the priced
    // basket, and keep them in the cache signature so an adjust-enabled run
    // never shares a quote with the same condor without adjustments.
    const adjLegs = [];
    if (c.adjust_on_sl && c.adjust) {
      for (const [lid, a] of Object.entries(c.adjust)) {
        if (!a || a.enabled === false || !(Number(a.lots) > 0)) continue;
        const src = c.legs.find((l) => l.id === lid);
        adjLegs.push({ side: src?.opt_type || (lid === "L2" ? "PE" : "CE"),
          action: "BUY", premium_max: a.premium_max, lots: a.lots });
      }
    }
    // ── ADJ_ONLY ── core legs are signal-tracked but never booked, so the
    // capital basket is the adjustment BUYs alone (long premium, no SPAN).
    // Quoting the full condor would overstate an adjust-only run's capital
    // ~10× and corrupt Return-on-capital in exactly the comparison this
    // toggle exists for. adjust_only stays in the sig via the leg list
    // itself differing, so it never shares a cache entry with a full run.
    const all = c.adjust_only ? adjLegs : [...legs, ...adjLegs];
    if (!all.length) return null;
    return { kind: "api", legs: all, sig: JSON.stringify(all) };
  }
  // ── GC_V1 ── SELL mode is a SPAN basket: short (representative PE — the
  // traded side flips with the signal day to day, SPAN is near-symmetric,
  // same convention as the form's margin preview) + the BUY hedge when
  // configured; identical configs share one quote via the sig. BUY mode is
  // premium-blocked capital → local math, no API.
  if (run?.strategy_id === "GC_V1" || (c.sl_lookback != null && c.signal_mode)) {
    if (!cap || !Number(c.lots)) return null;
    if (c.mode === "SELL") {
      const legs = [{ side: "PE", action: "SELL", premium_max: cap, lots: c.lots }];
      if (Number(c.hedge_premium_max) > 0)
        legs.push({ side: "PE", action: "BUY", premium_max: c.hedge_premium_max, lots: c.lots });   // ── GC_HEDGE ──
      return { kind: "api", legs, sig: JSON.stringify(legs) };
    }
    return { kind: "local", amount: Number(cap) * Number(c.lots) * lot };
  }
  // single-leg shorts (SCALP_V1/V2 grouped lots, PST_SELL summed legs)
  if (SHORT_ONE_LEG.has(run?.strategy_id) && cap && lots) {
    const legs = [{ side: "PE", action: "SELL", premium_max: cap, lots }];
    return { kind: "api", legs, sig: JSON.stringify(legs) };
  }
  // buy-only: local math, no API
  if (BUY_ONLY.has(run?.strategy_id) && cap && lots) {
    return { kind: "local", amount: Number(cap) * Number(lots) * lot };
  }
  return null;
}
function marginSigOf(run) {   // api-kind sig (cache key); null otherwise
  const spec = capitalSpecOf(run);
  return spec?.kind === "api" ? spec.sig : null;
}

// ── HEADER_FILTERS ── tiny expression parser for per-column threshold
// filters: ">30", "<=4,00,000", "=37", "30" (→ >=), with L / Cr suffixes
// ("30L" = 30,00,000). Returns null (no filter) or a predicate over the raw
// column value; unparseable text filters nothing rather than everything.
function parseColFilter(txt) {
  const t = String(txt || "").trim();
  if (!t) return null;
  const m = t.match(/^(>=|<=|>|<|=)?\s*([\d.,]+)\s*(l|cr)?$/i);
  if (!m) return null;
  let v = Number(m[2].replace(/,/g, ""));
  if (!Number.isFinite(v)) return null;
  const suf = (m[3] || "").toLowerCase();
  if (suf === "l") v *= 100000;
  if (suf === "cr") v *= 10000000;
  const op = m[1] || ">=";
  return (x) => {
    if (x == null) return false;
    if (op === ">") return x > v;
    if (op === "<") return x < v;
    if (op === ">=") return x >= v;
    if (op === "<=") return x <= v;
    return x === v;
  };
}

function makeKpiDefs(fmtInr, marginOf = () => null) {
  const money = (v) => (v == null ? "—" : `${v >= 0 ? "" : "-"}${fmtInr(Math.abs(v))}`);
  const num2 = (v) => (v == null ? "—" : v === Infinity ? "∞" : Number(v).toFixed(2));
  const pct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);
  const int = (v) => (v == null ? "—" : String(v));
  const dur = (s) => {
    if (!s) return "—";
    s = Math.round(s);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60), rs = s % 60;
    if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
    const h = Math.floor(m / 60), rm = m % 60;
    return `${h}h ${rm}m`;
  };
  // `def` flag = shown by default; others are off until the user adds them.
  const base = [
    { key: "net",         group: "Headline", label: "Net P&L",        dir: +1, def: true,  fmt: money, get: (m, s) => s?.net_pnl ?? m?.totalPnL },
    { key: "gross",       group: "Headline", label: "Gross P&L",      dir: +1, def: true,  fmt: money, get: (m, s) => s?.gross_pnl },
    { key: "charges",     group: "Headline", label: "Charges",        dir: -1, def: true,  fmt: money, get: (m, s) => s?.total_charges == null ? null : -Math.abs(s.total_charges) },
    { key: "trades",      group: "Headline", label: "Trades",         dir: 0,  def: true,  fmt: int,   get: (m, s) => s?.total_trades ?? m?.totalTrades },
    { key: "winRate",     group: "Headline", label: "Win rate",       dir: +1, def: true,  fmt: pct,   get: (m, s) => s?.win_rate ?? m?.winRate },
    { key: "maxDD",       group: "Risk",     label: "Max drawdown",   dir: -1, def: true,  fmt: money, get: (m, s) => (s?.max_drawdown != null ? -Math.abs(s.max_drawdown) : (m ? -Math.abs(m.maxDrawdown) : null)) },
    { key: "returnToDD",  group: "Risk",     label: "Return ÷ Max DD",dir: +1, def: true,  fmt: num2,  get: (m) => m?.returnToDD },
    // ── MARGIN_COLUMNS ── live basket-margin per config signature (today's
    // proxy; fetched via the ₹ Margins button; identical configs share one quote)
    { key: "marginReq",   group: "Capital",  label: "Capital/Margin", dir: -1, def: true,  fmt: money, get: (m, s, r) => marginOf(r)?.amount ?? null },
    { key: "rom",         group: "Capital",  label: "Return on capital", dir: +1, def: true, fmt: pct,  get: (m, s, r) => { const q = marginOf(r); const net = s?.net_pnl ?? m?.totalPnL; return (q?.amount > 0 && net != null) ? (100 * net / q.amount) : null; } },
    { key: "profitFactor",group: "Edge",     label: "Profit factor",  dir: +1, def: true,  fmt: num2,  get: (m) => m?.profitFactor },
    { key: "expectancy",  group: "Edge",     label: "Expectancy/trade",dir: +1, def: true,  fmt: money, get: (m) => m?.expectancy },
    { key: "winLoss",     group: "Edge",     label: "Win/Loss size",  dir: +1, def: false, fmt: num2,  get: (m) => m?.winLossRatio },
    { key: "avgWin",      group: "Edge",     label: "Avg win",        dir: +1, def: false, fmt: money, get: (m) => m?.avgWinX },
    { key: "avgLoss",     group: "Edge",     label: "Avg loss",       dir: +1, def: false, fmt: money, get: (m) => m?.avgLossX },
    { key: "wins",        group: "Counts",   label: "Wins",           dir: +1, def: true,  fmt: int,   get: (m, s) => s?.wins ?? m?.wins },
    { key: "losses",      group: "Counts",   label: "Losses",         dir: -1, def: true,  fmt: int,   get: (m, s) => s?.losses ?? m?.losses },
    { key: "bestWinStk",  group: "Streaks",  label: "Win streak (max)", dir: +1, def: true,  fmt: int,  get: (m) => m?.bestWinStreak },
    { key: "bestLossStk", group: "Streaks",  label: "Loss streak (max)",dir: -1, def: true,  fmt: int,  get: (m) => m?.bestLossStreak },
    { key: "largestWin",  group: "Tails",    label: "Largest win",    dir: +1, def: true,  fmt: money, get: (m) => m?.largestWin },
    { key: "largestLoss", group: "Tails",    label: "Largest loss",   dir: +1, def: true,  fmt: money, get: (m) => m?.largestLoss },
    // ── Period win/loss counts (derived) ──
    { key: "winDays",     group: "Periods",  label: "Profitable days",dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winDays : null) },
    { key: "lossDays",    group: "Periods",  label: "Loss days",      dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossDays : null) },
    { key: "winWeeks",    group: "Periods",  label: "Win weeks",      dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winWeeks : null) },
    { key: "lossWeeks",   group: "Periods",  label: "Loss weeks",     dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossWeeks : null) },
    { key: "winMonths",   group: "Periods",  label: "Win months",     dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winMonths : null) },
    { key: "lossMonths",  group: "Periods",  label: "Loss months",    dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossMonths : null) },
    // ── YEARLY ──
    { key: "winYears",    group: "Periods",  label: "Win years",      dir: +1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).winYears : null) },
    { key: "lossYears",   group: "Periods",  label: "Loss years",     dir: -1, def: false, fmt: int,   get: (m) => (m ? periodStats(m).lossYears : null) },
    { key: "avgHold",     group: "Holding",  label: "Avg hold",       dir: 0,  def: false, fmt: dur,   get: (m) => m?.avgHold },
    { key: "medHold",     group: "Holding",  label: "Median hold",    dir: 0,  def: false, fmt: dur,   get: (m) => m?.medHold },
    { key: "avgHoldWin",  group: "Holding",  label: "Avg hold (wins)",dir: 0,  def: false, fmt: dur,   get: (m) => m?.avgHoldWin },
    { key: "avgHoldLoss", group: "Holding",  label: "Avg hold (losses)",dir: 0,def: false, fmt: dur,   get: (m) => m?.avgHoldLoss },
    { key: "ambiguous",   group: "Quality",  label: "Ambiguous fills",dir: -1, def: false, fmt: int,   get: (m, s) => s?.ambiguous_fills },
  ];
  // ── Exit-reason COUNT rows (one per reason, derived) ──
  const exitDefs = EXIT_REASON_KEYS.map((reason) => ({
    key: `exit_${reason}`,
    group: "Exit counts",
    label: `${reason} count`,
    dir: 0,
    def: false,
    fmt: int,
    get: (m) => (m ? exitCount(m, reason) : null),
  }));
  return [...base, ...exitDefs];
}

const STRAT_LABEL = { SCALP_V1: "V1", SCALP_V3: "V3", SCALP_V5: "V5", HA_V1: "HA", HA_SELL: "HAS", WICK_V1: "WICK", IC_V1: "IC", IC_V2: "IC2", PST_V1: "PST", PST_SELL: "PSTS", PST_HEDGE: "PSTH", TMA_V1: "TMA", TMA_V2: "TMA2", TSG_V1: "TSG", GC_V1: "GC" };
const STATUS_COLOR = (c, status) =>
  status === "done" ? c.profit : status === "error" ? c.loss : status === "cancelled" ? c.warning : c.text.muted;

/* Sort comparator for the runs table. */
// ── PARAMS_FULL ── Compact, distinguishing parameter summary for a run (used
// in the runs-table Key params column, equity legend, compare headers, CSV).
// Built FROM PARAM_DEFS so it can never drift out of sync with the matrix —
// every param the matrix knows about appears here when set. From/To are
// skipped (they have their own Period column).
const SUMMARY_SKIP = new Set(["date_from", "date_to"]);
const SUMMARY_SHORT = {
  premium_min: "prem≥", premium_max: "prem≤", timeframe: "tf", top_wick_min: "wick≥",
  dual_side: "", rr: "RR", min_sl: "minSL", max_sl: "maxSL", risk_max_sl: "rMaxSL",
  hedge_sl: "hSL", sl_points: "SL", tp_points: "TP", fixed_target: "tgt",
  entry_conds: "", max_trades_side: "cap", tp_hold: "hold", max_loss: "ML",
  max_profit: "MP", side: "", sess_start: "", sess_end: "", lots: "",
  // ── V3_RISK_LIMITS ──
  daily_max_loss: "dML", daily_max_profit: "dMP",
  monthly_max_loss: "mML", monthly_max_profit: "mMP",
  // ── V3_TRADE_COUNT_LIMITS ──
  max_trades_day: "capD", max_trades_side_day: "capS",
};
function paramSummary(run) {
  const cfg = run.config || {};
  const parts = [];
  // premium range as one token
  if (cfg.option_premium) parts.push(`prem ${cfg.option_premium.min}-${cfg.option_premium.max}`);
  for (const p of PARAM_DEFS) {
    if (SUMMARY_SKIP.has(p.key) || p.key === "premium_min" || p.key === "premium_max") continue;
    if (p.key === "sess_start" || p.key === "sess_end") continue;   // merged below
    if (p.key === "side" && cfg.trade_side_mode === "BOTH") continue; // BOTH = default, skip in compact view
    const v = p.get(run);
    if (v == null || v === "" || v === 0) continue;
    const pre = SUMMARY_SHORT[p.key];
    parts.push(pre ? `${pre}${typeof v === "string" && pre.endsWith("≥") ? "" : " "}${v}`.trim() : String(v));
  }
  if (cfg.session?.primary) parts.push(`${cfg.session.primary.start}-${cfg.session.primary.end}`);
  if (cfg.quantity?.lots != null) parts.push(`${cfg.quantity.lots}L`);
  return parts.join(" · ");
}

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

export default function RunComparison({
  // shared design + helpers injected from Backtest.jsx (single source of truth)
  colors, spacing, typography, pnlStyle,
  Card, KpiTile,
  apiCall, fmtInr, fmtTs, computeMetrics, EquityCurve,
  onOpenRun,                 // (run_id) => void : jump to the main results view
}) {
  // ── MARGIN_COLUMNS ── sig → estimate; per-day localStorage cache (SPAN
  // is point-in-time, so quotes older than today are stale by definition)
  const [margins, setMargins] = useState(() => {
    // ── MARGIN_COLUMNS ── seed ONLY successful quotes from the daily cache;
    // errors (e.g. "not logged in yet") must NOT survive a reload, or a
    // pre-login page visit bricks the column for the whole day.
    try {
      const raw = JSON.parse(localStorage.getItem(`scalp_margin_cache_${new Date().toISOString().slice(0, 10)}`)) || {};
      return Object.fromEntries(Object.entries(raw).filter(([, v]) => v && v.ok));
    } catch { return {}; }
  });
  const marginFor = useCallback((r) => {
    const spec = capitalSpecOf(r);
    if (!spec) return null;
    if (spec.kind === "local") return { amount: spec.amount, kind: "buy" };
    const q = margins[spec.sig];
    if (q?.ok) return { amount: q.hedged_total, kind: "margin" };
    if (q && !q.ok) return { error: q.error || "margin fetch failed" };
    return null;
  }, [margins]);
  const KPI_DEFS = useMemo(() => makeKpiDefs(fmtInr, marginFor), [fmtInr, marginFor]);   // ── after marginFor (TDZ)
  // ── HEADER_FILTERS ── per-column threshold expressions
  const [colFilters, setColFilters] = useState({});

  const [runs, setRuns] = useState([]);
  // ── MARGIN_COLUMNS ── AUTO-fetch: one live quote per distinct
  // (caps × lots) signature, once per calendar day (errors cached too so a
  // missing Kite session never loops). No button — the column just fills.
  const marginFetching = React.useRef(false);
  useEffect(() => {
    const sigs = [...new Set(runs.map(marginSigOf).filter(Boolean))]
      .filter((g) => !(g in margins));
    if (!sigs.length || marginFetching.current) return;
    marginFetching.current = true;
    (async () => {
      const next = { ...margins };
      for (const g of sigs) {
        try {
          next[g] = await apiCall("/api/backtest/margin-estimate", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ legs: JSON.parse(g) }),   // ── GENERIC_LEGS ──
          });
        } catch (e) { next[g] = { ok: false, error: String(e.message || e) }; }
      }
      setMargins(next);
      try {
        const okOnly = Object.fromEntries(Object.entries(next).filter(([, v]) => v && v.ok));
        localStorage.setItem(`scalp_margin_cache_${new Date().toISOString().slice(0, 10)}`, JSON.stringify(okOnly));
      } catch { /* ignore */ }
      marginFetching.current = false;
    })();
  }, [runs, margins, apiCall]);   // ── after `runs` exists (TDZ fix)
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [limit, setLimit] = useState(300);

  // selection for compare (set of run_id)
  const [selected, setSelected] = useState(() => new Set());
  // lazily-loaded full detail (trades + computed metrics) per run_id
  const [detail, setDetail] = useState({});   // run_id -> { trades, metrics }
  const [detailLoading, setDetailLoading] = useState({}); // run_id -> bool

  // filters
  // ── STRAT_MULTISELECT ── empty Set = All; chips toggle membership
  const [fStrategy, setFStrategy] = useState(() => new Set());
  const [fStatus, setFStatus] = useState("ALL");
  const [fSearch, setFSearch] = useState("");
  const [fProfitableOnly, setFProfitableOnly] = useState(false);

  // sorting
  const [sortKey, setSortKey] = useState("created_at");
  const [sortDir, setSortDir] = useState("desc");

  // view mode
  const [mode, setMode] = useState("table");   // "table" | "compare"

  // inline status (replaces window.alert which Tauri's webview blocks)
  const [msg, setMsg] = useState(null);        // {kind:"ok"|"err"|"info", text}

  // ── REPORT_ENGINE BEGIN ── deterministic report over the selected runs
  const [report, setReport] = useState(null);       // {markdown, file}
  const [reportBusy, setReportBusy] = useState(false);
  const [reportRaw, setReportRaw] = useState(false);   // ── REPORT_VIEW ── raw .md toggle
  const generateReport = useCallback(async () => {
    if (selected.size < 2) return;
    setReportBusy(true);
    setMsg({ kind: "info", text: "Generating report…" });
    try {
      const d = await apiCall("/api/backtest/report", {
        method: "POST",
        body: JSON.stringify({ run_ids: [...selected], title: `report-${selected.size}runs` }),
      });
      setReport({ markdown: d.markdown, file: d.file });
      setMsg({ kind: "ok", text: `Report saved: ${String(d.file).split("/").pop()}` });
    } catch (e) {
      setMsg({ kind: "err", text: `Report failed: ${String(e.message || e)}` });
    } finally { setReportBusy(false); }
  }, [apiCall, selected]);
  // ── REPORT_ENGINE END ──

  // ── REPORT_LIBRARY BEGIN ── saved reports browser
  const [libOpen, setLibOpen] = useState(false);
  const [lib, setLib] = useState([]);            // [{name, size, modified}]
  const [libBusy, setLibBusy] = useState(false);
  const loadLibrary = useCallback(async () => {
    setLibBusy(true);
    try { const d = await apiCall("/api/backtest/reports"); setLib(d.reports || []); }
    catch (e) { setMsg({ kind: "err", text: `Couldn't list reports: ${String(e.message || e)}` }); }
    finally { setLibBusy(false); }
  }, [apiCall]);
  // open → refresh; also refresh after a new report is generated while open
  useEffect(() => { if (libOpen) loadLibrary(); }, [libOpen, loadLibrary, report]);
  const openSavedReport = useCallback(async (name) => {
    try {
      const d = await apiCall(`/api/backtest/reports/${encodeURIComponent(name)}`);
      setReport({ markdown: d.markdown, file: name });
    } catch (e) { setMsg({ kind: "err", text: `Couldn't open ${name}: ${String(e.message || e)}` }); }
  }, [apiCall]);
  const deleteSavedReport = useCallback(async (name) => {
    try {
      await apiCall(`/api/backtest/reports/${encodeURIComponent(name)}`, { method: "DELETE" });
      setLib((l) => l.filter((r) => r.name !== name));
      setReport((r) => (r && String(r.file).endsWith(name) ? null : r));
    } catch (e) { setMsg({ kind: "err", text: `Delete failed: ${String(e.message || e)}` }); }
  }, [apiCall]);
  // ── REPORT_LIBRARY END ──

  // ── AI_PANEL BEGIN ──
  const [aiOpen, setAiOpen] = useState(false);
  const [narrateBusy, setNarrateBusy] = useState(false);
  const addNarrative = useCallback(async () => {
    if (!report) return;
    const name = String(report.file).split("/").pop();
    setNarrateBusy(true);
    setMsg({ kind: "info", text: "Writing narrative locally… (up to a couple of minutes on Intel/CPU)" });
    try {
      const d = await apiCall("/api/backtest/ai/narrate", {
        method: "POST", body: JSON.stringify({ report: name }),
      });
      setReport({ markdown: d.markdown, file: name });
      setMsg({ kind: "ok", text: `Narrative added by ${d.model} in ${d.seconds}s.` });
    } catch (e) {
      setMsg({ kind: "err", text: String(e.message || e) });
    } finally { setNarrateBusy(false); }
  }, [apiCall, report]);
  // ── AI_PANEL END ──

  useEffect(() => {
    if (msg && msg.kind === "ok") {
      const t = setTimeout(() => setMsg(null), 4000);
      return () => clearTimeout(t);
    }
  }, [msg]);

  const reload = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const d = await apiCall(`/api/backtest/runs?limit=${limit}`);
      setRuns(d.runs || []);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [apiCall, limit]);

  useEffect(() => { reload(); }, [reload]);

  // ── lazy-load a run's trades + metrics (for compare / inspect) ──
  const ensureDetail = useCallback(async (runId) => {
    if (detail[runId] || detailLoading[runId]) return;
    setDetailLoading((s) => ({ ...s, [runId]: true }));
    try {
      const d = await apiCall(`/api/backtest/runs/${runId}`);
      const trades = d.trades || [];
      const metrics = computeMetrics(trades);
      setDetail((s) => ({ ...s, [runId]: { trades, metrics, summary: d.summary, config: d.config } }));
    } catch {
      setDetail((s) => ({ ...s, [runId]: { trades: [], metrics: null } }));
    } finally {
      setDetailLoading((s) => ({ ...s, [runId]: false }));
    }
  }, [apiCall, computeMetrics, detail, detailLoading]);

  // when entering compare mode, fetch detail for all selected runs
  useEffect(() => {
    if (mode !== "compare") return;
    selected.forEach((rid) => ensureDetail(rid));
  }, [mode, selected, ensureDetail]);

  const toggleSelect = useCallback((rid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(rid) ? next.delete(rid) : next.add(rid);
      return next;
    });
  }, []);

  const del = useCallback(async (rid) => {
    // No window.confirm — Tauri's webview doesn't reliably support it (it can
    // silently return false, making the button appear dead). Backtest runs are
    // regenerable, so we delete directly and report status inline.
    setMsg({ kind: "info", text: "Deleting…" });
    try {
      await apiCall(`/api/backtest/runs/${rid}`, { method: "DELETE" });
      setRuns((rs) => rs.filter((r) => r.run_id !== rid));
      setSelected((prev) => { const n = new Set(prev); n.delete(rid); return n; });
      setDetail((d) => { const n = { ...d }; delete n[rid]; return n; });
      setMsg({ kind: "ok", text: "Run deleted." });
    } catch (e) {
      setMsg({ kind: "err", text: `Delete failed: ${String(e.message || e)}` });
    }
  }, [apiCall]);

  const delSelected = useCallback(async () => {
    if (!selected.size) return;
    setMsg({ kind: "info", text: `Deleting ${selected.size} run(s)…` });
    const ids = [...selected];
    const deleted = [];
    const failed = [];
    for (const rid of ids) {
      try {
        await apiCall(`/api/backtest/runs/${rid}`, { method: "DELETE" });
        deleted.push(rid);
      } catch (e) {
        failed.push({ rid, msg: String(e.message || e) });
      }
    }
    if (deleted.length) {
      const delSet = new Set(deleted);
      setRuns((rs) => rs.filter((r) => !delSet.has(r.run_id)));
      setSelected((prev) => {
        const n = new Set(prev);
        deleted.forEach((rid) => n.delete(rid));
        return n;
      });
    }
    if (failed.length) {
      setMsg({ kind: "err", text: `Deleted ${deleted.length}, failed ${failed.length}. First error: ${failed[0].msg}` });
    } else {
      setMsg({ kind: "ok", text: `Deleted ${deleted.length} run(s).` });
    }
  }, [apiCall, selected]);

  // ── derived: filtered + sorted rows ──
  const filtered = useMemo(() => {
    let rows = runs.slice();
    if (fStrategy.size) rows = rows.filter((r) => fStrategy.has(r.strategy_id));   // ── STRAT_MULTISELECT ──
    if (fStatus !== "ALL") rows = rows.filter((r) => (r.status || "") === fStatus);
    if (fProfitableOnly) rows = rows.filter((r) => (r.summary?.net_pnl ?? 0) > 0);
    // ── HEADER_FILTERS ── numeric thresholds + params text, per column
    const FILTER_VAL = {
      gross: (r) => r.summary?.gross_pnl,
      charges: (r) => (r.summary?.total_charges != null ? Math.abs(r.summary.total_charges) : null),
      net: (r) => r.summary?.net_pnl,
      winRate: (r) => r.summary?.win_rate,
      trades: (r) => r.summary?.total_trades,
      maxDD: (r) => r.summary?.max_drawdown,
      margin: (r) => marginFor(r)?.amount,
    };
    for (const [k, txt] of Object.entries(colFilters)) {
      if (k === "params") {
        // ── HEADER_FILTERS ── '&'-separated terms, ALL must match (AND)
        const terms = String(txt || "").toLowerCase().split("&")
          .map((t) => t.trim()).filter(Boolean);
        if (terms.length) rows = rows.filter((r) => {
          const hay = paramSummary(r).toLowerCase();
          return terms.every((t) => hay.includes(t));
        });
        continue;
      }
      const pred = parseColFilter(txt);
      if (pred && FILTER_VAL[k]) rows = rows.filter((r) => pred(FILTER_VAL[k](r)));
    }
    if (fSearch.trim()) {
      const q = fSearch.trim().toLowerCase();
      rows = rows.filter((r) =>
        (r.run_id || "").toLowerCase().includes(q) ||
        (r.strategy_id || "").toLowerCase().includes(q) ||
        (r.date_from || "").includes(q) ||
        (r.date_to || "").includes(q)
      );
    }
    const getSort = (r) => {
      switch (sortKey) {
        case "strategy_id": return r.strategy_id;
        case "created_at":  return r.created_at;
        case "net":         return r.summary?.net_pnl;
        case "gross":       return r.summary?.gross_pnl;
        case "charges":     return r.summary?.total_charges;
        case "winRate":     return r.summary?.win_rate;
        case "trades":      return r.summary?.total_trades;
        case "maxDD":       return r.summary?.max_drawdown;
        case "margin":      return marginFor(r)?.amount;   // ── MARGIN_COLUMNS ──
        case "date_from":   return r.date_from;
        default:            return r.created_at;
      }
    };
    rows.sort((a, b) => {
      const r = cmp(getSort(a), getSort(b));
      return sortDir === "asc" ? r : -r;
    });
    return rows;
  }, [runs, fStrategy, fStatus, fProfitableOnly, fSearch, sortKey, sortDir, colFilters, marginFor]);

  const setSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  // ── SELECT_ALL BEGIN ── select/clear every VISIBLE (filtered) row at once.
  // Operates on the filtered set, not all runs, so it composes with the
  // strategy/status/search filters (e.g. filter to HA → select all → compare).
  const allFilteredSelected = useMemo(
    () => filtered.length > 0 && filtered.every((r) => selected.has(r.run_id)),
    [filtered, selected]
  );
  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (filtered.length && filtered.every((r) => next.has(r.run_id))) {
        filtered.forEach((r) => next.delete(r.run_id));   // all on → clear visible
      } else {
        filtered.forEach((r) => next.add(r.run_id));      // otherwise → select visible
      }
      return next;
    });
  }, [filtered]);
  // ── SELECT_ALL END ──

  const selectedRuns = useMemo(
    () => runs.filter((r) => selected.has(r.run_id)),
    [runs, selected]
  );

  // ── styles ──
  const c = colors;
  const th = (key, label, align = "left") => (
    <th
      onClick={key ? () => setSort(key) : undefined}
      style={{
        padding: "9px 10px", textAlign: align, ...typography.label, color: c.text.muted,
        borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap",
        cursor: key ? "pointer" : "default", userSelect: "none",
      }}
    >
      {label}{sortKey === key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
    </th>
  );
  const chip = (active) => ({
    padding: "5px 12px", borderRadius: 6, cursor: "pointer", fontSize: 12, fontWeight: 600,
    border: `1px solid ${active ? c.primary : c.border.light}`,
    background: active ? c.primaryBg : c.bg.secondary,
    color: active ? c.primary : c.text.secondary,
  });
  const smallBtn = (variant) => ({
    padding: "6px 12px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600,
    background: variant === "primary" ? c.primary : variant === "danger" ? c.loss : c.bg.tertiary,
    color: variant === "primary" || variant === "danger" ? "#fff" : c.text.primary,
  });

  /* ── TOOLBAR ── */
  const toolbar = (
    <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
      <div style={{ display: "flex", gap: spacing.md, alignItems: "center", flexWrap: "wrap" }}>
        <input
          placeholder="Search run id / date…"
          value={fSearch} onChange={(e) => setFSearch(e.target.value)}
          style={{
            padding: "7px 10px", borderRadius: 6, border: `1px solid ${c.border.light}`,
            background: c.bg.secondary, color: c.text.primary, fontSize: 13, outline: "none", minWidth: 200,
          }}
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {/* ── WICK_PST_V1_REMOVAL ── retired strategies dropped from the
              filter chips. Archived WICK_V1 / PST_V1 runs are NOT hidden —
              they still appear under "ALL", just without a dedicated chip. */}
          {["ALL", "SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "TSG_V1", "GC_V1" ].map((sId) => (
            <button key={sId}
              style={chip(sId === "ALL" ? fStrategy.size === 0 : fStrategy.has(sId))}
              title={sId === "ALL" ? "Clear strategy filter" : "Click to toggle — combine several strategies"}
              onClick={() => setFStrategy((prev) => {   /* ── STRAT_MULTISELECT ── */
                if (sId === "ALL") return new Set();
                const next = new Set(prev);
                if (next.has(sId)) next.delete(sId); else next.add(sId);
                return next;
              })}>
              {sId === "ALL" ? "All" : STRAT_LABEL[sId]}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["ALL", "done", "error", "cancelled"].map((st) => (
            <button key={st} style={chip(fStatus === st)} onClick={() => setFStatus(st)}>
              {st === "ALL" ? "Any status" : st}
            </button>
          ))}
        </div>
        <button style={chip(fProfitableOnly)} onClick={() => setFProfitableOnly((v) => !v)}>
          Profitable only
        </button>
        {/* ── SELECT_ALL ── toolbar counterpart of the header checkbox */}
        <button style={chip(allFilteredSelected)} onClick={toggleSelectAll}>
          {allFilteredSelected ? "Clear all" : "Select all"}
        </button>

        <div style={{ marginLeft: "auto", display: "flex", gap: spacing.sm, alignItems: "center" }}>
          {msg && (
            <span style={{ fontSize: 12, fontWeight: 600,
              color: msg.kind === "ok" ? c.profit : msg.kind === "err" ? c.loss : c.text.muted }}>
              {msg.text}
            </span>
          )}
          <span style={{ fontSize: 12, color: c.text.muted }}>
            {filtered.length} run{filtered.length === 1 ? "" : "s"}
            {selected.size ? ` · ${selected.size} selected` : ""}
          </span>
          {/* ── REPORT_ENGINE ── */}
          <button style={smallBtn("default")} disabled={selected.size < 2 || reportBusy}
            onClick={generateReport}
            title="Deterministic report: leaderboard, sensitivity, year slices, robust ranking (worst-year first)">
            {reportBusy ? "Generating…" : `📄 Report (${selected.size})`}
          </button>
          {/* ── REPORT_LIBRARY ── */}
          <button style={smallBtn("default")} onClick={() => setLibOpen((v) => !v)}
            title="Browse saved reports">
            {libOpen ? "▾ Saved" : "▸ Saved"}
          </button>
          {/* ── AI_PANEL ── */}
          <button style={smallBtn("default")} onClick={() => setAiOpen((v) => !v)} title="Local AI setup — models & narratives">
            {aiOpen ? "▾ AI" : "▸ AI"}
          </button>
          {selected.size > 0 && (
            <button style={smallBtn("danger")} onClick={delSelected}>Delete selected</button>
          )}
          <button style={smallBtn("default")} onClick={reload}>↻ Refresh</button>
        </div>
      </div>
    </Card>
  );

  if (loading) {
    return <div style={{ padding: 40, textAlign: "center", color: c.text.muted, fontSize: 13 }}>Loading runs…</div>;
  }
  if (err) {
    return (
      <Card elevated style={{ padding: spacing.lg }}>
        <div style={{ color: c.loss, fontSize: 13 }}>Couldn't load runs: {err}</div>
        <button style={{ ...smallBtn("default"), marginTop: spacing.md }} onClick={reload}>Try again</button>
      </Card>
    );
  }
  if (!runs.length) {
    return (
      <Card elevated style={{ padding: "48px 0", textAlign: "center" }}>
        <div style={{ fontSize: 14, color: c.text.secondary, marginBottom: 6 }}>No saved runs yet</div>
        <div style={{ fontSize: 12, color: c.text.muted }}>Run a backtest and it'll show up here to filter, compare, and analyse.</div>
      </Card>
    );
  }

  return (
    <div>
      {toolbar}
      {/* ── REPORT_LIBRARY BEGIN ── */}
      {libOpen && (
        <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: spacing.md }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: c.text.primary }}>Saved reports</span>
            <span style={{ fontSize: 11, ...typography.mono, color: c.text.muted }}>~/.scalp-app/backtest/reports</span>
            <button style={{ ...smallBtn("default"), marginLeft: "auto" }} onClick={loadLibrary}>↻</button>
          </div>
          {libBusy ? (
            <div style={{ color: c.text.muted, fontSize: 12 }}>Loading…</div>
          ) : !lib.length ? (
            <div style={{ color: c.text.muted, fontSize: 12 }}>No saved reports yet — select 2+ runs and press 📄 Report.</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
              <tbody>
                {lib.map((r) => (
                  <tr key={r.name} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                    <td style={{ padding: "8px 10px" }}>
                      <button onClick={() => openSavedReport(r.name)}
                        style={{ border: "none", background: "transparent", cursor: "pointer",
                          color: c.primary, fontSize: 12, fontWeight: 600, fontFamily: "monospace", padding: 0 }}>
                        {r.name}
                      </button>
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>
                      {new Date(r.modified * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontSize: 11, color: c.text.muted }}>
                      {(r.size / 1024).toFixed(1)} KB
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", width: 40 }}>
                      <button onClick={() => deleteSavedReport(r.name)} title="Delete report file"
                        style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13 }}>🗑</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      {/* ── REPORT_LIBRARY END ── */}
      {/* ── AI_PANEL ── */}
      {aiOpen && <AiPanel colors={c} spacing={spacing} typography={typography} Card={Card} apiCall={apiCall} />}
      {/* ── REPORT_ENGINE BEGIN ── */}
      {report && (
        <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, marginBottom: spacing.md }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: c.text.primary }}>Report</span>
            <span style={{ fontSize: 11, ...typography.mono, color: c.text.muted }}>{report.file}</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button style={smallBtn("default")} disabled={narrateBusy} onClick={addNarrative}
                title="Fill section 7 with observations written by the local model — every number stays computed">
                {narrateBusy ? "Writing…" : "✨ Narrative"}
              </button>
              {/* ── REPORT_VIEW ── */}
              <button style={smallBtn("default")} onClick={() => setReportRaw((v) => !v)}
                title="Toggle between the rendered view and the raw markdown">
                {reportRaw ? "Rendered" : "Raw .md"}
              </button>
              <button style={smallBtn("default")} title="Self-contained dark HTML — opens in any browser, share it anywhere"
                onClick={() => {
                  const base = (String(report.file).split("/").pop() || "report.md").replace(/\.md$/, "");
                  const blob = new Blob([buildReportHtml(report.markdown, base)], { type: "text/html;charset=utf-8;" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `${base}.html`;
                  document.body.appendChild(a); a.click(); document.body.removeChild(a);
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}>↓ .html</button>
              <button style={smallBtn("default")} onClick={() => {
                const blob = new Blob([report.markdown], { type: "text/markdown;charset=utf-8;" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url; a.download = String(report.file).split("/").pop() || "report.md";
                document.body.appendChild(a); a.click(); document.body.removeChild(a);
                setTimeout(() => URL.revokeObjectURL(url), 1000);
              }}>↓ .md</button>
              <button style={smallBtn("default")} onClick={() => setReport(null)}>Close</button>
            </div>
          </div>
          {/* ── REPORT_VIEW ── rendered by default; Raw toggle for the source */}
          {reportRaw ? (
            <pre style={{ margin: 0, maxHeight: "65vh", overflow: "auto", fontSize: 12,
              lineHeight: 1.55, fontFamily: "'JetBrains Mono','Fira Code',monospace",
              color: c.text.secondary, whiteSpace: "pre" }}>{report.markdown}</pre>
          ) : (
            <ReportView markdown={report.markdown} colors={c} typography={typography} />
          )}
        </Card>
      )}
      {/* ── REPORT_ENGINE END ── */}
      {mode === "table" ? (
        <RunsTable
          rows={filtered} c={c} spacing={spacing} typography={typography} pnlStyle={pnlStyle}
          Card={Card} fmtInr={fmtInr} th={th}
          selected={selected} toggleSelect={toggleSelect}
          allSelected={allFilteredSelected} toggleSelectAll={toggleSelectAll}
          onDelete={del} onOpenRun={onOpenRun}
          STRAT_LABEL={STRAT_LABEL} STATUS_COLOR={STATUS_COLOR}
          marginFor={marginFor} colFilters={colFilters} setColFilters={setColFilters}
        />
      ) : (
        <CompareView
          runs={selectedRuns} detail={detail} detailLoading={detailLoading}
          c={c} spacing={spacing} typography={typography} pnlStyle={pnlStyle}
          Card={Card} fmtInr={fmtInr} EquityCurve={EquityCurve}
          KPI_DEFS={KPI_DEFS} PARAM_DEFS={PARAM_DEFS} STRAT_LABEL={STRAT_LABEL}
          onOpenRun={onOpenRun}
        />
      )}
    </div>
  );
}

/* ============================================================================
   RUNS TABLE — sortable, selectable, with inline params + headline KPIs.
   ========================================================================== */
function RunsTable({
  rows, c, spacing, typography, pnlStyle, Card, fmtInr, th,
  selected, toggleSelect, allSelected, toggleSelectAll,
  onDelete, onOpenRun, STRAT_LABEL, STATUS_COLOR,
  marginFor, colFilters, setColFilters,   // ── MARGIN_COLUMNS / HEADER_FILTERS ──
}) {
  // ── HEADER_FILTERS ── one small input per filterable column
  const filterCell = (key, ph, align = "right") => (
    <th style={{ padding: "2px 6px 6px", borderBottom: `2px solid ${c.border.light}` }}>
      <input type="text" value={colFilters[key] || ""} placeholder={ph}
        onChange={(e) => setColFilters((f) => ({ ...f, [key]: e.target.value }))}
        style={{ width: "100%", minWidth: 54, boxSizing: "border-box", background: c.bg.primary,
          border: `1px solid ${c.border.dark}`, borderRadius: 4, color: c.text.secondary,
          fontSize: 10, padding: "2px 5px", textAlign: align }} />
    </th>
  );
  const tsLabel = (epoch) => {
    if (!epoch) return "—";
    const d = new Date(epoch * 1000);
    return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  };
  const money = (v, signed = true) =>
    v == null ? "—" : `${signed && v >= 0 ? "+" : v < 0 ? "-" : ""}${fmtInr(Math.abs(v))}`;

  return (
    <Card style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
        <thead style={{ background: c.bg.tertiary }}>
          <tr>
            {/* ── SELECT_ALL ── header checkbox: toggles every visible row */}
            <th style={{ padding: "9px 10px", width: 32, borderBottom: `2px solid ${c.border.light}`, textAlign: "center" }}>
              <input type="checkbox" checked={!!allSelected} onChange={toggleSelectAll}
                title={allSelected ? "Clear all visible" : "Select all visible"} />
            </th>
            {th("strategy_id", "Strat")}
            {th("created_at", "When")}
            {th("date_from", "Period")}
            <th style={{ padding: "9px 10px", textAlign: "left", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, whiteSpace: "nowrap" }}>Key params</th>
            {/* ── GROSS_CHARGES ── gross + charges alongside net so a run's cost
                drag is visible in the list (option-buying at high trade counts
                is charge-heavy; net alone hides edge-vs-cost). */}
            {/* ── MARGIN_COLUMNS ── today's basket margin per config, auto-fetched */}
            {th("margin", "Margin", "right")}
            {th("gross", "Gross", "right")}
            {th("charges", "Charges", "right")}
            {th("net", "Net", "right")}
            {th("winRate", "Win%", "right")}
            {th("trades", "Trades", "right")}
            {th("maxDD", "Max DD", "right")}
            <th style={{ padding: "9px 10px", textAlign: "right", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}` }}>Status</th>
            <th style={{ padding: "9px 10px", width: 90, borderBottom: `2px solid ${c.border.light}` }} />
          </tr>
          {/* ── HEADER_FILTERS ── threshold row: ">30", "<4L", ">=30L", "=37";
              plain number means >=; L/Cr suffixes supported */}
          <tr>
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            {filterCell("params", "a & b…", "left")}
            {filterCell("margin", "e.g. <10L")}
            {filterCell("gross", "e.g. >30L")}
            {filterCell("charges", "e.g. <6L")}
            {filterCell("net", "e.g. >30L")}
            {filterCell("winRate", "e.g. >30")}
            {filterCell("trades", "e.g. >1000")}
            {filterCell("maxDD", "e.g. <4L")}
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const s = r.summary || {};
            const isSel = selected.has(r.run_id);
            // ── PARAMS_FULL ── the Key params column now uses the SAME
            // paramSummary as the compare header/legend/CSV, so every set knob
            // (entry conds, caps, fixed target, TP hold, timeframe, wick, …)
            // is visible in the list — not just the old V1/V5 subset.
            const keyParams = paramSummary(r);

            return (
              <tr key={r.run_id}
                style={{ background: isSel ? c.primaryBg : i % 2 ? c.bg.secondary : c.bg.primary,
                  borderTop: `1px solid ${c.border.dark}` }}>
                <td style={{ padding: "8px 10px", textAlign: "center" }}>
                  <input type="checkbox" checked={isSel} onChange={() => toggleSelect(r.run_id)} />
                </td>
                <td style={{ padding: "8px 10px", fontWeight: 700 }}>{STRAT_LABEL[r.strategy_id] || r.strategy_id}</td>
                <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>{tsLabel(r.created_at)}</td>
                <td style={{ padding: "8px 10px", ...typography.mono, fontSize: 11, color: c.text.tertiary, whiteSpace: "nowrap" }}>{r.date_from} → {r.date_to}</td>
                <td style={{ padding: "8px 10px", fontSize: 11, color: c.text.secondary, maxWidth: 320 }}>{keyParams || "—"}</td>
                {/* ── MARGIN_COLUMNS ── snapshot + funding band (snapshot
                    × 1.25–1.4: adverse drift, SPAN refiles 5×/day, MTM drag;
                    heuristic, NOT an API figure — sort/filter use the snapshot) */}
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.text.secondary, whiteSpace: "nowrap" }}
                  title="Today's Zerodha basket margin for this run's caps × lots (identical configs share one quote). 'plan' = snapshot × 1.25–1.4 — the intraday funding band to hold against adverse drift, SPAN refiles and MTM drag; a stated heuristic, not an exchange figure.">
                  {(() => {
                    const mv = marginFor?.(r);
                    if (mv == null) return "—";
                    if (mv.error) return <span title={`${mv.error} — retried on next page load / Refresh`} style={{ color: c.text.muted }}>—!</span>;
                    const L = (x) => `₹${(x / 100000).toFixed(2)}L`;
                    if (mv.kind === "buy") return (<>{L(mv.amount)}<span style={{ fontSize: 10, color: c.text.muted }}> buy</span></>);
                    return (<>
                      {L(mv.amount)}
                      <span style={{ fontSize: 10, color: c.text.muted }}> plan ₹{(mv.amount * 1.25 / 100000).toFixed(1)}–{(mv.amount * 1.4 / 100000).toFixed(1)}L</span>
                    </>);
                  })()}
                </td>
                {/* ── GROSS_CHARGES ── gross (signed) + charges (always debit) + net */}
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(s.gross_pnl) }}>{money(s.gross_pnl)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>{s.total_charges != null ? `−${fmtInr(Math.abs(s.total_charges))}` : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, fontWeight: 700, ...pnlStyle(s.net_pnl) }}>{money(s.net_pnl)}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: (s.win_rate ?? 0) >= 50 ? c.profit : c.loss }}>{s.win_rate != null ? `${s.win_rate.toFixed(0)}%` : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono }}>{s.total_trades ?? "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", ...typography.mono, color: c.loss }}>{s.max_drawdown != null ? fmtInr(s.max_drawdown) : "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", fontSize: 11, fontWeight: 700, color: STATUS_COLOR(c, r.status) }}>{r.status || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                  <button title="Open in results"
                    onClick={() => onOpenRun?.(r.run_id)}
                    style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600, marginRight: 8 }}>
                    Open
                  </button>
                  <button title="Delete run"
                    onClick={() => onDelete(r.run_id)}
                    style={{ border: "none", background: "transparent", cursor: "pointer", color: c.loss, fontSize: 13 }}>
                    🗑
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

/* ============================================================================
   COMPARE VIEW — side-by-side params + exhaustive KPIs with best/worst
   highlighting and delta-vs-baseline, plus overlaid equity curves.
   ========================================================================== */
function CompareView({
  runs, detail, detailLoading, c, spacing, typography, pnlStyle,
  Card, fmtInr, EquityCurve, KPI_DEFS, PARAM_DEFS, STRAT_LABEL, onOpenRun,
}) {
  const [baselineIdx, setBaselineIdx] = useState(0);
  const wrapRef = useRef(null);
  const [w, setW] = useState(760);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width - 32)));
    ro.observe(wrapRef.current);
    setW(Math.max(320, wrapRef.current.offsetWidth - 32));
    return () => ro.disconnect();
  }, []);

  if (runs.length < 2) {
    return <Card elevated style={{ padding: 32, textAlign: "center", color: c.text.muted, fontSize: 13 }}>Select at least two runs to compare.</Card>;
  }

  const cols = runs.map((r) => ({
    run: r,
    d: detail[r.run_id],
    loading: detailLoading[r.run_id],
  }));

  // Per-KPI: compute each run's raw value, find best (by dir), format + delta.
  const valueFor = (def, col) => def.get(col.d?.metrics, col.run.summary, col.run);   // ── MARGIN_COLUMNS ── run passed for config-derived defs

  const colHead = (col, idx) => {
    const r = col.run;
    const isBase = idx === baselineIdx;
    return (
      <th key={r.run_id} style={{ padding: "10px 12px", textAlign: "right", minWidth: 150, verticalAlign: "bottom",
        borderBottom: `2px solid ${c.border.light}`, background: isBase ? c.primaryBg : "transparent" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: c.text.primary }}>{STRAT_LABEL[r.strategy_id] || r.strategy_id}</span>
          <span style={{ fontSize: 10, ...typography.mono, color: c.text.muted }}>{r.run_id.slice(0, 8)}</span>
          <span style={{ fontSize: 10, color: c.text.tertiary }}>{r.date_from} → {r.date_to}</span>
          <span style={{ fontSize: 9, color: c.text.muted, fontFamily: "monospace", maxWidth: 150, textAlign: "right", lineHeight: 1.3 }}>{paramSummary(r) || "—"}</span>
          <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
            <button onClick={() => setBaselineIdx(idx)}
              style={{ border: "none", background: "transparent", cursor: "pointer",
                fontSize: 10, fontWeight: 700, color: isBase ? c.primary : c.text.muted }}>
              {isBase ? "● baseline" : "set baseline"}
            </button>
            <button onClick={() => onOpenRun?.(r.run_id)}
              style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 10, fontWeight: 700, color: c.primary }}>open</button>
          </div>
        </div>
      </th>
    );
  };

  const anyLoading = cols.some((col) => col.loading || !col.d);

  // ── Column (metric) picker state — persisted in localStorage ──
  const [selectedKeys, setSelectedKeys] = useState(() => loadSelectedKeys(KPI_DEFS));
  const [pickerOpen, setPickerOpen] = useState(false);
  useEffect(() => { saveSelectedKeys(selectedKeys); }, [selectedKeys]);

  const toggleKey = (key) =>
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const setAll = (on) =>
    setSelectedKeys(on ? new Set(KPI_DEFS.map((d) => d.key)) : new Set());
  const resetDefaults = () =>
    setSelectedKeys(new Set(KPI_DEFS.filter((d) => d.def).map((d) => d.key)));

  // group KPIs by their `group` for sectioned rows (all defs, for the picker)
  const groups = [];
  const seen = {};
  KPI_DEFS.forEach((def) => {
    if (!seen[def.group]) { seen[def.group] = []; groups.push([def.group, seen[def.group]]); }
    seen[def.group].push(def);
  });

  // Only the groups/defs the user has enabled (drives the rendered matrix).
  const visibleGroups = groups
    .map(([group, defs]) => [group, defs.filter((d) => selectedKeys.has(d.key))])
    .filter(([, defs]) => defs.length > 0);

  // ── Download the comparison as CSV (params + visible KPIs, one col per run) ──
  const downloadComparisonCsv = () => {
    const esc = (v) => {
      if (v == null) return "";
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [];
    // header row: Metric, then one column per run (strategy + short id)
    lines.push(["Metric", ...cols.map((col) => `${STRAT_LABEL[col.run.strategy_id] || col.run.strategy_id} ${col.run.run_id.slice(0, 8)}`)].map(esc).join(","));
    lines.push(["run_id", ...cols.map((col) => col.run.run_id)].map(esc).join(","));
    lines.push(["period", ...cols.map((col) => `${col.run.date_from} to ${col.run.date_to}`)].map(esc).join(","));
    lines.push(["params", ...cols.map((col) => paramSummary(col.run))].map(esc).join(","));
    lines.push("");
    // params section (raw values)
    lines.push("PARAMETERS");
    PARAM_DEFS.forEach((p) => {
      const vals = cols.map((col) => p.get(col.run));
      if (vals.every((v) => v == null || v === "")) return;
      lines.push([p.label, ...vals.map((v) => (v == null ? "" : v))].map(esc).join(","));
    });
    lines.push("");
    // visible KPI sections (raw numeric values, not formatted, for spreadsheet use)
    visibleGroups.forEach(([group, defs]) => {
      lines.push(group.toUpperCase());
      defs.forEach((def) => {
        const vals = cols.map((col) => def.get(col.d?.metrics, col.run.summary, col.run));
        lines.push([def.label, ...vals.map((v) => (v == null || v === Infinity ? "" : v))].map(esc).join(","));
      });
      lines.push("");
    });
    const csv = lines.join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest_comparison_${cols.length}runs_${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const fmtDelta = (def, v, base) => {
    if (v == null || base == null || def.dir === 0) return null;
    const diff = v - base;
    if (!isFinite(diff) || diff === 0) return null;
    const better = def.dir > 0 ? diff > 0 : diff < 0;
    const arrow = diff > 0 ? "▲" : "▼";
    return { text: `${arrow} ${def.fmt(Math.abs(diff))}`, color: better ? c.profit : c.loss };
  };

  return (
    <div ref={wrapRef}>
      {/* ── COLUMN (metric) PICKER ── */}
      <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
          <button
            onClick={() => setPickerOpen((v) => !v)}
            style={{ padding: "6px 12px", borderRadius: 6, border: `1px solid ${c.border.light}`,
              background: c.bg.secondary, color: c.text.primary, cursor: "pointer", fontSize: 12, fontWeight: 600 }}>
            {pickerOpen ? "▾ Metrics" : "▸ Metrics"} ({selectedKeys.size})
          </button>
          <span style={{ fontSize: 11, color: c.text.muted }}>
            Pick which rows to show. Choice is remembered.
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button onClick={downloadComparisonCsv} title="Download the comparison matrix as CSV"
              style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.primary, color: "#fff" }}>↓ CSV</button>
            <button onClick={resetDefaults} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>Defaults</button>
            <button onClick={() => setAll(true)} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>All</button>
            <button onClick={() => setAll(false)} style={{ padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, background: c.bg.tertiary, color: c.text.primary }}>None</button>
          </div>
        </div>
        {pickerOpen && (
          <div style={{ marginTop: spacing.md, display: "flex", flexDirection: "column", gap: spacing.md }}>
            {groups.map(([group, defs]) => (
              <div key={group}>
                <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: 0.5, textTransform: "uppercase", color: c.text.muted, marginBottom: 6 }}>{group}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {defs.map((d) => {
                    const on = selectedKeys.has(d.key);
                    return (
                      <button key={d.key} onClick={() => toggleKey(d.key)}
                        style={{ padding: "4px 10px", borderRadius: 14, cursor: "pointer", fontSize: 11, fontWeight: 600,
                          border: `1px solid ${on ? c.primary : c.border.light}`,
                          background: on ? c.primaryBg : c.bg.secondary,
                          color: on ? c.primary : c.text.muted }}>
                        {on ? "✓ " : ""}{d.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* equity overlay */}
      <Card elevated style={{ padding: 16, marginBottom: spacing.lg }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>Equity curves (overlay)</span>
          <span style={{ fontSize: 11, color: c.text.muted }}>net cumulative P&L per run</span>
        </div>
        {anyLoading ? (
          <div style={{ padding: "48px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>Loading run trades…</div>
        ) : (
          <EquityOverlay cols={cols} width={w} height={260} c={c} fmtInr={fmtInr} STRAT_LABEL={STRAT_LABEL} />
        )}
      </Card>

      {/* params + KPI matrix (scrolls vertically; header stays put) */}
      <Card style={{ overflow: "auto", maxHeight: "70vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
          <thead style={{ background: c.bg.tertiary, position: "sticky", top: 0, zIndex: 1 }}>
            <tr>
              <th style={{ padding: "10px 12px", textAlign: "left", ...typography.label, color: c.text.muted, borderBottom: `2px solid ${c.border.light}`, background: c.bg.tertiary }}>Metric</th>
              {cols.map((col, idx) => colHead(col, idx))}
            </tr>
          </thead>
          <tbody>
            {/* PARAMS section */}
            <SectionRow label="Parameters" span={cols.length + 1} c={c} />
            {PARAM_DEFS.map((p) => {
              const vals = cols.map((col) => p.get(col.run));
              if (vals.every((v) => v == null || v === "")) return null;  // hide params none of them set
              return (
                <tr key={p.key} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                  <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{p.label}</td>
                  {vals.map((v, idx) => {
                    const base = vals[baselineIdx];
                    const diff = idx !== baselineIdx && v !== base;
                    return (
                      <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                        color: diff ? c.text.primary : c.text.muted,
                        fontWeight: diff ? 700 : 400,
                        background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                        {v == null || v === "" ? "—" : String(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}

            {/* KPI sections — only the user-enabled metrics */}
            {visibleGroups.map(([group, defs]) => (
              <React.Fragment key={group}>
                <SectionRow label={group} span={cols.length + 1} c={c} />
                {defs.map((def) => {
                  const raw = cols.map((col) => valueFor(def, col));
                  const nums = raw.filter((v) => typeof v === "number" && isFinite(v));
                  let bestVal = null;
                  if (def.dir !== 0 && nums.length) bestVal = def.dir > 0 ? Math.max(...nums) : Math.min(...nums);
                  const base = raw[baselineIdx];
                  return (
                    <tr key={def.key} style={{ borderTop: `1px solid ${c.border.dark}` }}>
                      <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{def.label}</td>
                      {raw.map((v, idx) => {
                        const isBest = def.dir !== 0 && v != null && v === bestVal && nums.length > 1;
                        const delta = idx !== baselineIdx ? fmtDelta(def, v, base) : null;
                        return (
                          <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                            background: idx === baselineIdx ? c.primaryBg : isBest ? c.successBg : "transparent" }}>
                            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                              <span style={{ fontWeight: isBest ? 800 : 600,
                                color: isBest ? c.profit : c.text.primary }}>{def.fmt(v)}</span>
                              {delta && <span style={{ fontSize: 9, color: delta.color }}>{delta.text}</span>}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}

            {/* Exit-reason mini-matrix (net per reason, if metrics present) */}
            <ExitReasonRows cols={cols} c={c} typography={typography} fmtInr={fmtInr} baselineIdx={baselineIdx} />

            {/* ── COND_MATRIX ── Entry-condition mini-matrix (HA runs). Same
                shape as exit reasons: net · trades · win% per condition per
                run — the direct read-out for the condition-isolation workflow
                (queue C1/C2/C3/all → compare here). */}
            <EntryConditionRows cols={cols} c={c} typography={typography} fmtInr={fmtInr} baselineIdx={baselineIdx} />
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function SectionRow({ label, span, c }) {
  return (
    <tr>
      <td colSpan={span} style={{ padding: "8px 12px", background: c.bg.secondary,
        fontSize: 11, fontWeight: 800, letterSpacing: 0.5, textTransform: "uppercase", color: c.text.muted,
        borderTop: `2px solid ${c.border.light}` }}>{label}</td>
    </tr>
  );
}

function ExitReasonRows({ cols, c, typography, fmtInr, baselineIdx }) {
  // union of exit reasons across runs
  const reasons = new Set();
  cols.forEach((col) => (col.d?.metrics?.exitReasons || []).forEach((er) => reasons.add(er.reason)));
  if (!reasons.size) return null;
  const reasonList = [...reasons].sort();
  const lookup = (col, reason) => (col.d?.metrics?.exitReasons || []).find((er) => er.reason === reason);
  return (
    <>
      <SectionRow label="Exit reasons (net · trades · win%)" span={cols.length + 1} c={c} />
      {reasonList.map((reason) => (
        <tr key={reason} style={{ borderTop: `1px solid ${c.border.dark}` }}>
          <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{reason}</td>
          {cols.map((col, idx) => {
            const er = lookup(col, reason);
            return (
              <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                {er ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                    <span style={{ color: er.pnl >= 0 ? c.profit : c.loss, fontWeight: 700 }}>
                      {er.pnl >= 0 ? "+" : "-"}{fmtInr(Math.abs(er.pnl))}
                    </span>
                    <span style={{ fontSize: 9, color: c.text.muted }}>
                      {er.trades}t · {er.trades ? Math.round((er.wins / er.trades) * 100) : 0}%
                    </span>
                  </div>
                ) : <span style={{ color: c.text.muted }}>—</span>}
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}

/* ── COND_MATRIX BEGIN ── per-entry-condition rows for HA_V1 / HA_SELL runs.
   Reads metrics.entryConditions (computed in Backtest.jsx's computeMetrics
   from each trade's `condition` field). Renders nothing for non-HA runs. */
function EntryConditionRows({ cols, c, typography, fmtInr, baselineIdx }) {
  const conds = new Set();
  cols.forEach((col) => (col.d?.metrics?.entryConditions || []).forEach((ec) => conds.add(ec.reason)));
  if (!conds.size) return null;
  const condList = [...conds].sort();
  const lookup = (col, cond) => (col.d?.metrics?.entryConditions || []).find((ec) => ec.reason === cond);
  return (
    <>
      <SectionRow label="Entry conditions (net · trades · win%)" span={cols.length + 1} c={c} />
      {condList.map((cond) => (
        <tr key={cond} style={{ borderTop: `1px solid ${c.border.dark}` }}>
          <td style={{ padding: "7px 12px", color: c.text.secondary, fontWeight: 600 }}>{cond}</td>
          {cols.map((col, idx) => {
            const ec = lookup(col, cond);
            return (
              <td key={idx} style={{ padding: "7px 12px", textAlign: "right", ...typography.mono,
                background: idx === baselineIdx ? c.primaryBg : "transparent" }}>
                {ec ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                    <span style={{ color: ec.pnl >= 0 ? c.profit : c.loss, fontWeight: 700 }}>
                      {ec.pnl >= 0 ? "+" : "-"}{fmtInr(Math.abs(ec.pnl))}
                    </span>
                    <span style={{ fontSize: 9, color: c.text.muted }}>
                      {ec.trades}t · {ec.trades ? Math.round((ec.wins / ec.trades) * 100) : 0}%
                    </span>
                  </div>
                ) : <span style={{ color: c.text.muted }}>—</span>}
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}
/* ── COND_MATRIX END ── */

/* ── overlaid equity curves (multi-series, net cumulative) ── */
function EquityOverlay({ cols, width, height, c, fmtInr, STRAT_LABEL }) {
  const series = cols
    .map((col, i) => {
      const eq = col.d?.metrics?.equityCurve;
      if (!eq || eq.length < 2) return null;
      return { idx: i, run: col.run, points: eq.map((p) => p.value) };
    })
    .filter(Boolean);

  if (!series.length) {
    return <div style={{ padding: "40px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>No closed trades to chart</div>;
  }

  const PAL = [c.primary, c.profit, c.warning, c.loss, "#a855f7", "#14b8a6", "#f97316"];
  const P = { top: 16, right: 16, bottom: 24, left: 76 };
  const W = width - P.left - P.right;
  const H = height - P.top - P.bottom;
  const maxLen = Math.max(...series.map((s) => s.points.length));
  const allVals = series.flatMap((s) => s.points).concat([0]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = (maxV - minV) || 1;
  const px = (i, len) => P.left + (len <= 1 ? 0 : (i / (len - 1)) * W);
  const py = (v) => P.top + H - ((v - minV) / range) * H;
  const y0 = py(0);
  const ticks = Array.from({ length: 5 }, (_, i) => minV + (range / 4) * i);

  return (
    <div>
      <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={P.left} y1={py(t)} x2={P.left + W} y2={py(t)} stroke={c.border.dark} strokeWidth={0.5} />
            <text x={P.left - 6} y={py(t) + 4} textAnchor="end" fontSize={9} fill={c.text.muted} fontFamily="monospace">
              {t < 0 ? "-" : ""}{fmtInr(Math.abs(t))}
            </text>
          </g>
        ))}
        {minV < 0 && maxV > 0 && (
          <line x1={P.left} y1={y0} x2={P.left + W} y2={y0} stroke={c.text.muted} strokeWidth={1} strokeDasharray="4 3" opacity={0.5} />
        )}
        {series.map((s) => {
          const color = PAL[s.idx % PAL.length];
          const d = s.points.map((v, i) => `${i === 0 ? "M" : "L"} ${px(i, s.points.length).toFixed(1)} ${py(v).toFixed(1)}`).join(" ");
          return <path key={s.run.run_id} d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" opacity={0.9} />;
        })}
      </svg>
      {/* legend — strategy, short id, params, end P&L */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
        {series.map((s) => {
          const color = PAL[s.idx % PAL.length];
          const end = s.points[s.points.length - 1];
          const summary = paramSummary(s.run);
          return (
            <div key={s.run.run_id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
              <span style={{ width: 14, height: 3, background: color, borderRadius: 2, flexShrink: 0 }} />
              <span style={{ color: c.text.secondary, fontWeight: 700, whiteSpace: "nowrap" }}>
                {STRAT_LABEL[s.run.strategy_id] || s.run.strategy_id} {s.run.run_id.slice(0, 6)}
              </span>
              <span style={{ color: c.text.muted, fontFamily: "monospace", fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {summary || "—"}
              </span>
              <span style={{ fontFamily: "monospace", color: end >= 0 ? c.profit : c.loss, marginLeft: "auto", flexShrink: 0, fontWeight: 700 }}>
                {end >= 0 ? "+" : "-"}{fmtInr(Math.abs(end))}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
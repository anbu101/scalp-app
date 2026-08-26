import { Fragment, useEffect, useState } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";
import { colors, spacing, typography } from "../tokens";
import { useIsMobile } from "../hooks/useIsMobile";
import AppSettingsSection from "../components/AppSettingsSection";
import { useEntitlements } from "../hooks/useEntitlements";
import AccountSelector from "../components/AccountSelector"; // ACC2
// ── UI_MASK ── non-admin licenses get the lots-only settings surface
import LotsOnlySettings from "./LotsOnlySettings";
// ── CAS_2026 ── single source of truth for session boundaries
import { MARKET_START_HM, FNO_END_HM } from "../marketSession";

/* ─────────────────────────────────────────────
   Settings-specific token aliases
───────────────────────────────────────────── */

const settingsSpacing = { ...spacing, xxl: 28 };

const label = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

/* ─────────────────────────────────────────────
   Strategy accent colours — MUST match the Dashboard
   StrategyHost META map so the colour code is identical
   across both pages. APP is a settings-only item and
   gets a neutral grey so it stays visually distinct.
───────────────────────────────────────────── */

const STRATEGY_ACCENT = {
  SCALP_V1: colors.warning ?? "#f59e0b",
  SCALP_V3: "#ec4899",
  PST_SELL: "#fb7185",
  PST_HEDGE: "#be123c",
  SCALP_V5: "#06b6d4",
  BB_V1:    colors.primary ?? "#3b82f6",
  BB_V2:    "#3b82f6",
  HA_V1:    "#14b8a6",
  IC_V1:    "#14b8a6",   // ── IC_SPLIT ──
  IC_V2:    "#6366f1",   // ── IC_SPLIT ── was "IC_V1"
  APP:      colors.text.muted,
};

/* ─────────────────────────────────────────────
   Layout helpers
───────────────────────────────────────────── */

/* ─────────────────────────────────────────────
   Default configs
───────────────────────────────────────────── */

const DEFAULT_SCALP_CONFIG = {
  trade_execution_mode: "PAPER",
  min_sl_points:     0,
  max_sl_points:     0,
  risk_max_sl_points: 0,
  risk_reward_ratio: 1,
  risk_reward_ratio: 1,
  max_loss:   0,
  max_profit: 0,
  // ── CAS_2026 ── fallback defaults only (real values come from the backend
  // via getStrategyConfig). NFO closes 15:40 from 2026-08-03.
  session: {
    primary:   { start: MARKET_START_HM, end: FNO_END_HM },
    secondary: { enabled: false, start: MARKET_START_HM, end: FNO_END_HM },
  },
  option_premium: { min: 0, max: 0 },
  quantity:       { lots: 1, lot_size: 65 },
};

const DEFAULT_BB_CONFIG = {
  trade_execution_mode: "PAPER",
  sl_pct:               20,
  tp_pct:               100,
  lots:                 1,
  multiple_targets:     false,
  tp1_pct:              50,
  tp2_pct:              100,
  lots_leg1:            1,
  lots_leg2:            1,
  trailing_sl:          false,
  max_premium:          200,
  max_trades_per_side:  2,
  auto_square_off_time: "15:15",
  session_start:        "09:15",
  session_end:          "15:15",
  st_exit_gap:          20,
  max_loss:   0,
  max_profit: 0,
};

const DEFAULT_BB_V2_CONFIG = {
  trade_execution_mode: "PAPER",
  sl_pct:               20,
  tp_pct:               100,
  max_premium:          300,
  max_trades_per_side:  10,
  ce_lots:              1,
  pe_lots:              1,
  auto_square_off_time: "15:15",
  session_start:        "09:15",
  session_end:          "15:15",
  max_loss:   0,
  max_profit: 0,
};

const DEFAULT_HA_CONFIG = {
  trade_execution_mode: "PAPER",
  risk_reward_ratio:    2.0,
  min_sl_points:        0,
  max_loss:   0,
  max_profit: 0,
  target_override:      { enabled: false, points: 0 },
  option_premium:       { min: 50, max: 300 },
  quantity:             { lots: 1, lot_size: 65 },
  max_trades_per_side:  10,
  // ── HA_COND_FILTER ── enabled entry conditions (subset of COND1/2/3).
  // The load path deep-merges DEFAULT_HA_CONFIG under the stored config, so
  // existing saved configs auto-backfill to ALL at load — matching the
  // backend's fail-open default.
  entry_conditions:     ["COND1", "COND2", "COND3"],
  // ── HA_COND1_FLIP ── COND1-only opposite-side entry (backtest-validated).
  // Deep-merge backfills existing saved configs to FALSE = current behaviour;
  // the tick engine treats an absent/false key as flip-off (fail-closed).
  cond1_flip_side:      false,
  trade_side_mode:      "BOTH",
  session: {
    primary:   { start: "09:15", end: "15:20" },
    secondary: { enabled: false, start: "09:15", end: "15:20" },
  },
};

// ── PST_SELL / PST_HEDGE defaults — same shape the backtest + live loop use.
// ── TMA_V1 BEGIN ──
// "HH:MM" → minutes-since-midnight, for the entry-window validity warning
// (mirrors the backend's hm_to_min; bad input sorts as -1 so it warns).
function hmToMinUI(v) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(v || "").trim());
  if (!m) return -1;
  return Number(m[1]) * 60 + Number(m[2]);
}

// Mirrors the backend default in strategy_loader (frozen 2026-07-19 spec).
const DEFAULT_TMA_CONFIG = {
  trade_execution_mode: "PAPER",
  trade_mode: "INTRADAY",
  cut_neg_mtm_eod: false,
  session_start: "09:15",
  session_end: "15:00",
  exit_time: "15:25",
  wing_mode: "real_fallback",
  margin_guard: true,
  quantity: { lot_size: 65 },
  c1: {
    sell: { premium_max: 100, lots: 1, sl_pct: 30, tp_pct: 50, sl_unit: "PCT", tp_unit: "PCT" },
    buy:  { premium_max: 3, lots: 1 },
    max_trades_per_day: 0,
  },
};
// ── TMA_V1 END ──

// ── TMA_V2 BEGIN ──
// Mirrors the backend default in strategy_loader (frozen study 2026-08-19).
// The LOCKED study params (xover_exit_ref 55, ema144_slope_gate, the
// always-on crossover exit, SELL-only execution) are deliberately NOT in
// this object and NOT rendered: they are backend config keys only, so the
// UI can never drift them. max_extension_pct IS user-facing.
const DEFAULT_TMA2_CONFIG = {
  trade_execution_mode: "PAPER",
  trade_mode: "POSITIONAL",
  cut_neg_mtm_eod: false,
  session_start: "09:15",
  session_end: "15:00",
  exit_time: "15:25",
  wing_mode: "real_fallback",
  margin_guard: false,
  max_extension_pct: 0.8,
  quantity: { lot_size: 65 },
  s1: {
    main:  { premium_max: 200, lots: 10, sl_pct: 12, tp_pct: 10, sl_unit: "PCT", tp_unit: "ABS" },
    hedge: { premium_max: 5, lots: 10 },
    max_trades_per_day: 0,
  },
};
// ── TMA_V2 END ──

// ── TSG_V1 BEGIN ──
// Mirrors the backend default in strategy_loader (backtest-validated
// 2026-08-02: MTM SL 35k · target 0 · IV Δ+4 · trail rejected).
const DEFAULT_TSG_CONFIG = {
  trade_execution_mode: "PAPER",
  entry_time: "09:16",
  exit_time: "15:26",
  entry_late_grace_s: 120,
  lots: 1,
  expiry_lots: 0,
  lot_size: 65,
  mtm_sl: 35000,
  mtm_target: 0,
  iv_sl_delta_pts: 4,
  iv_sl_pct: 0,
  min_entry_iv: 0.10,
  legs: [
    { id: "L1", action: "SELL", opt_type: "CE", premium_max: 85 },
    { id: "L2", action: "SELL", opt_type: "PE", premium_max: 85 },
    { id: "L3", action: "BUY", opt_type: "CE", premium_max: 5 },
    { id: "L4", action: "BUY", opt_type: "PE", premium_max: 5 },
  ],
};
// ── TSG_V1 END ──

const DEFAULT_PST_CONFIG = {
  trade_execution_mode: "PAPER",
  premium_max: 150,
  side_mode: "BOTH",
  max_trades_per_day: 0,
  exit_time: "15:25",
  entry_cutoff_time: "15:00",
  signal_tf: 3,
  sma: { period: 9, tf: 5 },
  supertrend: { period: 10, mult: 2, tf: 3 },
  legs: [
    { id: "L1", lots: 2, sl_pct: 15, spot_tg_points: 20 },
    { id: "L2", lots: 1, sl_pct: 15, spot_tg_points: 50 },
  ],
  daily_max_loss: 0, daily_max_profit: 0,
  monthly_max_loss: 0, monthly_max_profit: 0,
};

const DEFAULT_SCALP_V3_CONFIG = {
  trade_execution_mode: "PAPER",
  min_sl_points:        5,
  max_sl_points:        20,
  risk_max_sl_points:   0,
  hedge_sl_points:      20,
  risk_reward_ratio:    1.7,
  hedge_sl_points:      20,
  risk_reward_ratio:    1.7,
  max_loss:   0,
  max_profit: 0,
  option_premium: { min: 150, max: 200 },
  quantity: { lots: 15, lot_size: 65 },
  session: {
    primary:   { start: "09:30", end: "15:20" },
    secondary: { enabled: false, start: "10:00", end: "14:30" },
  },
  trade_side_mode: "BOTH",
};

const DEFAULT_SCALP_V5_CONFIG = {
  trade_execution_mode: "PAPER",
  timeframe:            "3m",
  sl_points:            0,
  tp_points:            0,
  max_loss:   0,
  max_profit: 0,
  option_premium: { min: 100, max: 300 },
  quantity: { lots: 1, lot_size: 65 },
  session: {
    primary:   { start: "09:15", end: "15:20" },
    secondary: { enabled: false, start: "10:00", end: "14:30" },
  },
  trade_side_mode: "BOTH",
};

// ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
// Legs schema identical to the backtest (IC_V1_STRATEGY_HANDOFF §3):
// lots 0 disables a leg (0 on L3/L4 = short strangle); sl/tp 0 = disabled;
// *_mode: "pct" | "pts". lot_size is user-set here — never hardcoded.
const DEFAULT_IC_V2_CONFIG = {
  trade_execution_mode: "OFF",
  entry_time: "09:18",
  exit_time:  "15:28",
  entry_late_grace_s: 120,
  freeze_qty: 1800,
  allow_strangle_degrade: false,
  margin_guard: true,
  // ── IC_V2 (2026-07-26) ── ONE_NIGHT_MAX carry + ADJ_ON_MTC adjustments.
  // Mirrors the loader defaults EXACTLY — a Settings save must never
  // silently downgrade the backtest-validated semantics.
  exit_mode: "NEXT_OPEN",          // NEXT_OPEN = carry 1 night · EOD = legacy
  next_open_time: "09:16",
  expiry_exit_time: "15:28",
  adjust_on_sl: true,
  adjust_delay_s: 60,
  adjust_only: false,
  adjust: {
    L1: { enabled: true, lots: 24, premium_max: 85,
          sl_val: 25, sl_mode: "pct", tp_val: 0, tp_mode: "pct" },
    L2: { enabled: true, lots: 24, premium_max: 85,
          sl_val: 25, sl_mode: "pct", tp_val: 0, tp_mode: "pct" },
  },
  gtt_limit_buffer_pct: 5,
  quantity: { lot_size: 65 },
  legs: [
    { id: "L1", action: "SELL", opt_type: "CE", lots: 24, premium_max: 85,
      sl_val: 42, sl_mode: "pct", tp_val: 0, tp_mode: "pct",
      mtc_other_on_sl: true, mtc_partner: "L2" },
    { id: "L2", action: "SELL", opt_type: "PE", lots: 24, premium_max: 85,
      sl_val: 42, sl_mode: "pct", tp_val: 0, tp_mode: "pct",
      mtc_other_on_sl: true, mtc_partner: "L1" },
    { id: "L3", action: "BUY", opt_type: "CE", lots: 24, premium_max: 4,
      sl_val: 0, sl_mode: "pct", tp_val: 0, tp_mode: "pct",
      mtc_other_on_sl: false, mtc_partner: null },
    { id: "L4", action: "BUY", opt_type: "PE", lots: 24, premium_max: 4,
      sl_val: 0, sl_mode: "pct", tp_val: 0, tp_mode: "pct",
      mtc_other_on_sl: false, mtc_partner: null },
  ],
};

// ── IC_SPLIT ── IC_V1 = the LEGACY condor: same leg template, but
// exit_mode EOD, no carry, no adjustments. Mirrors the loader default
// exactly (a Settings save must never invent semantics).
const DEFAULT_IC_V1_CONFIG = {
  ...structuredClone(DEFAULT_IC_V2_CONFIG),
  exit_mode: "EOD",
  adjust_on_sl: false,
  adjust_only: false,
};

const IC_DEFAULTS = { IC_V1: DEFAULT_IC_V1_CONFIG, IC_V2: DEFAULT_IC_V2_CONFIG };
const IC_SIDS = ["IC_V1", "IC_V2"];
// ── IC END ──

/* ─────────────────────────────────────────────
   Primitive input components
───────────────────────────────────────────── */

function Input({ type = "text", value, onChange, min, max, step, disabled, style }) {
  return (
    <input
      type={type} value={value} onChange={onChange}
      min={min} max={max} step={step} disabled={disabled}
      style={{
        padding: "5px 9px", borderRadius: 5,
        border:  `1px solid ${disabled ? colors.border.dark : colors.border.medium}`,
        background: disabled ? colors.bg.tertiary : colors.bg.input,
        color:      disabled ? colors.text.muted  : colors.text.primary,
        fontSize: 12, outline: "none", width: "100%",
        transition: "border-color 0.15s",
        ...style,
      }}
      onFocus={(e) => !disabled && (e.target.style.borderColor = colors.primary)}
      onBlur={(e)  => (e.target.style.borderColor = disabled ? colors.border.dark : colors.border.medium)}
    />
  );
}

function Select({ value, onChange, disabled, children, style }) {
  return (
    <select
      value={value} onChange={onChange} disabled={disabled}
      style={{
        padding: "5px 9px", borderRadius: 5,
        border:  `1px solid ${disabled ? colors.border.dark : colors.border.medium}`,
        background: disabled ? colors.bg.tertiary : colors.bg.input,
        color:      disabled ? colors.text.muted  : colors.text.primary,
        fontSize: 12, outline: "none",
        transition: "border-color 0.15s",
        cursor: disabled ? "not-allowed" : "pointer",
        ...style,
      }}
      onFocus={(e) => !disabled && (e.target.style.borderColor = colors.primary)}
      onBlur={(e)  => (e.target.style.borderColor = disabled ? colors.border.dark : colors.border.medium)}
    >
      {children}
    </select>
  );
}

function Checkbox({ checked, onChange, label: lbl, disabled }) {
  return (
    <label style={{
      display: "flex", alignItems: "center", gap: 7,
      cursor: disabled ? "not-allowed" : "pointer",
      fontSize: 12, color: disabled ? colors.text.muted : colors.text.secondary,
      userSelect: "none",
      opacity: disabled ? 0.5 : 1,
    }}>
      <input type="checkbox" checked={checked} onChange={onChange} disabled={disabled}
        style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0, cursor: disabled ? "not-allowed" : "pointer" }} />
      {lbl}
    </label>
  );
}

function TimeRange({ startValue, endValue, onStartChange, onEndChange, disabled }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Input type="time" value={startValue} onChange={onStartChange} disabled={disabled} style={{ width: 96 }} />
      <span style={{ fontSize: 11, color: colors.text.muted, flexShrink: 0 }}>to</span>
      <Input type="time" value={endValue}   onChange={onEndChange}   disabled={disabled} style={{ width: 96 }} />
    </div>
  );
}

/* ─────────────────────────────────────────────
   Mode toggle.
───────────────────────────────────────────── */

const MODE_LABEL = {
  OFF:   "⏸ Off",
  PAPER: "🧪 Paper",
  LIVE:  "🟢 Live",
};

function modeActiveColor(m) {
  if (m === "LIVE")  return colors.success;
  if (m === "PAPER") return colors.primary;
  return colors.text.muted;          // OFF — neutral
}

function ModeToggle({ value, onChange, modes = ["PAPER", "LIVE"] }) {
  const isMobile = useIsMobile();
  return (
    <div style={{ display: "flex", width: isMobile ? "100%" : "auto", gap: 3, background: colors.bg.tertiary, padding: 3, borderRadius: 6, border: `1px solid ${colors.border.medium}` }}>
      {modes.map((m) => {
        const active = value === m;
        const activeBg = modeActiveColor(m);
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              flex: isMobile ? 1 : undefined,
              padding: isMobile ? "8px 14px" : "4px 14px",
              borderRadius: 4, border: "none",
              background: active ? activeBg : "transparent",
              color:      active ? (m === "OFF" ? colors.bg.primary : "#fff") : colors.text.muted,
              fontSize: 11, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s ease",
              textTransform: "uppercase", letterSpacing: "0.3px",
            }}
          >
            {MODE_LABEL[m] || m}
          </button>
        );
      })}
    </div>
  );
}

/* ── HA-specific: CE / BOTH / PE toggle ── */
function SideToggle({ value, onChange }) {
  return (
    <div style={{ display: "flex", gap: 3, background: colors.bg.tertiary, padding: 3, borderRadius: 6, border: `1px solid ${colors.border.medium}` }}>
      {["CE", "BOTH", "PE"].map((m) => {
        const active = value === m;
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              padding: "4px 12px",
              borderRadius: 4, border: "none",
              background: active ? colors.primary : "transparent",
              color:      active ? "#fff" : colors.text.muted,
              fontSize: 11, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            {m}
          </button>
        );
      })}
    </div>
  );
}

function SaveButton({ onClick, saving, status }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
      {status && (
        <span style={{
          padding: "3px 10px", borderRadius: 5, fontSize: 11, fontWeight: 600,
          background: status === "success" ? colors.successBg : colors.warningBg,
          color:      status === "success" ? colors.success    : colors.warning,
          border: `1px solid ${(status === "success" ? colors.success : colors.warning)}30`,
        }}>
          {status === "success" ? "✓ Saved" : "✗ Failed"}
        </span>
      )}
      <button onClick={onClick} disabled={saving}
        style={{
          padding: "6px 18px", borderRadius: 5, border: "none",
          background: saving ? colors.bg.tertiary : colors.primary,
          color: "#fff", fontSize: 12, fontWeight: 600,
          cursor: saving ? "not-allowed" : "pointer",
          transition: "background 0.15s",
        }}
        onMouseEnter={(e) => !saving && (e.target.style.background = colors.primaryHover)}
        onMouseLeave={(e) => !saving && (e.target.style.background = colors.primary)}
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

const LABEL_W = 160;

function Field({ label: lbl, helper, children, indent, error }) {
  const isMobile = useIsMobile();
  return (
    <div style={{
      display: "flex",
      flexDirection: isMobile ? "column" : "row",
      alignItems: isMobile ? "flex-start" : "center",
      gap: isMobile ? spacing.xs : spacing.md,
      padding: isMobile ? "8px 0" : "6px 0",
      paddingLeft: indent ? 20 : 0,
      borderBottom: `1px solid ${error ? colors.danger + "40" : colors.border.dark}`,
    }}>
      <div style={{ flexShrink: 0, width: isMobile ? "100%" : LABEL_W }}>
        <div style={{ fontSize: 12, color: colors.text.secondary, fontWeight: 500 }}>{lbl}</div>
        {helper && <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 1, lineHeight: 1.4 }}>{helper}</div>}
        {error   && <div style={{ fontSize: 10, color: colors.danger,   marginTop: 2 }}>{error}</div>}
      </div>
      <div style={{ flex: 1, minWidth: 0, width: isMobile ? "100%" : undefined }}>{children}</div>
    </div>
  );
}

function Group({ title, children, highlight }) {
  return (
    <div style={{ marginBottom: spacing.xl }}>
      <div style={{
        ...label,
        marginBottom: spacing.xs, paddingBottom: 4,
        borderBottom: `1px solid ${highlight ? colors.primary + "60" : colors.border.medium}`,
        color: highlight ? colors.primary : colors.text.muted,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function ModeChip({ mode }) {
  const isLive = mode === "LIVE";
  const isOff  = mode === "OFF";
  const bg   = isOff ? "rgba(148,163,184,0.12)" : isLive ? "rgba(16,185,129,0.12)" : "rgba(59,130,246,0.12)";
  const fg   = isOff ? colors.text.muted : isLive ? colors.success : colors.primary;
  const text = isOff ? "⏸ Off" : isLive ? "🟢 Live" : "🧪 Paper";
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 5,
      background: bg,
      color:      fg,
      border:     `1px solid ${fg}25`,
      textTransform: "uppercase", letterSpacing: "0.3px",
    }}>
      {text}
    </span>
  );
}

/* ─────────────────────────────────────────────
   Lot split validator  (BB_V1)
───────────────────────────────────────────── */

function lotSplitError(lots, leg1, leg2, multipleTargets) {
  if (!multipleTargets) return null;
  if (leg1 + leg2 !== lots) {
    return `Leg 1 (${leg1}) + Leg 2 (${leg2}) must equal total lots (${lots})`;
  }
  if (leg1 < 1 || leg2 < 1) {
    return "Each leg must have at least 1 lot";
  }
  return null;
}

/* ─────────────────────────────────────────────
   Strategy meta (rail + detail headers)

   Sub-headers are intentionally generic — no engine /
   indicator / mechanism names are exposed in the UI.
───────────────────────────────────────────── */

const STRATEGY_META = {
  SCALP_V1: { name: "Scalp V1",     sub: "NIFTY options · intraday" },
  SCALP_V3: { name: "Scalp V3",     sub: "NIFTY options · intraday" },
  PST_SELL: { name: "PST Sell",     sub: "NIFTY options · pivot+ST short" },
  PST_HEDGE: { name: "PST Hedge",   sub: "NIFTY options · pivot+ST flip buy" },
  SCALP_V5: { name: "Scalp V5",     sub: "NIFTY options · intraday" },
  IC_V1:    { name: "Iron Condor V1", sub: "NIFTY weekly · time-entry · daily square-off" },   // ── IC_SPLIT ──
  IC_V2:    { name: "Iron Condor V2", sub: "NIFTY weekly · time-entry" },   // ── IC_SPLIT ── was "IC_V1"
  TMA_V1:   { name: "TMA V1",       sub: "NIFTY weekly · trend credit spread" },   // ── TMA_V1 ──
  TMA_V2:   { name: "TMA V2",       sub: "NIFTY weekly · 4-EMA stack credit spread" },   // ── TMA_V2 ──
  TSG_V1:   { name: "TSG V1",       sub: "NIFTY weekly · 09:16 time strangle" },   // ── TSG_V1 ──
  BB_V1:    { name: "BB V1",        sub: "BANKNIFTY options" },
  BB_V2:    { name: "BB V2",        sub: "BANKNIFTY options" },
  HA_V1:    { name: "Heikin Ashi",  sub: "NIFTY options" },
  APP:      { name: "App Settings", sub: "Notifications · sounds · pop-ups" },
};

/* ─────────────────────────────────────────────
   Sidebar rail item — one selectable strategy row.
───────────────────────────────────────────── */

function StrategyRailItem({ id, name, mode, accent, active, dirty, onClick }) {
  const isLive = mode === "LIVE";
  const isOff  = mode === "OFF";
  const dot    = mode == null ? colors.text.muted : isOff ? colors.text.muted : isLive ? colors.success : colors.primary;
  const modeLabel = mode == null ? "" : isOff ? "OFF" : isLive ? "LIVE" : "PAPER";
  const ac = accent || colors.primary;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      style={{
        display: "flex", alignItems: "center", gap: spacing.sm,
        width: "100%", textAlign: "left",
        padding: `${spacing.sm}px ${spacing.md}px`,
        borderRadius: 8,
        border: `1px solid ${active ? `${ac}66` : "transparent"}`,
        background: active ? `${ac}1f` : "transparent",
        cursor: "pointer",
        transition: "background 0.15s ease, border-color 0.15s ease",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = colors.bg.secondary + "80"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      <span style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3, borderRadius: "2px 0 0 2px",
        background: ac, opacity: active ? 1 : 0.55,
        transition: "opacity 0.2s ease",
      }} />
      <span style={{
        width: 8, height: 8, borderRadius: "50%", flexShrink: 0, marginLeft: 2,
        background: dot, boxShadow: `0 0 6px ${dot}66`,
      }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 13, fontWeight: 600,
          color: active ? colors.text.primary : colors.text.secondary,
        }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
          {dirty && (
            <span title="Unsaved changes" style={{
              width: 6, height: 6, borderRadius: "50%",
              background: colors.warning, flexShrink: 0,
            }} />
          )}
        </div>
        <div style={{
          ...label, marginTop: 2,
          color: colors.text.muted, fontSize: 9,
        }}>
          {id}{modeLabel ? ` · ${modeLabel}` : ""}
        </div>
      </div>
    </button>
  );
}

/* ─────────────────────────────────────────────
   Detail pane
───────────────────────────────────────────── */

function DetailPane({ id, name, meta, mode, onSave, saving, status, children }) {
  return (
    <div style={{
      background:   colors.bg.secondary,
      border:       `1px solid ${colors.border.medium}`,
      borderRadius: 12,
      overflow:     "hidden",
      height:       "100%",
      display:      "flex",
      flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        padding:        `${spacing.md}px ${spacing.xl}px`,
        background:     colors.bg.tertiary,
        borderBottom:   `1px solid ${colors.border.medium}`,
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        flexShrink:     0,
        gap:            spacing.md,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, minWidth: 0 }}>
          <span style={{
            ...label, background: colors.bg.primary, color: colors.text.muted,
            padding: "3px 8px", borderRadius: 4,
            border: `1px solid ${colors.border.medium}`, flexShrink: 0,
          }}>
            {id}
          </span>
          <span style={{ fontSize: 16, fontWeight: 700, color: colors.text.primary, flexShrink: 0 }}>
            {name}
          </span>
          <span style={{
            fontSize: 11, color: colors.text.muted,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {meta}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexShrink: 0 }}>
          <ModeChip mode={mode} />
          <SaveButton onClick={onSave} saving={saving} status={status} />
        </div>
      </div>

      {/* Scrollable config body */}
      <div style={{
        flex: 1, overflowY: "auto",
        padding: `${spacing.lg}px ${spacing.xl}px ${spacing.xl}px`,
      }}>
        <style>{`
          .sv2-detail-flow {
            column-gap: 28px;
            column-count: 2;
            max-width: 1180px;
            margin: 0 auto;
          }
          .sv2-detail-flow > * {
            break-inside: avoid;
            -webkit-column-break-inside: avoid;
            page-break-inside: avoid;
          }
          @media (max-width: 1180px) {
            .sv2-detail-flow { column-count: 1; max-width: 720px; }
          }
        `}</style>
        {/* ── ACC2 ── D2c per-strategy execution-account selector.
            Mounted ONCE here so every strategy pane gets it; a
            registry-absent strategy simply shows the default and saves are
            filtered server-side by STRATEGY_SIDE membership. */}
        <div style={{ maxWidth: 1180, margin: "0 auto", marginBottom: spacing.md }}>
          <AccountSelector strategyId={id} />
        </div>
        <div className="sv2-detail-flow">
          {children}
        </div>
      </div>
    </div>
  );
}


/* ─────────────────────────────────────────────
   Settings page
───────────────────────────────────────────── */

// ── UI_MASK BEGIN ──────────────────────────────────────────────────────
// Export-level fork so the admin component's hook order is untouched.
// Fail CLOSED: render nothing until the first license read resolves —
// a fail-open flash here would briefly expose the full admin form
// (unlike panel chrome, which follows the Phase 3 fail-open convention).
export default function Settings() {
  const { loaded, isAdminUi } = useEntitlements();
  if (!loaded) return null;
  if (!isAdminUi) return <LotsOnlySettings />;
  return <AdminSettings />;
}
// ── UI_MASK END ────────────────────────────────────────────────────────

function AdminSettings() {
  const isMobile = useIsMobile();
  const { allowsStrategy } = useEntitlements();
  const [primaryId, setPrimaryId] = useState("SCALP_V1");

  // ── SCALP_V1 ──────────────────────────────
  const [scalpConfig, setScalpConfig] = useState(null);
  const [scalpStatus, setScalpStatus] = useState("");
  const [scalpSaving, setScalpSaving] = useState(false);

  // ── BB_V1 ─────────────────────────────────
  const [bbConfig, setBBConfig] = useState(null);
  const [bbStatus, setBBStatus] = useState("");
  const [bbSaving, setBBSaving] = useState(false);

  // BB_V2:
  const [bbV2Config, setBBV2Config] = useState(null);
  const [bbV2Status, setBBV2Status] = useState("");
  const [bbV2Saving, setBBV2Saving] = useState(false);

  // ── HA_V1 ─────────────────────────────────
  const [haConfig, setHAConfig] = useState(null);
  const [haStatus, setHAStatus] = useState("");
  const [haSaving, setHASaving] = useState(false);


  // ── SCALP_V3 ──────────────────────────────
  const [scalpV3Config, setScalpV3Config] = useState(null);
  // ── PST_SELL / PST_HEDGE ──────────────────
  const [pstSellConfig, setPstSellConfig] = useState(null);
  const [pstSellStatus, setPstSellStatus] = useState("");
  const [pstSellSaving, setPstSellSaving] = useState(false);
  const [pstHedgeConfig, setPstHedgeConfig] = useState(null);
  const [pstHedgeStatus, setPstHedgeStatus] = useState("");
  const [pstHedgeSaving, setPstHedgeSaving] = useState(false);
  const [scalpV3Status, setScalpV3Status] = useState("");
  const [scalpV3Saving, setScalpV3Saving] = useState(false);


    // ── SCALP_V5 ──────────────────────────────
  const [scalpV5Config, setScalpV5Config] = useState(null);
  const [scalpV5Status, setScalpV5Status] = useState("");
  const [scalpV5Saving, setScalpV5Saving] = useState(false);

  // ── IC ── (IC_SPLIT: one state map per instance, sid-keyed)
  const [icConfigs, setICConfigs] = useState({ IC_V1: null, IC_V2: null });
  const [icStatus,  setICStatus]  = useState({ IC_V1: "",   IC_V2: "" });
  const [icSaving,  setICSaving]  = useState({ IC_V1: false, IC_V2: false });

  // ── TMA_V1 BEGIN ──
  const [tmaConfig, setTmaConfig] = useState(null);
  const [tmaStatus, setTmaStatus] = useState("");
  const [tmaSaving, setTmaSaving] = useState(false);
  // ── TMA_V1 END ──
  // ── TMA_V2 BEGIN ──
  const [tma2Config, setTma2Config] = useState(null);
  const [tma2Status, setTma2Status] = useState("");
  const [tma2Saving, setTma2Saving] = useState(false);
  // ── TMA_V2 END ──
  // ── TSG_V1 BEGIN ──
  const [tsgConfig, setTsgConfig] = useState(null);
  const [tsgStatus, setTsgStatus] = useState("");
  const [tsgSaving, setTsgSaving] = useState(false);
  // ── TSG_V1 END ──
  useEffect(() => { loadScalp(); loadBB(); loadBBV2(); loadHA(); loadScalpV3(); loadScalpV5(); IC_SIDS.forEach(loadIC); loadPstSell(); loadPstHedge(); loadTMA(); loadTMA2(); loadTSG(); }, []);   // ← TSG_V1, TMA_V2 added

  // ── SCALP_V1 load / update / save ──────────
  async function loadScalp() {
    try {
      const d = await getStrategyConfig("SCALP_V1");
      setScalpConfig({
        ...DEFAULT_SCALP_CONFIG, ...d,
        trade_execution_mode: d?.trade_execution_mode || "PAPER",
        session: {
          ...DEFAULT_SCALP_CONFIG.session, ...d?.session,
          primary:   { ...DEFAULT_SCALP_CONFIG.session.primary,   ...d?.session?.primary   },
          secondary: { ...DEFAULT_SCALP_CONFIG.session.secondary, ...d?.session?.secondary },
        },
        option_premium: { ...DEFAULT_SCALP_CONFIG.option_premium, ...d?.option_premium },
        quantity:       { ...DEFAULT_SCALP_CONFIG.quantity,       ...d?.quantity       },
      });
    } catch { setScalpConfig({ ...DEFAULT_SCALP_CONFIG }); }
  }

  function updateScalp(path, value) {
    const u = structuredClone(scalpConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setScalpConfig(u);
  }

  async function saveScalp() {
    setScalpSaving(true);
    try {
      await saveStrategyConfig("SCALP_V1", scalpConfig);
      setScalpStatus("success"); setTimeout(() => setScalpStatus(""), 3000);
    } catch {
      setScalpStatus("error");  setTimeout(() => setScalpStatus(""), 3000);
    } finally { setScalpSaving(false); }
  }

  // ── BB_V1 load / update / save ─────────────
  async function loadBB() {
    try {
      const d = await getStrategyConfig("BB_V1");
      const migratedLots = d?.lots ?? d?.ce_lots ?? d?.pe_lots ?? DEFAULT_BB_CONFIG.lots;
      setBBConfig({
        ...DEFAULT_BB_CONFIG,
        ...d,
        lots: Number(migratedLots),
      });
    } catch { setBBConfig({ ...DEFAULT_BB_CONFIG }); }
  }

  function updateBB(path, value) {
    const u = structuredClone(bbConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setBBConfig(u);
  }

  async function saveBB() {
    if (bbConfig.multiple_targets) {
      const err = lotSplitError(
        bbConfig.lots, bbConfig.lots_leg1, bbConfig.lots_leg2, true
      );
      if (err) { alert(err); return; }
    }
    setBBSaving(true);
    try {
      await saveStrategyConfig("BB_V1", bbConfig);
      setBBStatus("success"); setTimeout(() => setBBStatus(""), 3000);
    } catch {
      setBBStatus("error");  setTimeout(() => setBBStatus(""), 3000);
    } finally { setBBSaving(false); }
  }

  // ── BB_V2 load / update / save ─────────────
  async function loadBBV2() {
    try {
      const d = await getStrategyConfig("BB_V2");
      setBBV2Config({ ...DEFAULT_BB_V2_CONFIG, ...d });
    } catch {
      setBBV2Config({ ...DEFAULT_BB_V2_CONFIG });
    }
  }

  function updateBBV2(path, value) {
    const u = structuredClone(bbV2Config);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setBBV2Config(u);
  }

  async function saveBBV2() {
    setBBV2Saving(true);
    try {
      await saveStrategyConfig("BB_V2", bbV2Config);
      setBBV2Status("success"); setTimeout(() => setBBV2Status(""), 3000);
    } catch {
      setBBV2Status("error");  setTimeout(() => setBBV2Status(""), 3000);
    } finally { setBBV2Saving(false); }
  }

  // ── Smart lot-split updater (BB_V1) ─────────
  function handleLotsChange(newTotal) {
    const t  = Math.max(1, Number(newTotal));
    const l1 = Math.min(bbConfig.lots_leg1, t - 1) || 1;
    const l2 = t - l1;
    setBBConfig(u => ({ ...u, lots: t, lots_leg1: l1, lots_leg2: l2 }));
  }

  function handleLeg1Change(newLeg1) {
    const l1    = Math.max(1, Number(newLeg1));
    const total = bbConfig.lots;
    const safe1 = Math.min(l1, total - 1);
    setBBConfig(u => ({ ...u, lots_leg1: safe1, lots_leg2: total - safe1 }));
  }

  function handleLeg2Change(newLeg2) {
    const l2    = Math.max(1, Number(newLeg2));
    const total = bbConfig.lots;
    const safe2 = Math.min(l2, total - 1);
    setBBConfig(u => ({ ...u, lots_leg2: safe2, lots_leg1: total - safe2 }));
  }

  // ── HA_V1 load / update / save ─────────────
  async function loadHA() {
    try {
      const d = await getStrategyConfig("HA_V1");
      setHAConfig({
        ...DEFAULT_HA_CONFIG, ...d,
        target_override: { ...DEFAULT_HA_CONFIG.target_override, ...d?.target_override },
        option_premium:  { ...DEFAULT_HA_CONFIG.option_premium,  ...d?.option_premium  },
        quantity:        { ...DEFAULT_HA_CONFIG.quantity,         ...d?.quantity        },
        session: {
          ...DEFAULT_HA_CONFIG.session, ...d?.session,
          primary:   { ...DEFAULT_HA_CONFIG.session.primary,   ...d?.session?.primary   },
          secondary: { ...DEFAULT_HA_CONFIG.session.secondary, ...d?.session?.secondary },
        },
      });
    } catch { setHAConfig({ ...DEFAULT_HA_CONFIG }); }
  }

  function updateHA(path, value) {
    const u = structuredClone(haConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setHAConfig(u);
  }

  async function saveHA() {
    setHASaving(true);
    try {
      await saveStrategyConfig("HA_V1", haConfig);
      setHAStatus("success"); setTimeout(() => setHAStatus(""), 3000);
    } catch {
      setHAStatus("error");  setTimeout(() => setHAStatus(""), 3000);
    } finally { setHASaving(false); }
  }

  // ── SCALP_V3 load / update / save ──────────
  async function loadScalpV3() {
    try {
      const d = await getStrategyConfig("SCALP_V3");
      setScalpV3Config({
        ...DEFAULT_SCALP_V3_CONFIG, ...d,
        option_premium: { ...DEFAULT_SCALP_V3_CONFIG.option_premium, ...d?.option_premium },
        quantity:       { ...DEFAULT_SCALP_V3_CONFIG.quantity,       ...d?.quantity       },
        session: {
          ...DEFAULT_SCALP_V3_CONFIG.session, ...d?.session,
          primary:   { ...DEFAULT_SCALP_V3_CONFIG.session.primary,   ...d?.session?.primary   },
          secondary: { ...DEFAULT_SCALP_V3_CONFIG.session.secondary, ...d?.session?.secondary },
        },
      });
    } catch { setScalpV3Config({ ...DEFAULT_SCALP_V3_CONFIG }); }
  }

  function updateScalpV3(path, value) {
    const u = structuredClone(scalpV3Config);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setScalpV3Config(u);
  }

  async function saveScalpV3() {
    setScalpV3Saving(true);
    try {
      await saveStrategyConfig("SCALP_V3", scalpV3Config);
      setScalpV3Status("success"); setTimeout(() => setScalpV3Status(""), 3000);
    } catch {
      setScalpV3Status("error");  setTimeout(() => setScalpV3Status(""), 3000);
    } finally { setScalpV3Saving(false); }
  }
 
  // ── PST_SELL / PST_HEDGE load / update / save ──────────
  async function loadPstSell() {
    try {
      const d = await getStrategyConfig("PST_SELL");
      setPstSellConfig({ ...DEFAULT_PST_CONFIG, ...d,
        legs: Array.isArray(d?.legs) && d.legs.length === 2 ? d.legs : DEFAULT_PST_CONFIG.legs });
    } catch { setPstSellConfig({ ...DEFAULT_PST_CONFIG }); }
  }
  function updatePstSell(path, value) {
    const u = structuredClone(pstSellConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setPstSellConfig(u);
  }
  async function savePstSell() {
    setPstSellSaving(true);
    try {
      await saveStrategyConfig("PST_SELL", pstSellConfig);
      setPstSellStatus("success"); setTimeout(() => setPstSellStatus(""), 3000);
    } catch {
      setPstSellStatus("error");  setTimeout(() => setPstSellStatus(""), 3000);
    } finally { setPstSellSaving(false); }
  }

  async function loadPstHedge() {
    try {
      const d = await getStrategyConfig("PST_HEDGE");
      setPstHedgeConfig({ ...DEFAULT_PST_CONFIG, ...d,
        legs: Array.isArray(d?.legs) && d.legs.length === 2 ? d.legs : DEFAULT_PST_CONFIG.legs });
    } catch { setPstHedgeConfig({ ...DEFAULT_PST_CONFIG }); }
  }
  function updatePstHedge(path, value) {
    const u = structuredClone(pstHedgeConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setPstHedgeConfig(u);
  }
  async function savePstHedge() {
    setPstHedgeSaving(true);
    try {
      await saveStrategyConfig("PST_HEDGE", pstHedgeConfig);
      setPstHedgeStatus("success"); setTimeout(() => setPstHedgeStatus(""), 3000);
    } catch {
      setPstHedgeStatus("error");  setTimeout(() => setPstHedgeStatus(""), 3000);
    } finally { setPstHedgeSaving(false); }
  }

  // ── TMA_V1 BEGIN ── load / update / save (PST pattern; nested c1 merged
  // defensively so a partial saved config never renders undefined inputs)
  async function loadTMA() {
    try {
      const d = await getStrategyConfig("TMA_V1");
      setTmaConfig({
        ...DEFAULT_TMA_CONFIG, ...d,
        quantity: { ...DEFAULT_TMA_CONFIG.quantity, ...(d?.quantity || {}) },
        c1: {
          ...DEFAULT_TMA_CONFIG.c1, ...(d?.c1 || {}),
          sell: { ...DEFAULT_TMA_CONFIG.c1.sell, ...(d?.c1?.sell || {}) },
          buy:  { ...DEFAULT_TMA_CONFIG.c1.buy,  ...(d?.c1?.buy  || {}) },
        },
      });
    } catch { setTmaConfig({ ...DEFAULT_TMA_CONFIG }); }
  }
  function updateTMA(path, value) {
    const u = structuredClone(tmaConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setTmaConfig(u);
  }
  async function saveTMA() {
    setTmaSaving(true);
    try {
      await saveStrategyConfig("TMA_V1", tmaConfig);
      setTmaStatus("success"); setTimeout(() => setTmaStatus(""), 3000);
    } catch {
      setTmaStatus("error");  setTimeout(() => setTmaStatus(""), 3000);
    } finally { setTmaSaving(false); }
  }
  // ── TMA_V1 END ──

  // ── TMA_V2 BEGIN ── load / update / save. The saved payload is merged
  // OVER the defaults, so backend-only keys the UI never renders
  // (xover_exit_ref, ema144_slope_gate) are preserved on save rather than
  // dropped — the UI must not be able to unset a locked study param.
  async function loadTMA2() {
    try {
      const d = await getStrategyConfig("TMA_V2");
      setTma2Config({
        ...DEFAULT_TMA2_CONFIG, ...d,
        quantity: { ...DEFAULT_TMA2_CONFIG.quantity, ...(d?.quantity || {}) },
        s1: {
          ...DEFAULT_TMA2_CONFIG.s1, ...(d?.s1 || {}),
          main:  { ...DEFAULT_TMA2_CONFIG.s1.main,  ...(d?.s1?.main  || {}) },
          hedge: { ...DEFAULT_TMA2_CONFIG.s1.hedge, ...(d?.s1?.hedge || {}) },
        },
      });
    } catch { setTma2Config({ ...DEFAULT_TMA2_CONFIG }); }
  }
  function updateTMA2(path, value) {
    const u = structuredClone(tma2Config);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setTma2Config(u);
  }
  async function saveTMA2() {
    setTma2Saving(true);
    try {
      await saveStrategyConfig("TMA_V2", tma2Config);
      setTma2Status("success"); setTimeout(() => setTma2Status(""), 3000);
    } catch {
      setTma2Status("error");  setTimeout(() => setTma2Status(""), 3000);
    } finally { setTma2Saving(false); }
  }
  // ── TMA_V2 END ──

  // ── TSG_V1 BEGIN ── load / update / save (legs merged by index so a
  // partial saved config never renders undefined leg inputs)
  async function loadTSG() {
    try {
      const d = await getStrategyConfig("TSG_V1");
      const legs = DEFAULT_TSG_CONFIG.legs.map((dl, i) => ({
        ...dl, ...((d?.legs || [])[i] || {}),
      }));
      setTsgConfig({ ...DEFAULT_TSG_CONFIG, ...d, legs });
    } catch { setTsgConfig({ ...DEFAULT_TSG_CONFIG }); }
  }
  function updateTSG(path, value) {
    const u = structuredClone(tsgConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setTsgConfig(u);
  }
  async function saveTSG() {
    setTsgSaving(true);
    try {
      await saveStrategyConfig("TSG_V1", tsgConfig);
      setTsgStatus("success"); setTimeout(() => setTsgStatus(""), 3000);
    } catch {
      setTsgStatus("error");  setTimeout(() => setTsgStatus(""), 3000);
    } finally { setTsgSaving(false); }
  }
  // ── TSG_V1 END ──


  // ── SCALP_V5 load / update / save ──────────
  async function loadScalpV5() {
    try {
      const d = await getStrategyConfig("SCALP_V5");
      setScalpV5Config({
        ...DEFAULT_SCALP_V5_CONFIG, ...d,
        option_premium: { ...DEFAULT_SCALP_V5_CONFIG.option_premium, ...d?.option_premium },
        quantity:       { ...DEFAULT_SCALP_V5_CONFIG.quantity,       ...d?.quantity       },
        session: {
          ...DEFAULT_SCALP_V5_CONFIG.session, ...d?.session,
          primary:   { ...DEFAULT_SCALP_V5_CONFIG.session.primary,   ...d?.session?.primary   },
          secondary: { ...DEFAULT_SCALP_V5_CONFIG.session.secondary, ...d?.session?.secondary },
        },
      });
    } catch { setScalpV5Config({ ...DEFAULT_SCALP_V5_CONFIG }); }
  }
 
  function updateScalpV5(path, value) {
    const u = structuredClone(scalpV5Config);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setScalpV5Config(u);
  }
 
  async function saveScalpV5() {
    setScalpV5Saving(true);
    try {
      await saveStrategyConfig("SCALP_V5", scalpV5Config);
      setScalpV5Status("success"); setTimeout(() => setScalpV5Status(""), 3000);
    } catch {
      setScalpV5Status("error");  setTimeout(() => setScalpV5Status(""), 3000);
    } finally { setScalpV5Saving(false); }
  }

  // ── Loading guard ───────────────────────────
  // ── RENDER_GUARD ── every config dereferenced below (mode list, save
  // map, panels) must be non-null here, or the page crashes on first
  // paint before the loaders resolve. Adding a strategy = adding it here.
  if (!scalpConfig || !bbConfig || !bbV2Config || !haConfig || !scalpV3Config || !scalpV5Config || !icConfigs.IC_V1 || !icConfigs.IC_V2 || !pstSellConfig || !pstHedgeConfig || !tmaConfig || !tma2Config || !tsgConfig) {   // ← TSG_V1, TMA_V2 added
    return (
      <div style={{ padding: settingsSpacing.xxl, background: colors.bg.primary, color: colors.text.primary, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 13, color: colors.text.muted }}>Loading settings…</span>
      </div>
    );
  }

  const multipleTargets = bbConfig.multiple_targets;
  const canEnableMulti  = bbConfig.lots >= 2;
  const splitErr        = lotSplitError(
    bbConfig.lots, bbConfig.lots_leg1, bbConfig.lots_leg2, multipleTargets
  );

  const leg1Options = Array.from({ length: bbConfig.lots - 1 }, (_, i) => i + 1);
  const leg2Options = Array.from({ length: bbConfig.lots - 1 }, (_, i) => i + 1);

  // ── IC load / update / save (IC_SPLIT: every fn takes the sid) ──────
  async function loadIC(sid) {
    const DEF = IC_DEFAULTS[sid];
    try {
      const d = await getStrategyConfig(sid);
      const legs = Array.isArray(d?.legs) && d.legs.length === 4
        ? DEF.legs.map((dl, i) => ({ ...dl, ...d.legs[i] }))
        : DEF.legs.map((dl) => ({ ...dl }));
      setICConfigs((prev) => ({ ...prev, [sid]: {
        ...structuredClone(DEF), ...d,
        trade_execution_mode: d?.trade_execution_mode || "OFF",
        quantity: { ...DEF.quantity, ...d?.quantity },
        // ── IC_V2 ── deep-merge per-side adjust blocks: a partial saved
        // shape must never clobber unset sub-keys back to undefined.
        adjust: {
          L1: { ...DEF.adjust.L1, ...d?.adjust?.L1 },
          L2: { ...DEF.adjust.L2, ...d?.adjust?.L2 },
        },
        legs,
      } }));
    } catch {
      setICConfigs((prev) => ({ ...prev, [sid]: structuredClone(DEF) }));
    }
  }
  function updateIC(sid, path, value) {
    setICConfigs((prev) => {
      const u = structuredClone(prev[sid]);
      path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
      return { ...prev, [sid]: u };
    });
  }
  function updateICLegFor(sid, idx, key, value) {
    setICConfigs((prev) => {
      const u = structuredClone(prev[sid]);
      u.legs[idx][key] = value;
      return { ...prev, [sid]: u };
    });
  }
  // ── IC_V2 ── adjustment block updater (side = "L1" | "L2")
  function updateICAdjustFor(sid, side, key, value) {
    setICConfigs((prev) => {
      const u = structuredClone(prev[sid]);
      if (!u.adjust) u.adjust = structuredClone(IC_DEFAULTS[sid].adjust);
      u.adjust[side][key] = value;
      return { ...prev, [sid]: u };
    });
  }
  async function saveIC(sid) {
    setICSaving((p) => ({ ...p, [sid]: true }));
    try {
      await saveStrategyConfig(sid, icConfigs[sid]);
      setICStatus((p) => ({ ...p, [sid]: "success" }));
      setTimeout(() => setICStatus((p) => ({ ...p, [sid]: "" })), 3000);
    } catch {
      setICStatus((p) => ({ ...p, [sid]: "error" }));
      setTimeout(() => setICStatus((p) => ({ ...p, [sid]: "" })), 3000);
    } finally {
      setICSaving((p) => ({ ...p, [sid]: false }));
    }
  }

  // ── Rail metadata (id + live mode for status dot) ──
  const RAIL = [
    { id: "SCALP_V1", mode: scalpConfig.trade_execution_mode },
    { id: "SCALP_V3", mode: scalpV3Config.trade_execution_mode },
    { id: "PST_SELL", mode: pstSellConfig.trade_execution_mode },
    { id: "PST_HEDGE", mode: pstHedgeConfig.trade_execution_mode },
    { id: "SCALP_V5", mode: scalpV5Config.trade_execution_mode },
    { id: "IC_V1",    mode: icConfigs.IC_V1.trade_execution_mode },
    { id: "IC_V2",    mode: icConfigs.IC_V2.trade_execution_mode },
    { id: "TMA_V1",   mode: tmaConfig.trade_execution_mode },   // ── TMA_V1 ──
    { id: "TMA_V2",   mode: tma2Config.trade_execution_mode },   // ── TMA_V2 ──
    { id: "TSG_V1",   mode: tsgConfig.trade_execution_mode },   // ── TSG_V1 ──
    { id: "BB_V1",    mode: bbConfig.trade_execution_mode },
    { id: "BB_V2",    mode: bbV2Config.trade_execution_mode },
    { id: "HA_V1",    mode: haConfig.trade_execution_mode },
    { id: "APP",      mode: null },
  ].filter((s) => s.id === "APP" || allowsStrategy(s.id));

  if (!RAIL.some((s) => s.id === primaryId)) {
    setPrimaryId(RAIL[0]?.id ?? "APP");
  }

  // ── Detail header props per strategy ──
  const detailProps = {
    SCALP_V1: { mode: scalpConfig.trade_execution_mode, onSave: saveScalp,   saving: scalpSaving,  status: scalpStatus },
    SCALP_V3: { mode: scalpV3Config.trade_execution_mode, onSave: saveScalpV3, saving: scalpV3Saving, status: scalpV3Status },
    PST_SELL: { mode: pstSellConfig.trade_execution_mode, onSave: savePstSell, saving: pstSellSaving, status: pstSellStatus },
    PST_HEDGE: { mode: pstHedgeConfig.trade_execution_mode, onSave: savePstHedge, saving: pstHedgeSaving, status: pstHedgeStatus },
    SCALP_V5: { mode: scalpV5Config.trade_execution_mode, onSave: saveScalpV5, saving: scalpV5Saving, status: scalpV5Status },
    IC_V1:    { mode: icConfigs.IC_V1.trade_execution_mode, onSave: () => saveIC("IC_V1"), saving: icSaving.IC_V1, status: icStatus.IC_V1 },
    IC_V2:    { mode: icConfigs.IC_V2.trade_execution_mode, onSave: () => saveIC("IC_V2"), saving: icSaving.IC_V2, status: icStatus.IC_V2 },
    TMA_V1:   { mode: tmaConfig.trade_execution_mode,     onSave: saveTMA,     saving: tmaSaving,     status: tmaStatus },   // ── TMA_V1 ──
    TMA_V2:   { mode: tma2Config.trade_execution_mode,    onSave: saveTMA2,    saving: tma2Saving,    status: tma2Status },   // ── TMA_V2 ──
    TSG_V1:   { mode: tsgConfig.trade_execution_mode,     onSave: saveTSG,     saving: tsgSaving,     status: tsgStatus },   // ── TSG_V1 ──
    BB_V1:    { mode: bbConfig.trade_execution_mode,     onSave: saveBB,      saving: bbSaving,     status: bbStatus },
    BB_V2:    { mode: bbV2Config.trade_execution_mode,   onSave: saveBBV2,    saving: bbV2Saving,   status: bbV2Status },
    HA_V1:    { mode: haConfig.trade_execution_mode,     onSave: saveHA,      saving: haSaving,     status: haStatus },
  };

  function renderDetailBody(id) {
    switch (id) {
      case "SCALP_V1": return (<>
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                <ModeToggle value={scalpConfig.trade_execution_mode} onChange={(v) => updateScalp(["trade_execution_mode"], v)} />
              </Field>
            </Group>

            <Group title="Risk Management">
              <Field label="Risk Min SL" helper="Skip trade if risk distance is below this">
                <Input type="number" min="0" value={scalpConfig.min_sl_points}
                  onChange={(e) => updateScalp(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Risk Max SL" helper="0 = disabled · skip trade if risk distance exceeds this">
                <Input type="number" min="0" value={scalpConfig.risk_max_sl_points}
                  onChange={(e) => updateScalp(["risk_max_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Max SL Cap" helper="0 = disabled · caps the final stop-loss distance">
                <Input type="number" min="0" value={scalpConfig.max_sl_points}
                  onChange={(e) => updateScalp(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Risk / Reward" helper="Target-to-stop multiplier">
                <Input type="number" step="0.1" min="0" value={scalpConfig.risk_reward_ratio}
                  onChange={(e) => updateScalp(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            {/* ── SCALP_V1_LIVE_SETTINGS_20260825 ── redesign gates (sealed
                backtest config: RR 1 · EMA gate 89/30 ≥1 · TP 3.5× · session
                10:00–15:00). Fresh entry is HARDCODED ON in the engine. */}
            <Group title="Signal Gates (Redesign)">
              <Field label="EMA Gate" helper="Sell only when the gate EMA of the premium has FALLEN ≥ min slope over the lookback. Unwarmed slope blocks entries (fail-closed).">
                <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: colors.text.secondary, userSelect: "none", cursor: "pointer" }}>
                  <input type="checkbox" checked={!!scalpConfig.ema_gate?.enabled}
                    onChange={(e) => updateScalp(["ema_gate", "enabled"], e.target.checked)}
                    style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0 }} />
                  Enabled
                </label>
              </Field>
              {!!scalpConfig.ema_gate?.enabled && (<>
                <Field label="Gate EMA Period" helper="Sealed config: 89 · keep ≤ 300 (warmup depth)">
                  <Input type="number" min="10" max="300" value={scalpConfig.ema_gate?.period ?? 144}
                    onChange={(e) => updateScalp(["ema_gate", "period"], Math.max(10, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Slope Lookback (bars)" helper="Slope measured across this many 1-minute bars · sealed: 30">
                  <Input type="number" min="1" value={scalpConfig.ema_gate?.slope_lookback ?? 30}
                    onChange={(e) => updateScalp(["ema_gate", "slope_lookback"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Min Slope (pts)" helper="Required decline over the lookback · sealed: 1">
                  <Input type="number" min="0" step="0.1" value={scalpConfig.ema_gate?.min_slope_pts ?? 0}
                    onChange={(e) => updateScalp(["ema_gate", "min_slope_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </>)}
              <Field label="TP Multiplier" helper="Target sits risk × this below entry · 1 = classic prev-red-low · sealed: 3.5">
                <Input type="number" min="0.5" step="0.1" value={scalpConfig.tp_multiplier ?? 1}
                  onChange={(e) => updateScalp(["tp_multiplier"], Math.max(0.5, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="VWAP Filter" helper="Sell only when the premium closes below its session average by ≥ min pts. OFF in the sealed config (regime-shaped in backtest); available as the labelled challenger.">
                <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: colors.text.secondary, userSelect: "none", cursor: "pointer" }}>
                  <input type="checkbox" checked={!!scalpConfig.vwap_filter?.enabled}
                    onChange={(e) => updateScalp(["vwap_filter", "enabled"], e.target.checked)}
                    style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0 }} />
                  Enabled
                </label>
              </Field>
              {!!scalpConfig.vwap_filter?.enabled && (
                <Field label="Min Pts Below" helper="0 = below by any amount · live surface clamps to ≥ 0">
                  <Input type="number" min="0" step="0.5" value={scalpConfig.vwap_filter?.min_below_pts ?? 0}
                    onChange={(e) => updateScalp(["vwap_filter", "min_below_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              )}
            </Group>

            <Group title="Risk Limits (Daily)">
              <div style={{
                marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5,
              }}>
                Daily realised-P&L limits. When hit, no new entries are taken for the
                rest of the day (open trades run to their own exit). 0 = disabled.
              </div>
              <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                <Input type="number" min="0" value={scalpConfig.max_loss}
                  onChange={(e) => updateScalp(["max_loss"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
              <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                <Input type="number" min="0" value={scalpConfig.max_profit}
                  onChange={(e) => updateScalp(["max_profit"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
            </Group>

            <Group title="Option Premium Filter">
              <Field label="Minimum Premium" helper="Skip options below this price">
                <Input type="number" min="0" value={scalpConfig.option_premium.min}
                  onChange={(e) => updateScalp(["option_premium", "min"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Maximum Premium" helper="Skip options above this price">
                <Input type="number" min="0" value={scalpConfig.option_premium.max}
                  onChange={(e) => updateScalp(["option_premium", "max"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            <Group title="Trading Sessions">
              <Field label="Primary Session" helper="Main trading window">
                <TimeRange
                  startValue={scalpConfig.session.primary.start}
                  endValue={scalpConfig.session.primary.end}
                  onStartChange={(e) => updateScalp(["session", "primary", "start"], e.target.value)}
                  onEndChange={(e)   => updateScalp(["session", "primary", "end"],   e.target.value)} />
              </Field>
              <Field label="Secondary Session">
                <Checkbox
                  checked={scalpConfig.session.secondary.enabled}
                  onChange={(e) => updateScalp(["session", "secondary", "enabled"], e.target.checked)}
                  label="Enable secondary trading window" />
              </Field>
              <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                <TimeRange
                  startValue={scalpConfig.session.secondary.start}
                  endValue={scalpConfig.session.secondary.end}
                  disabled={!scalpConfig.session.secondary.enabled}
                  onStartChange={(e) => updateScalp(["session", "secondary", "start"], e.target.value)}
                  onEndChange={(e)   => updateScalp(["session", "secondary", "end"],   e.target.value)} />
              </Field>
            </Group>

            <Group title="Order Quantity">
              <Field label="Number of Lots" helper={`1 lot = ${scalpConfig.quantity.lot_size} units`}>
                <Input type="number" min="1" value={scalpConfig.quantity.lots}
                  onChange={(e) => updateScalp(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>
          
</>);
      case "BB_V1":    return (<>
            {/* ── Execution ── */}
            <Group title="Execution">
              <Field label="Mode" helper="Changes take effect on next trade cycle">
                <ModeToggle value={bbConfig.trade_execution_mode} onChange={(v) => updateBB(["trade_execution_mode"], v)} />
              </Field>
              <Field label="Session Start" helper="Strategy starts scanning after this time">
                <Input type="time" value={bbConfig.session_start}
                  onChange={(e) => updateBB(["session_start"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Session End" helper="No new entries after this time">
                <Input type="time" value={bbConfig.session_end}
                  onChange={(e) => updateBB(["session_end"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Auto Square-Off" helper="All open positions closed at this time">
                <Input type="time" value={bbConfig.auto_square_off_time}
                  onChange={(e) => updateBB(["auto_square_off_time"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
            </Group>

            {/* ── Lots ── */}
            <Group title="Order Quantity">
              <Field label="Total Lots" helper="Both CE and PE trades use this lot count">
                <Input type="number" min="1" max="20" value={bbConfig.lots}
                  onChange={(e) => handleLotsChange(e.target.value)}
                  style={{ maxWidth: 100 }} />
              </Field>
            </Group>

            {/* ── Risk Parameters ── */}
            <Group title="Risk Parameters">
              <Field label="Stop Loss %" helper="% of entry price — 0 = disabled. Applied to all legs.">
                <Input type="number" step="0.1" min="0" max="100" value={bbConfig.sl_pct}
                  onChange={(e) => updateBB(["sl_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field
                label="Take Profit %"
                helper={multipleTargets ? "Disabled — using TP1 / TP2 below" : "% of entry price — 0 = disabled"}
              >
                <Input type="number" step="0.1" min="0" max="500"
                  value={bbConfig.tp_pct}
                  disabled={multipleTargets}
                  onChange={(e) => updateBB(["tp_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>
                      
            <Group title="Risk Limits (Daily)">
              <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                Daily realised-P&L limits. When hit, no new entries for the rest of the
                day (open trades run to their own exit). 0 = disabled.
              </div>
              <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                <Input type="number" min="0" value={bbConfig.max_loss}
                  onChange={(e) => updateBB(["max_loss"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
              <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                <Input type="number" min="0" value={bbConfig.max_profit}
                  onChange={(e) => updateBB(["max_profit"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
            </Group>

            {/* ── Multiple Targets ── */}
            <Group title="Multiple Targets" highlight={multipleTargets}>
              <Field
                label="Enable"
                helper={!canEnableMulti ? "Increase Total Lots to ≥ 2 to enable" : "Split the trade into two legs with separate targets"}
              >
                <Checkbox
                  checked={multipleTargets}
                  disabled={!canEnableMulti}
                  onChange={(e) => {
                    const enabled = e.target.checked;
                    if (enabled) {
                      const total = bbConfig.lots;
                      const l1    = Math.max(1, Math.floor(total / 2));
                      const l2    = total - l1;
                      setBBConfig(u => ({ ...u, multiple_targets: true, lots_leg1: l1, lots_leg2: l2 }));
                    } else {
                      updateBB(["multiple_targets"], false);
                    }
                  }}
                  label="Multiple Targets"
                />
              </Field>

              <Field label="Target 1 %" helper="Take profit % for the first leg (book partial profit)" indent>
                <Input type="number" step="0.1" min="1" max="500"
                  value={bbConfig.tp1_pct}
                  disabled={!multipleTargets}
                  onChange={(e) => updateBB(["tp1_pct"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>

              <Field label="Target 2 %" helper="Take profit % for the runner leg (must be ≥ Target 1)" indent>
                <Input type="number" step="0.1" min="1" max="1000"
                  value={bbConfig.tp2_pct}
                  disabled={!multipleTargets}
                  onChange={(e) => updateBB(["tp2_pct"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
                {multipleTargets && bbConfig.tp2_pct < bbConfig.tp1_pct && (
                  <div style={{ fontSize: 10, color: colors.warning, marginTop: 3 }}>
                    ⚠ Target 2 is lower than Target 1
                  </div>
                )}
              </Field>

              <Field
                label="Leg 1 Lots"
                helper="Lots to close at Target 1"
                indent
                error={splitErr && multipleTargets ? splitErr : null}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {multipleTargets && bbConfig.lots >= 2 ? (
                    <Select
                      value={bbConfig.lots_leg1}
                      onChange={(e) => handleLeg1Change(Number(e.target.value))}
                      disabled={!multipleTargets}
                      style={{ maxWidth: 100 }}
                    >
                      {leg1Options.map((n) => (
                        <option key={n} value={n}>{n} lot{n > 1 ? "s" : ""}</option>
                      ))}
                    </Select>
                  ) : (
                    <Input type="number" min="1" value={bbConfig.lots_leg1} disabled style={{ maxWidth: 100 }} />
                  )}
                  <span style={{ fontSize: 11, color: colors.text.muted }}>of {bbConfig.lots} total</span>
                </div>
              </Field>

              <Field label="Leg 2 Lots" helper="Remaining lots — runs to Target 2 (auto-calculated)" indent>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Input type="number" value={bbConfig.lots_leg2} disabled style={{ maxWidth: 100, opacity: 0.7 }} />
                  <span style={{ fontSize: 11, color: colors.text.muted }}>lots (auto)</span>
                </div>
              </Field>

              <Field label="Trailing SL" helper="After Target 1 is hit, move Leg 2 stop loss to breakeven (entry price)" indent>
                <Checkbox
                  checked={bbConfig.trailing_sl}
                  disabled={!multipleTargets}
                  onChange={(e) => updateBB(["trailing_sl"], e.target.checked)}
                  label="Move Leg 2 SL to breakeven after Target 1 hit"
                />
              </Field>
            </Group>

            {/* ── Trade Filters ── */}
            <Group title="Trade Filters">
              <Field label="Max Premium (₹)" helper="Skip options above this price">
                <Input type="number" min="1" value={bbConfig.max_premium}
                  onChange={(e) => updateBB(["max_premium"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Max Trades / Side" helper="CE and PE limits are independent">
                <Input type="number" min="1" max="10" value={bbConfig.max_trades_per_side}
                  onChange={(e) => updateBB(["max_trades_per_side"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            {/* ── Exit Criteria ── */}
            <Group title="Exit Criteria">
              <Field
                label="Exit Gap"
                helper="Exit when candle close is within this many points of the exit level. 0 = exact level."
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Input
                    type="number" min="0" max="100" step="1"
                    value={bbConfig.st_exit_gap ?? 30}
                    onChange={(e) => {
                      const raw = Number(e.target.value);
                      const val = isNaN(raw) ? 30 : Math.min(100, Math.max(0, raw));
                      updateBB(["st_exit_gap"], val);
                    }}
                    style={{ maxWidth: 100 }}
                  />
                  <span style={{ fontSize: 11, color: colors.text.muted }}>points (0 – 100)</span>
                </div>
              </Field>
            </Group>


</>);
      case "BB_V2":    return (<>
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                <ModeToggle
                  value={bbV2Config.trade_execution_mode}
                  onChange={(v) => updateBBV2(["trade_execution_mode"], v)}
                />
              </Field>
              <Field label="Session Start">
                <Input type="time" value={bbV2Config.session_start}
                  onChange={(e) => updateBBV2(["session_start"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Session End">
                <Input type="time" value={bbV2Config.session_end}
                  onChange={(e) => updateBBV2(["session_end"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Auto Square-Off">
                <Input type="time" value={bbV2Config.auto_square_off_time}
                  onChange={(e) => updateBBV2(["auto_square_off_time"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
            </Group>

            <Group title="Risk Parameters">
              <Field label="Stop Loss %" helper="% of entry price">
                <Input type="number" step="0.1" min="0" max="100"
                  value={bbV2Config.sl_pct}
                  onChange={(e) => updateBBV2(["sl_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Take Profit %" helper="% of entry price">
                <Input type="number" step="0.1" min="0" max="100"
                  value={bbV2Config.tp_pct}
                  onChange={(e) => updateBBV2(["tp_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            <Group title="Trade Filters">
              <Field label="Max Premium (₹)">
                <Input type="number" min="1" value={bbV2Config.max_premium}
                  onChange={(e) => updateBBV2(["max_premium"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Max Trades / Side">
                <Input type="number" min="1" max="10" value={bbV2Config.max_trades_per_side}
                  onChange={(e) => updateBBV2(["max_trades_per_side"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="CE Lots">
                <Input type="number" min="1" value={bbV2Config.ce_lots}
                  onChange={(e) => updateBBV2(["ce_lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="PE Lots">
                <Input type="number" min="1" value={bbV2Config.pe_lots}
                  onChange={(e) => updateBBV2(["pe_lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            <Group title="Risk Limits (Daily)">
              <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                Daily realised-P&L limits. When hit, no new entries for the rest of the
                day (open trades run to their own exit). 0 = disabled.
              </div>
              <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                <Input type="number" min="0" value={bbV2Config.max_loss}
                  onChange={(e) => updateBBV2(["max_loss"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
              <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                <Input type="number" min="0" value={bbV2Config.max_profit}
                  onChange={(e) => updateBBV2(["max_profit"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
            </Group>

          
</>);
      case "HA_V1":    return (<>
            {/* ── Execution ──
                OFF keeps data collection running while suppressing NEW entries.
                Any open trade still exits normally. */}
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated · OFF = no new entries">
                <ModeToggle
                  value={haConfig.trade_execution_mode}
                  onChange={(v) => updateHA(["trade_execution_mode"], v)}
                  modes={["OFF", "PAPER", "LIVE"]}
                />
              </Field>
              {haConfig.trade_execution_mode === "OFF" && (
                <div style={{
                  marginTop: spacing.sm,
                  padding: spacing.sm,
                  background: "rgba(148,163,184,0.08)",
                  border: "1px solid rgba(148,163,184,0.25)",
                  borderRadius: 5,
                  fontSize: 11,
                  color: colors.text.muted,
                  lineHeight: 1.6,
                }}>
                  <strong style={{ color: colors.text.secondary }}>⏸ OFF</strong> — the
                  strategy takes <strong>no new entries</strong>. Any trade that
                  is already open will still be managed to its TP / SL / EOD exit.
                </div>
              )}
              <Field label="Trade Side" helper="Which option sides to trade">
                <SideToggle value={haConfig.trade_side_mode} onChange={(v) => updateHA(["trade_side_mode"], v)} />
              </Field>
            </Group>

            {/* ── Risk Management ── */}
            <Group title="Risk Management">
              <Field
                label="Risk : Reward"
                helper={haConfig.target_override?.enabled
                  ? "Disabled — using fixed target points below"
                  : "Target-to-stop multiplier. Default 1:2"}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 12, color: colors.text.muted }}>1 :</span>
                  <Input
                    type="number" step="0.1" min="0.1"
                    value={haConfig.risk_reward_ratio}
                    disabled={haConfig.target_override?.enabled}
                    onChange={(e) => updateHA(["risk_reward_ratio"], Math.max(0.1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }}
                  />
                </div>
              </Field>

              {/* ── Fixed Target Override ── */}
              <Field label="Fixed Target Override">
                <Checkbox
                  checked={haConfig.target_override?.enabled ?? false}
                  onChange={(e) => updateHA(["target_override", "enabled"], e.target.checked)}
                  label="Use fixed target points instead of R:R" />
              </Field>
              <Field
                label="Target Points"
                helper="TP = entry price + this value. Active only when override is on."
                indent
              >
                <Input
                  type="number" min="0" step="0.5"
                  disabled={!haConfig.target_override?.enabled}
                  value={haConfig.target_override?.points ?? 0}
                  onChange={(e) => updateHA(["target_override", "points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }}
                />
              </Field>

              <Field
                label="Min SL (points)"
                helper="Skip entry if the signal SL distance (entry − SL) is below this. 0 = disabled."
              >
                <Input type="number" min="0" step="0.5" value={haConfig.min_sl_points}
                  onChange={(e) => updateHA(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>

              <Field label="Max Trades / Side" helper="Daily ceiling per CE or PE side">
                <Input type="number" min="1" max="20" value={haConfig.max_trades_per_side}
                  onChange={(e) => updateHA(["max_trades_per_side"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>

              {/* ── HA_COND_FILTER BEGIN ── entry-condition multi-select.
                  Applies in BOTH PAPER and LIVE (gate sits in the shared
                  signal path in ha_tick_engine, before arbitration). The last
                  enabled chip cannot be turned off — empty is ambiguous; the
                  backend treats absent/empty as ALL, so we never persist one. */}
              <Field label="Entry Conditions"
                helper="Only selected conditions may enter (PAPER and LIVE). At least one must stay on.">
                <div style={{ display: "flex", gap: 8 }}>
                  {["COND1", "COND2", "COND3"].map((cond) => {
                    const list = Array.isArray(haConfig.entry_conditions) && haConfig.entry_conditions.length
                      ? haConfig.entry_conditions : ["COND1", "COND2", "COND3"];
                    const on = list.includes(cond);
                    const lastOn = on && list.length === 1;
                    return (
                      <button key={cond} type="button"
                        title={lastOn ? "At least one condition must stay enabled" : cond}
                        onClick={() => {
                          if (lastOn) return;   /* never allow an empty set */
                          const next = on
                            ? list.filter((x) => x !== cond)
                            : ["COND1", "COND2", "COND3"].filter((x) => list.includes(x) || x === cond);
                          updateHA(["entry_conditions"], next);
                        }}
                        style={{
                          padding: "6px 14px", borderRadius: 6, fontSize: 13, fontWeight: 700,
                          cursor: lastOn ? "not-allowed" : "pointer",
                          border: `1px solid ${on ? "#3b82f6" : "#374151"}`,
                          background: on ? "rgba(59,130,246,0.15)" : "transparent",
                          color: on ? "#3b82f6" : "#9ca3af",
                          opacity: lastOn ? 0.8 : 1,
                        }}>
                        {cond.replace("COND", "C")}
                      </button>
                    );
                  })}
                </div>
              </Field>
              {/* ── HA_COND_FILTER END ── */}

              {/* ── HA_COND1_FLIP BEGIN ── COND1-only opposite-side entry
                  (PAPER + LIVE — the transform sits in the shared signal path
                  in ha_tick_engine, before arbitration). A C1 signal on the
                  selected CE enters the selected PE and vice versa; risk
                  transfers in points. C2/C3 entries are never affected. */}
              <Field label="C1 Flip Side"
                helper="COND1 signals enter the OPPOSITE side's selected contract (CE↔PE). C2/C3 unaffected. Backtest: C1 standalone flips −4.1L → +1.5L; pair with C3.">
                <div style={{ display: "flex", gap: 8 }}>
                  {[["0", "Off (signal side)"], ["1", "On (CE↔PE opposite)"]].map(([v, label]) => {
                    const on = (haConfig.cond1_flip_side ? "1" : "0") === v;
                    return (
                      <button key={v} type="button"
                        onClick={() => updateHA(["cond1_flip_side"], v === "1")}
                        style={{
                          padding: "6px 14px", borderRadius: 6, fontSize: 13, fontWeight: 700,
                          cursor: "pointer",
                          border: `1px solid ${on ? "#3b82f6" : "#374151"}`,
                          background: on ? "rgba(59,130,246,0.15)" : "transparent",
                          color: on ? "#3b82f6" : "#9ca3af",
                        }}>
                        {label}
                      </button>
                    );
                  })}
                </div>
              </Field>
              {/* ── HA_COND1_FLIP END ── */}
            </Group>

            {/* ── Option Premium Filter ── */}
            <Group title="Option Premium Filter">
              <Field label="Minimum Premium" helper="Skip options below this price">
                <Input type="number" min="0" value={haConfig.option_premium.min}
                  onChange={(e) => updateHA(["option_premium", "min"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Maximum Premium" helper="Skip options above this price">
                <Input type="number" min="0" value={haConfig.option_premium.max}
                  onChange={(e) => updateHA(["option_premium", "max"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            {/* ── Order Quantity ── */}
            <Group title="Order Quantity">
              <Field label="Number of Lots" helper={`1 lot = ${haConfig.quantity.lot_size} units`}>
                <Input type="number" min="1" value={haConfig.quantity.lots}
                  onChange={(e) => updateHA(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            <Group title="Risk Limits (Daily)">
              <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                Daily realised-P&L limits. When hit, no new entries for the rest of the
                day (open trades run to their own exit). 0 = disabled.
              </div>
              <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                <Input type="number" min="0" value={haConfig.max_loss}
                  onChange={(e) => updateHA(["max_loss"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
              <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                <Input type="number" min="0" value={haConfig.max_profit}
                  onChange={(e) => updateHA(["max_profit"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 140 }} />
              </Field>
            </Group>

            {/* ── Trading Sessions ── */}
            <Group title="Trading Sessions">
              <Field label="Primary Session" helper="Entry window">
                <TimeRange
                  startValue={haConfig.session.primary.start}
                  endValue={haConfig.session.primary.end}
                  onStartChange={(e) => updateHA(["session", "primary", "start"], e.target.value)}
                  onEndChange={(e)   => updateHA(["session", "primary", "end"],   e.target.value)} />
              </Field>
              <Field label="Secondary Session">
                <Checkbox
                  checked={haConfig.session.secondary.enabled}
                  onChange={(e) => updateHA(["session", "secondary", "enabled"], e.target.checked)}
                  label="Enable secondary trading window" />
              </Field>
              <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                <TimeRange
                  startValue={haConfig.session.secondary.start}
                  endValue={haConfig.session.secondary.end}
                  disabled={!haConfig.session.secondary.enabled}
                  onStartChange={(e) => updateHA(["session", "secondary", "start"], e.target.value)}
                  onEndChange={(e)   => updateHA(["session", "secondary", "end"],   e.target.value)} />
              </Field>
            </Group>
          
</>);
      case "TMA_V1": return (<>
              {/* ── TMA_V1 BEGIN ── Redesigned 2026-07-20 to the IC_V1 layout
                  system (Group / Field / Input / Select), replacing the
                  hand-rolled grid. Signal params (EMA 5/13/89 @5m) stay
                  absent by design — fixed for backtest parity, and the
                  house convention is that indicator names aren't exposed. */}
              <Group title="Execution">
                <Field label="Mode" helper="PAPER = simulated at candle closes · LIVE = real orders (hedge bought first, GTT stop, cancel-verified exits). Applies from the NEXT entry — an open position keeps the mode it was opened with.">
                  <ModeToggle value={tmaConfig.trade_execution_mode}
                    onChange={(v) => updateTMA(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Trade Mode" helper="INTRADAY squares off every day at Exit Time · POSITIONAL carries overnight and hard-closes only on the contract's expiry day">
                  <Select value={tmaConfig.trade_mode}
                    onChange={(e) => updateTMA(["trade_mode"], e.target.value)}
                    style={{ maxWidth: 210 }}>
                    <option value="INTRADAY">INTRADAY — square off daily</option>
                    <option value="POSITIONAL">POSITIONAL — carry to expiry</option>
                  </Select>
                </Field>
                <Field label="Entry Window" helper="New entries only. Signals outside this window are logged and skipped; exits and monitoring continue past it.">
                  <TimeRange
                    startValue={tmaConfig.session_start}
                    endValue={tmaConfig.session_end}
                    onStartChange={(e) => updateTMA(["session_start"], e.target.value)}
                    onEndChange={(e) => updateTMA(["session_end"], e.target.value)} />
                </Field>
                <Field label="Exit Time" helper="EOD square-off (INTRADAY, and POSITIONAL on expiry day). Also the moment the negative-mark cut is evaluated.">
                  <Input value={tmaConfig.exit_time}
                    onChange={(e) => updateTMA(["exit_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Max Trades/Day" helper="0 = unlimited. One spread is open at a time regardless.">
                  <Input type="number" min="0" value={tmaConfig.c1.max_trades_per_day}
                    onChange={(e) => updateTMA(["c1", "max_trades_per_day"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                {/* WINDOW_GUARD — the loop ABORTS at boot (no trading, log-only)
                    unless start < end ≤ exit. Surface it here rather than let
                    a saved config silently kill tomorrow's session. */}
                {!(hmToMinUI(tmaConfig.session_start) < hmToMinUI(tmaConfig.session_end)
                   && hmToMinUI(tmaConfig.session_end) <= hmToMinUI(tmaConfig.exit_time)) && (
                  <div style={{ marginTop: spacing.xs, padding: "8px 10px", borderRadius: 6,
                    background: "#7f1d1d33", border: "1px solid #ef444466",
                    fontSize: 11, color: "#fca5a5", lineHeight: 1.5 }}>
                    Invalid window: entry start must be earlier than entry end, and entry end
                    no later than exit time. TMA will refuse to trade at next start-up.
                  </div>
                )}
              </Group>

              <Group title="Legs (sell = monitored · hedge = follows)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Strike = highest premium ≤ cap, priced on the candle before the signal. The
                  SELL leg goes on the side OPPOSITE the trend and carries every exit (stop,
                  target, trend reversal, EOD); the hedge is the same side, deeper OTM, and
                  exits in the same minute. Both legs must be sized — the spread is
                  all-or-nothing. Stop/target 0 disables that level.
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "72px 1fr 1fr 1fr 1fr", gap: 6, alignItems: "center", fontSize: 11 }}>
                  <span style={{ color: colors.text.muted }}>Leg</span>
                  <span style={{ color: colors.text.muted }}>Lots</span>
                  <span style={{ color: colors.text.muted }}>Prem ≤ ₹</span>
                  <span style={{ color: colors.text.muted }}>SL</span>
                  <span style={{ color: colors.text.muted }}>TP</span>

                  <span style={{ fontWeight: 700, color: "#ef4444" }}>SELL</span>
                  <Input type="number" min="1" value={tmaConfig.c1.sell.lots}
                    onChange={(e) => updateTMA(["c1", "sell", "lots"], Math.max(1, Number(e.target.value)))} />
                  <Input type="number" min="0" step="0.5" value={tmaConfig.c1.sell.premium_max}
                    onChange={(e) => updateTMA(["c1", "sell", "premium_max"], Math.max(0, Number(e.target.value)))} />
                  <div style={{ display: "flex", gap: 3 }}>
                    <Input type="number" min="0" value={tmaConfig.c1.sell.sl_pct}
                      onChange={(e) => updateTMA(["c1", "sell", "sl_pct"], Math.max(0, Number(e.target.value)))} />
                    <Select value={tmaConfig.c1.sell.sl_unit}
                      onChange={(e) => updateTMA(["c1", "sell", "sl_unit"], e.target.value)} style={{ width: 62 }}>
                      <option value="PCT">%</option><option value="PTS">pts</option><option value="ABS">₹</option>
                    </Select>
                  </div>
                  <div style={{ display: "flex", gap: 3 }}>
                    <Input type="number" min="0" value={tmaConfig.c1.sell.tp_pct}
                      onChange={(e) => updateTMA(["c1", "sell", "tp_pct"], Math.max(0, Number(e.target.value)))} />
                    <Select value={tmaConfig.c1.sell.tp_unit}
                      onChange={(e) => updateTMA(["c1", "sell", "tp_unit"], e.target.value)} style={{ width: 62 }}>
                      <option value="PCT">%</option><option value="PTS">pts</option><option value="ABS">₹</option>
                    </Select>
                  </div>

                  <span style={{ fontWeight: 700, color: "#10b981" }}>HEDGE</span>
                  <Input type="number" min="1" value={tmaConfig.c1.buy.lots}
                    onChange={(e) => updateTMA(["c1", "buy", "lots"], Math.max(1, Number(e.target.value)))} />
                  <Input type="number" min="0" step="0.5" value={tmaConfig.c1.buy.premium_max}
                    onChange={(e) => updateTMA(["c1", "buy", "premium_max"], Math.max(0, Number(e.target.value)))} />
                  <span style={{ fontSize: 10, color: colors.text.muted }}>follows sell</span>
                  <span style={{ fontSize: 10, color: colors.text.muted }}>follows sell</span>
                </div>
                <div style={{ marginTop: spacing.sm, fontSize: 10, color: colors.text.muted, lineHeight: 1.5 }}>
                  Units: % of the sold entry premium · pts from entry · ₹ absolute level. An
                  absolute level on the wrong side of entry is ignored rather than fired.
                </div>
              </Group>

              <Group title="Sizing & Ops">
                <Field label="Lot Size" helper="NIFTY lot size (65). Update here on an NSE revision — never hardcoded.">
                  <Input type="number" min="1" value={tmaConfig.quantity.lot_size}
                    onChange={(e) => updateTMA(["quantity", "lot_size"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="Margin Guard" helper="Basket-margin check before entry. Confirmed shortfall (available < required × 1.25) blocks the entry; API errors fail OPEN (advisory).">
                  <input type="checkbox" checked={!!tmaConfig.margin_guard}
                    onChange={(e) => updateTMA(["margin_guard"], e.target.checked)} />
                </Field>
                <Field label="Hedge Fallback" helper="If NO strike sits under the hedge cap: ON = take the cheapest real strike (flagged on the dashboard) · OFF = skip the signal. There is no synthetic hedge.">
                  <input type="checkbox" checked={tmaConfig.wing_mode === "real_fallback"}
                    onChange={(e) => updateTMA(["wing_mode"], e.target.checked ? "real_fallback" : "skip")} />
                </Field>
                {/* AT_EOD_DAILY — mirrors the backtest's "At EOD time, daily"
                    dropdown (carry all / cut losers). A disabled checkbox read
                    as "missing option" (2026-07-21); an explicit two-choice
                    control that only renders for POSITIONAL is clearer, and
                    INTRADAY gets a plain statement of what it does instead. */}
                {tmaConfig.trade_mode === "POSITIONAL" ? (
                  <Field label="At EOD, Daily" helper="Evaluated at Exit Time on every carried day (not on the contract's expiry day, which always squares off).">
                    <Select value={tmaConfig.cut_neg_mtm_eod ? "ON" : "OFF"}
                      onChange={(e) => updateTMA(["cut_neg_mtm_eod"], e.target.value === "ON")}
                      style={{ maxWidth: 230 }}>
                      <option value="OFF">Carry all overnight</option>
                      <option value="ON">Cut losers, carry winners</option>
                    </Select>
                  </Field>
                ) : (
                  <Field label="At EOD, Daily" helper="INTRADAY squares off every position at Exit Time — nothing is carried, so there is nothing to cut.">
                    <span style={{ fontSize: 12, color: colors.text.muted }}>Square off everything</span>
                  </Field>
                )}
              </Group>
              {/* ── TMA_V1 END ── */}
      </>);

      case "TMA_V2": return (<>
              {/* ── TMA_V2 BEGIN ── Same layout system as TMA_V1. What is
                  DELIBERATELY ABSENT (frozen by the 2026-08-19 walk-forward
                  study, backend config keys only — LD2): execution mode is
                  always SELL+hedge, the crossover exit is always ON at
                  EMA13×EMA55, and the EMA144 slope gate is always ON. Signal
                  periods (13/55/89/144 @5m) are fixed for backtest parity.
                  Only Max Extension is exposed, because its optimum is flat
                  across 0.5-1.5 and it is the one regime-facing knob. */}
              <Group title="Execution">
                <Field label="Mode" helper="PAPER = simulated at candle closes · LIVE = real orders (hedge bought first, GTT stop, cancel-verified exits). Applies from the NEXT entry — an open position keeps the mode it was opened with.">
                  <ModeToggle value={tma2Config.trade_execution_mode}
                    onChange={(v) => updateTMA2(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Trade Mode" helper="INTRADAY squares off every day at Exit Time · POSITIONAL carries overnight and hard-closes only on the contract's expiry day">
                  <Select value={tma2Config.trade_mode}
                    onChange={(e) => updateTMA2(["trade_mode"], e.target.value)}
                    style={{ maxWidth: 210 }}>
                    <option value="INTRADAY">INTRADAY — square off daily</option>
                    <option value="POSITIONAL">POSITIONAL — carry to expiry</option>
                  </Select>
                </Field>
                <Field label="Entry Window" helper="New entries only. Signals outside this window are logged and skipped; exits and monitoring continue past it.">
                  <TimeRange
                    startValue={tma2Config.session_start}
                    endValue={tma2Config.session_end}
                    onStartChange={(e) => updateTMA2(["session_start"], e.target.value)}
                    onEndChange={(e) => updateTMA2(["session_end"], e.target.value)} />
                </Field>
                <Field label="Exit Time" helper="EOD square-off (INTRADAY, and POSITIONAL on expiry day). Also the moment the negative-mark cut is evaluated.">
                  <Input value={tma2Config.exit_time}
                    onChange={(e) => updateTMA2(["exit_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Max Trades/Day" helper="0 = unlimited. One spread is open at a time regardless — in either direction.">
                  <Input type="number" min="0" value={tma2Config.s1.max_trades_per_day}
                    onChange={(e) => updateTMA2(["s1", "max_trades_per_day"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                {/* WINDOW_GUARD — the loop ABORTS at boot (no trading, log-only)
                    unless start < end ≤ exit. Surface it here rather than let
                    a saved config silently kill tomorrow's session. */}
                {!(hmToMinUI(tma2Config.session_start) < hmToMinUI(tma2Config.session_end)
                   && hmToMinUI(tma2Config.session_end) <= hmToMinUI(tma2Config.exit_time)) && (
                  <div style={{ marginTop: spacing.xs, padding: "8px 10px", borderRadius: 6,
                    background: "#7f1d1d33", border: "1px solid #ef444466",
                    fontSize: 11, color: "#fca5a5", lineHeight: 1.5 }}>
                    Invalid window: entry start must be earlier than entry end, and entry end
                    no later than exit time. TMA V2 will refuse to trade at next start-up.
                  </div>
                )}
              </Group>

              <Group title="Entry filter">
                <Field label="Max Extension %" helper="Skips a signal when the fast and slow averages have already spread this far apart (as % of spot) — the move is late and prone to snapping back. 0 disables it. Backtested best around 0.5-1.5; 0.8 is the shipped value.">
                  <Input type="number" min="0" step="0.05" value={tma2Config.max_extension_pct}
                    onChange={(e) => updateTMA2(["max_extension_pct"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
              </Group>

              <Group title="Legs (sell = monitored · hedge = follows)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Strike = highest premium ≤ cap, priced on the candle before the signal. The
                  SELL leg goes on the side OPPOSITE the trend and carries every exit (stop,
                  target, trend reversal, EOD); the hedge is the same side, deeper OTM, and
                  exits in the same minute. Both legs must be sized — the spread is
                  all-or-nothing. Stop/target 0 disables that level.
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "72px 1fr 1fr 1fr 1fr", gap: 6, alignItems: "center", fontSize: 11 }}>
                  <span style={{ color: colors.text.muted }}>Leg</span>
                  <span style={{ color: colors.text.muted }}>Lots</span>
                  <span style={{ color: colors.text.muted }}>Prem ≤ ₹</span>
                  <span style={{ color: colors.text.muted }}>SL</span>
                  <span style={{ color: colors.text.muted }}>TP</span>

                  <span style={{ fontWeight: 700, color: "#ef4444" }}>SELL</span>
                  <Input type="number" min="1" value={tma2Config.s1.main.lots}
                    onChange={(e) => updateTMA2(["s1", "main", "lots"], Math.max(1, Number(e.target.value)))} />
                  <Input type="number" min="0" step="0.5" value={tma2Config.s1.main.premium_max}
                    onChange={(e) => updateTMA2(["s1", "main", "premium_max"], Math.max(0, Number(e.target.value)))} />
                  <div style={{ display: "flex", gap: 3 }}>
                    <Input type="number" min="0" value={tma2Config.s1.main.sl_pct}
                      onChange={(e) => updateTMA2(["s1", "main", "sl_pct"], Math.max(0, Number(e.target.value)))} />
                    <Select value={tma2Config.s1.main.sl_unit}
                      onChange={(e) => updateTMA2(["s1", "main", "sl_unit"], e.target.value)} style={{ width: 62 }}>
                      <option value="PCT">%</option><option value="PTS">pts</option><option value="ABS">₹</option>
                    </Select>
                  </div>
                  <div style={{ display: "flex", gap: 3 }}>
                    <Input type="number" min="0" value={tma2Config.s1.main.tp_pct}
                      onChange={(e) => updateTMA2(["s1", "main", "tp_pct"], Math.max(0, Number(e.target.value)))} />
                    <Select value={tma2Config.s1.main.tp_unit}
                      onChange={(e) => updateTMA2(["s1", "main", "tp_unit"], e.target.value)} style={{ width: 62 }}>
                      <option value="PCT">%</option><option value="PTS">pts</option><option value="ABS">₹</option>
                    </Select>
                  </div>

                  <span style={{ fontWeight: 700, color: "#10b981" }}>HEDGE</span>
                  <Input type="number" min="1" value={tma2Config.s1.hedge.lots}
                    onChange={(e) => updateTMA2(["s1", "hedge", "lots"], Math.max(1, Number(e.target.value)))} />
                  <Input type="number" min="0" step="0.5" value={tma2Config.s1.hedge.premium_max}
                    onChange={(e) => updateTMA2(["s1", "hedge", "premium_max"], Math.max(0, Number(e.target.value)))} />
                  <span style={{ fontSize: 10, color: colors.text.muted }}>follows sell</span>
                  <span style={{ fontSize: 10, color: colors.text.muted }}>follows sell</span>
                </div>
                <div style={{ marginTop: spacing.sm, fontSize: 10, color: colors.text.muted, lineHeight: 1.5 }}>
                  Units: % of the sold entry premium · pts from entry · ₹ absolute level. An
                  absolute level on the wrong side of entry is ignored rather than fired.
                </div>
              </Group>

              <Group title="Sizing & Ops">
                <Field label="Lot Size" helper="NIFTY lot size (65). Update here on an NSE revision — never hardcoded.">
                  <Input type="number" min="1" value={tma2Config.quantity.lot_size}
                    onChange={(e) => updateTMA2(["quantity", "lot_size"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="Margin Guard" helper="Basket-margin check before entry. Confirmed shortfall (available < required × 1.25) blocks the entry; API errors fail OPEN (advisory).">
                  <input type="checkbox" checked={!!tma2Config.margin_guard}
                    onChange={(e) => updateTMA2(["margin_guard"], e.target.checked)} />
                </Field>
                <Field label="Hedge Fallback" helper="If NO strike sits under the hedge cap: ON = take the cheapest real strike (flagged on the dashboard) · OFF = skip the signal. There is no synthetic hedge.">
                  <input type="checkbox" checked={tma2Config.wing_mode === "real_fallback"}
                    onChange={(e) => updateTMA2(["wing_mode"], e.target.checked ? "real_fallback" : "skip")} />
                </Field>
                {tma2Config.trade_mode === "POSITIONAL" ? (
                  <Field label="At EOD, Daily" helper="Evaluated at Exit Time on every carried day (not on the contract's expiry day, which always squares off).">
                    <Select value={tma2Config.cut_neg_mtm_eod ? "ON" : "OFF"}
                      onChange={(e) => updateTMA2(["cut_neg_mtm_eod"], e.target.value === "ON")}
                      style={{ maxWidth: 230 }}>
                      <option value="OFF">Carry all overnight</option>
                      <option value="ON">Cut losers, carry winners</option>
                    </Select>
                  </Field>
                ) : (
                  <Field label="At EOD, Daily" helper="INTRADAY squares off every position at Exit Time — nothing is carried, so there is nothing to cut.">
                    <span style={{ fontSize: 12, color: colors.text.muted }}>Square off everything</span>
                  </Field>
                )}
              </Group>
              {/* ── TMA_V2 END ── */}
      </>);

      case "PST_SELL": return (<>
        {/* ── PST_SELL ── spot-signal params are FIXED (pivots + SMA9@5m +
            SuperTrend 10×2@3m, 3m signal TF) — execution knobs only. Legs
            mirror the backtest exactly. Risk ₹ fields: entry-gate in live
            (Phase-1 semantics), full V3 clamp in backtest. */}
        <div style={{ marginBottom: 12 }}>
          <Field label="Mode" helper="LIVE = real orders · PAPER = simulated · applies from the NEXT entry (no restart) — an open position keeps the mode it was opened with">
            <ModeToggle value={pstSellConfig.trade_execution_mode} onChange={(v) => updatePstSell(["trade_execution_mode"], v)} />
          </Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>PREMIUM &lt;
            <input type="number" value={pstSellConfig.premium_max} onChange={(e) => updatePstSell(["premium_max"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>SIDE (signal)
            <select value={pstSellConfig.side_mode} onChange={(e) => updatePstSell(["side_mode"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }}>
              <option value="BOTH">CE + PE</option><option value="CE">CE only</option><option value="PE">PE only</option>
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MAX TRADES/DAY (0=∞)
            <input type="number" value={pstSellConfig.max_trades_per_day} onChange={(e) => updatePstSell(["max_trades_per_day"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>ENTRY CUTOFF
            <input type="text" value={pstSellConfig.entry_cutoff_time} onChange={(e) => updatePstSell(["entry_cutoff_time"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>EXIT (EOD)
            <input type="text" value={pstSellConfig.exit_time} onChange={(e) => updatePstSell(["exit_time"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>DAILY MAX LOSS ₹
            <input type="number" min="0" value={pstSellConfig.daily_max_loss} onChange={(e) => updatePstSell(["daily_max_loss"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>DAILY MAX PROFIT ₹
            <input type="number" min="0" value={pstSellConfig.daily_max_profit} onChange={(e) => updatePstSell(["daily_max_profit"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MONTHLY MAX LOSS ₹
            <input type="number" min="0" value={pstSellConfig.monthly_max_loss} onChange={(e) => updatePstSell(["monthly_max_loss"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MONTHLY MAX PROFIT ₹
            <input type="number" min="0" value={pstSellConfig.monthly_max_profit} onChange={(e) => updatePstSell(["monthly_max_profit"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
        </div>
        <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr>{["Leg", "Lots", "TP % (premium)", "Spot SL (pts)"].map((h, i) => (
            <th key={i} style={{ padding: "4px 8px", textAlign: "left", fontSize: 10, color: "#8b93a7", textTransform: "uppercase" }}>{h}</th>))}</tr></thead>
          <tbody>
            {pstSellConfig.legs.map((leg, i) => (
              <tr key={leg.id}>
                <td style={{ padding: "3px 8px", fontWeight: 700, color: "#ef4444" }}>{leg.id} SELL</td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.lots} onChange={(e) => updatePstSell(["legs", i, "lots"], Number(e.target.value))}
                  style={{ width: 64, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.sl_pct} onChange={(e) => updatePstSell(["legs", i, "sl_pct"], Number(e.target.value))}
                  style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.spot_tg_points} onChange={(e) => updatePstSell(["legs", i, "spot_tg_points"], Number(e.target.value))}
                  style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </>);

      case "PST_HEDGE": return (<>
        {/* ── PST_HEDGE ── spot-signal params are FIXED (pivots + SMA9@5m +
            SuperTrend 10×2@3m, 3m signal TF) — execution knobs only. Legs
            mirror the backtest exactly. Risk ₹ fields: entry-gate in live
            (Phase-1 semantics), full V3 clamp in backtest. */}
        <div style={{ marginBottom: 12 }}>
          <Field label="Mode" helper="LIVE = real orders · PAPER = simulated · applies from the NEXT entry (no restart) — an open position keeps the mode it was opened with">
            <ModeToggle value={pstHedgeConfig.trade_execution_mode} onChange={(v) => updatePstHedge(["trade_execution_mode"], v)} />
          </Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>PREMIUM &lt;
            <input type="number" value={pstHedgeConfig.premium_max} onChange={(e) => updatePstHedge(["premium_max"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>SIDE (signal)
            <select value={pstHedgeConfig.side_mode} onChange={(e) => updatePstHedge(["side_mode"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }}>
              <option value="BOTH">CE + PE</option><option value="CE">CE only</option><option value="PE">PE only</option>
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MAX TRADES/DAY (0=∞)
            <input type="number" value={pstHedgeConfig.max_trades_per_day} onChange={(e) => updatePstHedge(["max_trades_per_day"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>ENTRY CUTOFF
            <input type="text" value={pstHedgeConfig.entry_cutoff_time} onChange={(e) => updatePstHedge(["entry_cutoff_time"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>EXIT (EOD)
            <input type="text" value={pstHedgeConfig.exit_time} onChange={(e) => updatePstHedge(["exit_time"], e.target.value)}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>DAILY MAX LOSS ₹
            <input type="number" min="0" value={pstHedgeConfig.daily_max_loss} onChange={(e) => updatePstHedge(["daily_max_loss"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>DAILY MAX PROFIT ₹
            <input type="number" min="0" value={pstHedgeConfig.daily_max_profit} onChange={(e) => updatePstHedge(["daily_max_profit"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MONTHLY MAX LOSS ₹
            <input type="number" min="0" value={pstHedgeConfig.monthly_max_loss} onChange={(e) => updatePstHedge(["monthly_max_loss"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}>MONTHLY MAX PROFIT ₹
            <input type="number" min="0" value={pstHedgeConfig.monthly_max_profit} onChange={(e) => updatePstHedge(["monthly_max_profit"], Number(e.target.value))}
              style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} />
          </label>
        </div>
        <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr>{["Leg", "Lots", "SL %", "Spot target (pts)"].map((h, i) => (
            <th key={i} style={{ padding: "4px 8px", textAlign: "left", fontSize: 10, color: "#8b93a7", textTransform: "uppercase" }}>{h}</th>))}</tr></thead>
          <tbody>
            {pstHedgeConfig.legs.map((leg, i) => (
              <tr key={leg.id}>
                <td style={{ padding: "3px 8px", fontWeight: 700, color: "#10b981" }}>{leg.id} BUY</td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.lots} onChange={(e) => updatePstHedge(["legs", i, "lots"], Number(e.target.value))}
                  style={{ width: 64, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.sl_pct} onChange={(e) => updatePstHedge(["legs", i, "sl_pct"], Number(e.target.value))}
                  style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
                <td style={{ padding: "3px 8px" }}><input type="number" value={leg.spot_tg_points} onChange={(e) => updatePstHedge(["legs", i, "spot_tg_points"], Number(e.target.value))}
                  style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </>);

      case "SCALP_V3": return (<>
              <Group title="Execution">
                <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                  <ModeToggle value={scalpV3Config.trade_execution_mode} onChange={(v) => updateScalpV3(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Trade Side" helper="Which option sides to trade">
                  <SideToggle value={scalpV3Config.trade_side_mode} onChange={(v) => updateScalpV3(["trade_side_mode"], v)} />
                </Field>
              </Group>

              <Group title="Risk Management">
                <Field label="Risk Min SL" helper="Skip trade if risk distance is below this">
                  <Input type="number" min="0" value={scalpV3Config.min_sl_points}
                    onChange={(e) => updateScalpV3(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Risk Max SL" helper="0 = disabled · skip trade if risk distance exceeds this">
                  <Input type="number" min="0" value={scalpV3Config.risk_max_sl_points}
                    onChange={(e) => updateScalpV3(["risk_max_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Max SL Cap" helper="Caps the SIGNAL contract SL. 0 = disabled">
                  <Input type="number" min="0" value={scalpV3Config.max_sl_points}
                    onChange={(e) => updateScalpV3(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Hedge SL Points" helper="GTT stop distance below the BOUGHT hedge fill price">
                  <Input type="number" min="1" value={scalpV3Config.hedge_sl_points ?? 20}
                    onChange={(e) => updateScalpV3(["hedge_sl_points"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Risk / Reward" helper="Target-to-stop multiplier">
                  <Input type="number" step="0.1" min="0" value={scalpV3Config.risk_reward_ratio}
                    onChange={(e) => updateScalpV3(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Option Premium Filter">
                <Field label="Minimum Premium" helper="Skip options below this price">
                  <Input type="number" min="0" value={scalpV3Config.option_premium.min}
                    onChange={(e) => updateScalpV3(["option_premium", "min"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Maximum Premium" helper="Skip options above this price">
                  <Input type="number" min="0" value={scalpV3Config.option_premium.max}
                    onChange={(e) => updateScalpV3(["option_premium", "max"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Risk Limits (Daily)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Daily realised-P&amp;L limits. When hit, no new entries for the rest of the
                  day (open trade runs to its own exit). 0 = disabled.
                </div>
                <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV3Config.max_loss}
                    onChange={(e) => updateScalpV3(["max_loss"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
                <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV3Config.max_profit}
                    onChange={(e) => updateScalpV3(["max_profit"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
              </Group>

              <Group title="Order Quantity">
                <Field label="Number of Lots" helper={`1 lot = ${scalpV3Config.quantity.lot_size} units`}>
                  <Input type="number" min="1" value={scalpV3Config.quantity.lots}
                    onChange={(e) => updateScalpV3(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Trading Sessions">
                <Field label="Primary Session" helper="Main trading window">
                  <TimeRange
                    startValue={scalpV3Config.session.primary.start}
                    endValue={scalpV3Config.session.primary.end}
                    onStartChange={(e) => updateScalpV3(["session", "primary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV3(["session", "primary", "end"],   e.target.value)} />
                </Field>
                <Field label="Secondary Session">
                  <Checkbox
                    checked={scalpV3Config.session.secondary.enabled}
                    onChange={(e) => updateScalpV3(["session", "secondary", "enabled"], e.target.checked)}
                    label="Enable secondary trading window" />
                </Field>
                <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                  <TimeRange
                    startValue={scalpV3Config.session.secondary.start}
                    endValue={scalpV3Config.session.secondary.end}
                    disabled={!scalpV3Config.session.secondary.enabled}
                    onStartChange={(e) => updateScalpV3(["session", "secondary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV3(["session", "secondary", "end"],   e.target.value)} />
                </Field>
              </Group>
            </>);

      case "TSG_V1": return (<>
              {/* ── TSG_V1 BEGIN ── backtest-validated defaults; every rule
                  evaluated at 1m closes for backtest parity (LD2). */}
              <Group title="Execution">
                <Field label="Mode" helper="OFF = no entry · PAPER = simulated fills at the evaluated 1m close · LIVE = real market orders (Phase 2; requires paper validation first). Ships PAPER.">
                  <ModeToggle value={tsgConfig.trade_execution_mode}
                    onChange={(v) => updateTSG(["trade_execution_mode"], v)}
                    modes={["OFF", "PAPER", "LIVE"]} />
                </Field>
                <Field label="Entry Time" helper="Strikes picked from the live chain + all 4 legs entered at this instant (IST). One entry/day; no qualifying short strike → day skipped with an alert.">
                  <Input value={tsgConfig.entry_time}
                    onChange={(e) => updateTSG(["entry_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Exit (EOD) Time" helper="Everything still open squares off at this instant. Continuous backstop + 15:26 cron (scheduler-death mitigation).">
                  <Input value={tsgConfig.exit_time}
                    onChange={(e) => updateTSG(["exit_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Lots" helper="Per leg, all 4 legs. Lot size 65 (NIFTY).">
                  <Input type="number" value={tsgConfig.lots}
                    onChange={(e) => updateTSG(["lots"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Expiry-Day Lots" helper="OPTIONAL (LD5): on the contract's OWN expiry day, use this lot count instead (0/blank = same as Lots). MTM SL/target auto-scale by the lots ratio on those days (LD5a) — e.g. 15 lots @ base 10 → SL ₹52,500. Backtest evidence: expiry days carry ~3.3× per-day expectancy at near-equal SL rate. Mind the margin.">
                  <Input type="number" value={tsgConfig.expiry_lots}
                    onChange={(e) => updateTSG(["expiry_lots"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
              </Group>
              <Group title="Risk (basket-level — no per-leg SL/TP)">
                <Field label="MTM SL ₹" helper="Exit ALL legs the first 1m close where combined day MTM (realized + unrealized) ≤ −this. Validated: 35000 @ 10 lots. AUTO-SCALES with lots on expiry days (LD5a): effective SL = this × (day lots ÷ Lots), keeping per-lot risk identical. Target scales the same way; IV knobs don't (vol pts are lot-independent).">
                  <Input type="number" value={tsgConfig.mtm_sl}
                    onChange={(e) => updateTSG(["mtm_sl"], Number(e.target.value))}
                    style={{ maxWidth: 110 }} />
                </Field>
                <Field label="MTM Target ₹" helper="0 = off (validated: the ₹1L target fired twice in 6.5y — inert; kept for parity).">
                  <Input type="number" value={tsgConfig.mtm_target}
                    onChange={(e) => updateTSG(["mtm_target"], Number(e.target.value))}
                    style={{ maxWidth: 110 }} />
                </Field>
                <Field label="IV SL Δ pts" helper="RELATIVE IV breaker: a LOSING short whose strike IV rises this many vol points above its OWN entry IV exits with its hedge (one-shot/day; winners never IV-closed). Validated: 4.">
                  <Input type="number" value={tsgConfig.iv_sl_delta_pts}
                    onChange={(e) => updateTSG(["iv_sl_delta_pts"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="IV SL % (absolute)" helper="Legacy absolute level; 0 = off. Only used when Δ pts is 0.">
                  <Input type="number" value={tsgConfig.iv_sl_pct}
                    onChange={(e) => updateTSG(["iv_sl_pct"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Min Entry IV (0 = off)" helper="IV13/LD11 ENTRY-IV FLOOR: at 09:16, solve both shorts' IVs from the live chain BEFORE placing anything; if the mean is below this decimal, skip the whole day (panel shows the rejection + alert fires). Validated 0.10: +5.9% net at unchanged drawdown, walk-forward pass — skips 'dead' low-vol days where premium-capped strikes sit too close to spot. Unsolvable IVs → enters anyway (fail-open).">
                  <Input type="number" step="0.01" value={tsgConfig.min_entry_iv}
                    onChange={(e) => updateTSG(["min_entry_iv"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
              </Group>
              <Group title="Legs (premium caps)">
                {tsgConfig.legs.map((l, i) => (
                  <Field key={l.id} label={`${l.id} ${l.action} ${l.opt_type} — premium ≤`}
                    helper={l.action === "SELL"
                      ? "Short: highest premium ≤ cap. No candidate → day skipped."
                      : "Wing: highest premium ≤ cap. No candidate → wing absent (short exits alone on IV)."}>
                    <Input type="number" value={l.premium_max}
                      onChange={(e) => updateTSG(["legs", i, "premium_max"], Number(e.target.value))}
                      style={{ maxWidth: 90 }} />
                  </Field>
                ))}
              </Group>
              {/* ── TSG_V1 END ── */}
            </>);

      // ── IC_SPLIT ── ONE body for BOTH instances. Local aliases bind the
      // shared markup to this sid's config + updaters; V2-only controls
      // (exit mode / carry times / adjustments) are gated on icSid below.
      case "IC_V1":
      case "IC_V2": {
        const icSid = id;
        const icV1Config = icConfigs[icSid];
        const updateICV1    = (path, v)        => updateIC(icSid, path, v);
        const updateICLeg   = (idx, k, v)      => updateICLegFor(icSid, idx, k, v);
        const updateICAdjust = (side, k, v)    => updateICAdjustFor(icSid, side, k, v);
        return (<>
              <Group title="Execution">
                <Field label="Mode" helper="OFF = no entry · PAPER = simulated · LIVE = real orders. Ships OFF.">
                  <ModeToggle value={icV1Config.trade_execution_mode}
                    onChange={(v) => updateICV1(["trade_execution_mode"], v)}
                    modes={["OFF", "PAPER", "LIVE"]} />
                </Field>
                <Field label="Entry Time" helper="Strikes picked + all 4 legs entered at this instant (IST). One entry/day.">
                  <Input value={icV1Config.entry_time}
                    onChange={(e) => updateICV1(["entry_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                {icSid === "IC_V2" ? (<>
                <Field label="Exit Mode"
                  helper="NEXT_OPEN (validated): legs carry ONE night, mandatory close at next-open time; only same-day-expiry entries square off intraday. EOD: legacy daily square-off at Exit Time.">
                  <Select value={icV1Config.exit_mode || "NEXT_OPEN"}
                    onChange={(e) => updateICV1(["exit_mode"], e.target.value)}
                    style={{ maxWidth: 140 }}>
                    <option value="NEXT_OPEN">NEXT_OPEN (carry)</option>
                    <option value="EOD">EOD (legacy)</option>
                  </Select>
                </Field>
                {(icV1Config.exit_mode || "NEXT_OPEN") === "NEXT_OPEN" ? (<>
                  <Field label="Next-Open Close" indent
                    helper="Mandatory close of carried legs at this instant next session (IST). First-candle rule: NO exits 09:15–09:16 — overnight GTTs are removed pre-market and this market close is the sole exit.">
                    <Input value={icV1Config.next_open_time || "09:16"}
                      onChange={(e) => updateICV1(["next_open_time"], e.target.value)}
                      style={{ maxWidth: 90 }} />
                  </Field>
                  <Field label="Expiry-Day Exit" indent
                    helper="Square-off ONLY for legs entered TODAY whose expiry is TODAY. Legs carried into expiry already closed at next-open.">
                    <Input value={icV1Config.expiry_exit_time || "15:28"}
                      onChange={(e) => updateICV1(["expiry_exit_time"], e.target.value)}
                      style={{ maxWidth: 90 }} />
                  </Field>
                </>) : (
                  <Field label="Exit Time" indent helper="Legacy EOD square-off for anything still open">
                    <Input value={icV1Config.exit_time}
                      onChange={(e) => updateICV1(["exit_time"], e.target.value)}
                      style={{ maxWidth: 90 }} />
                  </Field>
                )}
                </>) : (
                  /* ── IC_SPLIT ── IC_V1 is EOD by definition: no exit-mode
                     switch, no carry times. exit_mode stays "EOD" in config. */
                  <Field label="Exit Time"
                    helper="Full square-off of all open legs at this instant (IST). IC_V1 never carries overnight.">
                    <Input value={icV1Config.exit_time}
                      onChange={(e) => updateICV1(["exit_time"], e.target.value)}
                      style={{ maxWidth: 90 }} />
                  </Field>
                )}
              </Group>

              {/* ── IC_V2 ── ADJ_ON_MTC adjustment block (V2 only) ── */}
              {icSid === "IC_V2" && (
              <Group title="Adjustment (on short stop exit)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  A short leg's stop exit — SL <em>or</em> Move-To-Cost scratch — arms a BUY
                  on the same option side, opening after the delay. Strike = highest premium
                  ≤ cap from a fresh chain snapshot at that moment; no eligible strike →
                  adjustment dropped (fail closed). Activations past 15:29 are dropped.
                </div>
                <Field label="Adjust on Stop" helper="Master switch for the adjustment machinery">
                  <input type="checkbox" checked={!!icV1Config.adjust_on_sl}
                    onChange={(e) => updateICV1(["adjust_on_sl"], e.target.checked)} />
                </Field>
                <Field label="Delay (s)" helper="Stop-exit → adjustment entry gap. 60 = next minute (backtest-validated)." indent>
                  <Input type="number" min="0" disabled={!icV1Config.adjust_on_sl}
                    value={icV1Config.adjust_delay_s ?? 60}
                    onChange={(e) => updateICV1(["adjust_delay_s"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="ADJ-Only Execution"
                  helper="Condor runs as a SIMULATION (no orders, no GTTs, no bookings — SLs/MTC/arming all simulated); ONLY adjustment legs trade for real per Mode. Requires Adjust on Stop." indent>
                  <input type="checkbox" checked={!!icV1Config.adjust_only}
                    disabled={!icV1Config.adjust_on_sl}
                    onChange={(e) => updateICV1(["adjust_only"], e.target.checked)} />
                </Field>
                <div style={{ display: "grid", gridTemplateColumns: "72px 62px 1fr 1fr 1fr 1fr", gap: 6, alignItems: "center", fontSize: 11, opacity: icV1Config.adjust_on_sl ? 1 : 0.45 }}>
                  <span style={{ color: colors.text.muted }}>Source</span>
                  <span style={{ color: colors.text.muted }}>On</span>
                  <span style={{ color: colors.text.muted }}>Lots</span>
                  <span style={{ color: colors.text.muted }}>Prem ≤ ₹</span>
                  <span style={{ color: colors.text.muted }}>SL</span>
                  <span style={{ color: colors.text.muted }}>TP</span>
                  {["L1", "L2"].map((side) => {
                    const a = (icV1Config.adjust || {})[side] || {};
                    const dis = !icV1Config.adjust_on_sl || !a.enabled;
                    return (<Fragment key={side}>
                      <span style={{ fontWeight: 700, color: colors.text.primary }}>
                        {side}·ADJ <span style={{ color: "#10b981", fontWeight: 600 }}>B·{side === "L1" ? "CE" : "PE"}</span>
                      </span>
                      <input type="checkbox" checked={!!a.enabled}
                        disabled={!icV1Config.adjust_on_sl}
                        onChange={(e) => updateICAdjust(side, "enabled", e.target.checked)} />
                      <Input type="number" min="0" disabled={dis} value={a.lots ?? 24}
                        onChange={(e) => updateICAdjust(side, "lots", Math.max(0, Number(e.target.value)))} />
                      <Input type="number" min="0" step="0.5" disabled={dis} value={a.premium_max ?? 85}
                        onChange={(e) => updateICAdjust(side, "premium_max", Math.max(0, Number(e.target.value)))} />
                      <div style={{ display: "flex", gap: 3 }}>
                        <Input type="number" min="0" disabled={dis} value={a.sl_val ?? 25}
                          onChange={(e) => updateICAdjust(side, "sl_val", Math.max(0, Number(e.target.value)))} />
                        <Select value={a.sl_mode || "pct"} disabled={dis}
                          onChange={(e) => updateICAdjust(side, "sl_mode", e.target.value)} style={{ width: 58 }}>
                          <option value="pct">%</option><option value="pts">pts</option>
                        </Select>
                      </div>
                      <div style={{ display: "flex", gap: 3 }}>
                        <Input type="number" min="0" disabled={dis} value={a.tp_val ?? 0}
                          onChange={(e) => updateICAdjust(side, "tp_val", Math.max(0, Number(e.target.value)))} />
                        <Select value={a.tp_mode || "pct"} disabled={dis}
                          onChange={(e) => updateICAdjust(side, "tp_mode", e.target.value)} style={{ width: 58 }}>
                          <option value="pct">%</option><option value="pts">pts</option>
                        </Select>
                      </div>
                    </Fragment>);
                  })}
                </div>
              </Group>
              )}

              <Group title="Legs (L1/L2 short · L3/L4 wings)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Strike = highest premium ≤ cap at entry. Shorts fail CLOSED (no strike → day
                  skipped); wings fall back to the cheapest available. SL 42% on shorts = the
                  Move-To-Cost trigger: one short stopping out re-pins the other to its own
                  entry. Lots 0 disables a leg (0 on L3/L4 = pure short strangle).
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "56px 62px 1fr 1fr 1fr 1fr", gap: 6, alignItems: "center", fontSize: 11 }}>
                  <span style={{ color: colors.text.muted }}>Leg</span>
                  <span style={{ color: colors.text.muted }}>Side</span>
                  <span style={{ color: colors.text.muted }}>Lots</span>
                  <span style={{ color: colors.text.muted }}>Prem ≤ ₹</span>
                  <span style={{ color: colors.text.muted }}>SL</span>
                  <span style={{ color: colors.text.muted }}>TP</span>
                  {icV1Config.legs.map((leg, i) => (<Fragment key={leg.id}>
                    <span style={{ fontWeight: 700, color: colors.text.primary }}>{leg.id}</span>
                    <span style={{ color: leg.action === "SELL" ? "#ef4444" : "#10b981", fontWeight: 600 }}>
                      {leg.action === "SELL" ? "S" : "B"}·{leg.opt_type}
                    </span>
                    <Input type="number" min="0" value={leg.lots}
                      onChange={(e) => updateICLeg(i, "lots", Math.max(0, Number(e.target.value)))} />
                    <Input type="number" min="0" step="0.5" value={leg.premium_max}
                      onChange={(e) => updateICLeg(i, "premium_max", Math.max(0, Number(e.target.value)))} />
                    <div style={{ display: "flex", gap: 3 }}>
                      <Input type="number" min="0" value={leg.sl_val}
                        onChange={(e) => updateICLeg(i, "sl_val", Math.max(0, Number(e.target.value)))} />
                      <Select value={leg.sl_mode} onChange={(e) => updateICLeg(i, "sl_mode", e.target.value)} style={{ width: 58 }}>
                        <option value="pct">%</option><option value="pts">pts</option>
                      </Select>
                    </div>
                    <div style={{ display: "flex", gap: 3 }}>
                      <Input type="number" min="0" value={leg.tp_val}
                        onChange={(e) => updateICLeg(i, "tp_val", Math.max(0, Number(e.target.value)))} />
                      <Select value={leg.tp_mode} onChange={(e) => updateICLeg(i, "tp_mode", e.target.value)} style={{ width: 58 }}>
                        <option value="pct">%</option><option value="pts">pts</option>
                      </Select>
                    </div>
                  </Fragment>))}
                </div>
              </Group>

              <Group title="Sizing & Ops">
                <Field label="Lot Size" helper="NIFTY lot size (65). Update here on an NSE revision — never hardcoded.">
                  <Input type="number" min="1" value={icV1Config.quantity.lot_size}
                    onChange={(e) => updateICV1(["quantity", "lot_size"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="Freeze Qty" helper="NSE per-order freeze limit (NIFTY 1800, Mar-2026). Orders above floor(freeze/lot)×lot are sliced.">
                  <Input type="number" min="1" value={icV1Config.freeze_qty}
                    onChange={(e) => updateICV1(["freeze_qty"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="Margin Guard" helper="Basket-margin check before entry. Confirmed shortfall blocks the day; API errors fail OPEN (advisory).">
                  <input type="checkbox" checked={!!icV1Config.margin_guard}
                    onChange={(e) => updateICV1(["margin_guard"], e.target.checked)} />
                </Field>
                <Field label="Allow Strangle Degrade" helper="If NO wing strike exists at entry: ON = enter as short strangle · OFF = skip the day (default)">
                  <input type="checkbox" checked={!!icV1Config.allow_strangle_degrade}
                    onChange={(e) => updateICV1(["allow_strangle_degrade"], e.target.checked)} />
                </Field>
                <Field label="Late-Entry Grace (s)" helper="App waking later than this past entry time skips the day">
                  <Input type="number" min="0" value={icV1Config.entry_late_grace_s}
                    onChange={(e) => updateICV1(["entry_late_grace_s"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
                {/* ── IC_V2 ── gap defence layer 1 */}
                <Field label="GTT Limit Buffer %"
                  helper="SL-GTT buy-back limit sits this % past the trigger. 5% default — the old 0.3% rests off-market on any fast move (the GTT monitor's market-out escalation is layer 2).">
                  <Input type="number" min="0" step="0.5" value={icV1Config.gtt_limit_buffer_pct ?? 5}
                    onChange={(e) => updateICV1(["gtt_limit_buffer_pct"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
              </Group>
        </>);
      }

      case "SCALP_V5": return (<>
              <Group title="Execution">
                <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                  <ModeToggle value={scalpV5Config.trade_execution_mode} onChange={(v) => updateScalpV5(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Trade Side" helper="Which option sides to trade">
                  <SideToggle value={scalpV5Config.trade_side_mode} onChange={(v) => updateScalpV5(["trade_side_mode"], v)} />
                </Field>
              </Group>
 
              <Group title="Risk Management">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Fixed-point stop-loss and target on the bought option. 0 disables that
                  leg — with both at 0 the trade runs purely to its EMA exit (candle closes
                  below EMA20_HIGH).
                </div>
                <Field label="Stop Loss (points)" helper="SL = entry − points. 0 = disabled">
                  <Input type="number" min="0" value={scalpV5Config.sl_points}
                    onChange={(e) => updateScalpV5(["sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Take Profit (points)" helper="TP = entry + points. 0 = disabled">
                  <Input type="number" min="0" value={scalpV5Config.tp_points}
                    onChange={(e) => updateScalpV5(["tp_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>
 
              <Group title="Option Premium Filter">
                <Field label="Minimum Premium" helper="Skip options below this price">
                  <Input type="number" min="0" value={scalpV5Config.option_premium.min}
                    onChange={(e) => updateScalpV5(["option_premium", "min"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Maximum Premium" helper="Skip options above this price">
                  <Input type="number" min="0" value={scalpV5Config.option_premium.max}
                    onChange={(e) => updateScalpV5(["option_premium", "max"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>
 
              <Group title="Risk Limits (Daily)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Daily P&amp;L limits (realised + open MTM). When hit, the open trade is
                  squared off and no new entries are taken for the rest of the day. 0 = disabled.
                </div>
                <Field label="Max Loss (₹)" helper="Square off + block entries after losing this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV5Config.max_loss}
                    onChange={(e) => updateScalpV5(["max_loss"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
                <Field label="Max Profit (₹)" helper="Square off + block entries after gaining this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV5Config.max_profit}
                    onChange={(e) => updateScalpV5(["max_profit"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
              </Group>
 
              <Group title="Order Quantity">
                <Field label="Number of Lots" helper={`1 lot = ${scalpV5Config.quantity.lot_size} units`}>
                  <Input type="number" min="1" value={scalpV5Config.quantity.lots}
                    onChange={(e) => updateScalpV5(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>
 
              <Group title="Trading Sessions">
                <Field label="Primary Session" helper="Main trading window">
                  <TimeRange
                    startValue={scalpV5Config.session.primary.start}
                    endValue={scalpV5Config.session.primary.end}
                    onStartChange={(e) => updateScalpV5(["session", "primary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV5(["session", "primary", "end"],   e.target.value)} />
                </Field>
                <Field label="Secondary Session">
                  <Checkbox
                    checked={scalpV5Config.session.secondary.enabled}
                    onChange={(e) => updateScalpV5(["session", "secondary", "enabled"], e.target.checked)}
                    label="Enable secondary trading window" />
                </Field>
                <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                  <TimeRange
                    startValue={scalpV5Config.session.secondary.start}
                    endValue={scalpV5Config.session.secondary.end}
                    disabled={!scalpV5Config.session.secondary.enabled}
                    onStartChange={(e) => updateScalpV5(["session", "secondary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV5(["session", "secondary", "end"],   e.target.value)} />
                </Field>
              </Group>
            </>);

      case "APP": return <AppSettingsSection />;
      default:         return null;
    }
  }

  const activeMeta  = STRATEGY_META[primaryId] || {};
  const activeProps = detailProps[primaryId] || {};

  return (
    <div style={{
      padding: isMobile ? spacing.md : settingsSpacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      display: "flex",
      flexDirection: "column",
      gap: isMobile ? spacing.lg : settingsSpacing.xxl,
    }}>

      <div>
        <h1 style={{ margin: 0, fontSize: isMobile ? 22 : 26, fontWeight: 700, color: colors.text.primary }}>
          Strategy Settings
        </h1>
        <p style={{ margin: "5px 0 0", fontSize: 12, color: colors.text.muted }}>
          {isMobile ? "Pick a strategy to configure it." : "Select a strategy from the list to configure it."}
        </p>
      </div>

      {/* ── Master/detail shell ── */}
      <div style={{
        display: "flex",
        flexDirection: isMobile ? "column" : "row",
        gap: spacing.lg,
        alignItems: "stretch",
        minHeight: isMobile ? "auto" : 640,
      }}>

        {/* ── Sidebar rail ── */}
        {isMobile ? (
          <div style={{
            display: "flex", gap: spacing.sm, overflowX: "auto",
            paddingBottom: spacing.xs,
          }}>
            {RAIL.map((s) => {
              const active = primaryId === s.id;
              const isLive = s.mode === "LIVE";
              const isOff  = s.mode === "OFF";
              const ac     = STRATEGY_ACCENT[s.id] || colors.primary;
              return (
                <button key={s.id} onClick={() => setPrimaryId(s.id)}
                  style={{
                    flexShrink: 0,
                    display: "flex", alignItems: "center", gap: 6,
                    padding: "8px 14px", borderRadius: 8,
                    borderLeft: `3px solid ${ac}`,
                    border: `1px solid ${active ? `${ac}66` : colors.border.medium}`,
                    borderLeftWidth: 3,
                    borderLeftColor: ac,
                    background: active ? `${ac}1f` : colors.bg.tertiary,
                    color: active ? colors.text.primary : colors.text.muted,
                    fontSize: 12, fontWeight: 600, cursor: "pointer",
                    whiteSpace: "nowrap",
                  }}>
                  <span style={{
                    width: 7, height: 7, borderRadius: "50%",
                    background: s.mode == null ? "transparent" : isOff ? colors.text.muted : isLive ? colors.success : colors.primary,
                  }} />
                  {STRATEGY_META[s.id]?.name || s.id}
                </button>
              );
            })}
          </div>
        ) : (
          <div style={{
            flex: "0 0 230px",
            display: "flex", flexDirection: "column", gap: 4,
            padding: spacing.sm,
            background: colors.bg.secondary + "60",
            border: `1px solid ${colors.border.medium}`,
            borderRadius: 12,
            alignSelf: "flex-start",
            position: "sticky", top: spacing.lg,
          }}>
            <div style={{ ...label, padding: `${spacing.sm}px ${spacing.md}px 4px` }}>
              Strategies
            </div>
            {RAIL.map((s) => (
              <StrategyRailItem
                key={s.id}
                id={s.id}
                name={STRATEGY_META[s.id]?.name || s.id}
                mode={s.mode}
                accent={STRATEGY_ACCENT[s.id]}
                active={primaryId === s.id}
                dirty={false}
                onClick={() => setPrimaryId(s.id)}
              />
            ))}
          </div>
        )}

        {/* ── Detail pane ── */}
        {primaryId === "APP" ? (
          <div style={{
            background: colors.bg.secondary,
            border: `1px solid ${colors.border.medium}`,
            borderRadius: 12,
            padding: spacing.xl,
            height: "100%",
          }}>
            {renderDetailBody("APP")}
          </div>
        ) : (
          <DetailPane
            id={primaryId}
            name={activeMeta.name || primaryId}
            meta={activeMeta.sub || ""}
            mode={activeProps.mode}
            onSave={activeProps.onSave}
            saving={activeProps.saving}
            status={activeProps.status}
          >
            {renderDetailBody(primaryId)}
          </DetailPane>
        )}

      </div>
    </div>
  );
}
import { useEffect, useState } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";
import { colors, spacing, typography } from "../tokens";
import { useIsMobile } from "../hooks/useIsMobile";
import AppSettingsSection from "../components/AppSettingsSection";

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
  SCALP_V2: "#a855f7",
  SCALP_V3: "#ec4899",
  BB_V1:    colors.primary ?? "#3b82f6",
  BB_V2:    "#3b82f6",
  HA_V1:    "#14b8a6",
  APP:      colors.text.muted,
};

/* ─────────────────────────────────────────────
   Layout helpers
───────────────────────────────────────────── */

/* ─────────────────────────────────────────────
   Default configs
───────────────────────────────────────────── */

// CHANGE 1: target_override removed from DEFAULT_SCALP_CONFIG
// SCALP_V1 is now short selling — TP = prev red candle low (engine-computed)
const DEFAULT_SCALP_CONFIG = {
  trade_execution_mode: "LIVE",
  min_sl_points:     0,
  max_sl_points:     0,
  risk_reward_ratio: 1,
  max_loss:   0,
  max_profit: 0,
  session: {
    primary:   { start: "09:15", end: "15:30" },
    secondary: { enabled: false, start: "09:15", end: "15:30" },
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
  max_loss:   0,
  max_profit: 0,
  // Fixed target override — replaces R:R when enabled
  target_override:      { enabled: false, points: 0 },
  option_premium:       { min: 50, max: 300 },
  quantity:             { lots: 1, lot_size: 65 },
  max_trades_per_side:  10,
  trade_side_mode:      "BOTH",
  session: {
    primary:   { start: "09:15", end: "15:20" },
    secondary: { enabled: false, start: "09:15", end: "15:20" },
  },
};

// SCALP_V2 — V1 clone + 3-leg order split (SHORT). Matches backend
// DEFAULT_STRATEGY_CONFIGS["SCALP_V2"] shape exactly.
const DEFAULT_SCALP_V2_CONFIG = {
  trade_execution_mode: "PAPER",
  timeframe:            "1m",
  min_sl_points:        5,
  max_sl_points:        0,
  risk_reward_ratio:    1.0,
  max_loss:   0,
  max_profit: 0,
  option_premium: { min: 150, max: 200 },
  quantity: { leg1_lots: 5, leg2_lots: 5, leg3_lots: 5, lot_size: 65 },
  session: {
    primary:   { start: "09:15", end: "15:20" },
    secondary: { enabled: false, start: "10:00", end: "14:30" },
  },
  trade_side_mode: "BOTH",
};

// SCALP_V3 — TEST option-BUYING hedge clone of SCALP_V1. Matches backend
// DEFAULT_STRATEGY_CONFIGS["SCALP_V3"] shape exactly.
const DEFAULT_SCALP_V3_CONFIG = {
  trade_execution_mode: "PAPER",
  min_sl_points:        5,
  max_sl_points:        20,
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

   `modes` is configurable so a strategy can opt into an OFF mode without
   affecting any other strategy.  Default is the original two-mode set, so
   SCALP_V1 / BB_V1 / BB_V2 / SCALP_V2 are unchanged.  HA_V1 passes
   ["OFF", "PAPER", "LIVE"].

   OFF renders in a neutral/muted treatment (it neither trades live nor
   simulates — it just suppresses new entries while data keeps flowing).
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
  // OFF — neutral grey treatment; PAPER/LIVE rendering unchanged.
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
   StrategyPanel wrapper
───────────────────────────────────────────── */
/* ─────────────────────────────────────────────
   Strategy meta (rail + detail headers)
───────────────────────────────────────────── */

const STRATEGY_META = {
  SCALP_V1: { name: "Scalp",        sub: "Intraday CE/PE options scalp · Zerodha" },
  SCALP_V2: { name: "Scalp V2",      sub: "3-Leg order split · 1m · NIFTY · SHORT" },
  SCALP_V3: { name: "Scalp V3",      sub: "Buy-hedge test · signal CE/PE → buy opposite · 1m" },
  BB_V1:    { name: "BN BB Options", sub: "Bollinger Breakout · 3m · Zerodha" },
  BB_V2:    { name: "BB Options V2", sub: "Crossover-Pivot · ST(10,1.5) · R2→S3 · 3m" },
  HA_V1:    { name: "Heikin Ashi",   sub: "EMA20 Bounce · 1m HA · NIFTY Options" },
  APP:      { name: "App Settings",  sub: "Notifications · sounds · pop-ups" },
};

/* ─────────────────────────────────────────────
   Sidebar rail item — one selectable strategy row.

   The left accent bar + active tint use the strategy's
   own accent colour (matching the Dashboard StrategyHost),
   so the colour code is identical across both pages.
───────────────────────────────────────────── */

function StrategyRailItem({ id, name, mode, accent, active, dirty, onClick }) {
  const isLive = mode === "LIVE";
  const isOff  = mode === "OFF";
  // Status dot stays mode-driven (live = green, off = grey, paper = blue),
  // which matches the Dashboard rail dot semantics.
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
        // Accent-tinted surface when active (matches Dashboard active card)
        background: active ? `${ac}1f` : "transparent",
        cursor: "pointer",
        transition: "background 0.15s ease, border-color 0.15s ease",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = colors.bg.secondary + "80"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      {/* accent bar — full height of the row, strategy-coloured.
          Dim when inactive, solid when active (mirrors Dashboard card border-left). */}
      <span style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3, borderRadius: "2px 0 0 2px",
        background: ac, opacity: active ? 1 : 0.55,
        transition: "opacity 0.2s ease",
      }} />
      {/* live/paper/off status dot */}
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
   Detail pane — full-width config surface for the
   selected strategy. Replaces the old expand/collapse
   StrategyPanel; no compact state, always full width.
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

      {/* Scrollable config body — 2-column flow to reduce vertical scrolling.
          Each <Group> avoids breaking across columns. Collapses to 1 column
          on narrow panes via the container-query-ish min-width media rule. */}
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

export default function Settings() {
  const isMobile = useIsMobile();
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

  // ── SCALP_V2 ──────────────────────────────
  const [scalpV2Config, setScalpV2Config] = useState(null);
  const [scalpV2Status, setScalpV2Status] = useState("");
  const [scalpV2Saving, setScalpV2Saving] = useState(false);

  // ── SCALP_V3 ──────────────────────────────
  const [scalpV3Config, setScalpV3Config] = useState(null);
  const [scalpV3Status, setScalpV3Status] = useState("");
  const [scalpV3Saving, setScalpV3Saving] = useState(false);

  useEffect(() => { loadScalp(); loadBB(); loadBBV2(); loadHA(); loadScalpV2(); loadScalpV3(); }, []);

  // ── SCALP_V1 load / update / save ──────────
  async function loadScalp() {
    try {
      const d = await getStrategyConfig("SCALP_V1");
      setScalpConfig({
        ...DEFAULT_SCALP_CONFIG, ...d,
        trade_execution_mode: d?.trade_execution_mode || "LIVE",
        // CHANGE 2: target_override merge removed — not applicable to short selling
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
        // Merge nested objects so missing keys fall back to defaults
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

  // ── SCALP_V2 load / update / save ──────────
  async function loadScalpV2() {
    try {
      const d = await getStrategyConfig("SCALP_V2");
      setScalpV2Config({
        ...DEFAULT_SCALP_V2_CONFIG, ...d,
        option_premium: { ...DEFAULT_SCALP_V2_CONFIG.option_premium, ...d?.option_premium },
        quantity:       { ...DEFAULT_SCALP_V2_CONFIG.quantity,       ...d?.quantity       },
        session: {
          ...DEFAULT_SCALP_V2_CONFIG.session, ...d?.session,
          primary:   { ...DEFAULT_SCALP_V2_CONFIG.session.primary,   ...d?.session?.primary   },
          secondary: { ...DEFAULT_SCALP_V2_CONFIG.session.secondary, ...d?.session?.secondary },
        },
      });
    } catch { setScalpV2Config({ ...DEFAULT_SCALP_V2_CONFIG }); }
  }

  function updateScalpV2(path, value) {
    const u = structuredClone(scalpV2Config);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setScalpV2Config(u);
  }

  async function saveScalpV2() {
    setScalpV2Saving(true);
    try {
      await saveStrategyConfig("SCALP_V2", scalpV2Config);
      setScalpV2Status("success"); setTimeout(() => setScalpV2Status(""), 3000);
    } catch {
      setScalpV2Status("error");  setTimeout(() => setScalpV2Status(""), 3000);
    } finally { setScalpV2Saving(false); }
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

  // ── Loading guard ───────────────────────────
  if (!scalpConfig || !bbConfig || !bbV2Config || !haConfig || !scalpV2Config || !scalpV3Config) {
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

  // ── Rail metadata (id + live mode for status dot) ──
  const RAIL = [
    { id: "SCALP_V1", mode: scalpConfig.trade_execution_mode },
    { id: "SCALP_V2", mode: scalpV2Config.trade_execution_mode },
    { id: "SCALP_V3", mode: scalpV3Config.trade_execution_mode },
    { id: "BB_V1",    mode: bbConfig.trade_execution_mode },
    { id: "BB_V2",    mode: bbV2Config.trade_execution_mode },
    { id: "HA_V1",    mode: haConfig.trade_execution_mode },
    { id: "APP",      mode: null },  
  ];

  // ── Detail header props per strategy ──
  const detailProps = {
    SCALP_V1: { mode: scalpConfig.trade_execution_mode, onSave: saveScalp,   saving: scalpSaving,  status: scalpStatus },
    SCALP_V2: { mode: scalpV2Config.trade_execution_mode, onSave: saveScalpV2, saving: scalpV2Saving, status: scalpV2Status },
    SCALP_V3: { mode: scalpV3Config.trade_execution_mode, onSave: saveScalpV3, saving: scalpV3Saving, status: scalpV3Status },
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

            {/* CHANGE 3: Short selling info box added; Fixed Target Override
                and Target Points fields removed — not applicable to short selling.
                TP is always the previous red candle's low (engine-computed). */}
            <Group title="Risk Management">
              <div style={{
                marginBottom: spacing.md,
                padding: spacing.sm,
                background: "rgba(59,130,246,0.07)",
                border: "1px solid rgba(59,130,246,0.2)",
                borderRadius: 5,
                fontSize: 11,
                color: colors.text.muted,
                lineHeight: 1.6,
              }}>
                <strong style={{ color: colors.primary }}>Short Selling Mode</strong><br />
                <strong>Target (TP):</strong> Previous red candle's low — computed automatically by the engine.<br />
                <strong>Stop Loss (SL):</strong> Entry + (TP distance × R:R) — premium rising above this exits the trade.
              </div>
              <Field label="Min SL Points" helper="Minimum distance from entry to previous red candle low">
                <Input type="number" min="0" value={scalpConfig.min_sl_points}
                  onChange={(e) => updateScalp(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Max SL Points" helper="0 = disabled">
                <Input type="number" min="0" value={scalpConfig.max_sl_points}
                  onChange={(e) => updateScalp(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Risk / Reward" helper="SL = entry + (TP distance × this multiplier)">
                <Input type="number" step="0.1" min="0" value={scalpConfig.risk_reward_ratio}
                  onChange={(e) => updateScalp(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
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
                label="ST Exit Gap"
                helper="Exit when candle close is within this many points of SuperTrend. 0 = exact ST level."
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
            <div style={{
              columnSpan: "all",
              marginBottom: spacing.xl,
              padding: spacing.md,
              background: "rgba(20,184,166,0.08)",
              border: "1px solid rgba(20,184,166,0.25)",
              borderRadius: 6,
              fontSize: 12,
              color: "#94a3b8",
              lineHeight: 1.6,
            }}>
              <strong style={{ color: "#14b8a6" }}>BB V2 changes vs V1:</strong>
              <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
                <li>SuperTrend multiplier: <strong>1.5</strong> (was 2.0 — tighter trailing)</li>
                <li>Entry: pivot <strong>crossover</strong> (R2/R1/PP/S1/S2/S3)</li>
                <li>CE entry requires close to cross <em>above</em> any pivot</li>
                <li>PE entry requires close to cross <em>below</em> any pivot</li>
              </ul>
            </div>

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
                HA_V1 is the only strategy with an OFF mode.  OFF keeps data
                collection / candle / indicator formation running while
                suppressing NEW entries.  Any open trade still exits normally. */}
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated · OFF = collect data only, no new entries">
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
                  strategy keeps collecting ticks, building HA candles and computing
                  indicators, but takes <strong>no new entries</strong>. Any trade that
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
                  : "TP = entry ± (entry − SL) × R. Default 1:2"}
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

              <Field label="Max Trades / Side" helper="Daily ceiling per CE or PE side">
                <Input type="number" min="1" max="20" value={haConfig.max_trades_per_side}
                  onChange={(e) => updateHA(["max_trades_per_side"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
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

            {/* ── Strategy rules reminder ── */}
            <div style={{
              columnSpan: "all",
              marginTop: spacing.md,
              padding: spacing.md,
              background: "rgba(245,158,11,0.07)",
              border: "1px solid rgba(245,158,11,0.2)",
              borderRadius: 6,
              fontSize: 11,
              color: colors.text.muted,
              lineHeight: 1.7,
            }}>
              <strong style={{ color: "rgba(245,158,11,0.9)" }}>How it works</strong><br />
              1-minute Heikin Ashi candles on weekly NIFTY options (1 CE + 1 PE).<br />
              <strong>Entry:</strong> Candle touches <em>EMA20_Low</em> + reversal pattern (3 conditions).<br />
              <strong>SL:</strong> Last red HA candle low (CE) / last green HA candle high (PE) — evaluated on <em>candle close</em> only.<br />
              <strong>TP:</strong> Fixed target points (if override is on) or entry ± risk × R:R ratio.<br />
              Min SL Points and Max SL Points are not used by this strategy.
            </div>
          
</>);
      case "SCALP_V2": return (<>
              {/* ── Info box ── */}
              <div style={{
                columnSpan: "all", marginBottom: spacing.xl, padding: spacing.md,
                background: "rgba(168,85,247,0.08)", border: "1px solid rgba(168,85,247,0.25)",
                borderRadius: 6, fontSize: 12, color: "#94a3b8", lineHeight: 1.6,
              }}>
                <strong style={{ color: "#a855f7" }}>SCALP_V2 — 3-leg order split:</strong>
                <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
                  <li>Same signals as SCALP V1 (single premium range, same entry logic).</li>
                  <li>Each signal is split into <strong>3 legs</strong>: the signal strike, plus the <strong>+1</strong> and <strong>−1</strong> adjacent strikes.</li>
                  <li>Signal leg uses the signal's exact TP/SL; the ±1 legs use percentage-derived TP/SL off their own premium.</li>
                  <li><strong>All-or-nothing exit:</strong> the moment any leg hits its TP/SL, all 3 legs close.</li>
                  <li>One group at a time (a live group blocks new signals until it closes).</li>
                </ul>
              </div>

              <Group title="Execution">
                <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                  <ModeToggle value={scalpV2Config.trade_execution_mode} onChange={(v) => updateScalpV2(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Trade Side" helper="Which option sides to trade">
                  <SideToggle value={scalpV2Config.trade_side_mode} onChange={(v) => updateScalpV2(["trade_side_mode"], v)} />
                </Field>
              </Group>

              <Group title="Signal Entry (cloned from SCALP V1)">
                <div style={{
                  marginBottom: spacing.md, padding: spacing.sm,
                  background: "rgba(59,130,246,0.07)", border: "1px solid rgba(59,130,246,0.2)",
                  borderRadius: 5, fontSize: 11, color: colors.text.muted, lineHeight: 1.6,
                }}>
                  Signals are generated exactly like SCALP V1. The signal leg's TP is the
                  previous red candle low; SL = entry + (TP distance × R:R). The resulting
                  SL% / TP% are applied to the ±1 strike legs' own premium.
                </div>
                <Field label="Min SL Points" helper="Minimum distance from entry to prev red candle low">
                  <Input type="number" min="0" value={scalpV2Config.min_sl_points}
                    onChange={(e) => updateScalpV2(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Max SL Points" helper="0 = disabled">
                  <Input type="number" min="0" value={scalpV2Config.max_sl_points}
                    onChange={(e) => updateScalpV2(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Risk / Reward" helper="SL = entry + (TP distance × this multiplier)">
                  <Input type="number" step="0.1" min="0" value={scalpV2Config.risk_reward_ratio}
                    onChange={(e) => updateScalpV2(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Option Premium Filter">
                <Field label="Minimum Premium" helper="Skip options below this price">
                  <Input type="number" min="0" value={scalpV2Config.option_premium.min}
                    onChange={(e) => updateScalpV2(["option_premium", "min"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Maximum Premium" helper="Skip options above this price">
                  <Input type="number" min="0" value={scalpV2Config.option_premium.max}
                    onChange={(e) => updateScalpV2(["option_premium", "max"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Leg Sizing">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Lots per leg. Leg 1 = signal strike · Leg 2 = +1 strike · Leg 3 = −1 strike.
                  1 lot = {scalpV2Config.quantity.lot_size} units. A leg with 0 lots is skipped.
                </div>
                <Field label="Leg 1 Lots (signal)" helper="The strike that fired the signal">
                  <Input type="number" min="0" value={scalpV2Config.quantity.leg1_lots}
                    onChange={(e) => updateScalpV2(["quantity", "leg1_lots"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Leg 2 Lots (+1 strike)" helper="One strike above the signal">
                  <Input type="number" min="0" value={scalpV2Config.quantity.leg2_lots}
                    onChange={(e) => updateScalpV2(["quantity", "leg2_lots"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Leg 3 Lots (−1 strike)" helper="One strike below the signal">
                  <Input type="number" min="0" value={scalpV2Config.quantity.leg3_lots}
                    onChange={(e) => updateScalpV2(["quantity", "leg3_lots"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Lot Size" helper="Units per lot (NIFTY = 65)">
                  <Input type="number" min="1" value={scalpV2Config.quantity.lot_size}
                    onChange={(e) => updateScalpV2(["quantity", "lot_size"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>

              <Group title="Risk Limits (Daily)">
                <div style={{ marginBottom: spacing.sm, fontSize: 11, color: colors.text.muted, lineHeight: 1.5 }}>
                  Daily realised-P&L limits across all legs. When hit, no new groups are
                  entered for the rest of the day (open group runs to its own exit). 0 = disabled.
                </div>
                <Field label="Max Loss (₹)" helper="Stop new entries after losing this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV2Config.max_loss}
                    onChange={(e) => updateScalpV2(["max_loss"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
                <Field label="Max Profit (₹)" helper="Stop new entries after gaining this much today. 0 = off">
                  <Input type="number" min="0" value={scalpV2Config.max_profit}
                    onChange={(e) => updateScalpV2(["max_profit"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 140 }} />
                </Field>
              </Group>

              <Group title="Trading Sessions">
                <Field label="Primary Session" helper="Main trading window">
                  <TimeRange
                    startValue={scalpV2Config.session.primary.start}
                    endValue={scalpV2Config.session.primary.end}
                    onStartChange={(e) => updateScalpV2(["session", "primary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV2(["session", "primary", "end"],   e.target.value)} />
                </Field>
                <Field label="Secondary Session">
                  <Checkbox
                    checked={scalpV2Config.session.secondary.enabled}
                    onChange={(e) => updateScalpV2(["session", "secondary", "enabled"], e.target.checked)}
                    label="Enable secondary trading window" />
                </Field>
                <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                  <TimeRange
                    startValue={scalpV2Config.session.secondary.start}
                    endValue={scalpV2Config.session.secondary.end}
                    disabled={!scalpV2Config.session.secondary.enabled}
                    onStartChange={(e) => updateScalpV2(["session", "secondary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV2(["session", "secondary", "end"],   e.target.value)} />
                </Field>
              </Group>
            </>);

      case "SCALP_V3": return (<>
              <div style={{
                columnSpan: "all", marginBottom: spacing.xl, padding: spacing.md,
                background: "rgba(236,72,153,0.08)", border: "1px solid rgba(236,72,153,0.25)",
                borderRadius: 6, fontSize: 12, color: "#94a3b8", lineHeight: 1.6,
              }}>
                <strong style={{ color: "#ec4899" }}>SCALP_V3 — option-BUYING hedge (TEST):</strong>
                <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
                  <li>Same selection &amp; signals as SCALP V1 (2 CE + 2 PE in the premium range).</li>
                  <li>The contract that fires the signal (e.g. 24500CE) is <strong>tracked, never traded</strong>.</li>
                  <li>Instead V3 <strong>BUYS the highest-premium opposite-side option</strong> (e.g. 24450PE).</li>
                  <li>Hedge protected by an <strong>SL-only GTT</strong> at (buy price − Max SL Points).</li>
                  <li>Exit when the <strong>signal contract</strong> hits its own SL/TP, or the hedge's own SL fires.</li>
                  <li>One trade at a time. This is a TEST strategy — start in PAPER.</li>
                </ul>
              </div>

              <Group title="Execution">
                <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                  <ModeToggle value={scalpV3Config.trade_execution_mode} onChange={(v) => updateScalpV3(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Signal Side" helper="Which side may FIRE a signal (hedge is always the opposite)">
                  <SideToggle value={scalpV3Config.trade_side_mode} onChange={(v) => updateScalpV3(["trade_side_mode"], v)} />
                </Field>
              </Group>

              <Group title="Signal &amp; Hedge SL">
                <div style={{
                  marginBottom: spacing.md, padding: spacing.sm,
                  background: "rgba(59,130,246,0.07)", border: "1px solid rgba(59,130,246,0.2)",
                  borderRadius: 5, fontSize: 11, color: colors.text.muted, lineHeight: 1.6,
                }}>
                  Signal SL/TP are computed exactly like SCALP V1 on the SIGNAL contract.
                  <strong> Max SL Points</strong> does double duty: it caps the signal SL AND
                  sets the bought hedge's stop (hedge SL = buy price − Max SL Points).
                </div>
                <Field label="Min SL Points" helper="Minimum distance from entry to prev red candle low">
                  <Input type="number" min="0" value={scalpV3Config.min_sl_points}
                    onChange={(e) => updateScalpV3(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Max SL Points" helper="Hedge SL = buy price − this. Also caps the signal SL. (e.g. 20)">
                  <Input type="number" min="0" value={scalpV3Config.max_sl_points}
                    onChange={(e) => updateScalpV3(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Risk / Reward" helper="Signal SL = entry + (TP distance × this multiplier)">
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
          // Mobile: horizontal scroll of chips
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
                    // Accent-tinted active chip + accent left border (colour code parity with Dashboard)
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
          // Desktop: vertical rail
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
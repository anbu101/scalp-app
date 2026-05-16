import { useEffect, useState } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";
import { colors, spacing, typography } from "../tokens";
import { useIsMobile } from "../hooks/useIsMobile";

/* ─────────────────────────────────────────────
   Settings-specific token aliases
───────────────────────────────────────────── */

const settingsSpacing = { ...spacing, xxl: 28 };

const label = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

/* ─────────────────────────────────────────────
   Layout helpers
───────────────────────────────────────────── */

function getPanelStyle(isPrimary) {
  return {
    overflow:   "hidden",
    transition: "flex 0.28s ease, opacity 0.22s ease",
    minWidth:   0,
    flex:       isPrimary ? "7 1 0%" : "3 1 0%",
    cursor:     isPrimary ? "default" : "pointer",
    opacity:    isPrimary ? 1 : 0.85,
  };
}

/* ─────────────────────────────────────────────
   Default configs
───────────────────────────────────────────── */

const DEFAULT_SCALP_CONFIG = {
  trade_execution_mode: "LIVE",
  min_sl_points:     0,
  max_sl_points:     0,
  risk_reward_ratio: 1,
  target_override:   { enabled: false, points: 0 },
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
  st_exit_gap:          30,
};

const DEFAULT_HA_CONFIG = {
  trade_execution_mode: "PAPER",
  risk_reward_ratio:    2.0,
  option_premium:       { min: 50, max: 300 },
  quantity:             { lots: 1, lot_size: 65 },
  max_trades_per_side:  10,
  trade_side_mode:      "BOTH",
  session: {
    primary:   { start: "09:15", end: "15:20" },
    secondary: { enabled: false, start: "09:15", end: "15:20" },
  },
};

/* ─────────────────────────────────────────────
   Primitive input components
   (unchanged from your version)
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

function ModeToggle({ value, onChange }) {
  const isMobile = useIsMobile();
  return (
    <div style={{ display: "flex", width: isMobile ? "100%" : "auto", gap: 3, background: colors.bg.tertiary, padding: 3, borderRadius: 6, border: `1px solid ${colors.border.medium}` }}>
      {["PAPER", "LIVE"].map((m) => {
        const active = value === m;
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              flex: isMobile ? 1 : undefined,
              padding: isMobile ? "8px 14px" : "4px 14px",
              borderRadius: 4, border: "none",
              background: active ? (m === "LIVE" ? colors.success : colors.primary) : "transparent",
              color:      active ? "#fff" : colors.text.muted,
              fontSize: 11, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s ease",
              textTransform: "uppercase", letterSpacing: "0.3px",
            }}
          >
            {m === "LIVE" ? "🟢 Live" : "🧪 Paper"}
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
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 5,
      background: isLive ? "rgba(16,185,129,0.12)" : "rgba(59,130,246,0.12)",
      color:      isLive ? colors.success : colors.primary,
      border:     `1px solid ${isLive ? colors.success : colors.primary}25`,
      textTransform: "uppercase", letterSpacing: "0.3px",
    }}>
      {isLive ? "🟢 Live" : "🧪 Paper"}
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
   StrategyPanel wrapper  (unchanged)
───────────────────────────────────────────── */

function StrategyPanel({ id, name, meta, mode, onSave, saving, status, isPrimary, onBecomePrimary, children }) {
  return (
    <div
      onClick={!isPrimary ? onBecomePrimary : undefined}
      style={{
        background:   colors.bg.secondary,
        border:       `1px solid ${isPrimary ? colors.border.light : colors.border.medium}`,
        borderRadius: 10,
        overflow:     "hidden",
        boxShadow:    isPrimary ? "0 4px 12px rgba(0,0,0,0.3)" : "0 2px 6px rgba(0,0,0,0.2)",
        height:       "100%",
        display:      "flex",
        flexDirection: "column",
        transition:   "border-color 0.25s ease, box-shadow 0.25s ease",
        cursor:       isPrimary ? "default" : "pointer",
      }}
    >
      <div
        style={{
          padding:        `${spacing.md}px ${spacing.lg}px`,
          background:     colors.bg.tertiary,
          borderBottom:   `1px solid ${colors.border.medium}`,
          display:        "flex",
          alignItems:     "center",
          justifyContent: "space-between",
          flexShrink:     0,
          gap:            spacing.md,
          userSelect:     "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, minWidth: 0, overflow: "hidden" }}>
          {!isPrimary && (
            <span style={{ fontSize: 10, color: colors.text.muted, flexShrink: 0 }}>↗</span>
          )}
          <span style={{ ...label, background: colors.bg.primary, color: colors.text.muted, padding: "2px 7px", borderRadius: 4, border: `1px solid ${colors.border.medium}`, flexShrink: 0 }}>
            {id}
          </span>
          <span style={{ fontSize: 14, fontWeight: 600, color: colors.text.primary, flexShrink: 0 }}>
            {name}
          </span>
          {isPrimary && (
            <span style={{ fontSize: 11, color: colors.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {meta}
            </span>
          )}
        </div>
        <div
          style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <ModeChip mode={mode} />
          {isPrimary && <SaveButton onClick={onSave} saving={saving} status={status} />}
        </div>
      </div>

      {isPrimary ? (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            flex: 1, overflowY: "auto",
            padding: `${spacing.lg}px ${spacing.xl}px ${spacing.xl}px`,
          }}
        >
          {children}
        </div>
      ) : (
        <div style={{
          flex: 1, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: spacing.lg, padding: spacing.lg,
        }}>
          <div style={{
            writingMode: "vertical-rl",
            textOrientation: "mixed",
            transform: "rotate(180deg)",
            fontSize: 11, fontWeight: 600, color: colors.text.muted,
            letterSpacing: "1px", textTransform: "uppercase",
          }}>
            {name}
          </div>
          <div style={{ width: 1, flex: 1, background: colors.border.medium }} />
          <span style={{ fontSize: 10, color: colors.text.muted, textAlign: "center", lineHeight: 1.5 }}>
            Click to<br />configure
          </span>
        </div>
      )}
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

  // ── HA_V1 ─────────────────────────────────
  const [haConfig, setHAConfig] = useState(null);
  const [haStatus, setHAStatus] = useState("");
  const [haSaving, setHASaving] = useState(false);

  useEffect(() => { loadScalp(); loadBB(); loadHA(); }, []);

  // ── SCALP_V1 load / update / save ──────────
  async function loadScalp() {
    try {
      const d = await getStrategyConfig("SCALP_V1");
      setScalpConfig({
        ...DEFAULT_SCALP_CONFIG, ...d,
        trade_execution_mode: d?.trade_execution_mode || "LIVE",
        target_override: { ...DEFAULT_SCALP_CONFIG.target_override, ...d?.target_override },
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
      // Migrate old ce_lots / pe_lots → lots
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
        option_premium: { ...DEFAULT_HA_CONFIG.option_premium, ...d?.option_premium },
        quantity:       { ...DEFAULT_HA_CONFIG.quantity,       ...d?.quantity       },
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

  // ── Loading guard ───────────────────────────
  if (!scalpConfig || !bbConfig || !haConfig) {
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
          {isMobile ? "Tap a strategy to configure it." : "Click a strategy panel to configure it."}
        </p>
      </div>

      <div style={{
        display: "flex",
        flexDirection: isMobile ? "column" : "row",
        gap: spacing.lg,
        alignItems: "stretch",
        minHeight: isMobile ? "auto" : 600,
      }}>

        {/* ══ SCALP_V1 ══════════════════════════════════════ */}
        <div style={isMobile ? { width: "100%" } : getPanelStyle(primaryId === "SCALP_V1")}>
          <StrategyPanel
            id="SCALP_V1" name="Scalp"
            meta="Intraday CE/PE options scalp · Zerodha"
            mode={scalpConfig.trade_execution_mode}
            onSave={saveScalp} saving={scalpSaving} status={scalpStatus}
            isPrimary={primaryId === "SCALP_V1"}
            onBecomePrimary={() => setPrimaryId("SCALP_V1")}
          >
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                <ModeToggle value={scalpConfig.trade_execution_mode} onChange={(v) => updateScalp(["trade_execution_mode"], v)} />
              </Field>
            </Group>

            <Group title="Risk Management">
              <Field label="Min SL Points" helper="Minimum stop loss distance">
                <Input type="number" min="0" value={scalpConfig.min_sl_points}
                  onChange={(e) => updateScalp(["min_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Max SL Points" helper="0 = disabled">
                <Input type="number" min="0" value={scalpConfig.max_sl_points}
                  onChange={(e) => updateScalp(["max_sl_points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Risk / Reward" helper="Target = risk × this multiplier">
                <Input type="number" step="0.1" min="0" value={scalpConfig.risk_reward_ratio}
                  onChange={(e) => updateScalp(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Fixed Target Override">
                <Checkbox
                  checked={scalpConfig.target_override.enabled}
                  onChange={(e) => updateScalp(["target_override", "enabled"], e.target.checked)}
                  label="Use fixed target points instead of R:R" />
              </Field>
              <Field label="Target Points" helper="Active only when override is on" indent>
                <Input type="number" min="0" disabled={!scalpConfig.target_override.enabled}
                  value={scalpConfig.target_override.points}
                  onChange={(e) => updateScalp(["target_override", "points"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
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
          </StrategyPanel>
        </div>

        {/* ══ BB_V1 ═════════════════════════════════════════ */}
        <div style={isMobile ? { width: "100%" } : getPanelStyle(primaryId === "BB_V1")}>
          <StrategyPanel
            id="BB_V1" name="NIFTY BB Options"
            meta="Bollinger Breakout · 3m · Zerodha"
            mode={bbConfig.trade_execution_mode}
            onSave={saveBB} saving={bbSaving} status={bbStatus}
            isPrimary={primaryId === "BB_V1"}
            onBecomePrimary={() => setPrimaryId("BB_V1")}
          >
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
          </StrategyPanel>
        </div>

        {/* ══ HA_V1 ═════════════════════════════════════════ */}
        <div style={isMobile ? { width: "100%" } : getPanelStyle(primaryId === "HA_V1")}>
          <StrategyPanel
            id="HA_V1" name="Heikin Ashi"
            meta="EMA20 Bounce · 1m HA · NIFTY Options"
            mode={haConfig.trade_execution_mode}
            onSave={saveHA} saving={haSaving} status={haStatus}
            isPrimary={primaryId === "HA_V1"}
            onBecomePrimary={() => setPrimaryId("HA_V1")}
          >
            {/* ── Execution ── */}
            <Group title="Execution">
              <Field label="Mode" helper="LIVE = real orders · PAPER = simulated">
                <ModeToggle value={haConfig.trade_execution_mode} onChange={(v) => updateHA(["trade_execution_mode"], v)} />
              </Field>
              <Field label="Trade Side" helper="Which option sides to trade">
                <SideToggle value={haConfig.trade_side_mode} onChange={(v) => updateHA(["trade_side_mode"], v)} />
              </Field>
            </Group>

            {/* ── Risk Management ── */}
            <Group title="Risk Management">
              <Field
                label="Risk : Reward"
                helper="TP = entry ± (entry − SL) × R. Default 1:2"
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 12, color: colors.text.muted }}>1 :</span>
                  <Input
                    type="number" step="0.1" min="0.1"
                    value={haConfig.risk_reward_ratio}
                    onChange={(e) => updateHA(["risk_reward_ratio"], Math.max(0.1, Number(e.target.value)))}
                    style={{ maxWidth: 100 }}
                  />
                </div>
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
              <strong>TP:</strong> Entry ± risk × R:R ratio.<br />
              <strong>Note:</strong> Min SL Points, Max SL Points and fixed-point Target Override are not used by this strategy.
            </div>
          </StrategyPanel>
        </div>

      </div>
    </div>
  );
}
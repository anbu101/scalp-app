import { useEffect, useState } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";

/* ─────────────────────────────────────────────
   Tokens  (match dashboard / panel palette)
───────────────────────────────────────────── */

const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 28 };

const colors = {
  primary:      "#3b82f6",
  primaryHover: "#2563eb",
  success:      "#10b981",
  successBg:    "rgba(16, 185, 129, 0.12)",
  warning:      "#f59e0b",
  warningBg:    "rgba(245, 158, 11, 0.12)",
  bg: {
    primary:   "#020817",
    secondary: "#0f172a",
    tertiary:  "#1e293b",
    input:     "#060d1a",
  },
  border: { light: "#334155", medium: "#243044", dark: "#1a2540" },
  text:   { primary: "#f1f5f9", secondary: "#94a3b8", muted: "#4b6280" },
};

const label = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

/* ─────────────────────────────────────────────
   Layout helpers  — identical to StrategyHost
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
  sl_pct:               1.0,
  tp_pct:               2.0,
  max_premium:          200,
  max_trades_per_side:  2,
  ce_lots:              1,
  pe_lots:              1,
  auto_square_off_time: "15:15",
  session_start:        "09:15",
  session_end:          "15:15",
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

function Checkbox({ checked, onChange, label: lbl }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 7, cursor: "pointer", fontSize: 12, color: colors.text.secondary, userSelect: "none" }}>
      <input type="checkbox" checked={checked} onChange={onChange}
        style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0, cursor: "pointer" }} />
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
  return (
    <div style={{ display: "inline-flex", gap: 3, background: colors.bg.tertiary, padding: 3, borderRadius: 6, border: `1px solid ${colors.border.medium}` }}>
      {["PAPER", "LIVE"].map((m) => {
        const active = value === m;
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              padding: "4px 14px", borderRadius: 4, border: "none",
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

/* ─────────────────────────────────────────────
   Field — inline label (fixed width) + control
   Only shown when panel is primary/expanded.
───────────────────────────────────────────── */

const LABEL_W = 160;

function Field({ label: lbl, helper, children, indent }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: spacing.md,
      padding: "6px 0",
      paddingLeft: indent ? 20 : 0,
      borderBottom: `1px solid ${colors.border.dark}`,
    }}>
      <div style={{ flexShrink: 0, width: LABEL_W }}>
        <div style={{ fontSize: 12, color: colors.text.secondary, fontWeight: 500 }}>{lbl}</div>
        {helper && <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 1, lineHeight: 1.4 }}>{helper}</div>}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}

function Group({ title, children }) {
  return (
    <div style={{ marginBottom: spacing.xl }}>
      <div style={{ ...label, marginBottom: spacing.xs, paddingBottom: 4, borderBottom: `1px solid ${colors.border.medium}` }}>
        {title}
      </div>
      {children}
    </div>
  );
}

/* ─────────────────────────────────────────────
   Mode chip — compact badge shown in headers
───────────────────────────────────────────── */

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
   StrategyPanel
   Mirrors ScalpPanel / BBPanel structure:
   - isPrimary=true  → full settings form
   - isPrimary=false → compact summary strip
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

      {/* ── Header — always visible ─── */}
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
          {/* ↗ expand hint — only visible on compact panel */}
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

        {/* Right side — stop propagation so save click doesn't trigger promote */}
        <div
          style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <ModeChip mode={mode} />
          {isPrimary && <SaveButton onClick={onSave} saving={saving} status={status} />}
        </div>
      </div>

      {/* ── Body ────────────────────── */}
      {isPrimary ? (
        /* Full settings form */
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
        /* Compact strip — rotated label + a few key stats */
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

  const [primaryId, setPrimaryId] = useState("SCALP_V1");

  // ── SCALP_V1 ──────────────────────────────
  const [scalpConfig, setScalpConfig] = useState(null);
  const [scalpStatus, setScalpStatus] = useState("");
  const [scalpSaving, setScalpSaving] = useState(false);

  // ── BB_V1 ─────────────────────────────────
  const [bbConfig, setBBConfig] = useState(null);
  const [bbStatus, setBBStatus] = useState("");
  const [bbSaving, setBBSaving] = useState(false);

  useEffect(() => { loadScalp(); loadBB(); }, []);

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
      setBBConfig({ ...DEFAULT_BB_CONFIG, ...d });
    } catch { setBBConfig({ ...DEFAULT_BB_CONFIG }); }
  }

  function updateBB(path, value) {
    const u = structuredClone(bbConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setBBConfig(u);
  }

  async function saveBB() {
    setBBSaving(true);
    try {
      await saveStrategyConfig("BB_V1", bbConfig);
      setBBStatus("success"); setTimeout(() => setBBStatus(""), 3000);
    } catch {
      setBBStatus("error");  setTimeout(() => setBBStatus(""), 3000);
    } finally { setBBSaving(false); }
  }

  // ── Loading guard ───────────────────────────
  if (!scalpConfig || !bbConfig) {
    return (
      <div style={{ padding: spacing.xxl, background: colors.bg.primary, color: colors.text.primary, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 13, color: colors.text.muted }}>Loading settings…</span>
      </div>
    );
  }

  return (
    <div style={{
      padding: spacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      display: "flex",
      flexDirection: "column",
      gap: spacing.xxl,
    }}>

      {/* Page header */}
      <div>
        <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, color: colors.text.primary }}>
          Strategy Settings
        </h1>
        <p style={{ margin: "5px 0 0", fontSize: 12, color: colors.text.muted }}>
          Click a strategy panel to configure it.
        </p>
      </div>

      {/* ── Panel row — same flex mechanism as StrategyHost ── */}
      <div style={{
        display: "flex", flexDirection: "row",
        gap: spacing.lg, alignItems: "stretch",
        minHeight: 600,
      }}>

        {/* ══ SCALP_V1 ══════════════════════════════════════ */}
        <div style={getPanelStyle(primaryId === "SCALP_V1")}>
          <StrategyPanel
            id="SCALP_V1"
            name="Scalp"
            meta="Intraday CE/PE options scalp · Zerodha"
            mode={scalpConfig.trade_execution_mode}
            onSave={saveScalp}
            saving={scalpSaving}
            status={scalpStatus}
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
        <div style={getPanelStyle(primaryId === "BB_V1")}>
          <StrategyPanel
            id="BB_V1"
            name="NIFTY BB Options"
            meta="Bollinger Breakout · 3m · Zerodha"
            mode={bbConfig.trade_execution_mode}
            onSave={saveBB}
            saving={bbSaving}
            status={bbStatus}
            isPrimary={primaryId === "BB_V1"}
            onBecomePrimary={() => setPrimaryId("BB_V1")}
          >
            <Group title="Execution">
              <Field label="Mode" helper="Changes take effect on next trade cycle">
                <ModeToggle value={bbConfig.trade_execution_mode} onChange={(v) => updateBB(["trade_execution_mode"], v)} />
              </Field>
              <Field label="Session Start" helper="Strategy starts scanning after this time">
                <Input type="time" min="09:15" max="15:30" value={bbConfig.session_start}
                  onChange={(e) => updateBB(["session_start"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Session End" helper="No new entries after this time">
                <Input type="time" min="09:15" max="15:30" value={bbConfig.session_end}
                  onChange={(e) => updateBB(["session_end"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
              <Field label="Auto Square-Off" helper="All open positions closed at this time">
                <Input type="time" min="09:15" max="15:30" value={bbConfig.auto_square_off_time}
                  onChange={(e) => updateBB(["auto_square_off_time"], e.target.value)}
                  style={{ width: 108 }} />
              </Field>
            </Group>

            <Group title="Risk Parameters">
              <Field label="Stop Loss %" helper="% of entry price — 0 = disabled">
                <Input type="number" step="0.1" min="0" max="100" value={bbConfig.sl_pct}
                  onChange={(e) => updateBB(["sl_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Take Profit %" helper="% of entry price — 0 = disabled">
                <Input type="number" step="0.1" min="0" max="100" value={bbConfig.tp_pct}
                  onChange={(e) => updateBB(["tp_pct"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

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
              <Field label="CE Lots" helper="Lots per CE trade">
                <Input type="number" min="1" value={bbConfig.ce_lots}
                  onChange={(e) => updateBB(["ce_lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="PE Lots" helper="Lots per PE trade">
                <Input type="number" min="1" value={bbConfig.pe_lots}
                  onChange={(e) => updateBB(["pe_lots"], Math.max(1, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>
          </StrategyPanel>
        </div>

      </div>
    </div>
  );
}
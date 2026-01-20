import { useEffect, useState } from "react";
import {
  getStrategyConfig,
  saveStrategyConfig,
} from "../api";

/* -------------------------
   Design System Tokens
-------------------------- */

const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24
};

const typography = {
  displayLarge: { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  headingMedium: { fontSize: 14, fontWeight: 600, lineHeight: 1.4 },
  bodyMedium: { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall: { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label: { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: '0.5px', textTransform: 'uppercase' }
};

const colors = {
  primary: "#2563eb",
  primaryHover: "#1d4ed8",
  success: "#059669",
  successBg: "rgba(5, 150, 105, 0.12)",
  warning: "#d97706",
  warningBg: "rgba(217, 119, 6, 0.12)",
  bg: {
    primary: "#020817",
    secondary: "#0f172a",
    tertiary: "#1e293b",
  },
  border: {
    light: "#334155",
    dark: "#1e293b"
  },
  text: {
    primary: "#f8fafc",
    secondary: "#cbd5e1",
    tertiary: "#94a3b8",
    muted: "#64748b"
  }
};

/* -------------------------
   Default config shape
-------------------------- */

const DEFAULT_CONFIG = {
  min_sl_points: 0,
  max_sl_points: 0,
  risk_reward_ratio: 1,

  target_override: {
    enabled: false,
    points: 0,
  },

  session: {
    primary: {
      start: "09:15",
      end: "15:30",
    },
    secondary: {
      enabled: false,
      start: "09:15",
      end: "15:30",
    },
  },

  option_premium: {
    min: 0,
    max: 0,
  },

  quantity: {
    lots: 1,
    lot_size: 65,
  },
};

/* -------------------------
   Reusable UI Components
-------------------------- */

function Card({ children, style }) {
  return (
    <div
      style={{
        background: colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: "0 1px 3px rgba(0, 0, 0, 0.2)",
        ...style
      }}
    >
      {children}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <Card style={{ padding: spacing.sm, height: "100%" }}>
      <h3 style={{ 
        margin: 0, 
        marginBottom: spacing.sm, 
        ...typography.headingMedium,
        color: colors.text.primary,
        fontSize: 13,
        paddingBottom: spacing.xs,
        borderBottom: `1px solid ${colors.border.dark}`
      }}>
        {title}
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
        {children}
      </div>
    </Card>
  );
}

function Row({ label, children, helper }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <label style={{ 
        ...typography.bodySmall, 
        color: colors.text.secondary,
        fontWeight: 500,
        fontSize: 11
      }}>
        {label}
      </label>
      {children}
      {helper && (
        <div style={{ 
          ...typography.bodySmall, 
          color: colors.text.muted,
          fontSize: 10,
          fontStyle: "italic"
        }}>
          {helper}
        </div>
      )}
    </div>
  );
}

function Input({ type = "text", value, onChange, min, max, step, disabled, placeholder, style }) {
  return (
    <input
      type={type}
      value={value}
      onChange={onChange}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      placeholder={placeholder}
      style={{
        padding: "5px 8px",
        borderRadius: 4,
        border: `1px solid ${disabled ? colors.border.dark : colors.border.light}`,
        background: disabled ? colors.bg.tertiary : colors.bg.primary,
        color: disabled ? colors.text.muted : colors.text.primary,
        fontSize: 12,
        width: "100%",
        outline: "none",
        transition: "border-color 0.2s ease",
        ...style
      }}
      onFocus={(e) => !disabled && (e.target.style.borderColor = colors.primary)}
      onBlur={(e) => e.target.style.borderColor = colors.border.light}
    />
  );
}

function Checkbox({ checked, onChange, label }) {
  return (
    <label style={{ 
      display: "flex", 
      alignItems: "center", 
      gap: 6,
      cursor: "pointer",
      fontSize: 12,
      color: colors.text.secondary
    }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        style={{
          width: 14,
          height: 14,
          cursor: "pointer",
          accentColor: colors.primary
        }}
      />
      {label}
    </label>
  );
}

function TimeRange({ startValue, endValue, onStartChange, onEndChange, disabled }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Input
        type="time"
        value={startValue}
        onChange={onStartChange}
        disabled={disabled}
        style={{ width: "100px" }}
      />
      <span style={{ color: colors.text.muted, fontSize: 11 }}>to</span>
      <Input
        type="time"
        value={endValue}
        onChange={onEndChange}
        disabled={disabled}
        style={{ width: "100px" }}
      />
    </div>
  );
}

function StatusBadge({ type, text }) {
  const styles = {
    success: { bg: colors.successBg, color: colors.success },
    warning: { bg: colors.warningBg, color: colors.warning }
  };

  const style = styles[type] || styles.success;

  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      gap: spacing.xs,
      padding: "4px 10px",
      borderRadius: 6,
      background: style.bg,
      color: style.color,
      border: `1px solid ${style.color}40`,
      fontSize: 11,
      fontWeight: 600
    }}>
      {text}
    </span>
  );
}

/* -------------------------
   Settings Page
-------------------------- */

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    const data = await getStrategyConfig();

    setConfig({
      ...DEFAULT_CONFIG,
      ...data,

      trade_execution_mode: data?.trade_execution_mode || "LIVE",

      target_override: {
        ...DEFAULT_CONFIG.target_override,
        ...data?.target_override,
      },

      session: {
        ...DEFAULT_CONFIG.session,
        ...data?.session,
        primary: {
          ...DEFAULT_CONFIG.session.primary,
          ...data?.session?.primary,
        },
        secondary: {
          ...DEFAULT_CONFIG.session.secondary,
          ...data?.session?.secondary,
        },
      },

      option_premium: {
        ...DEFAULT_CONFIG.option_premium,
        ...data?.option_premium,
      },

      quantity: {
        ...DEFAULT_CONFIG.quantity,
        ...data?.quantity,
      },
    });
  }

  function update(path, value) {
    const updated = structuredClone(config);
    path.reduce((obj, key, i) => {
      if (i === path.length - 1) obj[key] = value;
      return obj[key];
    }, updated);
    setConfig(updated);
  }

  async function save() {
    setSaving(true);
    try {
      await saveStrategyConfig(config);
      setStatus("success");
      setTimeout(() => setStatus(""), 3000);
    } catch (error) {
      setStatus("error");
      setTimeout(() => setStatus(""), 3000);
    } finally {
      setSaving(false);
    }
  }

  if (!config) {
    return (
      <div style={{ 
        padding: spacing.xxl, 
        background: colors.bg.primary,
        color: colors.text.primary,
        minHeight: "100vh"
      }}>
        <div style={{ ...typography.bodyMedium }}>Loading settings…</div>
      </div>
    );
  }

  console.log('Config loaded:', config); // Debug log

  return (
    <div
      style={{
        padding: spacing.xxl,
        background: colors.bg.primary,
        color: colors.text.primary,
        minHeight: "100vh",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
            Strategy Settings
          </h1>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md }}>
            {status === "success" && (
              <StatusBadge type="success" text="✓ Saved" />
            )}
            {status === "error" && (
              <StatusBadge type="warning" text="✗ Failed" />
            )}
            <button
              onClick={save}
              disabled={saving}
              style={{
                padding: "8px 16px",
                borderRadius: 6,
                border: "none",
                background: saving ? colors.bg.tertiary : colors.primary,
                color: colors.text.primary,
                fontSize: 13,
                fontWeight: 600,
                cursor: saving ? "not-allowed" : "pointer",
                transition: "all 0.2s ease",
                boxShadow: "0 2px 4px rgba(0, 0, 0, 0.2)"
              }}
              onMouseEnter={(e) => !saving && (e.target.style.background = colors.primaryHover)}
              onMouseLeave={(e) => !saving && (e.target.style.background = colors.primary)}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>

      {/* Two Column Layout */}
      <div style={{ 
        display: "grid", 
        gridTemplateColumns: "1fr 1fr", 
        gap: spacing.md
      }}>
        {/* Left Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
          {/* Risk Management */}
          <Section title="Risk Management">
            <Row label="Min SL Points" helper="Minimum stop loss distance">
              <Input
                type="number"
                min="0"
                value={config.min_sl_points}
                onChange={(e) =>
                  update(["min_sl_points"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>

            <Row label="Max SL Points" helper="Maximum SL cap (0 = disabled)">
              <Input
                type="number"
                min="0"
                value={config.max_sl_points}
                onChange={(e) =>
                  update(["max_sl_points"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>

            <Row label="Risk/Reward Ratio" helper="Target profit as multiple of risk">
              <Input
                type="number"
                step="0.1"
                min="0"
                value={config.risk_reward_ratio}
                onChange={(e) =>
                  update(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>

            <div style={{ 
              borderTop: `1px solid ${colors.border.dark}`, 
              marginTop: spacing.xs,
              paddingTop: spacing.sm
            }}>
              <Row label="Target Override">
                <Checkbox
                  checked={config.target_override.enabled}
                  onChange={(e) =>
                    update(["target_override", "enabled"], e.target.checked)
                  }
                  label="Use fixed target points"
                />
              </Row>

              <Row label="Target Points" helper="Fixed profit target">
                <Input
                  type="number"
                  min="0"
                  disabled={!config.target_override.enabled}
                  value={config.target_override.points}
                  onChange={(e) =>
                    update(["target_override", "points"], Math.max(0, Number(e.target.value)))
                  }
                />
              </Row>
            </div>
          </Section>

          {/* Option Premium */}
          <Section title="Option Premium">
            <Row label="Minimum Premium" helper="Min option price">
              <Input
                type="number"
                min="0"
                value={config.option_premium.min}
                onChange={(e) =>
                  update(["option_premium", "min"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>

            <Row label="Maximum Premium" helper="Max option price">
              <Input
                type="number"
                min="0"
                value={config.option_premium.max}
                onChange={(e) =>
                  update(["option_premium", "max"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>
          </Section>
        </div>

        {/* Right Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
          {/* Trade Execution Mode */}
          <Section title="Trade Execution">
            <Row
              label="Execution Mode"
              helper="LIVE = real orders | PAPER = simulated only"
            >
              <select
                value={config.trade_execution_mode}
                onChange={(e) =>
                  update(["trade_execution_mode"], e.target.value)
                }
                style={{
                  padding: "6px 8px",
                  borderRadius: 4,
                  border: `1px solid ${colors.border.light}`,
                  background: colors.bg.primary,
                  color: colors.text.primary,
                  fontSize: 12,
                  width: "100%",
                }}
              >
                <option value="LIVE">LIVE</option>
                <option value="PAPER">PAPER</option>
              </select>
            </Row>
          </Section>

          {/* Trading Sessions */}
          <Section title="Trading Sessions">
            <Row label="Primary Session" helper="Main trading window">
              <TimeRange
                startValue={config.session.primary.start}
                endValue={config.session.primary.end}
                onStartChange={(e) =>
                  update(["session", "primary", "start"], e.target.value)
                }
                onEndChange={(e) =>
                  update(["session", "primary", "end"], e.target.value)
                }
              />
            </Row>

            <Row label="Secondary Session">
              <Checkbox
                checked={config.session.secondary.enabled}
                onChange={(e) =>
                  update(["session", "secondary", "enabled"], e.target.checked)
                }
                label="Enable secondary window"
              />
            </Row>

            <Row label="Secondary Times" helper="Additional trading window">
              <TimeRange
                startValue={config.session.secondary.start}
                endValue={config.session.secondary.end}
                disabled={!config.session.secondary.enabled}
                onStartChange={(e) =>
                  update(["session", "secondary", "start"], e.target.value)
                }
                onEndChange={(e) =>
                  update(["session", "secondary", "end"], e.target.value)
                }
              />
            </Row>
          </Section>

          {/* Order Quantity */}
          <Section title="Order Quantity">
            <Row label="Lots" helper={`1 lot = ${config.quantity.lot_size} units`}>
              <Input
                type="number"
                min="0"
                value={config.quantity.lots}
                onChange={(e) =>
                  update(["quantity", "lots"], Math.max(0, Number(e.target.value)))
                }
              />
            </Row>
          </Section>
        </div>
      </div>
    </div>
  );
}
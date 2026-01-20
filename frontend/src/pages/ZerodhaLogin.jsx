import { useEffect, useState } from "react";
import {
  getZerodhaStatus,
  getZerodhaLoginUrl,
  getZerodhaConfig,
  saveZerodhaConfig,
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
  headingLarge: { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge: { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium: { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall: { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label: { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: '0.5px', textTransform: 'uppercase' }
};

const colors = {
  primary: "#3b82f6",
  primaryHover: "#2563eb",
  success: "#10b981",
  successBg: "rgba(16, 185, 129, 0.15)",
  warning: "#f59e0b",
  warningBg: "rgba(245, 158, 11, 0.15)",
  danger: "#ef4444",
  dangerBg: "rgba(239, 68, 68, 0.15)",
  bg: {
    primary: "#0a0f1e",
    secondary: "#111827",
    tertiary: "#1f2937",
  },
  border: {
    light: "#374151",
    dark: "#1f2937"
  },
  text: {
    primary: "#f9fafb",
    secondary: "#d1d5db",
    tertiary: "#9ca3af",
    muted: "#6b7280"
  }
};

/* -------------------------
   UI Components
-------------------------- */

function Card({ children, style }) {
  return (
    <div
      style={{
        background: colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: "0 1px 3px rgba(0, 0, 0, 0.2)",
        padding: spacing.lg,
        ...style
      }}
    >
      {children}
    </div>
  );
}

function StatusBadge({ type, text, icon }) {
  const styles = {
    success: { bg: colors.successBg, color: colors.success, border: colors.success },
    warning: { bg: colors.warningBg, color: colors.warning, border: colors.warning },
    danger: { bg: colors.dangerBg, color: colors.danger, border: colors.danger }
  };

  const style = styles[type] || styles.success;

  return (
    <div style={{
      display: "inline-flex",
      alignItems: "center",
      gap: spacing.sm,
      padding: "8px 16px",
      borderRadius: 6,
      background: style.bg,
      color: style.color,
      border: `1px solid ${style.border}40`,
      ...typography.bodyMedium,
      fontWeight: 600
    }}>
      {icon && <span style={{ fontSize: 16 }}>{icon}</span>}
      {text}
    </div>
  );
}

function Input({ type = "text", value, onChange, placeholder, disabled, style }) {
  return (
    <input
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
      style={{
        padding: "10px 12px",
        borderRadius: 6,
        border: `1px solid ${disabled ? colors.border.dark : colors.border.light}`,
        background: disabled ? colors.bg.tertiary : colors.bg.primary,
        color: disabled ? colors.text.muted : colors.text.primary,
        ...typography.bodyMedium,
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

function Button({ onClick, children, variant = "primary", disabled, style }) {
  const variants = {
    primary: {
      bg: colors.primary,
      hover: colors.primaryHover,
      color: colors.text.primary
    },
    success: {
      bg: colors.success,
      hover: "#059669",
      color: colors.text.primary
    },
    danger: {
      bg: colors.danger,
      hover: "#dc2626",
      color: colors.text.primary
    },
    secondary: {
      bg: colors.bg.tertiary,
      hover: colors.border.light,
      color: colors.text.secondary
    }
  };

  const v = variants[variant] || variants.primary;

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "10px 20px",
        borderRadius: 6,
        border: "none",
        background: disabled ? colors.bg.tertiary : v.bg,
        color: disabled ? colors.text.muted : v.color,
        ...typography.bodyMedium,
        fontWeight: 600,
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "all 0.2s ease",
        boxShadow: "0 2px 4px rgba(0, 0, 0, 0.2)",
        ...style
      }}
      onMouseEnter={(e) => !disabled && (e.target.style.background = v.hover)}
      onMouseLeave={(e) => !disabled && (e.target.style.background = v.bg)}
    >
      {children}
    </button>
  );
}

/* -------------------------
   Main Component
-------------------------- */

export default function ZerodhaLogin() {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [strategy, setStrategy] = useState(null);
  const [loading, setLoading] = useState(true);

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [editingCreds, setEditingCreds] = useState(false);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    setLoading(true);
    try {
      const [cfg, st, strat] = await Promise.all([
        getZerodhaConfig(),
        getZerodhaStatus(),
        getStrategyConfig(),
      ]);

      setConfig(cfg);
      setStatus(st);
      setStrategy(strat);

      if (cfg?.api_key) {
        setApiKey(cfg.api_key);
      }
    } catch (e) {
      console.error(e);
      setConfig(null);
      setStatus(null);
      setStrategy(null);
    } finally {
      setLoading(false);
    }
  }

  async function saveCredentials() {
    if (!apiKey || !apiSecret) {
      alert("API Key and API Secret are required");
      return;
    }

    await saveZerodhaConfig(apiKey, apiSecret);
    alert("Credentials saved. Please login to Zerodha.");

    setApiSecret("");
    setEditingCreds(false);
    setStatus(null);

    await refresh();
  }

  async function login() {
    const { login_url } = await getZerodhaLoginUrl();
    const w = window.open(login_url, "_blank");

    const timer = setInterval(() => {
      if (w.closed) {
        clearInterval(timer);
        refresh();
      }
    }, 1000);
  }

  async function enable() {
    if (!strategy) return;

    await saveStrategyConfig({
      ...strategy,
      trade_on: true,
    });

    await refresh();
  }

  async function disable() {
    if (!strategy) return;

    await saveStrategyConfig({
      ...strategy,
      trade_on: false,
    });

    await refresh();
  }

  if (loading) {
    return (
      <div style={{ 
        padding: spacing.xxl, 
        background: colors.bg.primary,
        color: colors.text.primary,
        minHeight: "100vh"
      }}>
        <div style={{ ...typography.bodyLarge }}>Checking Zerodha status…</div>
      </div>
    );
  }

  const configured = config?.configured === true;
  const connected = status?.connected === true;
  const tradingEnabled = strategy?.trade_on === true;
  const sessionExpired = status?.session_expired === true;
  const loginAt = status?.login_at;

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
      <div style={{ marginBottom: spacing.xxl }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md }}>
          <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
            Zerodha Integration
          </h1>
          <div style={{ ...typography.label, color: colors.text.muted }}>
            Broker Connection
          </div>
        </div>
        <p style={{ 
          margin: 0, 
          ...typography.bodyMedium, 
          color: colors.text.tertiary 
        }}>
          Configure and manage your Zerodha trading account connection
        </p>
      </div>

      <div style={{ maxWidth: 800 }}>
        {/* Connection Status */}
        <Card style={{ marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md }}>
            <h3 style={{ margin: 0, ...typography.headingMedium, color: colors.text.primary }}>
              Connection Status
            </h3>
            <Button onClick={refresh} variant="secondary">
              ↻ Refresh
            </Button>
          </div>
          
          <div style={{ display: "flex", gap: spacing.md, flexWrap: "wrap" }}>
            {!configured && (
              <StatusBadge type="warning" text="Not Configured" icon="⚙️" />
            )}
            {configured && !connected && (
              <>
                <StatusBadge type="warning" text="Configured" icon="⚙️" />
                {sessionExpired ? (
                  <StatusBadge type="danger" text="Session Expired" icon="⏰" />
                ) : (
                  <StatusBadge type="warning" text="Not Connected" icon="🔑" />
                )}
              </>
            )}
            {configured && connected && (
              <>
                <StatusBadge type="success" text="Connected" icon="✓" />
                <StatusBadge 
                  type={tradingEnabled ? "success" : "warning"} 
                  text={tradingEnabled ? "Trading Enabled" : "Trading Disabled"} 
                  icon={tradingEnabled ? "▶" : "⏸"}
                />
              </>
            )}
          </div>

          {connected && loginAt && (
            <div style={{ 
              marginTop: spacing.md, 
              padding: spacing.md,
              background: colors.bg.primary,
              borderRadius: 6,
              ...typography.bodySmall,
              color: colors.text.tertiary
            }}>
              Last login: {new Date(loginAt).toLocaleString('en-IN', { 
                dateStyle: 'medium', 
                timeStyle: 'medium' 
              })}
            </div>
          )}
        </Card>

        {/* STATE 1: NOT CONFIGURED */}
        {!configured && (
          <Card>
            <h3 style={{ 
              marginTop: 0, 
              marginBottom: spacing.lg,
              ...typography.headingMedium, 
              color: colors.text.primary 
            }}>
              Configure Zerodha Credentials
            </h3>
            
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
              <div>
                <label style={{ 
                  display: "block", 
                  marginBottom: spacing.xs,
                  ...typography.bodySmall,
                  color: colors.text.secondary,
                  fontWeight: 500
                }}>
                  API Key
                </label>
                <Input
                  placeholder="Enter your Zerodha API Key"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>

              <div>
                <label style={{ 
                  display: "block", 
                  marginBottom: spacing.xs,
                  ...typography.bodySmall,
                  color: colors.text.secondary,
                  fontWeight: 500
                }}>
                  API Secret
                </label>
                <Input
                  type="password"
                  placeholder="Enter your Zerodha API Secret"
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                />
              </div>

              <div style={{ 
                marginTop: spacing.sm,
                padding: spacing.md,
                background: colors.bg.primary,
                borderRadius: 6,
                ...typography.bodySmall,
                color: colors.text.muted
              }}>
                ℹ️ Get your API credentials from your Zerodha Kite Connect developer console
              </div>

              <Button onClick={saveCredentials} style={{ marginTop: spacing.sm }}>
                Save Credentials
              </Button>
            </div>
          </Card>
        )}

        {/* STATE 2: CONFIGURED, NO SESSION */}
        {configured && !connected && !editingCreds && (
          <Card>
            <h3 style={{ 
              marginTop: 0, 
              marginBottom: spacing.lg,
              ...typography.headingMedium, 
              color: colors.text.primary 
            }}>
              Login Required
            </h3>
            
            <p style={{ 
              ...typography.bodyMedium, 
              color: colors.text.secondary,
              marginBottom: spacing.lg
            }}>
              {sessionExpired 
                ? "Your Zerodha session has expired. Please login again to continue trading."
                : "Connect to Zerodha to start trading. This will open a new window for authentication."}
            </p>

            <div style={{ display: "flex", gap: spacing.md }}>
              <Button onClick={login}>
                🔐 Login to Zerodha
              </Button>
              <Button onClick={() => setEditingCreds(true)} variant="secondary">
                Edit Credentials
              </Button>
            </div>
          </Card>
        )}

        {/* STATE 2a: EDITING CREDENTIALS */}
        {configured && editingCreds && (
          <Card>
            <h3 style={{ 
              marginTop: 0, 
              marginBottom: spacing.lg,
              ...typography.headingMedium, 
              color: colors.text.primary 
            }}>
              Edit Zerodha Credentials
            </h3>
            
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
              <div>
                <label style={{ 
                  display: "block", 
                  marginBottom: spacing.xs,
                  ...typography.bodySmall,
                  color: colors.text.secondary,
                  fontWeight: 500
                }}>
                  API Key
                </label>
                <Input
                  placeholder="Enter your Zerodha API Key"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>

              <div>
                <label style={{ 
                  display: "block", 
                  marginBottom: spacing.xs,
                  ...typography.bodySmall,
                  color: colors.text.secondary,
                  fontWeight: 500
                }}>
                  API Secret
                </label>
                <Input
                  type="password"
                  placeholder="Enter your Zerodha API Secret"
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                />
              </div>

              <div style={{ display: "flex", gap: spacing.md, marginTop: spacing.sm }}>
                <Button onClick={saveCredentials}>
                  Save Changes
                </Button>
                <Button onClick={() => setEditingCreds(false)} variant="secondary">
                  Cancel
                </Button>
              </div>
            </div>
          </Card>
        )}

        {/* STATE 3: CONNECTED */}
        {configured && connected && !editingCreds && (
          <Card>
            <h3 style={{ 
              marginTop: 0, 
              marginBottom: spacing.lg,
              ...typography.headingMedium, 
              color: colors.text.primary 
            }}>
              Trading Control
            </h3>
            
            <div style={{ 
              padding: spacing.lg,
              background: colors.bg.primary,
              borderRadius: 6,
              marginBottom: spacing.lg
            }}>
              <div style={{ 
                display: "flex", 
                alignItems: "center", 
                justifyContent: "space-between",
                marginBottom: spacing.sm
              }}>
                <div>
                  <div style={{ 
                    ...typography.bodySmall, 
                    color: colors.text.muted,
                    marginBottom: spacing.xs
                  }}>
                    Trading Status
                  </div>
                  <div style={{ 
                    ...typography.headingMedium,
                    color: tradingEnabled ? colors.success : colors.warning
                  }}>
                    {tradingEnabled ? "ENABLED" : "DISABLED"}
                  </div>
                </div>
                
                {tradingEnabled ? (
                  <Button onClick={disable} variant="danger">
                    ⏸ Disable Trading
                  </Button>
                ) : (
                  <Button onClick={enable} variant="success">
                    ▶ Enable Trading
                  </Button>
                )}
              </div>

              <div style={{ 
                ...typography.bodySmall,
                color: colors.text.tertiary,
                marginTop: spacing.md,
                paddingTop: spacing.md,
                borderTop: `1px solid ${colors.border.dark}`
              }}>
                {tradingEnabled 
                  ? "⚠️ Trading is active. The bot will execute trades based on your strategy settings."
                  : "ℹ️ Trading is paused. No trades will be executed until you enable it."}
              </div>
            </div>

            <Button onClick={() => setEditingCreds(true)} variant="secondary">
              Edit Credentials
            </Button>
          </Card>
        )}
      </div>
    </div>
  );
}
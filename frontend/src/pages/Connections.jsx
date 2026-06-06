import { useEffect, useState } from "react";
import {
  getZerodhaStatus,
  getZerodhaLoginUrl,
  saveZerodhaCredentials,
  getStrategyConfig,
  saveStrategyConfig,
  getGlobalConfig,
  setGlobalTradeSwitch,
  getTelegramConfig,
  saveTelegramConfig,
  testTelegramConnection,
} from "../api";
import RelayPanel from "../components/RelayPanel";

/* ─────────────────────────────────────────────
   Tokens (matching Settings page)
───────────────────────────────────────────── */

const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 28 };

const colors = {
  primary:      "#3b82f6",
  primaryHover: "#2563eb",
  success:      "#10b981",
  successBg:    "rgba(16, 185, 129, 0.12)",
  warning:      "#f59e0b",
  warningBg:    "rgba(245, 158, 11, 0.12)",
  danger:       "#ef4444",
  dangerBg:     "rgba(239, 68, 68, 0.12)",
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
   Layout helpers
───────────────────────────────────────────── */

function getPanelStyle(isPrimary, isMobile) {
  if (isMobile) {
    return {
      overflow:   "hidden",
      transition: "flex 0.28s ease, opacity 0.2s ease",
      width:      "100%",
      flex:       isPrimary ? "1 1 auto" : "0 0 auto",
      cursor:     isPrimary ? "default" : "pointer",
      opacity:    isPrimary ? 1 : 0.9,
    };
  }
  return {
    overflow:   "hidden",
    transition: "flex 0.28s ease, opacity 0.22s ease",
    minWidth:   0,
    flex:       isPrimary ? "1 1 65%" : "0 0 180px",
    cursor:     isPrimary ? "default" : "pointer",
    opacity:    isPrimary ? 1 : 0.85,
  };
}

/* ─────────────────────────────────────────────
   Components
───────────────────────────────────────────── */

function Input({ type = "text", value, onChange, placeholder, disabled, style }) {
  return (
    <input
      type={type} value={value} onChange={onChange} placeholder={placeholder} disabled={disabled}
      style={{
        padding: "8px 11px", borderRadius: 6,
        border:  `1px solid ${disabled ? colors.border.dark : colors.border.medium}`,
        background: disabled ? colors.bg.tertiary : colors.bg.input,
        color:      disabled ? colors.text.muted  : colors.text.primary,
        fontSize: 13, outline: "none", width: "100%",
        transition: "border-color 0.15s",
        ...style,
      }}
      onFocus={(e) => !disabled && (e.target.style.borderColor = colors.primary)}
      onBlur={(e)  => (e.target.style.borderColor = disabled ? colors.border.dark : colors.border.medium)}
    />
  );
}

function Button({ onClick, children, variant = "primary", disabled, style }) {
  const variants = {
    primary:   { bg: colors.primary, hover: colors.primaryHover, color: colors.text.primary },
    success:   { bg: colors.success, hover: "#059669", color: colors.text.primary },
    danger:    { bg: colors.danger, hover: "#dc2626", color: colors.text.primary },
    secondary: { bg: colors.bg.tertiary, hover: colors.border.light, color: colors.text.secondary }
  };
  const v = variants[variant] || variants.primary;

  return (
    <button
      onClick={onClick} disabled={disabled}
      style={{
        padding: "8px 16px", borderRadius: 6, border: "none",
        background: disabled ? colors.bg.tertiary : v.bg,
        color: disabled ? colors.text.muted : v.color,
        fontSize: 13, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
        transition: "all 0.2s ease",
        ...style,
      }}
      onMouseEnter={(e) => !disabled && (e.target.style.background = v.hover)}
      onMouseLeave={(e) => !disabled && (e.target.style.background = v.bg)}
    >
      {children}
    </button>
  );
}

function Checkbox({ checked, onChange, label: labelText }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: spacing.sm, cursor: "pointer", userSelect: "none" }}>
      <input
        type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        style={{ width: 16, height: 16, cursor: "pointer", accentColor: colors.primary }}
      />
      <span style={{ fontSize: 13, color: colors.text.secondary }}>
        {labelText}
      </span>
    </label>
  );
}

function RadioButton({ checked, onChange, label: labelText, description }) {
  return (
    <label style={{
      display: "flex", alignItems: "flex-start", gap: spacing.sm,
      cursor: "pointer", userSelect: "none",
      padding: spacing.sm, borderRadius: 6,
      background: checked ? colors.bg.tertiary : "transparent",
      border: `1px solid ${checked ? colors.primary + "40" : "transparent"}`,
      transition: "all 0.2s ease"
    }}>
      <input
        type="radio" checked={checked} onChange={() => onChange()}
        style={{ marginTop: 2, width: 16, height: 16, cursor: "pointer", accentColor: colors.primary }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, color: colors.text.secondary, fontWeight: checked ? 600 : 400 }}>
          {labelText}
        </div>
        {description && (
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 2 }}>
            {description}
          </div>
        )}
      </div>
    </label>
  );
}

function StatusBadge({ type, text, icon }) {
  const styles = {
    success: { bg: colors.successBg, color: colors.success },
    warning: { bg: colors.warningBg, color: colors.warning },
    danger:  { bg: colors.dangerBg, color: colors.danger }
  };
  const style = styles[type] || styles.success;

  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: spacing.sm,
      padding: "6px 12px", borderRadius: 6,
      background: style.bg, color: style.color,
      border: `1px solid ${style.color}40`,
      fontSize: 13, fontWeight: 600
    }}>
      {icon && <span style={{ fontSize: 14 }}>{icon}</span>}
      {text}
    </div>
  );
}

/* ─────────────────────────────────────────────
   Strategy filter options — EXACT strategy-id values.
   `value` is sent to the backend and matched EXACTLY against strategy_id
   (with legacy "bb"/"scalp" still honoured server-side for old saved configs).
───────────────────────────────────────────── */
const STRATEGY_FILTER_OPTIONS = [
  { value: "all",      title: "All Strategies", desc: "BB · BB V2 · Scalp · Scalp V2 · Scalp V3 · HA" },
  { value: "BB_V1",    title: "BB Only",        desc: "Bollinger Band" },
  { value: "BB_V2",    title: "BB V2 Only",     desc: "BB Pivot Variant" },
  { value: "SCALP_V1", title: "Scalp Only",     desc: "Options scalping" },
  { value: "SCALP_V2", title: "Scalp V2 Only",  desc: "3-leg scalp" },
  { value: "SCALP_V3", title: "Scalp V3 Only",  desc: "Hedge (option buying)" },
  { value: "HA_V1",    title: "HA Only",        desc: "Heikin Ashi" },
];

/* ─────────────────────────────────────────────
   Panel Component
───────────────────────────────────────────── */

function Panel({ name, isPrimary, onBecomePrimary, children, isMobile }) {
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
      {/* Header */}
      <div style={{
        padding:        `${spacing.md}px ${spacing.lg}px`,
        background:     colors.bg.tertiary,
        borderBottom:   `1px solid ${colors.border.medium}`,
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        flexShrink:     0,
        gap:            spacing.md,
        userSelect:     "none",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, minWidth: 0, overflow: "hidden" }}>
          {!isPrimary && (
            <span style={{ fontSize: 10, color: colors.text.muted, flexShrink: 0 }}>↗</span>
          )}
          <span style={{ fontSize: 15, fontWeight: 600, color: colors.text.primary, flexShrink: 0 }}>
            {name}
          </span>
        </div>
      </div>

      {/* Body */}
      <div onClick={(e) => isPrimary && e.stopPropagation()} style={{ flex: 1, overflow: "auto" }}>
        {isPrimary ? (
          <div style={{ padding: spacing.lg }}>
            {children}
          </div>
        ) : (
          isMobile ? (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: spacing.sm,
              padding: `${spacing.sm}px ${spacing.lg}px`,
              fontSize: 12,
              fontWeight: 500,
              color: colors.text.muted,
              letterSpacing: "0.5px",
              textTransform: "uppercase",
            }}>
              <span style={{ fontSize: 10 }}>↕</span>
              {name}
              <span style={{ fontSize: 10, marginLeft: 4, color: colors.primary }}>tap to expand</span>
            </div>
          ) : (
            <div style={{
              writingMode: "vertical-rl",
              textAlign: "center",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              fontSize: 12,
              fontWeight: 500,
              color: colors.text.muted,
              letterSpacing: "1px",
              textTransform: "uppercase",
              padding: spacing.md,
            }}>
              {name}
            </div>
          )
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Main Component
───────────────────────────────────────────── */

export default function Connections() {
  const [loading, setLoading] = useState(true);
  const [primaryPanel, setPrimaryPanel] = useState("services");

  // Zerodha state
  const [status, setStatus] = useState(null);
  const [globalConfig, setGlobalConfig] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [editingZerodha, setEditingZerodha] = useState(false);

  // Telegram credentials state
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [telegramConfigured, setTelegramConfigured] = useState(false);
  const [editingTelegram, setEditingTelegram] = useState(false);
  const [testing, setTesting] = useState(false);

  // Telegram notification settings state
  const [saving, setSaving] = useState(false);
  const [strategyFilter, setStrategyFilter] = useState("all");
  const [modeFilter, setModeFilter] = useState("all");
  const [notifications, setNotifications] = useState({
    tradeEntries: true,
    tpExits: true,
    slExits: true,
    manualExits: true,
    positionUpdates: false,
    dailySummary: true,
    systemAlerts: true,
    criticalAlerts: true,
  });

  // Mobile detection
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  // Re-check Zerodha status when the app window regains focus
  // (fires after closing the Zerodha login browser tab)
  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  useEffect(() => {
    refresh();
    loadTelegramConfig();
  }, []);

  async function refresh() {
    setLoading(true);
    try {
      const [st, global] = await Promise.all([
        getZerodhaStatus(),
        getGlobalConfig(),
      ]);
      setStatus(st);
      setGlobalConfig(global);
    } catch (e) {
      console.error("Refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }

  async function loadTelegramConfig() {
    try {
      const config = await getTelegramConfig();
      if (config) {
        const hasCredentials = config.bot_token && config.chat_id;
        setBotToken(config.bot_token || "");
        setChatId(config.chat_id || "");
        setTelegramConfigured(hasCredentials);
        setStrategyFilter(config.strategy_filter || "all");
        setModeFilter(config.mode_filter || "all");
        setNotifications(config.notification_levels || notifications);
      }
    } catch (e) {
      console.error("Failed to load Telegram config:", e);
    }
  }

  async function handleSaveZerodhaCredentials() {
    if (!apiKey || !apiSecret) {
      alert("API Key and API Secret are required");
      return;
    }
    await saveZerodhaCredentials(apiKey, apiSecret);  // now the imported one
    alert("Credentials saved. Please login to Zerodha.");
    setApiSecret("");
    setEditingZerodha(false);
    setStatus(null);
    await refresh();
  }

  async function saveTelegramCredentials() {
    if (!botToken || !chatId) {
      alert("Bot Token and Chat ID are required");
      return;
    }
    try {
      await saveTelegramConfig({
        bot_token: botToken,
        chat_id: chatId,
        strategy_filter: strategyFilter,
        mode_filter: modeFilter,
        notification_levels: notifications
      });
      setTelegramConfigured(true);
      setEditingTelegram(false);
      alert("✅ Telegram credentials saved!");
    } catch (e) {
      alert("❌ Failed to save: " + e.message);
    }
  }

  async function login() {
    const res = await getZerodhaLoginUrl();
    const login_url = res?.login_url;
    if (!login_url) {
      alert("Login URL not received from backend");
      return;
    }
    if (window.__TAURI__?.shell?.open) {
      await window.__TAURI__.shell.open(login_url);
    } else {
      window.open(login_url, "_blank");
    }
  }

  async function enable() {
    await setGlobalTradeSwitch(true);
    await refresh();
  }

  async function disable() {
    await setGlobalTradeSwitch(false);
    await refresh();
  }

  async function testConnection() {
    if (!botToken || !chatId) {
      alert("Please enter both Bot Token and Chat ID");
      return;
    }
    setTesting(true);
    try {
      const result = await testTelegramConnection(botToken, chatId);
      if (result.success) {
        alert("✅ Test message sent successfully! Check your Telegram.");
      } else {
        alert("❌ Failed to send test message. Check your credentials.");
      }
    } catch (e) {
      alert("❌ Connection failed: " + e.message);
    } finally {
      setTesting(false);
    }
  }

  async function saveNotificationSettings() {
    setSaving(true);
    try {
      await saveTelegramConfig({
        bot_token: botToken,
        chat_id: chatId,
        strategy_filter: strategyFilter,
        mode_filter: modeFilter,
        notification_levels: notifications
      });
      alert("✅ Notification settings saved!");
    } catch (e) {
      alert("❌ Failed to save: " + e.message);
    } finally {
      setSaving(false);
    }
  }

  function openBotFatherGuide() {
    const url = "https://core.telegram.org/bots#6-botfather";
    if (window.__TAURI__?.shell?.open) {
      window.__TAURI__.shell.open(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  function openChatIdGuide() {
    const url = "https://t.me/userinfobot";
    if (window.__TAURI__?.shell?.open) {
      window.__TAURI__.shell.open(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  if (loading) {
    return (
      <div style={{ padding: spacing.xxl, background: colors.bg.primary, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: colors.text.primary }}>
        Loading...
      </div>
    );
  }

  const zerodhaConfigured = status?.configured || false;
  const zerodhaConnected = status?.connected || false;
  const loginAt = status?.login_at;
  const sessionExpired = status?.session_expired || false;
  const tradingEnabled = globalConfig?.trade_on === true;

  return (
    <div style={{ padding: isMobile ? spacing.md : spacing.xxl, background: colors.bg.primary, color: colors.text.primary, minHeight: "100vh" }}>
      {/* Header */}
      <div style={{ marginBottom: spacing.xl }}>
        <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700, lineHeight: 1.2 }}>
          Connections
        </h1>
        <p style={{ margin: 0, marginTop: spacing.xs, fontSize: 13, color: colors.text.secondary }}>
          Manage external service integrations
        </p>
      </div>

      {/* Two-Panel Layout */}
      <div style={{
        display:       "flex",
        flexDirection: isMobile ? "column" : "row",
        gap:           spacing.lg,
        minHeight:     isMobile ? "auto" : "500px",
      }}>

        {/* ═══════════════════════════════════════════════════════════
            PANEL 1: SERVICE CREDENTIALS
        ═══════════════════════════════════════════════════════════ */}
        <div style={getPanelStyle(primaryPanel === "services", isMobile)}>
          <Panel
            name="🔗 Service Credentials"
            isPrimary={primaryPanel === "services"}
            onBecomePrimary={() => setPrimaryPanel("services")}
            isMobile={isMobile}
          >
            {/* ZERODHA SECTION */}
            <div style={{
              marginBottom: spacing.xxl,
              padding: spacing.lg,
              background: colors.bg.input,
              border: `1px solid ${colors.border.medium}`,
              borderRadius: 8
            }}>
              <div style={{
                ...label,
                marginBottom: spacing.md,
                paddingBottom: spacing.sm,
                borderBottom: `1px solid ${colors.border.medium}`,
                display: "flex",
                alignItems: "center",
                gap: spacing.sm
              }}>
                <span style={{ fontSize: 14 }}>🔗</span>
                <span>Zerodha Integration</span>
              </div>

              {/* Connection Status */}
              <div style={{ marginBottom: spacing.lg }}>
                <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.sm, marginBottom: spacing.md }}>
                  {!zerodhaConfigured && <StatusBadge type="warning" text="Not Configured" icon="⚙️" />}
                  {zerodhaConfigured && !zerodhaConnected && (
                    <>
                      {sessionExpired ? (
                        <StatusBadge type="danger" text="Session Expired" icon="⏰" />
                      ) : (
                        <StatusBadge type="warning" text="Not Connected" icon="🔌" />
                      )}
                    </>
                  )}
                  {zerodhaConfigured && zerodhaConnected && (
                    <>
                      <StatusBadge type="success" text="Connected" icon="✓" />
                      <StatusBadge type={tradingEnabled ? "success" : "warning"} text={tradingEnabled ? "Trading Enabled" : "Trading Disabled"} icon={tradingEnabled ? "▶" : "⏸"} />
                    </>
                  )}
                </div>
                {zerodhaConnected && loginAt && (
                  <div style={{ padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                    Last login: {new Date(loginAt).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'medium' })}
                  </div>
                )}
              </div>

              {/* NOT CONFIGURED */}
              {!zerodhaConfigured && !editingZerodha && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>API Key</div>
                    <Input placeholder="Enter your Zerodha API Key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>API Secret</div>
                    <Input type="password" placeholder="Enter your Zerodha API Secret" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} />
                  </div>
                  <div style={{ padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                    ℹ️ Get credentials from Zerodha Kite Connect developer console
                  </div>
                  <Button onClick={handleSaveZerodhaCredentials}>Save Credentials</Button>
                </div>
              )}

              {/* CONFIGURED, NO SESSION */}
              {zerodhaConfigured && !zerodhaConnected && !editingZerodha && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <p style={{ fontSize: 13, color: colors.text.secondary, margin: 0 }}>
                    {sessionExpired ? "Session expired. Please login again." : "Login to Zerodha to start trading."}
                  </p>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={login} style={{ flex: 1 }}>🔐 Login to Zerodha</Button>
                    <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ flex: 1 }}>
                      Edit Credentials
                    </Button>
                  </div>
                </div>
              )}

              {/* EDITING CREDENTIALS */}
              {editingZerodha && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>API Key</div>
                    <Input placeholder="Enter your Zerodha API Key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>API Secret</div>
                    <Input type="password" placeholder="Enter your Zerodha API Secret" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} />
                  </div>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={handleSaveZerodhaCredentials}>Save Changes</Button>
                    <Button onClick={() => setEditingZerodha(false)} variant="secondary">Cancel</Button>
                  </div>
                </div>
              )}

              {/* TRADING CONTROL */}
              {zerodhaConfigured && zerodhaConnected && !editingZerodha && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div style={{ padding: spacing.lg, background: colors.bg.input, borderRadius: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <div>
                        <div style={{ fontSize: 11, color: colors.text.muted, marginBottom: 2 }}>Trading Status</div>
                        <div style={{ fontSize: 16, fontWeight: 600, color: tradingEnabled ? colors.success : colors.warning }}>
                          {tradingEnabled ? "ENABLED" : "DISABLED"}
                        </div>
                      </div>
                      {tradingEnabled ? (
                        <Button onClick={disable} variant="danger">⏸ Disable</Button>
                      ) : (
                        <Button onClick={enable} variant="success">▶ Enable</Button>
                      )}
                    </div>
                  </div>
                  <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ width: "100%" }}>
                    Edit Credentials
                  </Button>
                </div>
              )}
            </div>

            {/* RELAY SECTION */}
            <RelayPanel />

            {/* TELEGRAM SECTION */}
            <div style={{
              padding: spacing.lg,
              background: colors.bg.input,
              border: `1px solid ${colors.border.medium}`,
              borderRadius: 8
            }}>
              <div style={{
                ...label,
                marginBottom: spacing.md,
                paddingBottom: spacing.sm,
                borderBottom: `1px solid ${colors.border.medium}`,
                display: "flex",
                alignItems: "center",
                gap: spacing.sm
              }}>
                <span style={{ fontSize: 14 }}>📱</span>
                <span>Telegram Integration</span>
              </div>

              {/* Connection Status */}
              <div style={{ marginBottom: spacing.lg }}>
                <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
                <StatusBadge
                  type={telegramConfigured ? "success" : "warning"}
                  text={telegramConfigured ? "Connected" : "Not Configured"}
                  icon={telegramConfigured ? "✓" : "⚙️"}
                />
              </div>

              {/* NOT CONFIGURED */}
              {!telegramConfigured && !editingTelegram && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.xs }}>
                      <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary }}>Bot Token</div>
                      <button onClick={openBotFatherGuide} style={{ background: "none", border: "none", color: colors.primary, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
                        How to get?
                      </button>
                    </div>
                    <Input type="password" placeholder="Enter Bot Token" value={botToken} onChange={(e) => setBotToken(e.target.value)} />
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.xs }}>
                      <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary }}>Chat ID</div>
                      <button onClick={openChatIdGuide} style={{ background: "none", border: "none", color: colors.primary, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
                        Get my Chat ID
                      </button>
                    </div>
                    <Input placeholder="Enter Chat ID" value={chatId} onChange={(e) => setChatId(e.target.value)} />
                  </div>
                  <div style={{ padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                    ℹ️ Create a bot via @BotFather and get your Chat ID from @userinfobot
                  </div>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={testConnection} variant="secondary" disabled={testing || !botToken || !chatId}>
                      {testing ? "Testing..." : "🧪 Test"}
                    </Button>
                    <Button onClick={saveTelegramCredentials} disabled={!botToken || !chatId}>Save Credentials</Button>
                  </div>
                </div>
              )}

              {/* CONFIGURED */}
              {telegramConfigured && !editingTelegram && (
                <div>
                  <Button onClick={() => setEditingTelegram(true)} variant="secondary" style={{ width: "100%" }}>
                    Edit Credentials
                  </Button>
                </div>
              )}

              {/* EDITING CREDENTIALS */}
              {editingTelegram && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>Bot Token</div>
                    <Input type="password" placeholder="Enter Bot Token" value={botToken} onChange={(e) => setBotToken(e.target.value)} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>Chat ID</div>
                    <Input placeholder="Enter Chat ID" value={chatId} onChange={(e) => setChatId(e.target.value)} />
                  </div>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={saveTelegramCredentials}>Save Changes</Button>
                    <Button onClick={() => setEditingTelegram(false)} variant="secondary">Cancel</Button>
                  </div>
                </div>
              )}
            </div>
          </Panel>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            PANEL 2: TELEGRAM NOTIFICATIONS
        ═══════════════════════════════════════════════════════════ */}
        <div style={getPanelStyle(primaryPanel === "notifications", isMobile)}>
          <Panel
            name="📱 Telegram Notifications"
            isPrimary={primaryPanel === "notifications"}
            onBecomePrimary={() => setPrimaryPanel("notifications")}
            isMobile={isMobile}
          >
            {!telegramConfigured ? (
              <div style={{ padding: spacing.lg, background: colors.bg.input, borderRadius: 6, textAlign: "center" }}>
                <div style={{ fontSize: 13, color: colors.text.muted, marginBottom: spacing.md }}>
                  Configure Telegram credentials in Service Credentials panel first
                </div>
                <Button onClick={() => setPrimaryPanel("services")} variant="secondary">
                  → Go to Service Credentials
                </Button>
              </div>
            ) : (
              <>
                {/* Filters */}
                <div style={{ marginBottom: spacing.xl }}>
                  <div style={{ ...label, marginBottom: spacing.md }}>Notification Filters</div>
                  <div style={{ padding: spacing.lg, background: colors.bg.input, borderRadius: 6, display: "grid", gridTemplateColumns: "1fr 1fr", gap: spacing.lg }}>

                    {/* ── Strategy filter (exact strategy-id values) ── */}
                    <div>
                      <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Strategy</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: spacing.xs }}>
                        {STRATEGY_FILTER_OPTIONS.map((opt) => (
                          <RadioButton
                            key={opt.value}
                            checked={strategyFilter === opt.value}
                            onChange={() => setStrategyFilter(opt.value)}
                            label={opt.title}
                            description={opt.desc}
                          />
                        ))}
                      </div>
                    </div>

                    {/* ── Mode filter ───────────────────────────────── */}
                    <div>
                      <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Mode</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: spacing.xs }}>
                        <RadioButton checked={modeFilter === "all"}   onChange={() => setModeFilter("all")}   label="All Modes"   description="LIVE + PAPER" />
                        <RadioButton checked={modeFilter === "live"}  onChange={() => setModeFilter("live")}  label="LIVE Only"   description="Real money" />
                        <RadioButton checked={modeFilter === "paper"} onChange={() => setModeFilter("paper")} label="PAPER Only"  description="Simulated" />
                      </div>
                    </div>

                  </div>
                </div>

                {/* Notification Levels */}
                <div style={{ marginBottom: spacing.xl }}>
                  <div style={{ ...label, marginBottom: spacing.md }}>Notification Types</div>
                  <div style={{ padding: spacing.lg, background: colors.bg.input, borderRadius: 6, display: "flex", flexDirection: "column", gap: spacing.md }}>
                    <Checkbox checked={notifications.tradeEntries}    onChange={(v) => setNotifications({ ...notifications, tradeEntries: v })}    label="Trade Entries" />
                    <Checkbox checked={notifications.tpExits}         onChange={(v) => setNotifications({ ...notifications, tpExits: v })}         label="Target Exits" />
                    <Checkbox checked={notifications.slExits}         onChange={(v) => setNotifications({ ...notifications, slExits: v })}         label="Stop-Loss Exits" />
                    <Checkbox checked={notifications.manualExits}     onChange={(v) => setNotifications({ ...notifications, manualExits: v })}     label="Manual Exits" />
                    <Checkbox checked={notifications.positionUpdates} onChange={(v) => setNotifications({ ...notifications, positionUpdates: v })} label="Position Updates (30 min)" />
                    <Checkbox checked={notifications.dailySummary}    onChange={(v) => setNotifications({ ...notifications, dailySummary: v })}    label="Daily Summary (15:30)" />
                    <Checkbox checked={notifications.systemAlerts}    onChange={(v) => setNotifications({ ...notifications, systemAlerts: v })}    label="System Alerts" />
                    <Checkbox checked={notifications.criticalAlerts ?? true} onChange={(v) => setNotifications({ ...notifications, criticalAlerts: v })} label="Critical Alerts (GTT failures, unprotected positions)" />
                  </div>
                </div>

                {/* Save Button */}
                <Button onClick={saveNotificationSettings} disabled={saving}>
                  {saving ? "Saving..." : "💾 Save Notification Settings"}
                </Button>

                <div style={{ marginTop: spacing.lg, padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                  ℹ️ Notifications are free and unlimited. Settings are saved automatically.
                </div>
              </>
            )}
          </Panel>
        </div>

      </div>
    </div>
  );
}
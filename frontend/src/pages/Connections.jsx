import { useEffect, useState } from "react";
import {
  getZerodhaStatus,
  getZerodhaLoginUrl,
  saveZerodhaCredentials,
  getGlobalConfig,
  setGlobalTradeSwitch,
  getTelegramConfig,
  saveTelegramConfig,
  testTelegramConnection,
} from "../api";
import RelayPanel from "../components/RelayPanel";
import { useEntitlements } from "../hooks/useEntitlements";
import { getApiBase } from "../api/base";

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

/* Per-strategy accent colors (consistent with the rest of the app). */
const STRATEGY_ACCENT = {
  SCALP_V1: "#f59e0b",
  SCALP_V3: "#ec4899",
  SCALP_V5: "#06b6d4",
  IC_V1:    "#6366f1",
  BB_V1:    "#3b82f6",
  BB_V2:    "#3b82f6",
  HA_V1:    "#14b8a6",
  PST_SELL:  "#fb7185",
  PST_HEDGE: "#be123c",
  TMA_V1:    "#8b5cf6",   // ── TMA_V1 ──
};

/* Strategy options for the MULTI-SELECT filter (exact strategy-id values).
   Fixed app order: SCALP_V1..V5, BB_V1, BB_V2, HA_V1. */
const STRATEGY_OPTIONS = [
  { value: "SCALP_V1", title: "Scalp V1" },
  { value: "SCALP_V3", title: "Scalp V3" },
  { value: "SCALP_V5", title: "Scalp V5" },
  { value: "IC_V1",    title: "IC V1" },
  { value: "BB_V1",    title: "BB V1" },
  { value: "BB_V2",    title: "BB V2" },
  { value: "HA_V1",    title: "Heikin Ashi" },
  { value: "PST_SELL",  title: "PST Sell" },
  { value: "PST_HEDGE", title: "PST Hedge" },
  { value: "TMA_V1",    title: "TMA V1" },   // ── TMA_V1 ──
];

/* The FOUR collapsed notification types. */
const NOTIFICATION_TYPES = [
  { key: "tradeActivity",   title: "Trade Activity",
    desc: "Entries, target hits, stop-losses, and manual exits" },
  { key: "positionUpdates", title: "Position Updates",
    desc: "Open-position P&L snapshot every 30 min" },
  { key: "dailySummary",    title: "Daily Summary",
    desc: "End-of-day P&L card at 15:30" },
  { key: "criticalAlerts",  title: "Critical Alerts",
    desc: "Order rejections, GTT failures, relay/system issues" },
];

const DEFAULT_NOTIFICATIONS = {
  tradeActivity:   true,
  positionUpdates: false,
  dailySummary:    true,
  criticalAlerts:  true,
};

const DEFAULT_SCHEDULE = { enabled: false, start: "09:15", end: "15:45" };

function emptyChannel(idx) {
  return {
    id:              `channel_${idx}`,
    name:            idx === 1 ? "Primary" : "Secondary",
    chat_id:         "",
    enabled:         false,
    strategy_filter: [],
    mode_filter:     "all",
    notifications:   { ...DEFAULT_NOTIFICATIONS },
    schedule:        { ...DEFAULT_SCHEDULE },
  };
}

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

function Checkbox({ checked, onChange, label: labelText, description }) {
  return (
    <label style={{ display: "flex", alignItems: "flex-start", gap: spacing.sm, cursor: "pointer", userSelect: "none" }}>
      <input
        type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
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

/* Multi-select strategy chip. */
function StrategyChip({ value, title, accent, selected, onToggle }) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={selected ? "true" : "false"}
      style={{
        display: "inline-flex", alignItems: "center", gap: 7,
        padding: "7px 12px", borderRadius: 8,
        borderTop: `1px solid ${selected ? `${accent}66` : colors.border.medium}`,
        borderRight: `1px solid ${selected ? `${accent}66` : colors.border.medium}`,
        borderBottom: `1px solid ${selected ? `${accent}66` : colors.border.medium}`,
        borderLeft: `3px solid ${accent}`,
        background: selected ? `${accent}1f` : colors.bg.input,
        color: selected ? colors.text.primary : colors.text.muted,
        fontSize: 12, fontWeight: 600, cursor: "pointer",
        transition: "background 0.15s, border-color 0.15s, color 0.15s",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{
        width: 14, height: 14, borderRadius: 4, flexShrink: 0,
        border: `1.5px solid ${selected ? accent : colors.border.light}`,
        background: selected ? accent : "transparent",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        color: "#0a0f1e", fontSize: 10, fontWeight: 900, lineHeight: 1,
      }}>
        {selected ? "✓" : ""}
      </span>
      {title}
    </button>
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

/* Small on/off pill toggle for "channel enabled". */
function Toggle({ checked, onChange, accent }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      aria-pressed={checked ? "true" : "false"}
      style={{
        position: "relative", width: 44, height: 24, borderRadius: 999, border: "none",
        background: checked ? (accent || colors.success) : colors.border.medium,
        cursor: "pointer", transition: "background 0.18s", flexShrink: 0,
      }}
    >
      <span style={{
        position: "absolute", top: 2, left: checked ? 22 : 2,
        width: 20, height: 20, borderRadius: "50%", background: "#fff",
        transition: "left 0.18s",
      }} />
    </button>
  );
}

/* ─────────────────────────────────────────────
   Panel Component (credentials side — unchanged)
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
      <div style={{
        padding:        `${spacing.md}px ${spacing.lg}px`,
        background:     colors.bg.tertiary,
        borderBottom:   `1px solid ${colors.border.medium}`,
        display:        "flex", alignItems: "center", justifyContent: "space-between",
        flexShrink:     0, gap: spacing.md, userSelect: "none",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, minWidth: 0, overflow: "hidden" }}>
          {!isPrimary && <span style={{ fontSize: 10, color: colors.text.muted, flexShrink: 0 }}>↗</span>}
          <span style={{ fontSize: 15, fontWeight: 600, color: colors.text.primary, flexShrink: 0 }}>{name}</span>
        </div>
      </div>

      <div onClick={(e) => isPrimary && e.stopPropagation()} style={{ flex: 1, overflow: "auto" }}>
        {isPrimary ? (
          <div style={{ padding: spacing.lg }}>{children}</div>
        ) : (
          isMobile ? (
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: spacing.sm,
              padding: `${spacing.sm}px ${spacing.lg}px`, fontSize: 12, fontWeight: 500,
              color: colors.text.muted, letterSpacing: "0.5px", textTransform: "uppercase",
            }}>
              <span style={{ fontSize: 10 }}>↕</span>
              {name}
              <span style={{ fontSize: 10, marginLeft: 4, color: colors.primary }}>tap to expand</span>
            </div>
          ) : (
            <div style={{
              writingMode: "vertical-rl", textAlign: "center", display: "flex",
              alignItems: "center", justifyContent: "center", height: "100%",
              fontSize: 12, fontWeight: 500, color: colors.text.muted,
              letterSpacing: "1px", textTransform: "uppercase", padding: spacing.md,
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
   Channel Card — the per-channel settings block
───────────────────────────────────────────── */

function ChannelCard({ channel, index, allowedStrategies, onChange }) {
  const accent = colors.primary;
  const set = (patch) => onChange({ ...channel, ...patch });
  const setNotif = (key, val) =>
    set({ notifications: { ...channel.notifications, [key]: val } });
  const setSched = (patch) =>
    set({ schedule: { ...channel.schedule, ...patch } });

  const toggleStrategy = (value) => {
    const cur = channel.strategy_filter || [];
    const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
    set({ strategy_filter: next });
  };

  const allSelected = (channel.strategy_filter || []).length === 0;
  const setAll = () => set({ strategy_filter: [] });

  const dim = !channel.enabled;

  return (
    <div style={{
      border: `1px solid ${channel.enabled ? colors.border.light : colors.border.medium}`,
      borderLeft: `3px solid ${channel.enabled ? accent : colors.border.medium}`,
      borderRadius: 10, background: colors.bg.secondary,
      padding: spacing.lg, marginBottom: spacing.lg,
      transition: "border-color 0.2s, opacity 0.2s",
    }}>
      {/* Header: name + enable toggle */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: colors.text.primary }}>
            {index === 1 ? "Channel 1" : "Channel 2"}
          </span>
          <span style={{ ...label, fontSize: 9 }}>
            {channel.enabled ? "Active" : "Off"}
          </span>
        </div>
        <Toggle checked={channel.enabled} onChange={(v) => set({ enabled: v })} accent={accent} />
      </div>

      {/* Chat ID */}
      <div style={{ marginBottom: spacing.lg, opacity: dim ? 0.55 : 1 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>
          Chat ID
        </div>
        <Input
          placeholder="e.g. -1001234567890 or a personal chat ID"
          value={channel.chat_id}
          onChange={(e) => set({ chat_id: e.target.value })}
          disabled={!channel.enabled}
        />
      </div>

      {/* Strategy multi-select */}
      <div style={{ marginBottom: spacing.lg, opacity: dim ? 0.55 : 1 }}>
        <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Strategies</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.sm }}>
          <button
            onClick={setAll}
            disabled={!channel.enabled}
            aria-pressed={allSelected ? "true" : "false"}
            style={{
              padding: "7px 12px", borderRadius: 8,
              border: `1px solid ${allSelected ? colors.primary : colors.border.medium}`,
              background: allSelected ? `${colors.primary}1f` : colors.bg.input,
              color: allSelected ? colors.text.primary : colors.text.muted,
              fontSize: 12, fontWeight: 600,
              cursor: channel.enabled ? "pointer" : "not-allowed",
            }}
          >
            All Strategies
          </button>
          {STRATEGY_OPTIONS.filter((o) => allowedStrategies(o.value)).map((o) => (
            <StrategyChip
              key={o.value}
              value={o.value}
              title={o.title}
              accent={STRATEGY_ACCENT[o.value] || colors.primary}
              selected={!allSelected && (channel.strategy_filter || []).includes(o.value)}
              onToggle={() => channel.enabled && toggleStrategy(o.value)}
            />
          ))}
        </div>
        <div style={{ fontSize: 11, color: colors.text.muted, marginTop: spacing.sm }}>
          {allSelected
            ? "Receiving alerts for all strategies."
            : `Receiving alerts for ${(channel.strategy_filter || []).length} selected.`}
        </div>
      </div>

      {/* Mode */}
      <div style={{ marginBottom: spacing.lg, opacity: dim ? 0.55 : 1 }}>
        <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Mode</div>
        <div style={{ display: "flex", gap: spacing.sm, flexWrap: "wrap" }}>
          {[
            { v: "all",   t: "All",   d: "Live + Paper" },
            { v: "live",  t: "Live",  d: "Real money" },
            { v: "paper", t: "Paper", d: "Simulated" },
          ].map((m) => (
            <button
              key={m.v}
              onClick={() => channel.enabled && set({ mode_filter: m.v })}
              disabled={!channel.enabled}
              aria-pressed={channel.mode_filter === m.v ? "true" : "false"}
              style={{
                padding: "7px 14px", borderRadius: 8,
                border: `1px solid ${channel.mode_filter === m.v ? colors.primary : colors.border.medium}`,
                background: channel.mode_filter === m.v ? `${colors.primary}1f` : colors.bg.input,
                color: channel.mode_filter === m.v ? colors.text.primary : colors.text.muted,
                fontSize: 12, fontWeight: 600,
                cursor: channel.enabled ? "pointer" : "not-allowed",
              }}
              title={m.d}
            >
              {m.t}
            </button>
          ))}
        </div>
      </div>

      {/* Notification types (the four) */}
      <div style={{ marginBottom: spacing.lg, opacity: dim ? 0.55 : 1 }}>
        <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Send</div>
        <div style={{
          padding: spacing.md, background: colors.bg.input, borderRadius: 8,
          display: "flex", flexDirection: "column", gap: spacing.md,
        }}>
          {NOTIFICATION_TYPES.map((t) => (
            <Checkbox
              key={t.key}
              checked={!!channel.notifications[t.key]}
              onChange={(v) => channel.enabled && setNotif(t.key, v)}
              label={t.title}
              description={t.desc}
            />
          ))}
        </div>
      </div>

      {/* Schedule */}
      <div style={{ opacity: dim ? 0.55 : 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm }}>
          <div style={{ ...label, fontSize: 9 }}>Schedule</div>
          <Checkbox
            checked={!!channel.schedule.enabled}
            onChange={(v) => channel.enabled && setSched({ enabled: v })}
            label="Limit to a time window"
          />
        </div>
        {channel.schedule.enabled && (
          <div style={{
            padding: spacing.md, background: colors.bg.input, borderRadius: 8,
          }}>
            <div style={{ display: "flex", gap: spacing.md, alignItems: "flex-end", flexWrap: "wrap" }}>
              <div>
                <div style={{ fontSize: 11, color: colors.text.muted, marginBottom: spacing.xs }}>Start</div>
                <Input
                  type="time"
                  value={channel.schedule.start}
                  onChange={(e) => setSched({ start: e.target.value })}
                  disabled={!channel.enabled}
                  style={{ width: 130 }}
                />
              </div>
              <div>
                <div style={{ fontSize: 11, color: colors.text.muted, marginBottom: spacing.xs }}>End</div>
                <Input
                  type="time"
                  value={channel.schedule.end}
                  onChange={(e) => setSched({ end: e.target.value })}
                  disabled={!channel.enabled}
                  style={{ width: 130 }}
                />
              </div>
            </div>
            <div style={{
              marginTop: spacing.sm, fontSize: 11, color: colors.warning,
              display: "flex", alignItems: "center", gap: 6,
            }}>
              <span>⏱</span>
              Alerts fire only when triggered at or after Start and before End.
              The daily summary fires at 15:30 — set End after 15:30 (e.g. 15:45) to receive it.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Main Component
───────────────────────────────────────────── */

export default function Connections() {
  const { allowsStrategy, isAdminUi } = useEntitlements();

  const [loading, setLoading] = useState(true);
  const [primaryPanel, setPrimaryPanel] = useState("services");

  // Zerodha state
  const [status, setStatus] = useState(null);
  const [globalConfig, setGlobalConfig] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [editingZerodha, setEditingZerodha] = useState(false);

  // Dhan (data-only, backfill) state
  const [dhanClientId, setDhanClientId] = useState("");
  const [dhanToken, setDhanToken] = useState("");
  const [dhanCredsSet, setDhanCredsSet] = useState(false);
  const [dhanSavedClientId, setDhanSavedClientId] = useState("");
  const [dhanSaving, setDhanSaving] = useState(false);

  // Telegram — shared bot token + channels
  const [botToken, setBotToken] = useState("");
  const [telegramConfigured, setTelegramConfigured] = useState(false);
  const [editingToken, setEditingToken] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testChatId, setTestChatId] = useState("");
  const [channels, setChannels] = useState([emptyChannel(1), emptyChannel(2)]);
  const [saving, setSaving] = useState(false);

  // Mobile detection
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  useEffect(() => {
    if (!isAdminUi && primaryPanel === "notifications") {
      setPrimaryPanel("services");
    }
  }, [isAdminUi, primaryPanel]);

  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  useEffect(() => {
    refresh();
    if (isAdminUi) loadTelegramConfig();
  }, [isAdminUi]);

  async function refresh() {
    setLoading(true);
    try {
      const [st, global] = await Promise.all([getZerodhaStatus(), getGlobalConfig()]);
      setStatus(st);
      setGlobalConfig(global);
      // Dhan status (backtest-scoped, admin-gated). Non-fatal if unavailable.
      try {
        const ds = await fetch(`${getApiBase()}/api/backtest/dhan/status`).then(r => r.ok ? r.json() : null);
        if (ds) {
          setDhanCredsSet(!!ds.creds_set);
          setDhanSavedClientId(ds.client_id || "");
        }
      } catch { /* ignore */ }
    } catch (e) {
      console.error("Refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }

  function normalizeChannels(raw) {
    const out = [emptyChannel(1), emptyChannel(2)];
    (raw || []).slice(0, 2).forEach((c, i) => {
      out[i] = {
        ...emptyChannel(i + 1),
        ...c,
        notifications: { ...DEFAULT_NOTIFICATIONS, ...(c.notifications || {}) },
        schedule: { ...DEFAULT_SCHEDULE, ...(c.schedule || {}) },
        strategy_filter: Array.isArray(c.strategy_filter) ? c.strategy_filter : [],
      };
    });
    return out;
  }

  async function loadTelegramConfig() {
    try {
      const config = await getTelegramConfig();
      if (config) {
        setBotToken(config.bot_token || "");
        setTelegramConfigured(!!config.bot_token);
        setChannels(normalizeChannels(config.channels));
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
    await saveZerodhaCredentials(apiKey, apiSecret);
    alert("Credentials saved. Please login to Zerodha.");
    setApiSecret("");
    setEditingZerodha(false);
    setStatus(null);
    await refresh();
  }

  async function handleSaveDhanCreds() {
    if (!dhanClientId || !dhanToken) {
      alert("Dhan Client ID and Access Token are required");
      return;
    }
    setDhanSaving(true);
    try {
      const res = await fetch(`${getApiBase()}/api/backtest/dhan/creds`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: dhanClientId.trim(), access_token: dhanToken.trim() }),
      });
      if (!res.ok) throw new Error(await res.text());
      setDhanCredsSet(true);
      setDhanSavedClientId(dhanClientId.trim());
      setDhanToken("");   // don't keep the token in component state
      alert("✅ Dhan credentials saved (data backfill only).");
    } catch (e) {
      alert("❌ Failed to save Dhan credentials: " + (e.message || e));
    } finally {
      setDhanSaving(false);
    }
  }

  function buildConfigPayload() {
    return { bot_token: botToken, channels };
  }

  async function saveBotToken() {
    if (!botToken) {
      alert("Bot Token is required");
      return;
    }
    try {
      await saveTelegramConfig(buildConfigPayload());
      setTelegramConfigured(true);
      setEditingToken(false);
      alert("✅ Telegram bot token saved!");
    } catch (e) {
      alert("❌ Failed to save: " + e.message);
    }
  }

  async function login() {
    const res = await getZerodhaLoginUrl();
    const login_url = res?.login_url;
    if (!login_url) { alert("Login URL not received from backend"); return; }
    if (window.__TAURI__?.shell?.open) await window.__TAURI__.shell.open(login_url);
    else window.open(login_url, "_blank");
  }

  async function enable()  { await setGlobalTradeSwitch(true);  await refresh(); }
  async function disable() { await setGlobalTradeSwitch(false); await refresh(); }

  async function testConnection() {
    if (!botToken || !testChatId) {
      alert("Enter the Bot Token and a Chat ID to test");
      return;
    }
    setTesting(true);
    try {
      const result = await testTelegramConnection(botToken, testChatId);
      if (result.success) alert("✅ Test message sent! Check that Telegram chat.");
      else alert("❌ Failed to send test message. Check the token and chat ID.");
    } catch (e) {
      alert("❌ Connection failed: " + e.message);
    } finally {
      setTesting(false);
    }
  }

  async function saveNotificationSettings() {
    setSaving(true);
    try {
      await saveTelegramConfig(buildConfigPayload());
      alert("✅ Channel settings saved!");
    } catch (e) {
      alert("❌ Failed to save: " + e.message);
    } finally {
      setSaving(false);
    }
  }

  function openBotFatherGuide() {
    const url = "https://core.telegram.org/bots#6-botfather";
    if (window.__TAURI__?.shell?.open) window.__TAURI__.shell.open(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  }

  function openChatIdGuide() {
    const url = "https://t.me/userinfobot";
    if (window.__TAURI__?.shell?.open) window.__TAURI__.shell.open(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  }

  function updateChannel(idx, next) {
    setChannels((prev) => prev.map((c, i) => (i === idx ? next : c)));
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

  const servicesIsPrimary = isAdminUi ? primaryPanel === "services" : true;

  return (
    <div style={{ padding: isMobile ? spacing.md : spacing.xxl, background: colors.bg.primary, color: colors.text.primary, minHeight: "100vh" }}>
      <div style={{ marginBottom: spacing.xl }}>
        <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700, lineHeight: 1.2 }}>Connections</h1>
        <p style={{ margin: 0, marginTop: spacing.xs, fontSize: 13, color: colors.text.secondary }}>
          Manage external service integrations
        </p>
      </div>

      <div style={{
        display: "flex", flexDirection: isMobile ? "column" : "row",
        gap: spacing.lg, minHeight: isMobile ? "auto" : "500px",
      }}>

        {/* ═══════════════════════════════════════════════════════════
            PANEL 1: SERVICE CREDENTIALS
        ═══════════════════════════════════════════════════════════ */}
        <div style={getPanelStyle(servicesIsPrimary, isMobile)}>
          <Panel
            name="🔗 Service Credentials"
            isPrimary={servicesIsPrimary}
            onBecomePrimary={() => setPrimaryPanel("services")}
            isMobile={isMobile}
          >
            {/* ZERODHA SECTION */}
            <div style={{
              marginBottom: spacing.xxl, padding: spacing.lg, background: colors.bg.input,
              border: `1px solid ${colors.border.medium}`, borderRadius: 8
            }}>
              <div style={{
                ...label, marginBottom: spacing.md, paddingBottom: spacing.sm,
                borderBottom: `1px solid ${colors.border.medium}`,
                display: "flex", alignItems: "center", gap: spacing.sm
              }}>
                <span style={{ fontSize: 14 }}>🔗</span>
                <span>Zerodha Integration</span>
              </div>

              <div style={{ marginBottom: spacing.lg }}>
                <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.sm, marginBottom: spacing.md }}>
                  {!zerodhaConfigured && <StatusBadge type="warning" text="Not Configured" icon="⚙️" />}
                  {zerodhaConfigured && !zerodhaConnected && (
                    <>
                      {sessionExpired
                        ? <StatusBadge type="danger" text="Session Expired" icon="⏰" />
                        : <StatusBadge type="warning" text="Not Connected" icon="🔌" />}
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

              {zerodhaConfigured && !zerodhaConnected && !editingZerodha && (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <p style={{ fontSize: 13, color: colors.text.secondary, margin: 0 }}>
                    {sessionExpired ? "Session expired. Please login again." : "Login to Zerodha to start trading."}
                  </p>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={login} style={{ flex: 1 }}>🔐 Login to Zerodha</Button>
                    <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ flex: 1 }}>Edit Credentials</Button>
                  </div>
                </div>
              )}

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
                      {tradingEnabled
                        ? <Button onClick={disable} variant="danger">⏸ Disable</Button>
                        : <Button onClick={enable} variant="success">▶ Enable</Button>}
                    </div>
                  </div>
                  <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ width: "100%" }}>Edit Credentials</Button>
                </div>
              )}
            </div>

            {/* RELAY SECTION */}
            <RelayPanel />

            {/* DHAN — data-only, backfill. Admin-gated like the rest of this page. */}
            {isAdminUi && (
            <div style={{
              marginTop: spacing.xxl, marginBottom: spacing.xxl, padding: spacing.lg, background: colors.bg.input,
              border: `1px solid ${colors.border.medium}`, borderRadius: 8
            }}>
              <div style={{
                ...label, marginBottom: spacing.md, paddingBottom: spacing.sm,
                borderBottom: `1px solid ${colors.border.medium}`,
                display: "flex", alignItems: "center", gap: spacing.sm
              }}>
                <span style={{ fontSize: 14 }}>📈</span>
                <span>Dhan (data backfill only)</span>
              </div>

              <div style={{ marginBottom: spacing.lg }}>
                <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
                <StatusBadge
                  type={dhanCredsSet ? "success" : "warning"}
                  text={dhanCredsSet ? `Set · Client ${dhanSavedClientId}` : "Not Configured"}
                  icon={dhanCredsSet ? "✓" : "⚙️"}
                />
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>Client ID</div>
                  <Input placeholder="Dhan numeric Client ID" value={dhanClientId} onChange={(e) => setDhanClientId(e.target.value)} />
                </div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary, marginBottom: spacing.xs }}>Access Token (24h)</div>
                  <Input type="password" placeholder="Paste today's Dhan access token" value={dhanToken} onChange={(e) => setDhanToken(e.target.value)} />
                </div>
                <div style={{ padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                  ℹ️ Used ONLY to backfill historical option candles into the backtest corpus — never for live orders. Dhan tokens expire every 24h; re-paste when needed. Requires the Dhan Data API subscription.
                </div>
                <Button onClick={handleSaveDhanCreds} disabled={dhanSaving || !dhanClientId || !dhanToken}>
                  {dhanSaving ? "Saving…" : "Save Dhan Credentials"}
                </Button>
              </div>
            </div>
            )}

            {/* TELEGRAM BOT TOKEN — admin-only, shared across both channels */}
            {isAdminUi && (
            <div style={{
              padding: spacing.lg, background: colors.bg.input,
              border: `1px solid ${colors.border.medium}`, borderRadius: 8
            }}>
              <div style={{
                ...label, marginBottom: spacing.md, paddingBottom: spacing.sm,
                borderBottom: `1px solid ${colors.border.medium}`,
                display: "flex", alignItems: "center", gap: spacing.sm
              }}>
                <span style={{ fontSize: 14 }}>📱</span>
                <span>Telegram Bot</span>
              </div>

              <div style={{ marginBottom: spacing.lg }}>
                <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
                <StatusBadge
                  type={telegramConfigured ? "success" : "warning"}
                  text={telegramConfigured ? "Bot Token Set" : "Not Configured"}
                  icon={telegramConfigured ? "✓" : "⚙️"}
                />
              </div>

              {(!telegramConfigured || editingToken) ? (
                <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.xs }}>
                      <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary }}>Bot Token (shared)</div>
                      <button onClick={openBotFatherGuide} style={{ background: "none", border: "none", color: colors.primary, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
                        How to get?
                      </button>
                    </div>
                    <Input type="password" placeholder="Enter Bot Token" value={botToken} onChange={(e) => setBotToken(e.target.value)} />
                  </div>

                  <div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.xs }}>
                      <div style={{ fontSize: 12, fontWeight: 500, color: colors.text.secondary }}>Test Chat ID</div>
                      <button onClick={openChatIdGuide} style={{ background: "none", border: "none", color: colors.primary, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
                        Get a Chat ID
                      </button>
                    </div>
                    <Input placeholder="Chat ID to send a test message to" value={testChatId} onChange={(e) => setTestChatId(e.target.value)} />
                  </div>

                  <div style={{ padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                    ℹ️ One bot, two destinations. Set the shared token here, then point each channel below at its own chat or group.
                  </div>
                  <div style={{ display: "flex", gap: spacing.sm }}>
                    <Button onClick={testConnection} variant="secondary" disabled={testing || !botToken || !testChatId}>
                      {testing ? "Testing..." : "🧪 Test"}
                    </Button>
                    <Button onClick={saveBotToken} disabled={!botToken}>Save Bot Token</Button>
                    {telegramConfigured && (
                      <Button onClick={() => setEditingToken(false)} variant="secondary">Cancel</Button>
                    )}
                  </div>
                </div>
              ) : (
                <Button onClick={() => setEditingToken(true)} variant="secondary" style={{ width: "100%" }}>
                  Edit Bot Token
                </Button>
              )}
            </div>
            )}
          </Panel>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            PANEL 2: TELEGRAM CHANNELS — admin-only
        ═══════════════════════════════════════════════════════════ */}
        {isAdminUi && (
        <div style={getPanelStyle(primaryPanel === "notifications", isMobile)}>
          <Panel
            name="📣 Notification Channels"
            isPrimary={primaryPanel === "notifications"}
            onBecomePrimary={() => setPrimaryPanel("notifications")}
            isMobile={isMobile}
          >
            {!telegramConfigured ? (
              <div style={{ padding: spacing.lg, background: colors.bg.input, borderRadius: 6, textAlign: "center" }}>
                <div style={{ fontSize: 13, color: colors.text.muted, marginBottom: spacing.md }}>
                  Set the shared bot token in Service Credentials first, then configure channels here.
                </div>
                <Button onClick={() => setPrimaryPanel("services")} variant="secondary">
                  → Go to Service Credentials
                </Button>
              </div>
            ) : (
              <>
                <div style={{ ...label, marginBottom: spacing.md }}>
                  Two channels · independent settings
                </div>
                <div style={{ fontSize: 12, color: colors.text.secondary, marginBottom: spacing.lg }}>
                  Each channel sends to its own chat with its own strategies, mode, alert types, and schedule.
                </div>

                {channels.map((ch, i) => (
                  <ChannelCard
                    key={ch.id || i}
                    channel={ch}
                    index={i + 1}
                    allowedStrategies={allowsStrategy}
                    onChange={(next) => updateChannel(i, next)}
                  />
                ))}

                <Button onClick={saveNotificationSettings} disabled={saving} style={{ width: "100%" }}>
                  {saving ? "Saving..." : "💾 Save Channel Settings"}
                </Button>

                <div style={{ marginTop: spacing.lg, padding: spacing.sm, background: colors.bg.input, borderRadius: 6, fontSize: 11, color: colors.text.muted }}>
                  ℹ️ Notifications are free and unlimited. A disabled channel sends nothing.
                </div>
              </>
            )}
          </Panel>
        </div>
        )}

      </div>
    </div>
  );
}
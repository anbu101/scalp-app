/**
 * RelayPanel.jsx — src/components/RelayPanel.jsx
 *
 * "Static IP (Order Relay)" section for the Connections page.
 * Handles OCI relay deployment, status display, and disable.
 *
 * Drop this into Connections.jsx alongside the Zerodha and Telegram panels.
 */

import { useEffect, useRef, useState } from "react";
import { getApiBase } from "../api/base";

// ── Design tokens (match Connections.jsx) ────────────────────────
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

const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 28 };

const label = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

// ── Small atoms ───────────────────────────────────────────────────

function Input({ type = "text", value, onChange, placeholder, disabled, style, rows }) {
  const base = {
    padding: "8px 11px", borderRadius: 6,
    border: `1px solid ${disabled ? colors.border.dark : colors.border.medium}`,
    background: disabled ? colors.bg.tertiary : colors.bg.input,
    color: disabled ? colors.text.muted : colors.text.primary,
    fontSize: 13, outline: "none", width: "100%",
    transition: "border-color 0.15s", ...style,
  };
  if (rows) {
    return (
      <textarea
        value={value} onChange={onChange} placeholder={placeholder}
        disabled={disabled} rows={rows}
        style={{ ...base, resize: "vertical", fontFamily: "monospace", fontSize: 11 }}
        onFocus={e => !disabled && (e.target.style.borderColor = colors.primary)}
        onBlur={e => (e.target.style.borderColor = disabled ? colors.border.dark : colors.border.medium)}
      />
    );
  }
  return (
    <input
      type={type} value={value} onChange={onChange}
      placeholder={placeholder} disabled={disabled}
      style={base}
      onFocus={e => !disabled && (e.target.style.borderColor = colors.primary)}
      onBlur={e => (e.target.style.borderColor = disabled ? colors.border.dark : colors.border.medium)}
    />
  );
}

function Button({ onClick, children, variant = "primary", disabled, style }) {
  const variants = {
    primary:   { bg: colors.primary,    hover: colors.primaryHover, color: "#fff" },
    success:   { bg: colors.success,    hover: "#059669",           color: "#fff" },
    danger:    { bg: colors.danger,     hover: "#dc2626",           color: "#fff" },
    secondary: { bg: colors.bg.tertiary, hover: colors.border.light, color: colors.text.secondary },
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
        transition: "all 0.2s ease", ...style,
      }}
      onMouseEnter={e => !disabled && (e.currentTarget.style.background = v.hover)}
      onMouseLeave={e => !disabled && (e.currentTarget.style.background = v.bg)}
    >
      {children}
    </button>
  );
}

function StatusBadge({ type, text, icon }) {
  const styles = {
    success: { bg: colors.successBg, color: colors.success },
    warning: { bg: colors.warningBg, color: colors.warning },
    danger:  { bg: colors.dangerBg,  color: colors.danger  },
  };
  const s = styles[type] || styles.warning;
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: spacing.sm,
      padding: "6px 12px", borderRadius: 6,
      background: s.bg, color: s.color,
      border: `1px solid ${s.color}40`,
      fontSize: 13, fontWeight: 600,
    }}>
      {icon && <span style={{ fontSize: 14 }}>{icon}</span>}
      {text}
    </div>
  );
}

// ── Step indicator ────────────────────────────────────────────────

function ProgressLog({ steps }) {
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps]);

  if (!steps.length) return null;

  return (
    <div style={{
      marginTop: spacing.md,
      padding: spacing.md,
      background: colors.bg.primary,
      border: `1px solid ${colors.border.dark}`,
      borderRadius: 6,
      maxHeight: 180,
      overflowY: "auto",
      fontFamily: "monospace",
      fontSize: 11,
    }}>
      {steps.map((s, i) => (
        <div key={i} style={{
          color: s.type === "error" ? colors.danger
               : s.type === "success" ? colors.success
               : colors.text.secondary,
          marginBottom: 2,
          display: "flex", gap: 8,
        }}>
          <span style={{ color: colors.text.muted, flexShrink: 0 }}>
            {s.type === "success" ? "✓" : s.type === "error" ? "✗" : "›"}
          </span>
          {s.message}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────

export default function RelayPanel() {
  const [status, setStatus]         = useState(null);   // relay status from backend
  const [loading, setLoading]       = useState(true);
  const [showForm, setShowForm]     = useState(false);
  const [deploying, setDeploying]   = useState(false);
  const [steps, setSteps]           = useState([]);

  // Form state
  const [host, setHost]             = useState("");
  const [sshUser, setSshUser]       = useState("opc");
  const [sshKey, setSshKey]         = useState("");

  // ── Load status on mount and every 30s ─────────────────────────
  useEffect(() => {
    loadStatus();
    const t = setInterval(loadStatus, 30_000);
    return () => clearInterval(t);
  }, []);

  async function loadStatus() {
    try {
      const res = await fetch(`${getApiBase()}/api/relay/status`);
      const data = await res.json();
      setStatus(data);
    } catch {
      setStatus({ configured: false, active: false });
    } finally {
      setLoading(false);
    }
  }

  // ── Deploy ──────────────────────────────────────────────────────
  async function handleDeploy() {
    if (!host.trim()) {
      alert("Please enter the OCI public IP address.");
      return;
    }
    if (!sshKey.trim()) {
      alert("Please paste your SSH private key.");
      return;
    }

    setDeploying(true);
    setSteps([{ type: "info", message: `Connecting to ${host}...` }]);

    try {
      const res = await fetch(`${getApiBase()}/api/relay/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host: host.trim(),
          ssh_username: sshUser.trim() || "opc",
          ssh_private_key: sshKey.trim(),
        }),
      });

      // Stream newline-delimited JSON progress
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const event = JSON.parse(line);
            if (event.type === "progress") {
              setSteps(prev => [...prev, { type: "info", message: event.message }]);
            } else if (event.type === "result") {
              setSteps(prev => [...prev, {
                type: event.success ? "success" : "error",
                message: event.message,
              }]);
              if (event.success) {
                setTimeout(() => {
                  setShowForm(false);
                  loadStatus();
                }, 1500);
              }
            }
          } catch { /* malformed line */ }
        }
      }
    } catch (e) {
      setSteps(prev => [...prev, { type: "error", message: `Request failed: ${e.message}` }]);
    } finally {
      setDeploying(false);
    }
  }

  // ── Disable ─────────────────────────────────────────────────────
  async function handleDisable() {
    if (!confirm("Disable the order relay? Orders will go direct from your machine.\n\nFrom April 1 this will cause Zerodha to reject orders.")) return;
    await fetch(`${getApiBase()}/api/relay/disable`, { method: "POST" });
    await loadStatus();
  }

  // ── Render ───────────────────────────────────────────────────────

  const isActive     = status?.active;
  const isConfigured = status?.configured;

  return (
    <div style={{
      padding: spacing.lg,
      background: colors.bg.input,
      border: `1px solid ${colors.border.medium}`,
      borderRadius: 8,
    }}>
      {/* Section header */}
      <div style={{
        ...label,
        marginBottom: spacing.md,
        paddingBottom: spacing.sm,
        borderBottom: `1px solid ${colors.border.medium}`,
        display: "flex", alignItems: "center", gap: spacing.sm,
      }}>
        <span style={{ fontSize: 14 }}>🛡️</span>
        <span>Static IP — Order Relay</span>
        <span style={{
          marginLeft: "auto", fontSize: 9, fontWeight: 600,
          padding: "2px 6px", borderRadius: 3,
          background: "rgba(239,68,68,0.15)", color: colors.danger,
          border: `1px solid ${colors.danger}30`,
        }}>
          Required from April 1
        </span>
      </div>

      {/* Why this is needed */}
      <div style={{
        padding: spacing.md,
        background: "rgba(245,158,11,0.07)",
        border: `1px solid rgba(245,158,11,0.2)`,
        borderRadius: 6,
        fontSize: 12,
        color: colors.text.secondary,
        marginBottom: spacing.lg,
        lineHeight: 1.6,
      }}>
        <strong style={{ color: colors.warning }}>SEBI regulation from April 1:</strong>{" "}
        Zerodha will only accept order placement from a registered static IP address.
        This feature routes your orders through your own cloud server (OCI free instance)
        which has a permanent static IP.
      </div>

      {/* Status */}
      {!loading && (
        <div style={{ marginBottom: spacing.lg }}>
          <div style={{ ...label, fontSize: 9, marginBottom: spacing.sm }}>Status</div>
          {isActive ? (
            <div style={{ display: "flex", alignItems: "center", gap: spacing.md }}>
              <StatusBadge type="success" text="Relay Active" icon="✓" />
              <span style={{ fontSize: 12, color: colors.text.muted, fontFamily: "monospace" }}>
                {status.host}
              </span>
            </div>
          ) : isConfigured ? (
            <StatusBadge type="warning" text="Relay Unreachable" icon="⚠️" />
          ) : (
            <StatusBadge type="danger" text="Not Configured" icon="✗" />
          )}
        </div>
      )}

      {/* Active state — show host + disable option */}
      {isActive && !showForm && (
        <div style={{ display: "flex", gap: spacing.sm }}>
          <Button
            onClick={() => { setShowForm(true); setSteps([]); }}
            variant="secondary"
            style={{ flex: 1 }}
          >
            Redeploy / Change IP
          </Button>
          <Button onClick={handleDisable} variant="danger">
            Disable
          </Button>
        </div>
      )}

      {/* Not configured — show setup prompt */}
      {!isActive && !showForm && (
        <Button
          onClick={() => { setShowForm(true); setSteps([]); }}
          style={{ width: "100%" }}
        >
          ⚙️ Set Up Static IP Relay
        </Button>
      )}

      {/* Setup form */}
      {showForm && (
        <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>

          {/* OCI IP */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: colors.text.secondary, marginBottom: spacing.xs }}>
              OCI Instance Public IP
            </div>
            <Input
              placeholder="e.g. 144.24.159.177"
              value={host}
              onChange={e => setHost(e.target.value)}
              disabled={deploying}
            />
            <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 4 }}>
              Found in Oracle Cloud → Compute → Instances → your instance → Networking tab
            </div>
          </div>

          {/* SSH username */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: colors.text.secondary, marginBottom: spacing.xs }}>
              SSH Username
            </div>
            <Input
              placeholder="opc"
              value={sshUser}
              onChange={e => setSshUser(e.target.value)}
              disabled={deploying}
            />
            <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 4 }}>
              Almost always <code style={{ color: colors.warning }}>opc</code> for OCI opc instances
            </div>
          </div>

          {/* SSH private key */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: colors.text.secondary, marginBottom: spacing.xs }}>
              SSH Private Key
            </div>
            <Input
              placeholder={`-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----`}
              value={sshKey}
              onChange={e => setSshKey(e.target.value)}
              disabled={deploying}
              rows={6}
            />
            <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 4 }}>
              Paste the contents of the <code style={{ color: colors.warning }}>.key</code> file
              you downloaded when creating your OCI instance.
              Open it in Notepad / TextEdit and copy everything including the
              <code style={{ color: colors.warning }}> -----BEGIN</code> and
              <code style={{ color: colors.warning }}> -----END</code> lines.
            </div>
          </div>

          {/* Progress log */}
          <ProgressLog steps={steps} />

          {/* Action buttons */}
          <div style={{ display: "flex", gap: spacing.sm }}>
            <Button
              onClick={handleDeploy}
              disabled={deploying || !host.trim() || !sshKey.trim()}
              style={{ flex: 1 }}
            >
              {deploying ? "⏳ Deploying..." : "🚀 Deploy Relay"}
            </Button>
            <Button
              onClick={() => { setShowForm(false); setSteps([]); }}
              variant="secondary"
              disabled={deploying}
            >
              Cancel
            </Button>
          </div>

          {deploying && (
            <div style={{ fontSize: 11, color: colors.text.muted, textAlign: "center" }}>
              This takes about 60–90 seconds. Please keep this window open.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
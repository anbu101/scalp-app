// frontend/src/components/AngelAccountCard.jsx
// ============================================================
// ACC2 BEGIN — Account 2 (Angel One) card for Connections page
//
// Self-contained: fetches /api/acc2/status on mount, saves
// credentials, and exposes the D3 "Force Login" button. Mounted
// from Connections.jsx with a one-line patch (see ACC2 W1
// integration notes). Visible to ALL users (D6).
// ============================================================

import { useEffect, useState } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing } from "../tokens";

// Token shapes are NESTED (colors.bg.input, colors.border.medium) — see
// src/tokens.js and the Connections page's local palette. Flat access like
// colors.border stringifies an object into the CSS and kills the border.
const field = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  marginBottom: spacing?.sm || 8,
  borderRadius: 6,
  border: `1px solid ${colors?.border?.medium || "#243044"}`,
  background: colors?.bg?.input || "#060d1a",
  color: colors?.text?.primary || "#f1f5f9",
  fontSize: 13,
};

const btn = (variant) => ({
  padding: "8px 14px",
  borderRadius: 6,
  border: "none",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
  marginRight: 8,
  // ── THEME_PHASE2B_20260831 ── secondary variant follows the theme (was fixed #374151)
  background: variant === "primary" ? colors.primary : colors.bg.tertiary,
  color: variant === "primary" ? "#fff" : colors.text.primary,
  boxShadow: variant === "primary" ? "none" : `inset 0 0 0 1px ${colors.border.light}`,
});

export default function AngelAccountCard() {
  const [status, setStatus] = useState(null);
  const [creds, setCreds] = useState({
    api_key: "", client_code: "", pin: "", totp_secret: "",
  });
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function refresh() {
    try {
      const r = await fetch(`${getApiBase()}/api/acc2/status`);
      if (r.ok) setStatus(await r.json());
    } catch { /* backend not up yet */ }
  }

  useEffect(() => { refresh(); }, []);

  async function saveCreds() {
    setBusy(true); setMsg("");
    try {
      const r = await fetch(`${getApiBase()}/api/acc2/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds),
      });
      const j = await r.json();
      setMsg(j.connected
        ? "Saved — connected."
        : `Saved — login failed: ${j.last_error || "check credentials"}`);
      setEditing(false);
      setCreds({ api_key: "", client_code: "", pin: "", totp_secret: "" });
      refresh();
    } catch (e) {
      setMsg(`Save failed: ${e}`);
    } finally { setBusy(false); }
  }

  async function forceLogin() {
    setBusy(true); setMsg("");
    try {
      const r = await fetch(`${getApiBase()}/api/acc2/force-login`, {
        method: "POST",
      });
      const j = await r.json();
      setMsg(j.connected
        ? "Login OK."
        : `Login failed: ${j.last_error || "unknown"}`);
      refresh();
    } catch (e) {
      setMsg(`Login error: ${e}`);
    } finally { setBusy(false); }
  }

  const connected = !!status?.connected;
  const configured = !!status?.configured;

  return (
    <div style={{
      border: `1px solid ${colors?.border?.medium || "#243044"}`,
      borderRadius: 8,
      padding: spacing?.lg || 16,
      marginBottom: spacing?.xxl || 28,
      background: colors?.bg?.input || "#060d1a",
    }}>
      <div style={{ display: "flex", alignItems: "center",
                    justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          Account 2 — Angel One
          <span style={{ fontWeight: 400, fontSize: 12, opacity: 0.7,
                         marginLeft: 8 }}>
            (optional secondary execution account)
          </span>
        </div>
        <span style={{
          fontSize: 12, fontWeight: 700,
          color: connected ? colors.success : (configured ? colors.warning : colors.text.muted),
        }}>
          {connected ? "● CONNECTED"
            : configured ? "● NOT CONNECTED" : "● NOT CONFIGURED"}
        </span>
      </div>

      {configured && !editing && (
        <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 10 }}>
          Client: {status?.client_code || "—"}
          {status?.last_login &&
            ` · last login ${String(status.last_login).slice(11, 19)} IST`}
          {status?.last_error && !connected &&
            ` · ${status.last_error}`}
        </div>
      )}

      {(editing || !configured) && (
        <div style={{ maxWidth: 420 }}>
          <input style={field} placeholder="SmartAPI API Key"
            value={creds.api_key}
            onChange={(e) => setCreds({ ...creds, api_key: e.target.value })} />
          <input style={field} placeholder="Client Code"
            value={creds.client_code}
            onChange={(e) => setCreds({ ...creds, client_code: e.target.value })} />
          <input style={field} placeholder="PIN" type="password"
            value={creds.pin}
            onChange={(e) => setCreds({ ...creds, pin: e.target.value })} />
          <input style={field} placeholder="TOTP Secret (base32)" type="password"
            value={creds.totp_secret}
            onChange={(e) => setCreds({ ...creds, totp_secret: e.target.value })} />
          <div style={{ fontSize: 11, opacity: 0.65, marginBottom: 10 }}>
            This machine's public IP must be registered as the Static IP on
            your Angel SmartAPI app. Credentials are stored only on this
            machine.
          </div>
        </div>
      )}

      <div>
        {(editing || !configured) ? (
          <>
            <button style={btn("primary")} disabled={busy} onClick={saveCreds}>
              {busy ? "Saving…" : "Save & Connect"}
            </button>
            {configured && (
              <button style={btn()} disabled={busy}
                onClick={() => setEditing(false)}>Cancel</button>
            )}
          </>
        ) : (
          <>
            <button style={btn("primary")} disabled={busy} onClick={forceLogin}>
              {busy ? "Logging in…" : "Force Login"}
            </button>
            <button style={btn()} disabled={busy}
              onClick={() => setEditing(true)}>Edit Credentials</button>
          </>
        )}
      </div>

      {msg && (
        <div style={{ fontSize: 12, marginTop: 8,
                      color: /OK|connected/i.test(msg) ? "#22c55e" : "#f59e0b" }}>
          {msg}
        </div>
      )}
    </div>
  );
}
// ACC2 END
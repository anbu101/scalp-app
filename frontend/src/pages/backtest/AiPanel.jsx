// frontend/src/pages/backtest/AiPanel.jsx
//
// ── AI_PANEL ── local-AI management, built for non-technical users:
//   · Not installed → plain-language card with the install address, and a
//     "check again" button. We never auto-install (that needs admin rights);
//     one guided download is the honest UX.
//   · Installed → the whole model lifecycle in-app: curated model menu with
//     DISK SIZES shown BEFORE download, one-click Download with a live
//     progress bar (Ollama streams pull progress), radio to pick the Active
//     model, 🗑 to delete a model and reclaim the disk instantly.
//   · Fail-open by design: nothing else in the app depends on this panel.
//
// Self-contained; takes design primitives + apiCall from the host page.

import React, { useEffect, useState, useCallback, useRef } from "react";

export default function AiPanel({ colors, spacing, typography, Card, apiCall }) {
  const c = colors;
  const [st, setSt] = useState(null);        // /ai/status payload
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [customName, setCustomName] = useState("");
  const poll = useRef(null);

  const refresh = useCallback(async () => {
    try { setSt(await apiCall("/api/backtest/ai/status")); }
    catch (e) { setMsg({ kind: "err", text: String(e.message || e) }); }
  }, [apiCall]);

  useEffect(() => { refresh(); }, [refresh]);

  // poll while a download is running
  const pulling = !!st?.pull?.running;
  useEffect(() => {
    clearInterval(poll.current);
    if (pulling) poll.current = setInterval(refresh, 1500);
    return () => clearInterval(poll.current);
  }, [pulling, refresh]);

  useEffect(() => {
    if (msg && msg.kind === "ok") {
      const t = setTimeout(() => setMsg(null), 5000);
      return () => clearTimeout(t);
    }
  }, [msg]);

  const call = useCallback(async (path, body, okText) => {
    setBusy(true);
    try {
      await apiCall(path, body ? { method: "POST", body: JSON.stringify(body) } : { method: "POST" });
      if (okText) setMsg({ kind: "ok", text: okText });
      await refresh();
    } catch (e) { setMsg({ kind: "err", text: String(e.message || e) }); }
    finally { setBusy(false); }
  }, [apiCall, refresh]);

  const smallBtn = (variant, disabled) => ({
    padding: "6px 12px", borderRadius: 6, border: "none",
    cursor: disabled ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 600,
    opacity: disabled ? 0.5 : 1,
    background: variant === "primary" ? c.primary : c.bg.tertiary,
    color: variant === "primary" ? "#fff" : c.text.primary,
  });
  const gb = (bytes) => `${(bytes / 1e9).toFixed(1)} GB`;

  const installed = !!st?.installed;
  const models = st?.models || [];
  const installedNames = new Set(models.map((m) => m.name));
  const active = st?.active_model || null;
  const prog = st?.pull?.progress;

  return (
    <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: c.text.primary }}>Local AI (report narratives)</span>
        {installed && (
          <span style={{ fontSize: 11, color: c.profit, fontWeight: 700 }}>
            ● Ollama {st.version} running
          </span>
        )}
        {msg && (
          <span style={{ fontSize: 12, fontWeight: 600,
            color: msg.kind === "ok" ? c.profit : msg.kind === "err" ? c.loss : c.text.muted }}>
            {msg.text}
          </span>
        )}
        <button style={{ ...smallBtn("default", false), marginLeft: "auto" }} onClick={refresh}>↻</button>
      </div>

      {!st ? (
        <div style={{ marginTop: spacing.md, fontSize: 12, color: c.text.muted }}>Checking…</div>
      ) : !installed ? (
        /* ── not installed: plain-language guidance ── */
        <div style={{ marginTop: spacing.md, fontSize: 13, color: c.text.secondary, lineHeight: 1.7 }}>
          Narratives run on a free AI model <b>on this computer</b> — nothing is sent anywhere,
          and there is no subscription. One-time setup:
          <div style={{ margin: "10px 0", padding: "10px 14px", borderRadius: 8,
            background: c.bg.secondary, border: `1px solid ${c.border.light}` }}>
            1. In your web browser, open <b style={{ ...typography.mono, color: c.text.primary }}>ollama.com/download</b><br />
            2. Install it (Mac or Windows — pick your system) and open it once<br />
            3. Come back here and press <b>Check again</b>
          </div>
          <button style={smallBtn("primary", false)} onClick={refresh}>Check again</button>
          <div style={{ marginTop: 8, fontSize: 11, color: c.text.tertiary }}>
            Everything else in the app works without this — reports simply skip the written observations.
          </div>
        </div>
      ) : (
        <div style={{ marginTop: spacing.md, display: "flex", flexDirection: "column", gap: spacing.md }}>
          {/* ── installed models: switch / delete ── */}
          {models.length > 0 && (
            <div>
              <div style={{ ...typography.label, color: c.text.muted, marginBottom: 6 }}>
                Installed models — the ● Active one writes the narratives
              </div>
              {models.map((m) => (
                <div key={m.name} style={{ display: "flex", alignItems: "center", gap: 10,
                  padding: "7px 10px", borderRadius: 6,
                  background: m.name === active ? c.primaryBg : "transparent" }}>
                  <button onClick={() => call("/api/backtest/ai/settings", { model: m.name }, `Active model: ${m.name}`)}
                    disabled={busy}
                    title={m.name === active ? "Active" : "Make this the active model"}
                    style={{ border: "none", background: "transparent", cursor: "pointer",
                      color: m.name === active ? c.primary : c.text.muted, fontSize: 14, padding: 0 }}>
                    {m.name === active ? "●" : "○"}
                  </button>
                  <span style={{ ...typography.mono, fontSize: 12, color: c.text.primary, fontWeight: 700 }}>{m.name}</span>
                  <span style={{ fontSize: 11, color: c.text.muted }}>{gb(m.size_bytes)} on disk</span>
                  <button onClick={() => call("/api/backtest/ai/model/delete", { name: m.name },
                      `${m.name} deleted — ${gb(m.size_bytes)} freed`)}
                    disabled={busy}
                    title={`Delete ${m.name} and free ${gb(m.size_bytes)} — you can re-download it any time`}
                    style={{ marginLeft: "auto", border: "none", background: "transparent",
                      cursor: "pointer", color: c.loss, fontSize: 13 }}>🗑</button>
                </div>
              ))}
            </div>
          )}

          {/* ── download: curated menu with sizes up-front ── */}
          <div>
            <div style={{ ...typography.label, color: c.text.muted, marginBottom: 6 }}>
              Download a model {models.length ? "(switching = download new → make Active → delete old)" : ""}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(st.curated || []).map((m) => {
                const have = installedNames.has(m.name);
                return (
                  <div key={m.name} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12 }}>
                    <button style={smallBtn(m.recommended ? "primary" : "default", have || pulling || busy)}
                      disabled={have || pulling || busy}
                      onClick={() => call("/api/backtest/ai/pull/start", { name: m.name })}>
                      {have ? "✓ installed" : `↓ ${m.disk_gb} GB`}
                    </button>
                    <span style={{ ...typography.mono, fontWeight: 700, color: c.text.primary }}>{m.name}</span>
                    <span style={{ color: c.text.muted }}>{m.note}{m.recommended ? " ★" : ""}</span>
                  </div>
                );
              })}
            </div>
            <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
              <input placeholder="advanced: any Ollama model tag…" value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                style={{ padding: "6px 10px", borderRadius: 6, border: `1px solid ${c.border.light}`,
                  background: c.bg.secondary, color: c.text.primary, fontSize: 12,
                  fontFamily: "monospace", minWidth: 240, outline: "none" }} />
              <button style={smallBtn("default", !customName.trim() || pulling || busy)}
                disabled={!customName.trim() || pulling || busy}
                onClick={() => { call("/api/backtest/ai/pull/start", { name: customName.trim() }); setCustomName(""); }}>
                ↓ Download
              </button>
            </div>
          </div>

          {/* ── live download progress ── */}
          {pulling && (
            <div>
              <div style={{ fontSize: 12, color: c.text.secondary, marginBottom: 6 }}>
                Downloading <b style={typography.mono}>{st.pull.name}</b>
                {prog?.pct != null ? ` — ${prog.pct.toFixed(0)}%` : ""} · {prog?.status || "starting…"}
              </div>
              <div style={{ height: 8, background: c.bg.secondary, borderRadius: 4, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.min(100, prog?.pct || 2)}%`,
                  background: c.primary, transition: "width 0.5s ease" }} />
              </div>
              <button style={{ ...smallBtn("default", false), marginTop: 8 }}
                onClick={() => call("/api/backtest/ai/pull/cancel", null)}>Cancel download</button>
            </div>
          )}
          {!pulling && st.pull?.error && st.pull.error !== "cancelled" && (
            <div style={{ fontSize: 12, color: c.loss }}>Last download failed: {st.pull.error}</div>
          )}
        </div>
      )}
    </Card>
  );
}
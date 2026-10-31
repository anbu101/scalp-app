/* frontend/src/pages/backtest/RecorderPanel.jsx — ── CHECKS_LAYOUT_20261029 ──
   Backtest → Backfill: the own NIFTY data recorder (DATA_RECORDER_20261029),
   moved here from the Dashboard at Anbu's request. GET /api/recorder/status
   (every 30 s while the tab is open): per-day grade, instruments, lost
   contracts, errors, spot bars, completeness, last pull; where the DB and the
   backups live (amber while backups stay on this Mac) with an inline folder
   field; "Pull now" runs one pass on the recorder's own worker. Hidden on
   403/404. Card / btn / inputStyle come from Backtest.jsx so it matches. */
import React, { useCallback, useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, typography } from "../../tokens";

const GRADE = {
  clean: { c: "var(--c-success)", t: "clean" },
  partial: { c: "var(--c-warning)", t: "partial" },
  lost: { c: "var(--c-loss)", t: "lost" },
  recording: { c: "var(--c-text-secondary)", t: "recording" },
};
const hhmm = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "—");

async function api(path, method = "GET", body) {
  const r = await fetch(`${getApiBase()}${path}`, { method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.detail || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return j;
}

export default function RecorderPanel({ Card, btn, inputStyle }) {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  const [folder, setFolder] = useState("");
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try { setSt(await api("/api/recorder/status?days=10")); setErr(""); }
    catch (e) { if (e.status === 403 || e.status === 404) setSt(false); else setErr(e.message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  if (st === false) return null;
  const off = st?.backup_off_machine;
  const days = st?.days || [];
  const lb = st?.last_backup;

  const pull = async () => {
    setBusy(true); setMsg("");
    try { await api("/api/recorder/pull", "POST", {}); setMsg("Pass started — the table refreshes as it lands."); setTimeout(load, 5000); }
    catch (e) { setMsg(`Not started: ${e.message}`); } finally { setBusy(false); }
  };
  const saveFolder = async () => {
    setBusy(true); setMsg("");
    try { await api("/api/recorder/settings", "POST", { backup_dir: folder.trim() }); setEdit(false); setMsg("Backup folder saved."); await load(); }
    catch (e) { setMsg(`Not saved: ${e.message}`); } finally { setBusy(false); }
  };

  const th = { padding: "6px 10px", borderBottom: `1px solid ${colors.border.dark}`, fontWeight: 600, whiteSpace: "nowrap", textAlign: "right" };
  const td = { padding: "6px 10px", textAlign: "right", whiteSpace: "nowrap" };
  return (
    <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.xl }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: spacing.md, flexWrap: "wrap", marginBottom: spacing.sm }}>
        <div style={{ ...typography.label, color: colors.text.muted }}>Own data recorder (Kite · NIFTY)</div>
        <div style={{ display: "flex", gap: spacing.sm }}>
          <button style={btn("default")} onClick={load} disabled={busy}>Refresh</button>
          <button style={btn("default")} onClick={pull} disabled={busy || st?.busy}>{st?.busy ? "Recording…" : "Pull now"}</button>
        </div>
      </div>
      <div style={{ fontSize: 13, color: colors.text.secondary, lineHeight: 1.5, marginBottom: spacing.md }}>
        Exchange 1m candles from Kite for NIFTY spot and every strike within ±10 steps of the day's range, for the current-week,
        next-week and monthly expiries — pulled every 30 min from 09:45, finalised at 15:40 (15:32 on expiry day). Stored beside
        backtest.db; Dhan data is never touched. Backtests do not read it yet.
      </div>
      {err && <div style={{ fontSize: 12, color: colors.loss, marginBottom: spacing.sm }}>{err}</div>}
      {st?.last_error && <div style={{ fontSize: 12, color: colors.warning, marginBottom: spacing.sm }}>Last pass stopped: {st.last_error}</div>}

      {st && !days.length && <div style={{ fontSize: 12, color: colors.text.muted }}>No day recorded yet — the first pass runs within a minute of the app starting (admin licence, Kite logged in).</div>}
      {days.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: colors.text.muted }}>
                <th style={{ ...th, textAlign: "left" }}>Day</th><th style={{ ...th, textAlign: "left" }}>Grade</th>
                <th style={th}>Recorded / band</th><th style={th}>Lost (expired)</th><th style={th}>Errors</th>
                <th style={th}>Spot bars</th><th style={th}>Completeness</th><th style={th}>Last pull</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d, i) => {
                const g = GRADE[d.quality] || { c: colors.text.muted, t: d.quality || "—" };
                return (
                  <tr key={d.day} style={{ background: i % 2 ? "transparent" : colors.bg.secondary }}>
                    <td style={{ ...td, textAlign: "left", fontFamily: "monospace" }}>{d.day}</td>
                    <td style={{ ...td, textAlign: "left", color: g.c, fontWeight: 600 }}>{g.t}</td>
                    <td style={td}>{d.pulled_n} / {d.band_n}</td>
                    <td style={{ ...td, color: d.lost_n ? colors.warning : undefined }}>{d.lost_n || "—"}</td>
                    <td style={{ ...td, color: d.error_n ? colors.loss : undefined }}>{d.error_n || "—"}</td>
                    <td style={{ ...td, color: d.spot_bars < 375 && d.quality !== "recording" ? colors.warning : undefined }}>{d.spot_bars}</td>
                    <td style={td}>{d.completeness != null ? `${(d.completeness * 100).toFixed(1)}%` : "—"}</td>
                    <td style={td}>{hhmm(d.last_pull_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {st && (
        <div style={{ marginTop: spacing.md, paddingTop: spacing.md, borderTop: `1px solid ${colors.border.dark}`,
                      display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", fontSize: 12.5 }}>
          <span style={{ color: off ? colors.text.secondary : colors.warning, flex: "1 1 320px", minWidth: 0 }}>
            Backups: <span style={{ fontFamily: "monospace" }}>{st.backup_dir}</span>
            {off ? " (off this Mac)" : " — still on this Mac; a disk failure would lose recorded days that cannot be downloaded again."}
            {lb?.day ? ` Last copy ${lb.day}.` : ""} Keeps {st.keep}.
          </span>
          {!edit && <button style={btn("default")} onClick={() => { setFolder(off ? st.backup_dir : ""); setEdit(true); }}>{off ? "Change folder" : "Set backup folder"}</button>}
        </div>
      )}
      {edit && (
        <div style={{ marginTop: spacing.sm, display: "flex", gap: spacing.sm, flexWrap: "wrap", alignItems: "center" }}>
          <input aria-label="Backup folder" style={{ ...inputStyle, flex: "1 1 360px", minWidth: 0 }} value={folder} onChange={(e) => setFolder(e.target.value)}
            placeholder="~/Library/Mobile Documents/com~apple~CloudDocs/ScalpRecorder" />
          <button style={btn("primary")} onClick={saveFolder} disabled={busy || !folder.trim()}>Save folder</button>
          <button style={btn("default")} onClick={() => setEdit(false)} disabled={busy}>Cancel</button>
        </div>
      )}
      {st?.db_path && <div style={{ marginTop: spacing.sm, fontSize: 11.5, color: colors.text.muted }}>DB: <span style={{ fontFamily: "monospace" }}>{st.db_path}</span>{st.size_mb != null ? ` · ${st.size_mb} MB` : ""}{st.session_config_days ? ` · strategy configs saved for ${st.session_config_days} day${st.session_config_days > 1 ? "s" : ""}` : ""}</div>}
      {msg && <div style={{ marginTop: spacing.sm, fontSize: 12, color: msg.startsWith("Not") ? colors.loss : colors.text.secondary }}>{msg}</div>}
    </Card>
  );
}

// frontend/src/components/calm/ReadyPanel.jsx — ── READY_PANEL_20261118 ── (TD-030)
// "Ready for the open": before 09:15 on a weekday, one line per thing that must be in
// place when the market opens — Zerodha login (time, or a Log in button), Angel login
// (only when Angel is set up), market feed, engine (+ how many strategies are armed),
// and the stops kept at the broker for LIVE carried legs (only when there are any).
// Reads /zerodha/status, /api/acc2/status and /api/fleet/ready every 15 s while shown.
import { useCallback, useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { MARKET_START_MIN } from "../../marketSession";
import { stratName } from "../../strategies/displayNames";
import { C, T, SP, RADIUS } from "./calm";

const DOT = { ok: C.up, warn: C.warn, bad: C.down, idle: C.muted };

async function getJson(path, init) {
  const r = await fetch(`${getApiBase()}${path}`, init);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function isPreOpen(nowMin, day = new Date().getDay()) {
  return day >= 1 && day <= 5 && nowMin < MARKET_START_MIN;
}

const hhmm = (v) => {
  if (!v) return null;
  const d = new Date(typeof v === "number" ? v * 1000 : v);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
};

export function useReady(enabled) {
  const [s, setS] = useState({ loaded: false });
  const reload = useCallback(async () => {
    const [z, a, r] = await Promise.allSettled([getJson("/zerodha/status"), getJson("/api/acc2/status"), getJson("/api/fleet/ready")]);
    setS({
      loaded: true,
      reachable: [z, a, r].some((x) => x.status === "fulfilled"),
      zerodha: z.status === "fulfilled" ? z.value : null,
      angel: a.status === "fulfilled" ? a.value : null,
      ready: r.status === "fulfilled" ? r.value : null,
    });
  }, []);
  useEffect(() => {
    if (!enabled) return undefined;
    reload();
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [enabled, reload]);
  return { ...s, reload };
}

function LoginButton({ label = "Log in", busy, onClick, title }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} title={title} data-testid="ready-login"
      style={{ font: "inherit", ...T.body, fontWeight: 600, cursor: busy ? "default" : "pointer", minHeight: 32,
        padding: `${SP.xs}px ${SP.md + 2}px`, borderRadius: RADIUS.inner - 4, border: 0,
        background: C.accent, color: "var(--c-bg-primary)", opacity: busy ? 0.6 : 1 }}>   {/* ── READY_LOGIN_LABEL_20261118 (DEF-062) ── was --c-bg-page: transparent on Arcade / Kolam, so the label vanished */}{busy ? "Opening…" : label}</button>
  );
}

function Row({ id, state, label, children }) {
  return (
    <div data-ready={id} data-state={state}
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: SP.md, minHeight: 36 }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: SP.md, ...T.lead, color: C.ink, minWidth: 0 }}>
        <span aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 5, background: DOT[state], flexShrink: 0 }} />
        {label}
      </span>
      <span style={{ ...T.body, color: C.sub, textAlign: "right", ...T.num }}>{children}</span>
    </div>
  );
}

export default function ReadyPanel({ nowMin, armed, isAdminUi = false, compact = false, day }) {
  const show = isPreOpen(nowMin, day);
  const s = useReady(show);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  if (!show || !s.loaded) return null;

  const z = s.zerodha;
  const zOk = !!(z && z.connected && !z.session_expired);
  const a = s.angel;
  const angelOn = !!(a && a.configured && a.enabled !== false);
  const feed = s.ready?.feed || { state: "unknown" };
  const stops = (s.ready?.carried_stops || []).filter((c) => c.legs);

  const zLogin = async () => {
    setBusy("zerodha"); setNote(null);
    try {
      const res = await getJson("/zerodha/login-url");
      const url = res?.login_url;
      if (!url) throw new Error("no login URL");
      if (window.__TAURI__?.shell?.open) await window.__TAURI__.shell.open(url);
      else window.open(url, "_blank");
    } catch (e) { setNote(`Zerodha: ${e.message}`); }
    setTimeout(() => setBusy(null), 4000);
  };
  const aLogin = async () => {
    setBusy("angel"); setNote(null);
    try {
      const res = await getJson("/api/acc2/force-login", { method: "POST" });
      if (!res?.connected) setNote(`Angel: ${res?.last_error || "login failed"}`);
    } catch (e) { setNote(`Angel: ${e.message}`); }
    await s.reload();
    setBusy(null);
  };

  const feedRow = feed.state === "connected" ? ["ok", "connected"]
    : feed.state === "connecting" ? ["warn", "connecting…"]
    : feed.state === "not_started" ? (zOk ? ["warn", "not started"] : ["idle", "starts after the Zerodha login"])
    : ["idle", "—"];

  let stopsRow = null;
  if (stops.length) {
    const legs = stops.reduce((n, c) => n + (c.legs || 0), 0);
    const unknown = stops.some((c) => c.armed == null);
    const armedN = stops.reduce((n, c) => n + (c.armed || 0), 0);
    const names = stops.map((c) => stratName(c.id, isAdminUi)).join(", ");
    stopsRow = nowMin >= MARKET_START_MIN - 1
      ? ["idle", "taken down at 09:14 for the 09:16 exit"]
      : unknown ? ["warn", zOk ? "Kite list not readable yet" : "log in to Zerodha to check"]
      : [armedN === legs ? "ok" : "bad", `${armedN} of ${legs} armed`];
    stopsRow.push(names);
  }

  return (
    <section aria-label="Ready for the open" data-testid="ready-panel"
      style={{ flex: compact ? "1 1 auto" : "1 1 420px", maxWidth: compact ? undefined : 640, minWidth: 0, boxSizing: "border-box",
        background: C.surface, borderRadius: RADIUS.card, padding: compact ? SP.lg : `${SP.xl}px ${SP.xl + 4}px`,
        display: "flex", flexDirection: "column", gap: SP.sm }}>
      <h2 style={{ margin: `0 0 ${SP.xs}px`, ...T.title, fontSize: 17 }}>Ready for the open</h2>
      <Row id="zerodha" state={zOk ? "ok" : z && z.configured === false ? "idle" : "warn"} label="Zerodha login">
        {zOk ? (hhmm(z.login_at) || "logged in")
          : z && z.configured === false ? "not set up (Connections)"
          : <LoginButton busy={busy === "zerodha"} onClick={zLogin} title="Open the Kite login page" />}
      </Row>
      {angelOn && (
        <Row id="angel" state={a.connected ? "ok" : "warn"} label="Angel login">
          {a.connected ? (hhmm(a.last_login) || "logged in")
            : <LoginButton busy={busy === "angel"} onClick={aLogin} title="Log in to Angel One now" />}
        </Row>
      )}
      <Row id="feed" state={feedRow[0]} label="Market feed">{feedRow[1]}</Row>
      <Row id="engine" state={s.reachable ? "ok" : "bad"} label="Engine">
        {s.reachable ? `on${armed != null ? ` · ${armed} armed` : ""}` : "backend not reachable"}
      </Row>
      {stopsRow && (
        <Row id="stops" state={stopsRow[0]} label="Carried stops at broker">
          <span title={stopsRow[2]}>{stopsRow[1]}</span>
        </Row>
      )}
      {note && <div role="status" style={{ ...T.meta, color: C.warn }}>{note}</div>}
    </section>
  );
}

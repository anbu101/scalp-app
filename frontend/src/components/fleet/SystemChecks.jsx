/* frontend/src/components/fleet/SystemChecks.jsx — ── SYSTEM_CHECKS_UI_20261029 ──
   ── CHECKS_LAYOUT_20261029 ── (Anbu: "a small badge like 'Market closed' when
   there is nothing to worry"; the recorder moved to Backtest → Backfill).

   Broker check (BROKER_RECON_20261029), admin only:
     useBrokerCheck   polls GET /api/recon/status every 15 s; hidden on 403/404.
     BrokerBadge      header pill beside the session pill:
                        green  "Broker in sync"     (last check today was clean)
                        grey   "Broker check idle"  (no check yet today)
                        amber  "Broker check failed"
                        amber / red "Broker: N mismatch(es)" (red when a GHOST)
                      Clicking it opens / closes the detail card.
     BrokerIssues     the detail card — ONLY when there are mismatches or the
                      badge was clicked: one row per ORPHAN / GHOST / QTY with
                      broker vs expected qty and strategies; "Mark as manual"
                      (two-step) only where the broker holds EXTRA quantity in
                      the same direction; marked positions with "Unmark";
                      "Check now" (one read-only poll). */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing } from "../../tokens";
import { stratName } from "../../strategies/displayNames";

const KIND_TEXT = {
  ORPHAN: "No strategy holds this position",
  GHOST: "The app holds this position, the broker is flat",
  QTY: "Quantities differ",
};

const hhmmss = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "—");
const istDay = (ts) => new Date(ts * 1000 + 19800000).toISOString().slice(0, 10);
const signed = (q) => (q > 0 ? `+${q}` : q < 0 ? `−${Math.abs(q)}` : "0");

async function api(path, method = "GET", body) {
  const r = await fetch(`${getApiBase()}${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.detail || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return j;
}

export function useBrokerCheck(isAdminUi) {
  const [status, setStatus] = useState(null);     // null = loading, false = hidden
  const [expanded, setExpanded] = useState(false);
  const reload = useCallback(async () => {
    try { setStatus(await api("/api/recon/status")); }
    catch (e) { setStatus((p) => (e.status === 403 || e.status === 404 ? false : (p ? { ...p, fetchError: e.message } : { fetchError: e.message }))); }
  }, []);
  useEffect(() => {
    if (!isAdminUi) return undefined;
    reload();
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [isAdminUi, reload]);
  return { status: isAdminUi ? status : false, expanded, setExpanded, reload };
}

function summary(status) {
  const issues = status?.open_issues || [];
  const last = status?.last_run;
  if (!status) return { color: colors.text.muted, label: "Broker check…", title: "Loading" };
  if (issues.length) {
    const ghost = issues.some((i) => i.kind === "GHOST");
    return { color: ghost ? colors.loss : colors.warning, label: `Broker: ${issues.length} mismatch${issues.length > 1 ? "es" : ""}`,
             title: "Positions differ from Zerodha — click for details", alert: true };
  }
  if (status.fetchError) return { color: colors.warning, label: "Broker check unreachable", title: status.fetchError };
  if (!last || istDay(last.ts) !== istDay(Date.now() / 1000)) {
    return { color: colors.text.muted, label: "Broker check idle", title: "No check yet today — runs every 45 s, 09:10–15:40 on trading days" };
  }
  if (!last.ok) return { color: colors.warning, label: "Broker check failed", title: `${hhmmss(last.ts)}: ${last.error || "error"}` };
  return { color: colors.success, label: "Broker in sync", title: `Matches Zerodha · last check ${hhmmss(last.ts)}` };
}

export function BrokerBadge({ status, expanded, setExpanded }) {
  if (status === false) return null;
  const s = summary(status);
  return (
    <button type="button" onClick={() => setExpanded((e) => !e)} title={s.title} aria-expanded={expanded}
      style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, color: s.color, border: `1px solid ${s.color}`,
               background: s.alert ? (s.color === colors.loss ? colors.lossBg : colors.warningBg) : "transparent",
               display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer", opacity: 0.95, fontFamily: "inherit", lineHeight: "normal", margin: 0 }}>
      <i aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: s.color, display: "inline-block" }} />
      {s.label}
    </button>
  );
}

function SmallBtn({ children, onClick, disabled, tone }) {
  const bg = tone === "warn" ? colors.warning : colors.bg.tertiary;
  const fg = tone === "warn" ? "#fff" : colors.text.primary;
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      style={{ padding: "4px 10px", borderRadius: 6, border: `1px solid ${colors.border.light}`, background: bg, color: fg,
               fontSize: 12, fontWeight: 600, cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.6 : 1, whiteSpace: "nowrap" }}>
      {children}
    </button>
  );
}

function IssueRow({ iss, isAdminUi, meta, onMark, busy }) {
  const [arm, setArm] = useState(false);
  const t = useRef(null);
  useEffect(() => () => clearTimeout(t.current), []);
  let who = {};
  try { who = JSON.parse(iss.strategies || "{}") || {}; } catch { who = {}; }
  const names = Object.entries(who).map(([sid, q]) => `${stratName(sid, isAdminUi, meta?.[sid]?.name)} ${signed(q)}`).join(", ");
  const expected = iss.app_qty + iss.ignored_qty;
  const extra = iss.broker_qty - expected;
  // A manual mark only explains EXTRA broker quantity in the same direction
  // (an orphan, or more than the strategies hold). Less at the broker than
  // the app expects (partial exit / missing fill) is never "manual".
  const canMark = iss.kind === "ORPHAN" || (iss.kind === "QTY" && extra !== 0 &&
    Math.sign(extra) === Math.sign(iss.broker_qty) && Math.abs(iss.broker_qty) > Math.abs(expected));
  const tone = iss.kind === "GHOST" ? colors.loss : colors.warning;
  const click = () => {
    if (!arm) { setArm(true); clearTimeout(t.current); t.current = setTimeout(() => setArm(false), 5000); return; }
    setArm(false); onMark(iss.symbol, extra);
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", padding: `${spacing.sm}px 0`,
                  borderTop: `1px solid ${colors.border.light}` }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: tone, border: `1px solid ${tone}`, borderRadius: 4, padding: "1px 6px" }}>{iss.kind}</span>
      <span style={{ fontFamily: "var(--c-font-mono, monospace)", fontSize: 13, fontWeight: 600 }}>{iss.symbol}</span>
      <span style={{ fontSize: 12.5, color: colors.text.secondary, flex: "1 1 260px", minWidth: 0 }}>
        {KIND_TEXT[iss.kind]}: broker <b>{signed(iss.broker_qty)}</b>, app expects <b>{signed(expected)}</b>
        {names ? <> ({names})</> : null}
        {iss.ignored_qty ? <>, of which {signed(iss.ignored_qty)} marked manual</> : null}. Since {hhmmss(iss.confirmed_at)}.
      </span>
      {canMark && (
        <SmallBtn tone={arm ? "warn" : undefined} disabled={busy} onClick={click}>
          {arm ? `Confirm: ${signed(extra)} is manual` : "Mark as manual"}
        </SmallBtn>
      )}
    </div>
  );
}

export function BrokerIssues({ status, expanded, setExpanded, reload, isAdminUi, meta, compact }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!isAdminUi || !status) return null;
  const issues = status.open_issues || [];
  if (!issues.length && !expanded) return null;
  const marks = status.ignores || [];
  const s = summary(status);

  const act = async (fn, ok) => {
    setBusy(true); setMsg("");
    try { await fn(); if (ok) setMsg(ok); } catch (e) { setMsg(`Failed: ${e.message}`); }
    finally { setBusy(false); reload(); }
  };
  const checkNow = () => act(async () => {
    const r = await api("/api/recon/run", "POST");
    if (r && r.ok === false) throw new Error(r.error || "check failed");
    if (r && r.ran === false) throw new Error(r.reason || "not run");
  }, "Checked just now.");
  const mark = (symbol, qty) => act(() => api("/api/recon/ignore", "POST", { tradingsymbol: symbol, qty, note: "marked on Dashboard" }),
                                    `${symbol} ${signed(qty)} marked as manual.`);
  const unmark = (id) => act(() => api(`/api/recon/ignore/${id}`, "DELETE"), "Mark removed.");

  const pad = compact ? `${spacing.sm}px ${spacing.md}px` : `${spacing.sm + 2}px ${spacing.lg}px`;
  return (
    <section aria-label="Broker check" style={{ background: colors.bg.secondary, borderRadius: 8, padding: pad,
      border: `1px solid ${issues.length ? s.color : colors.border.light}`, boxShadow: "0 1px 3px var(--c-shadow)",
      display: "flex", flexDirection: "column", gap: spacing.xs, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexWrap: "wrap", minWidth: 0 }}>
        <span role="status" style={{ fontSize: 13, fontWeight: issues.length ? 700 : 500, color: issues.length ? s.color : colors.text.primary,
                                     flex: "1 1 220px", minWidth: 0 }}>
          {issues.length ? `${issues.length} position${issues.length > 1 ? "s" : ""} differ from Zerodha` : s.title}
        </span>
        <SmallBtn onClick={checkNow} disabled={busy}>{busy ? "Checking…" : "Check now"}</SmallBtn>
        {!issues.length && <SmallBtn onClick={() => setExpanded(false)}>Close</SmallBtn>}
      </div>
      {issues.map((iss) => <IssueRow key={iss.id} iss={iss} isAdminUi={isAdminUi} meta={meta} onMark={mark} busy={busy} />)}
      {marks.length > 0 && (
        <div style={{ fontSize: 11.5, color: colors.text.muted, paddingTop: spacing.xs }}>Marked as manual (cleared automatically when the broker is flat)</div>
      )}
      {marks.map((m) => (
        <div key={m.id} style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", fontSize: 12.5,
                                 color: colors.text.secondary, padding: `${spacing.xs}px 0`, borderTop: `1px solid ${colors.border.light}` }}>
          <span style={{ fontFamily: "var(--c-font-mono, monospace)", color: colors.text.primary }}>{m.symbol}</span>
          <span>{signed(m.qty)}{m.note ? ` · ${m.note}` : ""}</span>
          <span style={{ marginLeft: "auto" }}><SmallBtn onClick={() => unmark(m.id)} disabled={busy}>Unmark</SmallBtn></span>
        </div>
      ))}
      {msg && <div style={{ fontSize: 12, color: msg.startsWith("Failed") ? colors.loss : colors.text.secondary }}>{msg}</div>}
    </section>
  );
}

export default BrokerIssues;

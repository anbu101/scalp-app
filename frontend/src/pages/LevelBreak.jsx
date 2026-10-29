// frontend/src/pages/LevelBreak.jsx
//
// ── LVB_V1_20261023 ── Levels page ("Tripwire", experimental, admin-only;
// route + nav in App.jsx). Everything comes from GET /api/lvb_v1/state
// (3 s poll); every action is one POST/PUT/DELETE and a re-pull.
//
//   * Mode — OFF / PAPER / LIVE / PAPER+LIVE for the whole strategy (LIVE is
//     two-tap). Each trade remembers the book it was taken in.
//   * New condition — instrument, Upper / Lower spot levels (either or
//     both), premium band, SL and TP in spot points FROM THE LIMIT, EOD, lots. Valid for today only. Edit re-arms it from now.
//   * Open trades — live LTP / spot / MTM, per-trade Square off, Square off all
//     (cards on a phone so Square off is always on screen).
//   * Today — every condition with its status (ARMED → TRIGGERED / EXPIRED /
//     CANCELLED), result and the engine's last note (skipped breakouts).
//
// ── LVB_SL_SPOT_20261029 ──
//   * No confirm() dialogs anywhere: Tauri's webview silently returns false,
//     so Cancel / Square off / Square off all / Live were dead buttons. Every
//     destructive action is now TWO-TAP (house pattern: first tap arms for
//     4 s and relabels "Tap again …", second tap acts).
//   * SL is spot points from the limit (CE: Upper − SL, PE: Lower + SL).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApiBase } from "../api/base";
import { useIsMobile } from "../hooks/useIsMobile";
import { colors, spacing, typography, pnlStyle, alpha, fonts } from "../tokens";

const API = () => `${getApiBase()}/api/lvb_v1`;
const MODES = [
  { id: "OFF", label: "Off" },
  { id: "PAPER", label: "Paper" },
  { id: "LIVE", label: "Live" },
  { id: "PAPER_LIVE", label: "Paper + Live" },
];
const INSTS = ["NIFTY", "SENSEX"];

const inr = (v, d = 0) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d })}`;
const n2 = (v, d = 2) => (v == null || v === "" ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
const istTime = (epoch) =>
  epoch ? new Date(epoch * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";

async function call(path, method = "GET", body) {
  const r = await fetch(`${API()}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 403) return { ok: false, error: "admin licence required" };
  try { return await r.json(); } catch { return { ok: false, error: `HTTP ${r.status}` }; }
}

const card = {
  background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 10,
  padding: spacing.lg, marginBottom: spacing.lg,
};
const th = { ...typography.label, color: colors.text.tertiary, textAlign: "left", padding: "6px 8px", whiteSpace: "nowrap" };
const td = { ...typography.bodySmall, color: colors.text.primary, padding: "7px 8px", borderTop: `1px solid ${colors.border.dark}`, whiteSpace: "nowrap", verticalAlign: "top" };
const mono = { fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" };

function Btn({ children, onClick, tone = "default", disabled, small, title }) {
  const tones = {
    default: { bg: colors.bg.tertiary, fg: colors.text.primary, bd: colors.border.light },
    primary: { bg: colors.primary, fg: "#fff", bd: colors.primary },
    danger: { bg: alpha(colors.danger, 14), fg: colors.danger, bd: alpha(colors.danger, 45) },
    ghost: { bg: "transparent", fg: colors.text.secondary, bd: colors.border.light },
  }[tone];
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} style={{
      background: tones.bg, color: tones.fg, border: `1px solid ${tones.bd}`, borderRadius: 7,
      padding: small ? "3px 9px" : "7px 14px", fontSize: small ? 12 : 13, fontWeight: 600,
      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1, fontFamily: "inherit",
    }}>{children}</button>
  );
}

function Chip({ text, tone }) {
  const c = { ARMED: colors.primary, TRIGGERED: colors.warning, OPEN: colors.warning, EXPIRED: colors.text.tertiary,
              CANCELLED: colors.text.tertiary, CE: colors.profit, PE: colors.loss, LIVE: colors.danger,
              PAPER: colors.text.secondary, TP: colors.profit, SL: colors.loss }[tone || text] || colors.text.secondary;
  return (
    <span style={{ display: "inline-block", padding: "1px 7px", borderRadius: 999, fontSize: 11, fontWeight: 700,
                   color: c, background: alpha(c, 13), border: `1px solid ${alpha(c, 35)}`, letterSpacing: 0.3 }}>{text}</span>
  );
}

function Field({ label, children, hint, width = 120 }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: width, flex: `1 1 ${width}px` }}>
      <span style={{ ...typography.label, color: colors.text.tertiary }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 11, color: colors.text.muted }}>{hint}</span>}
    </label>
  );
}

const inputStyle = {
  background: colors.bg.input, color: colors.text.primary, border: `1px solid ${colors.border.light}`,
  borderRadius: 7, padding: "7px 9px", fontSize: 13, fontFamily: "inherit", width: "100%", boxSizing: "border-box",
};

function emptyForm(defaults, inst = "NIFTY") {
  const d = defaults || {};
  return {
    instrument: inst, upper: "", lower: "",
    prem_min: d.prem_min ?? 150, prem_max: d.prem_max ?? 200,
    sl_pts: (d.sl_pts || {})[inst] ?? (inst === "SENSEX" ? 75 : 25),
    tp_pts: (d.tp_pts || {})[inst] ?? (inst === "SENSEX" ? 150 : 50),
    eod: d.eod || "15:15", lots: d.lots ?? 1,
  };
}

export default function LevelBreak() {
  const isMobile = useIsMobile();
  const [st, setSt] = useState(null);
  const [err, setErr] = useState(null);
  const [form, setForm] = useState(() => emptyForm(null));
  const [tpTouched, setTpTouched] = useState(false);
  const [slTouched, setSlTouched] = useState(false);
  const [armedKey, setArmedKey] = useState(null);       // two-tap: which action is armed
  const armTimer = useRef(null);
  useEffect(() => () => clearTimeout(armTimer.current), []);
  const twoTap = (key, fn) => {
    if (armedKey !== key) {
      setArmedKey(key);
      clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => setArmedKey(null), 4000);
      return;
    }
    clearTimeout(armTimer.current);
    setArmedKey(null);
    fn();
  };
  const isArmed = (key) => armedKey === key;
  const [editId, setEditId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const pull = useCallback(async () => {
    try {
      const d = await call("/state");
      if (d && d.ok) { setSt(d); setErr(null); } else setErr(d?.error || "state unavailable");
    } catch (e) { setErr(String(e)); }
  }, []);

  useEffect(() => { pull(); const t = setInterval(pull, 3000); return () => clearInterval(t); }, [pull]);

  const defaults = st?.defaults;
  const [adopted, setAdopted] = useState(false);   // ── LVB_V1_FIX1_20261023 ── no lint-rule comment needed
  useEffect(() => {                       // first load: adopt server defaults once (only into an untouched form)
    if (!defaults || adopted) return;
    setAdopted(true);
    setForm((f) => (f.upper === "" && f.lower === "" ? emptyForm(defaults, f.instrument) : f));
  }, [defaults, adopted]);

  const flash = (text, tone = "ok") => { setMsg({ text, tone }); setTimeout(() => setMsg(null), 6000); };

  const setF = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setInst = (inst) => setForm((f) => ({
    ...f, instrument: inst,
    tp_pts: tpTouched ? f.tp_pts : ((defaults?.tp_pts || {})[inst] ?? (inst === "SENSEX" ? 150 : 50)),
    sl_pts: slTouched ? f.sl_pts : ((defaults?.sl_pts || {})[inst] ?? (inst === "SENSEX" ? 75 : 25)),
  }));

  const spot = st?.spot?.[form.instrument];
  const preview = useMemo(() => {
    const tp = Number(form.tp_pts) || 0;
    const sl = Number(form.sl_pts) || 0;
    const out = [];
    if (form.upper !== "") out.push(`5m close above ${n2(form.upper)} → buy ${form.instrument} CE ₹${form.prem_min}–${form.prem_max}` +
      (sl > 0 ? ` · SL when spot ≤ ${n2(Number(form.upper) - sl)}` : "") +
      (tp > 0 ? ` · TP when spot ≥ ${n2(Number(form.upper) + tp)}` : " · no target"));
    if (form.lower !== "") out.push(`5m close below ${n2(form.lower)} → buy ${form.instrument} PE ₹${form.prem_min}–${form.prem_max}` +
      (sl > 0 ? ` · SL when spot ≥ ${n2(Number(form.lower) + sl)}` : "") +
      (tp > 0 ? ` · TP when spot ≤ ${n2(Number(form.lower) - tp)}` : " · no target"));
    if (out.length) out.push(`Exits fill at the option LTP · square-off ${form.eod} · ${form.lots} lot(s) · first breakout only`);
    return out;
  }, [form]);

  async function submit() {
    setBusy(true);
    const body = { ...form };
    ["upper", "lower"].forEach((k) => { if (body[k] === "") body[k] = null; });
    const d = editId ? await call(`/conditions/${editId}`, "PUT", body) : await call("/conditions", "POST", body);
    setBusy(false);
    if (d?.ok) {
      flash(editId ? `Condition #${editId} updated — re-armed from now` : `Condition #${d.id} armed`);
      setEditId(null); setTpTouched(false); setSlTouched(false); setForm(emptyForm(defaults, form.instrument)); pull();
    } else flash(d?.error || "failed", "err");
  }

  function startEdit(c) {
    setEditId(c.id); setTpTouched(true); setSlTouched(c.sl_pts != null);
    setForm({ instrument: c.instrument, upper: c.upper ?? "", lower: c.lower ?? "", prem_min: c.prem_min, prem_max: c.prem_max,
              sl_pts: c.sl_pts ?? ((defaults?.sl_pts || {})[c.instrument] ?? (c.instrument === "SENSEX" ? 75 : 25)),
              tp_pts: c.tp_pts, eod: c.eod, lots: c.lots });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function cancelCond(c) {
    const d = await call(`/conditions/${c.id}`, "DELETE");
    d?.ok ? flash(`#${c.id} cancelled`) : flash(d?.error || "failed", "err");
    pull();
  }

  async function squareOff(c) {
    const d = await call(`/squareoff/${c.id}`, "POST");
    d?.ok ? flash(d.queued ? d.message : `#${c.id} squared off @ ${n2(d.exit_price)}`) : flash(d?.error || "failed", "err");
    pull();
  }

  async function squareOffAll() {
    const d = await call("/squareoff_all", "POST");
    d?.ok ? flash(`closed ${d.closed}${d.queued ? `, ${d.queued} queued (broker deferred)` : ""}`) : flash(d?.error || "failed", "err");
    pull();
  }

  async function setMode(m) {
    if (m === st?.mode) return;
    const d = await call("/config", "POST", { trade_execution_mode: m });
    d?.ok ? flash(`Mode: ${m.replace("_", " + ")}`) : flash(d?.error || "failed", "err");
    pull();
  }

  const conds = st?.conditions || [];
  const open = conds.filter((c) => c.open);
  const hb = st?.engine?.heartbeat_ts;
  const hbAge = hb && st?.server_ts ? st.server_ts - hb : null;
  const dayPnl = conds.reduce((s, c) => s + (c.pnl || 0), 0);
  const traded = conds.filter((c) => c.entry_px != null).length;

  return (
    <div style={{ padding: isMobile ? spacing.md : spacing.xxl, paddingBottom: isMobile ? 140 : 60, maxWidth: 1280,
                  margin: "0 auto", fontFamily: fonts.ui, color: colors.text.primary, background: colors.bg.page }}>
      {/* header */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: spacing.md, marginBottom: spacing.lg }}>
        <div style={{ flex: "1 1 260px" }}>
          <div style={{ ...typography.headingLarge }}>🎯 Levels <span style={{ ...typography.bodySmall, color: colors.text.tertiary }}>· LVB V1 "Tripwire" · experimental</span></div>
          <div style={{ ...typography.bodySmall, color: colors.text.secondary }}>
            A 5-minute close through your level buys one option · SL on premium · TP on spot · flat by your EOD
          </div>
        </div>
        <div style={{ display: "flex", gap: spacing.md, alignItems: "center", flexWrap: "wrap" }}>
          {INSTS.map((k) => (
            <span key={k} style={{ ...typography.bodySmall, color: colors.text.secondary }}>
              {k} <b style={{ ...mono, color: colors.text.primary }}>{n2(st?.spot?.[k])}</b>
            </span>
          ))}
          <span title="engine heartbeat" style={{ ...typography.bodySmall, color: !st?.running ? colors.danger : hbAge != null && hbAge > 10 ? colors.warning : colors.success }}>
            ● {!st?.running ? "runtime not running" : hbAge == null ? "idle (outside session)" : hbAge > 10 ? `stale ${hbAge}s` : "live"}
          </span>
        </div>
      </div>

      {/* mode */}
      <div style={{ ...card, display: "flex", flexWrap: "wrap", gap: spacing.md, alignItems: "center" }}>
        <span style={{ ...typography.label, color: colors.text.tertiary }}>Mode</span>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {MODES.map((m) => (
            <Btn key={m.id} small
                 tone={st?.mode === m.id ? (m.id.includes("LIVE") ? "danger" : "primary") : isArmed(`mode:${m.id}`) ? "danger" : "ghost"}
                 title={m.id.includes("LIVE") ? "Real orders at the broker — tap twice" : undefined}
                 onClick={() => (m.id.includes("LIVE") && st?.mode !== m.id
                   ? twoTap(`mode:${m.id}`, () => setMode(m.id)) : setMode(m.id))}>
              {isArmed(`mode:${m.id}`) ? `Tap again → ${m.label} (real orders)` : m.label}
            </Btn>
          ))}
        </div>
        <span style={{ ...typography.bodySmall, color: colors.text.tertiary, flex: "1 1 300px" }}>
          {st?.mode === "OFF" ? "No new entries; open trades are still managed to their exit."
            : (st?.mode || "").includes("LIVE") ? "LIVE: real orders. SL / TP / EOD are engine exits — there is no stop at the broker while the app is closed."
            : "Paper fills at the LTP right after the 5m close."}
        </span>
        <span style={{ ...typography.bodySmall, color: colors.text.secondary }}>
          Today: {traded} trade(s) · <span style={pnlStyle(dayPnl)}>{inr(dayPnl)}</span> gross
        </span>
      </div>

      {err && <div style={{ ...card, borderColor: alpha(colors.danger, 50), color: colors.danger }}>Backend: {err}</div>}
      {msg && <div style={{ ...card, padding: spacing.md, borderColor: msg.tone === "err" ? alpha(colors.danger, 50) : alpha(colors.success, 50),
                            color: msg.tone === "err" ? colors.danger : colors.success }}>{msg.text}</div>}
      {(st?.stale_open || []).length > 0 && (
        <div style={{ ...card, borderColor: alpha(colors.danger, 50), color: colors.danger }}>
          {st.stale_open.length} trade(s) from an earlier day are still open — the engine closes them as STALE at its next session start. Verify the broker book.
        </div>
      )}

      {/* form */}
      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.md }}>
          <div style={{ ...typography.headingSmall }}>{editId ? `Edit condition #${editId}` : "New condition (today only)"}</div>
          <div style={{ display: "flex", gap: 4 }}>
            {INSTS.map((k) => (
              <Btn key={k} small tone={form.instrument === k ? "primary" : "ghost"} onClick={() => setInst(k)}>{k}</Btn>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.md }}>
          <Field label="Upper limit (spot)" hint={spot ? `spot ${n2(spot)}` : "CE side"} width={140}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.upper} placeholder="e.g. 25000"
                   onChange={(e) => setF("upper", e.target.value)} />
          </Field>
          <Field label="Lower limit (spot)" hint="PE side" width={140}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.lower} placeholder="e.g. 24800"
                   onChange={(e) => setF("lower", e.target.value)} />
          </Field>
          <Field label="Premium min ₹" width={100}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.prem_min} onChange={(e) => setF("prem_min", e.target.value)} />
          </Field>
          <Field label="Premium max ₹" width={100}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.prem_max} onChange={(e) => setF("prem_max", e.target.value)} />
          </Field>
          <Field label="SL spot pts" hint="from the limit" width={100}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.sl_pts}
                   onChange={(e) => { setSlTouched(true); setF("sl_pts", e.target.value); }} />
          </Field>
          <Field label="TP spot pts" hint="from the limit · 0 = none" width={110}>
            <input style={{ ...inputStyle, ...mono }} inputMode="decimal" value={form.tp_pts}
                   onChange={(e) => { setTpTouched(true); setF("tp_pts", e.target.value); }} />
          </Field>
          <Field label="EOD" hint="09:20–15:20" width={100}>
            <input style={{ ...inputStyle, ...mono }} type="time" min="09:20" max="15:20" value={form.eod} onChange={(e) => setF("eod", e.target.value)} />
          </Field>
          <Field label="Lots" width={70}>
            <input style={{ ...inputStyle, ...mono }} inputMode="numeric" value={form.lots} onChange={(e) => setF("lots", e.target.value)} />
          </Field>
        </div>
        {preview.length > 0 && (
          <ul style={{ margin: `${spacing.md}px 0 0`, paddingLeft: 18, ...typography.bodySmall, color: colors.text.secondary }}>
            {preview.map((p) => <li key={p}>{p}</li>)}
          </ul>
        )}
        <div style={{ display: "flex", gap: spacing.sm, marginTop: spacing.md }}>
          <Btn tone="primary" disabled={busy || (form.upper === "" && form.lower === "")} onClick={submit}>
            {editId ? "Save changes" : "Arm condition"}
          </Btn>
          {editId && <Btn tone="ghost" onClick={() => { setEditId(null); setTpTouched(false); setSlTouched(false); setForm(emptyForm(defaults, form.instrument)); }}>Cancel edit</Btn>}
        </div>
      </div>

      {/* open trades */}
      {open.length > 0 && (
        <div style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.sm }}>
            <div style={{ ...typography.headingSmall }}>Open trades ({open.length})</div>
            <Btn tone="danger" small onClick={() => twoTap("sqall", squareOffAll)}>
              {isArmed("sqall") ? "Tap again: square off ALL" : "Square off all"}
            </Btn>
          </div>
          {isMobile ? (
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
              {open.map((c) => (
                <div key={c.id} style={{ border: `1px solid ${colors.border.light}`, borderRadius: 8, padding: spacing.md, background: colors.bg.tertiary }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: spacing.sm }}>
                    <span style={{ ...mono, fontSize: 13 }}><Chip text={c.side} /> {c.symbol}</span>
                    <span style={{ ...mono, fontSize: 15, ...pnlStyle(c.pnl) }}>{inr(c.pnl)}</span>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: spacing.sm, fontSize: 12, color: colors.text.secondary }}>
                    <span>Entry <b style={mono}>{n2(c.entry_px)}</b></span>
                    <span>LTP <b style={mono}>{n2(c.mark)}</b></span>
                    <span>SL <b style={{ ...mono, color: colors.loss }}>{c.sl_spot != null ? n2(c.sl_spot) : `₹${n2(c.sl_px)}`}</b></span>
                    <span>Spot <b style={mono}>{n2(c.mark_spot)}</b></span>
                    <span>TP <b style={{ ...mono, color: colors.profit }}>{c.tp_spot == null ? "none" : n2(c.tp_spot)}</b></span>
                    <span>EOD <b style={mono}>{c.eod}</b></span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: spacing.sm }}>
                    <span style={{ fontSize: 11, color: colors.text.tertiary }}>#{c.id} · <Chip text={c.mode} /> · {c.qty} qty · in {istTime(c.entry_ts)}</span>
                    <Btn tone="danger" small onClick={() => twoTap(`sq:${c.id}`, () => squareOff(c))}>
                      {isArmed(`sq:${c.id}`) ? "Tap again to square off" : "Square off"}
                    </Btn>
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>{["#", "Side", "Contract", "Book", "Entry", "LTP", "SL spot", "Spot", "TP spot", "MTM", "EOD", ""].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {open.map((c) => (
                  <tr key={c.id}>
                    <td style={td}>{c.id}</td>
                    <td style={td}><Chip text={c.side} /></td>
                    <td style={{ ...td, ...mono }}>{c.symbol}<div style={{ fontSize: 11, color: colors.text.tertiary }}>{c.qty} qty · in {istTime(c.entry_ts)}</div></td>
                    <td style={td}><Chip text={c.mode} /></td>
                    <td style={{ ...td, ...mono }}>{n2(c.entry_px)}</td>
                    <td style={{ ...td, ...mono }}>{n2(c.mark)}</td>
                    <td style={{ ...td, ...mono, color: colors.loss }}>{c.sl_spot != null ? n2(c.sl_spot) : `₹${n2(c.sl_px)}`}</td>
                    <td style={{ ...td, ...mono }}>{n2(c.mark_spot)}</td>
                    <td style={{ ...td, ...mono, color: colors.profit }}>{c.tp_spot == null ? "none" : n2(c.tp_spot)}</td>
                    <td style={{ ...td, ...mono, ...pnlStyle(c.pnl) }}>{inr(c.pnl)}</td>
                    <td style={{ ...td, ...mono }}>{c.eod}</td>
                    <td style={td}><Btn tone="danger" small onClick={() => twoTap(`sq:${c.id}`, () => squareOff(c))}>
                      {isArmed(`sq:${c.id}`) ? "Tap again to square off" : "Square off"}
                    </Btn></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </div>
      )}

      {/* today */}
      <div style={card}>
        <div style={{ ...typography.headingSmall, marginBottom: spacing.sm }}>Today · {st?.day || "—"} ({conds.length})</div>
        {conds.length === 0 ? (
          <div style={{ ...typography.bodySmall, color: colors.text.tertiary }}>No conditions yet. Armed conditions are checked on every 5-minute close until their EOD.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>{["#", "Inst", "Upper → CE", "Lower → PE", "Band ₹", "SL", "EOD", "Lots", "Status", "Trade", "Result", "Note", ""].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {conds.slice().reverse().map((c) => (
                  <tr key={c.id} style={{ opacity: ["EXPIRED", "CANCELLED"].includes(c.status) ? 0.6 : 1 }}>
                    <td style={td}>{c.id}<div style={{ fontSize: 11, color: colors.text.tertiary }}>{istTime(c.created_at)}</div></td>
                    <td style={td}>{c.instrument}</td>
                    <td style={{ ...td, ...mono }}>{c.upper == null ? "—" : n2(c.upper)}
                      {c.stops?.CE != null && <div style={{ fontSize: 11, color: colors.loss }}>SL {n2(c.stops.CE)}</div>}
                      {c.targets?.CE != null && <div style={{ fontSize: 11, color: colors.profit }}>TP {n2(c.targets.CE)}</div>}</td>
                    <td style={{ ...td, ...mono }}>{c.lower == null ? "—" : n2(c.lower)}
                      {c.stops?.PE != null && <div style={{ fontSize: 11, color: colors.loss }}>SL {n2(c.stops.PE)}</div>}
                      {c.targets?.PE != null && <div style={{ fontSize: 11, color: colors.profit }}>TP {n2(c.targets.PE)}</div>}</td>
                    <td style={{ ...td, ...mono }}>{n2(c.prem_min, 0)}–{n2(c.prem_max, 0)}</td>
                    <td style={{ ...td, ...mono }}>{c.legacy_sl ? `${n2(c.sl_pct, 1)}% (old)` : `${n2(c.sl_pts, 0)} pts`}</td>
                    <td style={{ ...td, ...mono }}>{c.eod}</td>
                    <td style={{ ...td, ...mono }}>{c.lots}</td>
                    <td style={td}><Chip text={c.open ? "OPEN" : c.status} /></td>
                    <td style={{ ...td, ...mono }}>{c.symbol ? <>
                      <Chip text={c.side} /> {c.symbol}
                      <div style={{ fontSize: 11, color: colors.text.tertiary }}>{c.mode} · {istTime(c.entry_ts)} @ {n2(c.entry_px)}{c.exit_ts ? ` → ${istTime(c.exit_ts)} @ ${n2(c.exit_px)}` : ""}</div>
                    </> : "—"}</td>
                    <td style={{ ...td, ...mono }}>{c.entry_px != null ? <>
                      <span style={pnlStyle(c.pnl)}>{inr(c.pnl)}</span>
                      {c.exit_reason && <div><Chip text={c.exit_reason} /></div>}
                      {c.net != null && <div style={{ fontSize: 11, color: colors.text.tertiary }}>net {inr(c.net)}</div>}
                    </> : "—"}</td>
                    <td style={{ ...td, whiteSpace: "normal", minWidth: 150, maxWidth: 240, color: colors.text.secondary }}>{c.note || ""}</td>
                    <td style={td}>
                      {c.status === "ARMED" && <div style={{ display: "flex", gap: 4 }}>
                        <Btn small onClick={() => startEdit(c)}>Edit</Btn>
                        <Btn small tone={isArmed(`cancel:${c.id}`) ? "danger" : "ghost"}
                             onClick={() => twoTap(`cancel:${c.id}`, () => cancelCond(c))}>
                          {isArmed(`cancel:${c.id}`) ? "Tap again to cancel" : "Cancel"}
                        </Btn>
                      </div>}
                      {c.open && <Btn small tone="danger" onClick={() => twoTap(`sq:${c.id}`, () => squareOff(c))}>
                        {isArmed(`sq:${c.id}`) ? "Tap again to square off" : "Square off"}
                      </Btn>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ ...typography.bodySmall, color: colors.text.muted, marginTop: spacing.md }}>
          Rules: the 5m close must cross the level fresh (previous close inside; the day's first candle uses its open, so a gap beyond the level never triggers).
          A breakout that can't be traded (no strike in the band, judged late after a restart, already past the target, mode Off) leaves the condition armed with a note.
          Only a filled entry uses the condition up.
        </div>
      </div>
    </div>
  );
}

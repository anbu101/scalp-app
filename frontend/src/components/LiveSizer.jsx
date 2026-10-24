/* frontend/src/components/LiveSizer.jsx — ── LIVE_SIZER_20261024 ──
   Portfolio-level compounding for the LIVE account, SUGGEST-ONLY. The live twin
   of Backtest → Portfolio "Shared equity": each morning (09:05, trading days)
   the backend works out every member strategy's lots from the account —
     equity = account start + net realised of the members' LIVE legs closed
              since the start date (before today) + deposits − withdrawals − tax
     lots   = max(start, ⌊start × equity ÷ sizing capital⌋), capped
   — and compares them with the lots set in each strategy's Settings. It never
   changes a setting: when a suggestion differs, a bell alert names it and this
   panel shows exactly which lot fields to change. Strategy names go through
   stratName(), so non-admin users never see a raw strategy id. */
import React, { useCallback, useEffect, useState } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography } from "../tokens";
import { stratName } from "../strategies/displayNames";
import { useEntitlements } from "../hooks/useEntitlements";

const TH = { padding: "8px 8px", textAlign: "right", ...typography.label, color: colors.text.muted,
             borderBottom: `2px solid ${colors.border.light}`, fontWeight: 600, whiteSpace: "nowrap" };
const TD = { padding: "7px 8px", ...typography.bodyMedium, ...typography.mono, textAlign: "right", whiteSpace: "nowrap" };
const INR = (v) => (v == null ? "—" : `${Number(v) < 0 ? "−" : ""}₹${Math.abs(Math.round(Number(v))).toLocaleString("en-IN")}`);
const LAKH = (v) => (v == null ? "—" : `₹${(Number(v) / 100000).toFixed(2)}L`);
const todayIst = () => new Date(Date.now() + 19800000).toISOString().slice(0, 10);

export default function LiveSizer() {
  const { loaded, isAdminUi } = useEntitlements();
  const admin = !loaded || isAdminUi;
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(true);
  const [edit, setEdit] = useState(null);           // draft config while the plan editor is open
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [adj, setAdj] = useState({ day: todayIst(), kind: "DEPOSIT", amount: "", note: "" });
  const [funds, setFunds] = useState(null);

  const load = useCallback(async () => {
    try { setData(await (await fetch(`${getApiBase()}/api/live-sizer`)).json()); }
    catch (e) { setData({ ok: false, warnings: [String(e.message || e)], rows: [] }); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  const call = useCallback(async (path, method, body) => {
    const r = await fetch(`${getApiBase()}${path}`, { method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `API ${r.status}`);
    return j;
  }, []);

  const saveCfg = async () => {
    setBusy(true); setMsg("");
    try { await call("/api/live-sizer/config", "PUT", edit); setEdit(null); setMsg("Plan saved."); await load(); }
    catch (e) { setMsg(`Not saved: ${e.message}`); } finally { setBusy(false); }
  };
  const addAdj = async () => {
    setBusy(true); setMsg("");
    try { await call("/api/live-sizer/adjustments", "POST", adj); setAdj({ ...adj, amount: "", note: "" }); await load(); }
    catch (e) { setMsg(`Not added: ${e.message}`); } finally { setBusy(false); }
  };
  const delAdj = async (id) => {
    try { await call(`/api/live-sizer/adjustments/${id}`, "DELETE"); await load(); } catch (e) { setMsg(e.message); }
  };
  const checkFunds = async () => {
    setFunds({ busy: true });
    try { setFunds(await (await fetch(`${getApiBase()}/api/live-sizer/broker-funds`)).json()); }
    catch (e) { setFunds({ ok: false, reason: String(e.message || e) }); }
  };

  const d = data || {};
  const cfg = d.config || {};
  const rows = d.rows || [];
  const name = (id) => stratName(id, admin, id);
  const sel = { padding: "5px 8px", borderRadius: 6, border: `1px solid ${colors.border.light}`, background: colors.bg.tertiary,
                color: colors.text.primary, fontSize: 12 };
  const btn = { ...sel, cursor: "pointer", fontWeight: 600 };
  const inp = (w) => ({ ...sel, width: w, fontFamily: "var(--c-font-ui)" });
  const lab = { display: "flex", flexDirection: "column", gap: 3, fontSize: 11, color: colors.text.muted };

  const startEdit = () => setEdit(JSON.parse(JSON.stringify({ enabled: cfg.enabled, start_date: cfg.start_date || todayIst(),
    account_start: cfg.account_start, sizing_capital: cfg.sizing_capital, floor_start: cfg.floor_start,
    max_total: cfg.max_total, members: cfg.members || {} })));
  const setM = (sid, k, v) => setEdit((e) => ({ ...e, members: { ...e.members, [sid]: { ...(e.members[sid] || {}), [k]: v } } }));
  const toggleM = (sid) => setEdit((e) => {
    const m = { ...e.members };
    if (m[sid]) delete m[sid]; else m[sid] = { start: 1, max: 0 };
    return { ...e, members: m };
  });

  return (
    <div data-testid="live-sizer" style={{ background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8,
                  boxShadow: "0 1px 3px var(--c-shadow)", marginTop: spacing.xl, padding: spacing.lg }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btn, background: "transparent", border: "none", padding: 0,
                fontSize: 15, color: colors.text.primary }} title="Show / hide">
          {open ? "▾" : "▸"} Live sizer
        </button>
        <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, border: `1px solid ${colors.border.light}`,
                       color: colors.text.secondary }}>suggest only</span>
        <span style={{ fontSize: 12, color: colors.text.muted }}>
          Lots for each strategy from one shared account · worked out every trading morning at 09:05 · never changes a setting
        </span>
        <div style={{ flex: 1 }} />
        {!edit && <button onClick={startEdit} style={btn} data-testid="ls-edit">Edit plan</button>}
        <button onClick={checkFunds} style={btn} title="Kite's view of the account, for comparison only">Check broker funds</button>
      </div>
      {msg && <div style={{ fontSize: 12, color: colors.text.secondary, marginTop: 6 }}>{msg}</div>}

      {open && (
        <div style={{ marginTop: spacing.md }}>
          {/* ── account ── */}
          <div data-testid="ls-account" style={{ display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "baseline", fontSize: 12.5,
                        color: colors.text.secondary, marginBottom: spacing.md }}>
            <span>Account <b style={{ fontSize: 18, color: colors.text.primary, ...typography.mono }}>{INR(d.equity)}</b></span>
            <span>= start {INR(d.account_start)}</span>
            <span>+ realised {INR(d.realised)}</span>
            {d.adjustments_total ? <span>{d.adjustments_total >= 0 ? "+" : "−"} adjustments {INR(Math.abs(d.adjustments_total))}</span> : null}
            <span style={{ color: colors.text.muted }}>
              · sizing capital {LAKH(cfg.sizing_capital)} ({LAKH(d.capital_per_lot)} per start lot) · ratio {d.ratio != null ? d.ratio.toFixed(2) : "—"}
              {cfg.start_date ? ` · counting since ${cfg.start_date}` : ""}
            </span>
            {d.realised_today_total ? <span style={{ color: colors.text.muted }}>· today {INR(d.realised_today_total)} (counts from tomorrow)</span> : null}
          </div>

          {/* ── suggestion table ── */}
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={{ ...TH, textAlign: "left" }}>Strategy</th><th style={{ ...TH, textAlign: "left" }}>Mode</th>
                <th style={TH}>Start</th><th style={TH}>Max</th><th style={TH}>Set now</th><th style={TH}>Suggested</th>
                <th style={TH}>Realised since start</th><th style={TH} title="Account value at which this strategy goes up one more lot">Next lot at</th>
                <th style={{ ...TH, textAlign: "left" }}>Fields to change</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.strategy_id} data-testid={`ls-row-${r.strategy_id}`} style={{ borderBottom: `1px solid ${colors.border.light}`,
                      background: r.change ? "rgba(245, 158, 11, 0.10)" : "transparent" }}>
                    <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", fontWeight: 600 }}>{name(r.strategy_id)}</td>
                    <td style={{ ...TD, textAlign: "left", color: r.mode === "LIVE" || r.mode === "PAPER_LIVE" ? colors.warning : colors.text.muted }}>{r.mode}</td>
                    <td style={TD}>{r.start}</td><td style={TD}>{r.max || "—"}</td>
                    <td style={TD}>{r.current ?? "—"}</td>
                    <td style={{ ...TD, fontWeight: 700, color: r.change ? colors.warning : colors.text.primary }}>{r.suggested}</td>
                    <td style={{ ...TD, color: r.realised_since_start >= 0 ? colors.profit : colors.loss }}>{INR(r.realised_since_start)}</td>
                    <td style={TD}>{r.next_at ? LAKH(r.next_at) : "at cap"}</td>
                    <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", whiteSpace: "normal", fontSize: 12 }}>
                      {r.change ? (r.preview || []).filter((p) => p.now !== p.to).map((p) => `${p.field} ${p.now}→${p.to}`).join(" · ") || "—"
                                : <span style={{ color: colors.text.muted }}>matches</span>}
                    </td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan={9} style={{ ...TD, ...typography.bodyMedium, textAlign: "left", color: colors.text.muted }}>Loading…</td></tr>}
              </tbody>
            </table>
          </div>
          {(d.warnings || []).length > 0 && (
            <div data-testid="ls-warnings" style={{ marginTop: 8, fontSize: 12, color: colors.warning, lineHeight: 1.5 }}>
              {d.warnings.map((w) => <div key={w}>⚠ {w}</div>)}
            </div>
          )}

          {funds && (
            <div style={{ marginTop: 8, fontSize: 12, color: colors.text.secondary }}>
              {funds.busy ? "Asking Kite…" : funds.ok
                ? <>Kite: net {INR(funds.net)} · opening balance {INR(funds.opening_balance)} · realised M2M today {INR(funds.m2m_realised)} · unrealised {INR(funds.m2m_unrealised)}.
                    <span style={{ color: colors.text.muted }}> Kite counts premium received on open shorts and blocked margin, so it differs from the sizer's account while positions are open; compare on a flat evening.</span></>
                : <>Broker funds unavailable: {funds.reason}</>}
            </div>
          )}

          {/* ── plan editor ── */}
          {edit && (
            <div data-testid="ls-editor" style={{ marginTop: spacing.md, paddingTop: spacing.md, borderTop: `1px solid ${colors.border.light}` }}>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={lab}>Start date (go-live)<input type="date" style={inp(150)} value={edit.start_date || ""}
                  onChange={(e) => setEdit({ ...edit, start_date: e.target.value })} /></label>
                <label style={lab}>Account at start ₹<input type="number" style={inp(130)} value={edit.account_start ?? ""}
                  onChange={(e) => setEdit({ ...edit, account_start: e.target.value })} /></label>
                <label style={lab} title="The starting capital the plan was backtested with">Sizing capital ₹<input type="number" style={inp(130)}
                  value={edit.sizing_capital ?? ""} onChange={(e) => setEdit({ ...edit, sizing_capital: e.target.value })} /></label>
                <label style={lab}>Max total lots<input type="number" style={inp(100)} placeholder="no cap" value={edit.max_total || ""}
                  onChange={(e) => setEdit({ ...edit, max_total: e.target.value })} /></label>
                <label style={{ ...lab, flexDirection: "row", alignItems: "center", gap: 6, paddingBottom: 6, color: colors.text.secondary }}>
                  <input type="checkbox" checked={!!edit.floor_start} onChange={(e) => setEdit({ ...edit, floor_start: e.target.checked })} />Never below start lots</label>
                <label style={{ ...lab, flexDirection: "row", alignItems: "center", gap: 6, paddingBottom: 6, color: colors.text.secondary }}>
                  <input type="checkbox" checked={!!edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} />Morning check on</label>
              </div>
              <table style={{ borderCollapse: "collapse", marginTop: 10 }}>
                <thead><tr>{["", "Strategy", "Start lots", "Max lots"].map((h) => <th key={h} style={{ ...TH, textAlign: "left" }}>{h}</th>)}</tr></thead>
                <tbody>
                  {(d.supported || Object.keys(edit.members)).map((sid) => {
                    const m = edit.members[sid];
                    return (
                      <tr key={sid}>
                        <td style={{ ...TD, textAlign: "left" }}><input type="checkbox" checked={!!m} onChange={() => toggleM(sid)} /></td>
                        <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", opacity: m ? 1 : 0.55 }}>{name(sid)}</td>
                        <td style={TD}>{m ? <input type="number" style={inp(64)} value={m.start ?? ""} onChange={(e) => setM(sid, "start", e.target.value)} /> : null}</td>
                        <td style={TD}>{m ? <input type="number" style={inp(64)} placeholder="no cap" value={m.max || ""} onChange={(e) => setM(sid, "max", e.target.value)} /> : null}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button style={{ ...btn, background: colors.primary, color: "#fff", border: "none" }} disabled={busy} onClick={saveCfg} data-testid="ls-save">Save plan</button>
                <button style={btn} onClick={() => setEdit(null)}>Cancel</button>
              </div>
            </div>
          )}

          {/* ── adjustments ── */}
          <div style={{ marginTop: spacing.md, paddingTop: spacing.md, borderTop: `1px solid ${colors.border.light}` }}>
            <div style={{ fontSize: 12.5, color: colors.text.secondary, fontWeight: 600, marginBottom: 6 }}>
              Deposits, withdrawals and tax paid
              <span style={{ fontWeight: 400, color: colors.text.muted }}> — they move the account from the next morning</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <input type="date" style={inp(150)} value={adj.day} onChange={(e) => setAdj({ ...adj, day: e.target.value })} />
              <select style={sel} value={adj.kind} onChange={(e) => setAdj({ ...adj, kind: e.target.value })}>
                <option value="DEPOSIT">Deposit</option><option value="WITHDRAW">Withdrawal</option><option value="TAX">Tax paid</option>
              </select>
              <input type="number" style={inp(130)} placeholder="₹ amount" value={adj.amount} onChange={(e) => setAdj({ ...adj, amount: e.target.value })} />
              <input style={inp(200)} placeholder="note (optional)" value={adj.note} onChange={(e) => setAdj({ ...adj, note: e.target.value })} />
              <button style={btn} disabled={busy || !adj.amount} onClick={addAdj}>Add</button>
            </div>
            {(d.adjustments || []).length > 0 && (
              <table style={{ borderCollapse: "collapse", marginTop: 8 }}>
                <tbody>
                  {d.adjustments.map((a) => (
                    <tr key={a.id}>
                      <td style={{ ...TD, textAlign: "left", color: colors.text.muted }}>{a.day}</td>
                      <td style={{ ...TD, textAlign: "left" }}>{a.kind === "DEPOSIT" ? "Deposit" : a.kind === "WITHDRAW" ? "Withdrawal" : "Tax paid"}</td>
                      <td style={{ ...TD, color: a.kind === "DEPOSIT" ? colors.profit : colors.loss }}>{a.kind === "DEPOSIT" ? "+" : "−"}{INR(a.amount)}</td>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", color: colors.text.muted }}>{a.note}</td>
                      <td style={TD}><button style={{ ...btn, padding: "2px 8px" }} onClick={() => delAdj(a.id)}>Remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {(d.history || []).length > 0 && (
            <details style={{ marginTop: spacing.md }}>
              <summary style={{ cursor: "pointer", fontSize: 12, color: colors.text.secondary }}>Morning checks ({d.history.length})</summary>
              <table style={{ borderCollapse: "collapse", marginTop: 6 }}>
                <tbody>
                  {d.history.map((h) => (
                    <tr key={h.day}>
                      <td style={{ ...TD, textAlign: "left", color: colors.text.muted }}>{h.day}</td>
                      <td style={TD}>{INR(h.equity)}</td>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left" }}>
                        {Object.entries(h.lots || {}).map(([sid, v]) => `${name(sid)} ${v.current ?? "—"}${v.current !== v.suggested ? `→${v.suggested}` : ""}`).join(" · ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 8, lineHeight: 1.5 }}>
            Lots = max(start lots, ⌊start lots × account ÷ sizing capital⌋), capped at each strategy's max — the rule backtested in Portfolio → Shared equity.
            The account counts only the member strategies' LIVE trades closed since the start date (net of charges), plus the adjustments above.
            Suggest only: when a suggestion differs from the lots set in Settings, a bell alert names it at 09:05 and the row turns amber; change the lots yourself after the day's positions are closed.
          </div>
        </div>
      )}
    </div>
  );
}

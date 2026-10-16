/* frontend/src/components/MarginLog.jsx — ── MARGIN_LOG_20261016 ── ── MARGIN_LOG_ENTRY_20261016 ──
   "How much money does each strategy really need?" Reads the margin log the
   backend samples from the OPEN paper and live legs via Zerodha's basket-margin
   API — within a minute of every entry / leg change (from 09:17 IST) and every
   5 min 09:20–15:25.
   ── MARGIN_LOG_FLEET_SEL_20261016 ── tick which strategies count in "Fleet at
   once" (e.g. only the ones you will run live); remembered on this machine. Summary per strategy + book, fleet
   concurrent peak per book, CSV export of every sample. Read-only; the only
   write is "Sample now" (one manual pass). Strategy names go through
   stratName(), so non-admin users never see a raw strategy id. */
import React, { useCallback, useEffect, useState } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography } from "../tokens";
import { exportToCSV } from "../utils/export";
import { stratName } from "../strategies/displayNames";
import { useEntitlements } from "../hooks/useEntitlements";

const TH = { padding: "8px 8px", textAlign: "right", ...typography.label, color: colors.text.muted,
             borderBottom: `2px solid ${colors.border.light}`, fontWeight: 600, whiteSpace: "nowrap" };
const TD = { padding: "7px 8px", ...typography.bodyMedium, ...typography.mono, textAlign: "right", whiteSpace: "nowrap" };
const L = (v) => (v == null ? "—" : `₹${(Number(v) / 100000).toFixed(2)}L`);
const K = (v) => (v == null ? "—" : `₹${Math.round(Number(v)).toLocaleString("en-IN")}`);
const lots = (v) => (v == null ? "—" : (Math.round(Number(v) * 10) / 10).toString());
const SEL_KEY = "marginLog.fleetSel.v1";   // JSON array of strategy ids; absent = all
function loadSel() {
  try { const v = JSON.parse(window.localStorage.getItem(SEL_KEY)); return Array.isArray(v) ? new Set(v) : null; }
  catch { return null; }
}
function saveSel(sel) {
  try { if (sel === null) window.localStorage.removeItem(SEL_KEY); else window.localStorage.setItem(SEL_KEY, JSON.stringify([...sel])); }
  catch { /* storage unavailable — selection lasts for this session */ }
}
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : "—");

export default function MarginLog() {
  const { loaded, isAdminUi } = useEntitlements();
  const admin = !loaded || isAdminUi;
  const [days, setDays] = useState(14);
  const [book, setBook] = useState("ALL");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(true);
  const [fleetSel, setFleetSel] = useState(loadSel);       // null = every strategy
  useEffect(() => { saveSel(fleetSel); }, [fleetSel]);

  const load = useCallback(async () => {
    try {
      const fq = fleetSel === null ? "" : `&fleet=${encodeURIComponent(fleetSel.size ? [...fleetSel].join(",") : "-")}`;
      const r = await fetch(`${getApiBase()}/api/margin-log/summary?days=${days}&book=${book}${fq}`);
      setData(await r.json());
    } catch (e) { setData({ ok: false, error: String(e.message || e), strategies: [], fleet: {} }); }
  }, [days, book, fleetSel]);
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

  const sampleNow = useCallback(async () => {
    setBusy(true); setMsg("");
    try {
      const r = await (await fetch(`${getApiBase()}/api/margin-log/sample-now`, { method: "POST" })).json();
      setMsg(r.sampled ? `Sampled ${r.rows} position(s)${r.errors ? ` · ${r.errors} failed` : ""}${r.reason ? ` · ${r.reason}` : ""}` : `Not sampled: ${r.reason || "unknown"}`);
      await load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [load]);

  const exportCsv = useCallback(async () => {
    try {
      const r = await (await fetch(`${getApiBase()}/api/margin-log/samples?days=${days}&book=${book}`)).json();
      const rows = (r.rows || []).map((x) => ({
        day: x.day, time: x.hhmm, strategy: stratName(x.strategy_id, admin, x.strategy_id), book: x.book, mode: x.mode,
        trigger: x.trigger || "TIMER",
        legs: x.n_legs, lots: x.base_lots, margin_total: x.total, span: x.span, exposure: x.exposure,
        option_premium: x.premium, margin_per_lot: x.per_lot, error: x.error || "", legs_detail: x.legs_json,
      }));
      if (!rows.length) { setMsg("Nothing to export yet."); return; }
      exportToCSV(rows, `margin_log_${days}d_${book.toLowerCase()}.csv`);
    } catch (e) { setMsg(String(e.message || e)); }
  }, [days, book, admin]);

  const S = (data && data.strategies) || [];
  const F = (data && data.fleet) || {};
  const ids = [...new Set(S.map((r) => r.strategy_id))];
  const inFleet = (id) => fleetSel === null || fleetSel.has(id);
  const nSel = ids.filter(inFleet).length;
  const toggle = (id) => setFleetSel((prev) => {
    const n = new Set(prev === null ? ids : prev);
    if (n.has(id)) n.delete(id); else n.add(id);
    return ids.every((x) => n.has(x)) && n.size === ids.length ? null : n;
  });
  const toggleAll = () => setFleetSel(nSel === ids.length ? new Set() : null);
  const sel = { padding: "5px 8px", borderRadius: 6, border: `1px solid ${colors.border.light}`, background: colors.bg.tertiary,
                color: colors.text.primary, fontSize: 12 };
  const btn = { ...sel, cursor: "pointer", fontWeight: 600 };
  return (
    <div style={{ background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8,
                  boxShadow: "0 1px 3px var(--c-shadow)", marginTop: spacing.xl, padding: spacing.lg }}>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btn, background: "transparent", border: "none", padding: 0,
                fontSize: 15, color: colors.text.primary }} title="Show / hide">
          {open ? "▾" : "▸"} Margin log
        </button>
        <span style={{ fontSize: 12, color: colors.text.muted }}>
          Zerodha basket margin of each strategy's open legs · at every entry / leg change (from 09:17) and every 5 min · paper and live kept apart
        </span>
        <div style={{ flex: 1 }} />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={sel}>
          {[3, 7, 14, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
        <select value={book} onChange={(e) => setBook(e.target.value)} style={sel}>
          <option value="ALL">Paper + Live</option><option value="PAPER">Paper</option><option value="LIVE">Live</option>
        </select>
        <button onClick={sampleNow} disabled={busy} style={btn}>{busy ? "Sampling…" : "Sample now"}</button>
        <button onClick={exportCsv} style={btn}>Export CSV</button>
      </div>
      {msg && <div style={{ fontSize: 12, color: colors.text.secondary, marginTop: 6 }}>{msg}</div>}
      {open && (
        <div style={{ marginTop: spacing.md }}>
          {data && data.ok === false && <div style={{ color: colors.loss, fontSize: 12 }}>Margin log unavailable: {data.error}</div>}
          {!S.length ? (
            <div style={{ fontSize: 12, color: colors.text.muted, padding: "8px 0" }}>
              No samples yet. They start on the next market-hours tick while any strategy holds a position — or press “Sample now”.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr>
                  <th style={{ ...TH, textAlign: "center", width: 1 }} title="Count in “Fleet at once”">
                    <input type="checkbox" checked={nSel === ids.length && ids.length > 0}
                      ref={(el) => { if (el) el.indeterminate = nSel > 0 && nSel < ids.length; }}
                      onChange={toggleAll} style={{ cursor: "pointer" }} aria-label="All strategies in the fleet total" />
                  </th>
                  <th style={{ ...TH, textAlign: "left" }}>Strategy</th><th style={{ ...TH, textAlign: "left" }}>Book</th>
                  <th style={TH}>Days</th><th style={TH}>Samples</th><th style={TH}>Lots</th>
                  <th style={TH}>Median</th><th style={TH}>P90</th><th style={TH}>Peak</th>
                  <th style={TH} title="Margin sampled within a minute of the position being opened">At entry · median</th>
                  <th style={TH}>At entry · peak</th>
                  <th style={TH}>Per lot · median</th><th style={TH}>Per lot · peak</th><th style={TH}>Last</th>
                </tr></thead>
                <tbody>
                  {S.map((r) => (
                    <tr key={r.strategy_id + r.book} style={{ borderBottom: `1px solid ${colors.border.light}`,
                        opacity: inFleet(r.strategy_id) ? 1 : 0.55 }}>
                      <td style={{ ...TD, textAlign: "center" }}>
                        <input type="checkbox" checked={inFleet(r.strategy_id)} onChange={() => toggle(r.strategy_id)}
                          style={{ cursor: "pointer" }} title="Count this strategy in “Fleet at once” (both books)"
                          aria-label={`Fleet total: ${r.strategy_id}`} />
                      </td>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", fontWeight: 600 }}>{stratName(r.strategy_id, admin, r.strategy_id)}</td>
                      <td style={{ ...TD, textAlign: "left", color: r.book === "LIVE" ? colors.warning : colors.text.secondary }}>{r.book}</td>
                      <td style={TD}>{r.days}</td>
                      <td style={TD} title={r.errors ? `${r.errors} failed sample(s) — last: ${r.last_error}` : ""}>
                        {r.samples}{r.errors ? <span style={{ color: colors.loss }}> (+{r.errors}✕)</span> : null}</td>
                      <td style={TD} title={r.lots_max !== r.lots_typical ? `up to ${lots(r.lots_max)}` : ""}>{lots(r.lots_typical)}</td>
                      <td style={TD}>{L(r.median)}</td><td style={TD}>{L(r.p90)}</td>
                      <td style={{ ...TD, fontWeight: 700, lineHeight: 1.25 }}>{L(r.peak)}
                        {r.peak_at && <div style={{ fontSize: 10, fontWeight: 400, color: colors.text.muted }}>{r.peak_at.slice(5)}{r.peak_lots ? ` · ${lots(r.peak_lots)}L` : ""}</div>}</td>
                      <td style={TD} title={r.entries ? `${r.entries} entr${r.entries === 1 ? "y" : "ies"} recorded` : "no entry recorded yet"}>{L(r.entry_median)}</td>
                      <td style={TD}>{L(r.entry_peak)}</td>
                      <td style={TD}>{K(r.per_lot_median)}</td><td style={TD}>{K(r.per_lot_peak)}</td>
                      <td style={{ ...TD, color: colors.text.muted }}>{when(r.last_ts)}</td>
                    </tr>
                  ))}
                  {["PAPER", "LIVE"].filter((b) => F[b]).map((b) => (
                    <tr key={"fleet" + b} style={{ background: colors.bg.tertiary }}>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", fontWeight: 700 }} colSpan={3}>
                        Fleet at once · {b}
                        <span style={{ fontSize: 11, fontWeight: 400, color: colors.text.muted }}>
                          {" "}· {fleetSel === null ? "all strategies" : `${(F[b].strategies || []).length ? (F[b].strategies || []).map((x) => stratName(x, admin, x)).join(", ") : "—"}`}
                        </span>
                      </td>
                      <td style={TD} /><td style={TD}>{F[b].samples}</td><td style={TD} />
                      <td style={TD}>{L(F[b].median)}</td><td style={TD}>{L(F[b].p90)}</td>
                      <td style={{ ...TD, fontWeight: 700, lineHeight: 1.25 }}>{L(F[b].peak)}
                        <div style={{ fontSize: 10, fontWeight: 400, color: colors.text.muted }}>{(F[b].peak_at || "").slice(5)}</div></td>
                      <td style={TD} colSpan={5} />
                    </tr>
                  ))}
                  {nSel === 0 && (
                    <tr><td colSpan={14} style={{ ...TD, ...typography.bodyMedium, textAlign: "left", color: colors.text.muted }}>
                      No strategy ticked — tick the ones you plan to run together to see their “Fleet at once”.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 8, lineHeight: 1.5 }}>
            Lots = the largest sell leg (or the largest leg for buy-only strategies); per lot = margin ÷ those lots.
            “At entry” = sampled within a minute of the position opening (09:16 entries are measured at 09:17, clear of the opening order burst).
            “Fleet at once” = the ticked strategies of a book at their latest sampled margin, added up at each sample time — what the account has to hold together. Ticks are remembered on this machine; an unticked strategy is still recorded.
            Margin between samples is not seen — keep a buffer above the peak.
          </div>
        </div>
      )}
    </div>
  );
}

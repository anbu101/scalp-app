/* frontend/src/components/MarginLog.jsx — ── MARGIN_LOG_20261016 ──
   "How much money does each strategy really need?" Reads the margin log the
   backend samples every 5 min (09:20–15:25 IST) from the OPEN paper and live
   legs via Zerodha's basket-margin API. Summary per strategy + book, fleet
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

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${getApiBase()}/api/margin-log/summary?days=${days}&book=${book}`);
      setData(await r.json());
    } catch (e) { setData({ ok: false, error: String(e.message || e), strategies: [], fleet: {} }); }
  }, [days, book]);
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
        legs: x.n_legs, lots: x.base_lots, margin_total: x.total, span: x.span, exposure: x.exposure,
        option_premium: x.premium, margin_per_lot: x.per_lot, error: x.error || "", legs_detail: x.legs_json,
      }));
      if (!rows.length) { setMsg("Nothing to export yet."); return; }
      exportToCSV(rows, `margin_log_${days}d_${book.toLowerCase()}.csv`);
    } catch (e) { setMsg(String(e.message || e)); }
  }, [days, book, admin]);

  const S = (data && data.strategies) || [];
  const F = (data && data.fleet) || {};
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
          Zerodha basket margin of each strategy's open legs · every 5 min, 09:20–15:25 · paper and live kept apart
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
                  <th style={{ ...TH, textAlign: "left" }}>Strategy</th><th style={{ ...TH, textAlign: "left" }}>Book</th>
                  <th style={TH}>Days</th><th style={TH}>Samples</th><th style={TH}>Lots</th>
                  <th style={TH}>Median</th><th style={TH}>P90</th><th style={TH}>Peak</th><th style={TH}>Peak at</th>
                  <th style={TH}>Per lot · median</th><th style={TH}>Per lot · peak</th><th style={TH}>Last</th>
                </tr></thead>
                <tbody>
                  {S.map((r) => (
                    <tr key={r.strategy_id + r.book} style={{ borderBottom: `1px solid ${colors.border.light}` }}>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", fontWeight: 600 }}>{stratName(r.strategy_id, admin, r.strategy_id)}</td>
                      <td style={{ ...TD, textAlign: "left", color: r.book === "LIVE" ? colors.warning : colors.text.secondary }}>{r.book}</td>
                      <td style={TD}>{r.days}</td>
                      <td style={TD} title={r.errors ? `${r.errors} failed sample(s) — last: ${r.last_error}` : ""}>
                        {r.samples}{r.errors ? <span style={{ color: colors.loss }}> (+{r.errors}✕)</span> : null}</td>
                      <td style={TD} title={r.lots_max !== r.lots_typical ? `up to ${lots(r.lots_max)}` : ""}>{lots(r.lots_typical)}</td>
                      <td style={TD}>{L(r.median)}</td><td style={TD}>{L(r.p90)}</td>
                      <td style={{ ...TD, fontWeight: 700 }}>{L(r.peak)}</td>
                      <td style={{ ...TD, color: colors.text.muted }}>{r.peak_at ? `${r.peak_at}${r.peak_lots ? ` · ${lots(r.peak_lots)}L` : ""}` : "—"}</td>
                      <td style={TD}>{K(r.per_lot_median)}</td><td style={TD}>{K(r.per_lot_peak)}</td>
                      <td style={{ ...TD, color: colors.text.muted }}>{when(r.last_ts)}</td>
                    </tr>
                  ))}
                  {["PAPER", "LIVE"].filter((b) => F[b]).map((b) => (
                    <tr key={"fleet" + b} style={{ background: colors.bg.tertiary }}>
                      <td style={{ ...TD, ...typography.bodyMedium, textAlign: "left", fontWeight: 700 }} colSpan={2}>Fleet at once · {b}</td>
                      <td style={TD} /><td style={TD}>{F[b].samples}</td><td style={TD} />
                      <td style={TD}>{L(F[b].median)}</td><td style={TD}>{L(F[b].p90)}</td>
                      <td style={{ ...TD, fontWeight: 700 }}>{L(F[b].peak)}</td>
                      <td style={{ ...TD, color: colors.text.muted }}>{F[b].peak_at}</td>
                      <td style={TD} colSpan={3} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 8, lineHeight: 1.5 }}>
            Lots = the largest sell leg (or the largest leg for buy-only strategies); per lot = margin ÷ those lots.
            “Fleet at once” = all strategies of a book added up within the same 5-minute sample — what the account has to hold together.
            A 5-minute sample can miss a short spike (e.g. right after an adjustment) — keep a buffer above the peak.
          </div>
        </div>
      )}
    </div>
  );
}

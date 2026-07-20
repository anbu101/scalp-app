// frontend/src/strategies/tma/TMAPanel.jsx
//
// ── TMA_V1 dashboard panel ── (PSTPanel v2 conventions, 2026-07-19)
// Triple-EMA (5/13/89 @5m spot) credit spread: SHORT the trend-opposite
// side + far-OTM hedge. Open-group card shows the SELL leg's progress bars
// (premium falling toward TP = green; rising toward SL = red) plus the
// hedge line and combined live spread P&L from the shared ltpMap.
// "Today" numbers are EXIT-timestamp based and the open list ignores entry
// day entirely (positional carries must show) — the lifetime-totals-
// mislabeled-as-today bug family starts with entry-day filters; not here.
// Engine-health strip surfaces the signal engine's frozen flag and the
// manager's disabled flag — silent failure is the enemy.

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, typography, pnlStyle } from "../../tokens";

const ACCENT = "#8b5cf6";

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}
function fmt(v, dec = 2) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(dec);
}
function fmtInr(v) {
  if (v == null) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : ""}₹${a.toLocaleString("en-IN")}`;
}
function fmtTs(e) {
  if (!e) return "—";
  return new Date(e * 1000).toLocaleTimeString("en-IN",
    { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}
const clampPct = (p) => Math.max(0, Math.min(100, p));

/* Premium falling from entry toward TP (short — falling is good). */
function TargetBar({ from, to, cur }) {
  const has = cur != null && from != null && to != null && from !== to;
  const pct = has ? clampPct(((from - cur) / (from - to)) * 100) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 3 }}>
        <span style={{ color: colors.text.muted }}>Sold premium (short — falling is good) · entry {fmt(from)}</span>
        <span style={{ color: colors.profit, fontWeight: 700 }}>TP {fmt(to)}</span>
      </div>
      <div style={{ height: 5, background: colors.bg.tertiary, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: colors.profit, borderRadius: 3, transition: "width 0.5s ease" }} />
      </div>
      <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 3 }}>
        {has ? `${Math.round(pct)}% toward TP` : "live LTP unavailable — levels only"}
      </div>
    </div>
  );
}

/* Premium rising from entry toward SL — fills red as danger approaches. */
function RiskBar({ from, to, cur }) {
  const has = cur != null && from != null && to != null && from !== to;
  const pct = has ? clampPct(((cur - from) / (to - from)) * 100) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 3 }}>
        <span style={{ color: colors.text.muted }}>Sold premium vs stop · entry {fmt(from)}</span>
        <span style={{ color: colors.loss, fontWeight: 700 }}>SL {fmt(to)}</span>
      </div>
      <div style={{ height: 5, background: colors.bg.tertiary, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: colors.loss, borderRadius: 3, transition: "width 0.5s ease" }} />
      </div>
      <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 3 }}>
        {has ? `${Math.round(pct)}% of the way to the stop (GTT-guarded when LIVE)` : "live LTP unavailable — levels only"}
      </div>
    </div>
  );
}

function LiveStat({ label, children, big }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: colors.text.muted, textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</div>
      <div style={{ fontSize: big ? 20 : 14, fontWeight: 700, ...typography.mono }}>{children}</div>
    </div>
  );
}

function OpenGroupCard({ group, ltpMap }) {
  const sell = group.sell || {};
  const hedge = group.hedge || {};
  const sellLtp = ltpMap?.[normalizeSymbol(sell.symbol)] ?? null;
  const hedgeLtp = ltpMap?.[normalizeSymbol(hedge.symbol)] ?? null;
  const sellPnl = sellLtp != null ? (sell.entry - sellLtp) * (sell.qty || 0) : null;
  const hedgePnl = hedgeLtp != null ? (hedgeLtp - hedge.entry) * (hedge.qty || 0) : null;
  const spreadPnl = sellPnl != null ? sellPnl + (hedgePnl || 0) : null;
  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderLeft: `3px solid ${ACCENT}`, borderRadius: 8, padding: spacing.lg };
  return (
    <div style={card}>
      <div style={{ ...typography.label, color: colors.text.muted, fontSize: 11, marginBottom: 10 }}>
        OPEN SPREAD — LIVE PROGRESS
        <span style={{ marginLeft: 8, color: colors.text.tertiary, textTransform: "none", letterSpacing: 0 }}>
          trend {group.trend_side} · {group.trade_mode} · expiry {group.expiry || "—"}
          {group.mode === "LIVE" && (sell.gtt_id
            ? ` · SL GTT #${sell.gtt_id}`
            : " · ⚠ NO SL GTT — app-monitored SL only")}
        </span>
      </div>
      <div style={{ display: "flex", gap: spacing.xl, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ ...typography.mono, fontSize: 14 }}>{sell.symbol}</b>
        <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 4, background: colors.lossBg, color: colors.loss }}>
          SHORT · qty {sell.qty}
        </span>
        <LiveStat label="Entry">{fmt(sell.entry)}</LiveStat>
        <LiveStat label="Live LTP" big>{fmt(sellLtp)}</LiveStat>
        <LiveStat label="Spread P&L" big>
          <span style={pnlStyle(spreadPnl ?? 0)}>{spreadPnl != null ? fmtInr(spreadPnl) : "—"}</span>
        </LiveStat>
      </div>
      {sell.tp != null && <TargetBar from={sell.entry} to={sell.tp} cur={sellLtp} />}
      {sell.sl != null && <RiskBar from={sell.entry} to={sell.sl} cur={sellLtp} />}
      <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${colors.border.dark}`, display: "flex", gap: spacing.xl, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ ...typography.mono, fontSize: 13 }}>{hedge.symbol}</b>
        <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 4, background: colors.successBg, color: colors.success }}>
          HEDGE LONG · qty {hedge.qty}{hedge.fallback ? " · cheapest-real FB" : ""}
        </span>
        <LiveStat label="Entry">{fmt(hedge.entry)}</LiveStat>
        <LiveStat label="Live LTP">{fmt(hedgeLtp)}</LiveStat>
        <LiveStat label="Hedge P&L">
          <span style={pnlStyle(hedgePnl ?? 0)}>{hedgePnl != null ? fmtInr(hedgePnl) : "—"}</span>
        </LiveStat>
      </div>
    </div>
  );
}

export default function TMAPanel({ ltpMap = {} }) {
  const [mode, setMode] = useState(null);
  const [trades, setTrades] = useState([]);
  const [status, setStatus] = useState({});
  const [err, setErr] = useState(null);

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const [tr, st] = await Promise.all([
          fetch(`${getApiBase()}/api/tma/trades?limit=100`).then((r) => r.json()),
          fetch(`${getApiBase()}/api/tma/status`).then((r) => r.json()),
        ]);
        if (!stop) { setTrades(tr.trades || []); setStatus(st || {}); setErr(tr.error || null); }
      } catch (e) { if (!stop) setErr(String(e.message || e)); }
    }
    async function cfg() {
      try {
        const r = await fetch(`${getApiBase()}/api/strategy-config/TMA_V1`);
        const d = await r.json();
        if (!stop) setMode((d.trade_execution_mode || "PAPER").toUpperCase());
      } catch { /* ignore */ }
    }
    // mode badge tracks Settings live (dynamic mode) — polls with the trades
    cfg(); tick();
    const t = setInterval(() => { tick(); cfg(); }, 5000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const dayStartIst = (() => {
    const now = new Date();
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    ist.setHours(0, 0, 0, 0);
    return Math.floor(ist.getTime() / 1000);
  })();
  // OPEN ignores entry day (positional carries must show); "today" is by
  // EXIT time — that's what "Net today" honestly means.
  const open = trades.filter((t) => t.status === "OPEN");
  const closedToday = trades.filter((t) => t.status === "CLOSED" && (t.exit_ts || 0) >= dayStartIst - 6 * 3600);
  const netToday = closedToday.reduce((a, t) => a + (t.net_pnl || 0), 0);
  const listed = [...open, ...closedToday];
  const sigEng = status.signal_engine || {};

  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8, padding: spacing.lg };
  const label = { ...typography.label, color: colors.text.muted, fontSize: 11 };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.lg }}>
      <div style={{ ...card, borderLeft: `3px solid ${ACCENT}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: colors.text.primary }}>
            TMA V1
            <span style={{ marginLeft: 10, fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
              background: mode === "LIVE" ? colors.lossBg : colors.successBg,
              color: mode === "LIVE" ? colors.loss : colors.success }}>
              {mode || "…"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3 }}>
            EMA 5/13/89 @5m spot · credit spread — SELLS opposite the trend + far-OTM hedge · SL/TP/XOVER on the sold premium
          </div>
        </div>
        <div style={{ display: "flex", gap: spacing.xl }}>
          <div><div style={label}>Open legs</div><div style={{ fontSize: 20, fontWeight: 700 }}>{open.length}</div></div>
          <div><div style={label}>Closed today</div><div style={{ fontSize: 20, fontWeight: 700 }}>{closedToday.length}</div></div>
          <div><div style={label}>Net today</div><div style={{ fontSize: 20, fontWeight: 700, ...typography.mono, ...pnlStyle(netToday) }}>{fmtInr(netToday)}</div></div>
        </div>
      </div>

      {err && <div style={{ ...card, color: colors.loss, fontSize: 12 }}>API error: {err}</div>}
      {status.disabled && (
        <div style={{ ...card, color: colors.loss, fontSize: 12, fontWeight: 700 }}>
          ⚠ TMA manager DISABLED (boot reconciliation mismatch) — resolve manually and restart the app.
        </div>
      )}
      {sigEng.frozen && (
        <div style={{ ...card, color: colors.loss, fontSize: 12, fontWeight: 700 }}>
          ⚠ Signal engine FROZEN ({sigEng.freeze_reason || "prefix instability"}) — no new TMA entries today.
        </div>
      )}

      {status.group && <OpenGroupCard group={status.group} ltpMap={ltpMap} />}
      {status.pending && (
        <div style={{ ...card, fontSize: 12, color: colors.text.tertiary }}>
          Entry pending fill at {fmtTs(status.pending.fill_ts + 60)}: SELL {status.pending.sell} + BUY {status.pending.hedge}
        </div>
      )}

      <div style={{ ...card, padding: 0 }}>
        <div style={{ ...label, padding: "12px 16px 6px" }}>Open + closed-today legs</div>
        {listed.length === 0 ? (
          <div style={{ padding: "24px 16px", fontSize: 12, color: colors.text.muted }}>
            No TMA legs yet — signals fire at 5m EMA-cross boundaries inside the entry window
            {sigEng.candles != null ? ` · engine fed ${sigEng.candles} candles, ${sigEng.signals_emitted || 0} signals` : ""}.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr>{["Symbol", "Leg", "Entry", "Entry ₹", "Exit", "Exit ₹", "Reason", "Net"].map((h) => (
                  <th key={h} style={{ padding: "6px 10px", textAlign: "left", ...typography.label, fontSize: 10, color: colors.text.muted, borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>))}
                </tr>
              </thead>
              <tbody>
                {listed.map((t) => (
                  <tr key={t.id} style={{ borderTop: `1px solid ${colors.border.dark}` }}>
                    <td style={{ padding: "6px 10px", ...typography.mono, whiteSpace: "nowrap" }}>{t.tradingsymbol}</td>
                    <td style={{ padding: "6px 10px" }}>
                      <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 4,
                        background: t.direction === "SELL" ? colors.lossBg : colors.successBg,
                        color: t.direction === "SELL" ? colors.loss : colors.success }}>
                        {t.direction === "SELL" ? "SHORT" : "HEDGE"}
                      </span>
                    </td>
                    <td style={{ padding: "6px 10px", ...typography.mono, color: colors.text.tertiary }}>{fmtTs(t.entry_ts)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, textAlign: "right" }}>{t.entry_price?.toFixed(2)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, color: colors.text.tertiary }}>{fmtTs(t.exit_ts)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, textAlign: "right" }}>{t.exit_price != null ? t.exit_price.toFixed(2) : "—"}</td>
                    <td style={{ padding: "6px 10px" }}>
                      {t.exit_reason ? (
                        <span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                          background: t.exit_reason === "TP" ? colors.successBg : ["EOD", "XOVER", "MTM_CUT"].includes(t.exit_reason) ? colors.warningBg : colors.lossBg,
                          color: t.exit_reason === "TP" ? colors.success : ["EOD", "XOVER", "MTM_CUT"].includes(t.exit_reason) ? colors.warning : colors.loss }}>
                          {t.exit_reason}
                        </span>
                      ) : <span style={{ fontSize: 10, color: colors.text.muted }}>OPEN</span>}
                    </td>
                    <td style={{ padding: "6px 10px", ...typography.mono, textAlign: "right", fontWeight: 700, ...pnlStyle(t.net_pnl || 0) }}>
                      {t.net_pnl != null ? fmtInr(t.net_pnl) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

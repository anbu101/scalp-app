// frontend/src/strategies/pst/PSTPanel.jsx
//
// ── PST SELL / PST HEDGE dashboard panel ── (shared; strategyId prop)
// Self-contained: polls /api/pst/trades + the strategy config. Shows mode
// badge (PAPER/LIVE), open position, today's P&L and the day's legs.
// Accents: PST_SELL #fb7185 · PST_HEDGE #be123c.

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, typography, pnlStyle } from "../../tokens";

const ACCENT = { PST_SELL: "#fb7185", PST_HEDGE: "#be123c" };
const NAME = { PST_SELL: "PST Sell", PST_HEDGE: "PST Hedge" };
const SUB = {
  PST_SELL: "pivot+ST spot signals · option SELLING · TP on premium (resting limit) · SL on spot",
  PST_HEDGE: "pivot+ST spot signals · BUYS opposite side · exits tracked on the SIGNAL contract + spot",
};

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

export default function PSTPanel({ strategyId = "PST_SELL" }) {
  const accent = ACCENT[strategyId] || colors.primary;
  const [mode, setMode] = useState(null);
  const [trades, setTrades] = useState([]);
  const [summary, setSummary] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const r = await fetch(`${getApiBase()}/api/pst/trades?strategy_id=${strategyId}&limit=100`);
        const d = await r.json();
        if (!stop) { setTrades(d.trades || []); setSummary(d.summary || null); setErr(d.error || null); }
      } catch (e) { if (!stop) setErr(String(e.message || e)); }
    }
    async function cfg() {
      try {
        const r = await fetch(`${getApiBase()}/api/strategy-config/${strategyId}`);
        const d = await r.json();
        if (!stop) setMode((d.trade_execution_mode || "PAPER").toUpperCase());
      } catch { /* ignore */ }
    }
    cfg(); tick();
    const t = setInterval(tick, 5000);
    return () => { stop = true; clearInterval(t); };
  }, [strategyId]);

  const dayStartIst = (() => {
    const now = new Date();
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    ist.setHours(0, 0, 0, 0);
    return Math.floor(ist.getTime() / 1000) - 0; // epoch of IST midnight (approx, display only)
  })();
  const today = trades.filter((t) => (t.entry_ts || 0) >= dayStartIst - 6 * 3600);
  const open = today.filter((t) => t.status === "OPEN");
  const closedToday = today.filter((t) => t.status === "CLOSED");
  const netToday = closedToday.reduce((a, t) => a + (t.net_pnl || 0), 0);

  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderRadius: 8, padding: spacing.lg };
  const label = { ...typography.label, color: colors.text.muted, fontSize: 11 };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.lg }}>
      <div style={{ ...card, borderLeft: `3px solid ${accent}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: colors.text.primary }}>
            {NAME[strategyId]}
            <span style={{ marginLeft: 10, fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
              background: mode === "LIVE" ? colors.lossBg : colors.successBg,
              color: mode === "LIVE" ? colors.loss : colors.success }}>
              {mode || "…"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3 }}>{SUB[strategyId]}</div>
        </div>
        <div style={{ display: "flex", gap: spacing.xl }}>
          <div><div style={label}>Open legs</div><div style={{ fontSize: 20, fontWeight: 700 }}>{open.length}</div></div>
          <div><div style={label}>Closed today</div><div style={{ fontSize: 20, fontWeight: 700 }}>{closedToday.length}</div></div>
          <div><div style={label}>Net today</div><div style={{ fontSize: 20, fontWeight: 700, ...typography.mono, ...pnlStyle(netToday) }}>{fmtInr(netToday)}</div></div>
        </div>
      </div>

      {err && <div style={{ ...card, color: colors.loss, fontSize: 12 }}>API error: {err}</div>}

      {open.length > 0 && (
        <div style={{ ...card }}>
          <div style={{ ...label, marginBottom: 8 }}>Open position</div>
          {open.map((t) => (
            <div key={t.id} style={{ display: "flex", gap: spacing.lg, alignItems: "baseline", flexWrap: "wrap", padding: "4px 0", fontSize: 13 }}>
              <b style={{ ...typography.mono }}>{t.tradingsymbol}</b>
              <span style={{ fontSize: 11, color: colors.text.muted }}>{t.leg_id} · {t.direction} · qty {t.qty}</span>
              <span style={{ ...typography.mono }}>entry {t.entry_price?.toFixed(2)}</span>
              {t.tp != null && <span style={{ ...typography.mono, color: colors.profit }}>TP {t.tp.toFixed(2)}{strategyId === "PST_HEDGE" ? " (signal)" : ""}</span>}
              {t.spot_sl != null && <span style={{ ...typography.mono, color: colors.loss }}>Spot SL {t.spot_sl.toFixed(0)}</span>}
              {strategyId === "PST_HEDGE" && t.sig_symbol && <span style={{ fontSize: 11, color: colors.text.tertiary }}>tracking {t.sig_symbol}</span>}
            </div>
          ))}
        </div>
      )}

      <div style={{ ...card, padding: 0 }}>
        <div style={{ ...label, padding: "12px 16px 6px" }}>Today's legs</div>
        {today.length === 0 ? (
          <div style={{ padding: "24px 16px", fontSize: 12, color: colors.text.muted }}>
            No PST legs yet today — entries appear at 1m candle boundaries after a signal.
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
                {today.map((t) => (
                  <tr key={t.id} style={{ borderTop: `1px solid ${colors.border.dark}` }}>
                    <td style={{ padding: "6px 10px", ...typography.mono, whiteSpace: "nowrap" }}>{t.tradingsymbol}</td>
                    <td style={{ padding: "6px 10px" }}>{t.leg_id}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, color: colors.text.tertiary }}>{fmtTs(t.entry_ts)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, textAlign: "right" }}>{t.entry_price?.toFixed(2)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, color: colors.text.tertiary }}>{fmtTs(t.exit_ts)}</td>
                    <td style={{ padding: "6px 10px", ...typography.mono, textAlign: "right" }}>{t.exit_price != null ? t.exit_price.toFixed(2) : "—"}</td>
                    <td style={{ padding: "6px 10px" }}>
                      {t.exit_reason ? (
                        <span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                          background: ["TP", "SIG_TP"].includes(t.exit_reason) ? colors.successBg : t.exit_reason === "EOD" ? colors.warningBg : colors.lossBg,
                          color: ["TP", "SIG_TP"].includes(t.exit_reason) ? colors.success : t.exit_reason === "EOD" ? colors.warning : colors.loss }}>
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
// frontend/src/strategies/pst/PSTPanel.jsx
//
// ── PST SELL / PST HEDGE dashboard panel v2 ── (design overhaul 2026-07-17)
// Live trade PROGRESS, V3-panel conventions: distance bars toward TP (green)
// and toward the SPOT SL (red), live LTPs from the shared ltpMap (PST's
// contracts sit inside the shared weekly subscription band; a missing LTP
// degrades that bar to levels-only, never breaks). Closed-legs list kept.
// PST_SELL: SHORT — premium falling toward TP is good; SL lives on SPOT.
// PST_HEDGE: LONG the held side; the TP is tracked on the SIGNAL contract.

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, typography, pnlStyle } from "../../tokens";
import { useEntitlements } from "../../hooks/useEntitlements";   // ── UI_MASK ──
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = { PST_SELL: "#fb7185", PST_HEDGE: "#be123c" };
const NAME = { PST_SELL: "PST Sell", PST_HEDGE: "PST Hedge" };
const SUB = {
  PST_SELL: "pivot+ST spot signals · option SELLING · TP on premium (resting limit) · SL on spot",
  PST_HEDGE: "pivot+ST spot signals · BUYS opposite side · exits tracked on the SIGNAL contract + spot",
};
const SPOT_KEYS = ["NIFTY50", "NIFTY", "NSE:NIFTY50"];

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

/* Progress toward a GOOD exit level (TP). from → to, cur in between. */
function TargetBar({ label, from, to, cur, caption }) {
  const has = cur != null && from != null && to != null && from !== to;
  const pct = has ? clampPct(((from - cur) / (from - to)) * 100) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 3 }}>
        <span style={{ color: colors.text.muted }}>{label} · from {fmt(from)}</span>
        <span style={{ color: colors.profit, fontWeight: 700 }}>TP {fmt(to)}</span>
      </div>
      <div style={{ height: 5, background: colors.bg.tertiary, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: colors.profit, borderRadius: 3, transition: "width 0.5s ease" }} />
      </div>
      <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 3 }}>
        {has ? `${Math.round(pct)}% toward TP-exit` : "live LTP unavailable — levels only"}{caption ? ` · ${caption}` : ""}
      </div>
    </div>
  );
}

/* Progress toward the BAD level (SPOT SL) — fills red as danger approaches. */
function RiskBar({ label, from, to, cur }) {
  const has = cur != null && from != null && to != null && from !== to;
  const pct = has ? clampPct(((cur - from) / (to - from)) * 100) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 3 }}>
        <span style={{ color: colors.text.muted }}>{label} · entry {fmt(from, 0)}</span>
        <span style={{ color: colors.loss, fontWeight: 700 }}>SL {fmt(to, 0)}</span>
      </div>
      <div style={{ height: 5, background: colors.bg.tertiary, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: colors.loss, borderRadius: 3, transition: "width 0.5s ease" }} />
      </div>
      <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 3 }}>
        {has ? `${Math.round(pct)}% of the way to the spot stop${cur != null ? ` · spot ${fmt(cur, 0)}` : ""}` : "spot LTP unavailable — levels only"}
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

function OpenCard({ strategyId, legs, ltpMap, accent, showParams = true }) {   // ── UI_MASK ──
  const spotLtp = SPOT_KEYS.map((k) => ltpMap?.[k]).find((v) => v != null) ?? null;
  const card = { background: colors.bg.secondary, border: `1px solid ${colors.border.light}`, borderLeft: `3px solid ${accent}`, borderRadius: 8, padding: spacing.lg };
  return (
    <div style={card}>
      <div style={{ ...typography.label, color: colors.text.muted, fontSize: 11, marginBottom: 10 }}>OPEN POSITION — LIVE PROGRESS</div>
      {legs.map((t) => {
        const heldLtp = ltpMap?.[normalizeSymbol(t.tradingsymbol)] ?? null;
        const isSell = strategyId === "PST_SELL";
        const pnl = heldLtp != null
          ? (isSell ? (t.entry_price - heldLtp) : (heldLtp - t.entry_price)) * (t.qty || 0)
          : null;
        const sigLtp = !isSell && t.sig_symbol ? (ltpMap?.[normalizeSymbol(t.sig_symbol)] ?? null) : null;
        return (
          <div key={t.id} style={{ padding: "6px 0", borderTop: `1px solid ${colors.border.dark}` }}>
            <div style={{ display: "flex", gap: spacing.xl, alignItems: "baseline", flexWrap: "wrap" }}>
              <b style={{ ...typography.mono, fontSize: 14 }}>{t.tradingsymbol}</b>
              <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 4,
                background: isSell ? colors.lossBg : colors.successBg,
                color: isSell ? colors.loss : colors.success }}>
                {isSell ? "SHORT" : "LONG"} · {t.leg_id} · qty {t.qty}
              </span>
              <LiveStat label="Entry">{fmt(t.entry_price)}</LiveStat>
              <LiveStat label="Live LTP" big>{fmt(heldLtp)}</LiveStat>
              <LiveStat label="Live P&L" big>
                <span style={pnlStyle(pnl ?? 0)}>{pnl != null ? fmtInr(pnl) : "—"}</span>
              </LiveStat>
            </div>
            {/* ── UI_MASK BEGIN ── TP levels, the tracked SIGNAL contract and
                the spot-SL bar together narrate the whole mechanism — admin only */}
            {showParams && (isSell ? (
              <TargetBar label="Own premium (short — falling is good)"
                from={t.entry_price} to={t.tp} cur={heldLtp} />
            ) : (
              <TargetBar label={`SIGNAL ${t.sig_symbol || ""} (tracked — drives the TP)`}
                from={t.sig_entry} to={t.tp} cur={sigLtp}
                caption={sigLtp != null ? `sig LTP ${fmt(sigLtp)}` : null} />
            ))}
            {showParams && t.spot_sl != null && (
              <RiskBar label="NIFTY spot" from={t.spot_entry} to={t.spot_sl} cur={spotLtp} />
            )}
            {/* ── UI_MASK END ── */}
          </div>
        );
      })}
    </div>
  );
}

export default function PSTPanel({ strategyId = "PST_SELL", ltpMap = {} }) {
  // ── UI_MASK ── fail-OPEN until first license read (Phase 3 convention)
  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();
  const showParams = !licenseLoaded || isAdminUi;
  const accent = ACCENT[strategyId] || colors.primary;
  const [mode, setMode] = useState(null);
  const [trades, setTrades] = useState([]);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const r = await fetch(`${getApiBase()}/api/pst/trades?strategy_id=${strategyId}&limit=100`);
        const d = await r.json();
        if (!stop) { setTrades(d.trades || []); setErr(d.error || null); }
      } catch (e) { if (!stop) setErr(String(e.message || e)); }
    }
    async function cfg() {
      try {
        const r = await fetch(`${getApiBase()}/api/strategy-config/${strategyId}`);
        const d = await r.json();
        if (!stop) setMode((d.trade_execution_mode || "PAPER").toUpperCase());
      } catch { /* ignore */ }
    }
    // mode badge tracks Settings live (dynamic mode) — polls with the trades
    cfg(); tick();
    const t = setInterval(() => { tick(); cfg(); }, 5000);
    return () => { stop = true; clearInterval(t); };
  }, [strategyId]);

  const dayStartIst = (() => {
    const now = new Date();
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    ist.setHours(0, 0, 0, 0);
    return Math.floor(ist.getTime() / 1000);
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
            {showParams ? NAME[strategyId] : stratName(strategyId, false)}   {/* ── UI_MASK ── */}
            <span style={{ marginLeft: 10, fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
              background: mode === "LIVE" ? colors.lossBg : colors.successBg,
              color: mode === "LIVE" ? colors.loss : colors.success }}>
              {mode || "…"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3 }}>{showParams ? SUB[strategyId] : "NIFTY options"}</div>   {/* ── UI_MASK ── */}
        </div>
        <div style={{ display: "flex", gap: spacing.xl }}>
          <div><div style={label}>Open legs</div><div style={{ fontSize: 20, fontWeight: 700 }}>{open.length}</div></div>
          <div><div style={label}>Closed today</div><div style={{ fontSize: 20, fontWeight: 700 }}>{closedToday.length}</div></div>
          <div><div style={label}>Net today</div><div style={{ fontSize: 20, fontWeight: 700, ...typography.mono, ...pnlStyle(netToday) }}>{fmtInr(netToday)}</div></div>
        </div>
      </div>

      {err && <div style={{ ...card, color: colors.loss, fontSize: 12 }}>API error: {err}</div>}

      {open.length > 0 && (
        <OpenCard strategyId={strategyId} legs={open} ltpMap={ltpMap} accent={accent} showParams={showParams} />
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
                          {showParams ? t.exit_reason : "CLOSED"}   {/* ── UI_MASK ── */}
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
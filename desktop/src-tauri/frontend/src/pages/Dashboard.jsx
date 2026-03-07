/**
 * DASHBOARD
 *
 * Intended path: src/pages/Dashboard.jsx
 *
 * Owns only global, cross-strategy concerns:
 *   - Zerodha connection status      (3s fast poll)
 *   - Backend / engine health        (3s fast poll)
 *   - trade_on global flag           (15s slow poll via getGlobalConfig)
 *   - Today's Zerodha positions/P&L  (15s slow poll)
 *   - ltpMap                         (500ms live poll — passed into StrategyHost)
 *   - NIFTY / BANKNIFTY indices      (500ms live poll)
 *
 * Does NOT own:
 *   - selection, tradeState, tradeSideMode, strategyConfig — all in ScalpPanel
 *   - inTrade, executionMode badges — all in ScalpPanel
 *   - CE/PE switcher — in ScalpPanel
 *   - Audio alerts, toast on trade events — in ScalpPanel
 *   - DebugPanel — in ScalpPanel
 *
 * Adding a new strategy:
 *   - Nothing changes here.
 *   - Add the strategy id to ACTIVE_STRATEGY_IDS in StrategyHost.
 *   - Create its panel component under src/strategies/<id>/.
 */

import { useEffect, useState, useRef } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import {
  getZerodhaStatus,
  getStatus,
  getTodayPositions,
  getGlobalConfig,
} from "../api";
import {
  LoadingAnimations,
  FullPageLoader,
  EmptyState,
  CardSkeleton,
} from "../components/LoadingStates";
import StrategyHost from "../components/StrategyHost";
import { getApiBase } from "../api/base";
import { colors, spacing, typography, pnlStyle as _pnlStyle } from "../tokens";

/* ----------------------------------
   Shared helpers
----------------------------------- */
const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

const pnlStyle = _pnlStyle;

/* ----------------------------------
   UI sub-components
----------------------------------- */
function Card({ children, style, elevated }) {
  return (
    <div
      style={{
        background: elevated ? colors.bg.tertiary : colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: elevated
          ? "0 4px 6px -1px rgba(0,0,0,0.3), 0 2px 4px -1px rgba(0,0,0,0.2)"
          : "0 1px 3px rgba(0,0,0,0.2)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function StatusBadge({ ok, text, warn, danger, icon }) {
  let bg          = colors.dangerBg;
  let color       = colors.danger;
  let borderColor = colors.danger;

  if (ok) {
    bg = colors.successBg; color = colors.success; borderColor = colors.success;
  } else if (warn) {
    bg = colors.warningBg; color = colors.warning; borderColor = colors.warning;
  } else if (danger) {
    bg = colors.dangerBg;  color = colors.danger;  borderColor = colors.danger;
  }

  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 6,
        ...typography.bodySmall,
        fontWeight: 600,
        background: bg,
        color,
        border: `1px solid ${borderColor}40`,
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        minWidth: "90px",
        justifyContent: "center",
        textTransform: "uppercase",
        letterSpacing: "0.3px",
      }}
    >
      {icon && <span style={{ fontSize: 10 }}>{icon}</span>}
      {text}
    </span>
  );
}

function MarketBadge({ name, data }) {
  const [pulse, setPulse] = useState(false);
  const prevLtpRef = useRef(null);

  const ltp       = typeof data?.ltp        === "number" ? data.ltp        : null;
  const prevClose = typeof data?.prev_close === "number" ? data.prev_close : ltp;

  useEffect(() => {
    if (ltp === null) return;
    if (prevLtpRef.current !== null && prevLtpRef.current !== ltp) {
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 180);
      return () => clearTimeout(t);
    }
    prevLtpRef.current = ltp;
  }, [ltp]);

  if (ltp === null || prevClose === null) return null;

  const change = ltp - prevClose;
  const pct    = prevClose !== 0 ? (change / prevClose) * 100 : 0;
  const up     = change >= 0;
  const bg     = up ? colors.successBg : colors.dangerBg;
  const color  = up ? colors.success   : colors.danger;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 12px",
        minHeight: 28,
        borderRadius: 6,
        background: bg,
        color,
        border: `1px solid ${color}40`,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.3px",
        textTransform: "uppercase",
        filter:    pulse ? "brightness(1.25)" : "brightness(1)",
        boxShadow: pulse ? `0 0 8px ${color}55` : "none",
        transition: "filter 0.18s ease, box-shadow 0.18s ease",
      }}
    >
      <span style={{ opacity: 0.9 }}>{name}</span>
      <span style={{ ...typography.mono, fontSize: 12 }}>{ltp.toFixed(2)}</span>
      <span style={{ ...typography.mono, fontSize: 11 }}>{up ? "▲" : "▼"} {pct.toFixed(2)}%</span>
    </span>
  );
}

function PnLRow({ label, value, large }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{
        ...(large ? typography.bodyLarge : typography.bodyMedium),
        color: colors.text.secondary,
        fontWeight: large ? 600 : 400,
      }}>
        {label}
      </span>
      <span style={{
        ...(large ? typography.headingMedium : typography.bodyMedium),
        ...typography.mono,
        ...pnlStyle(value),
      }}>
        ₹{Math.round(value).toLocaleString("en-IN")}
      </span>
    </div>
  );
}

/* Coloured left-border row used in Open/Closed position lists */
function PositionRow({ symbol, qty, pnl }) {
  const v      = safeNum(pnl);
  const accent = v > 0 ? colors.profit : v < 0 ? colors.loss : colors.border.light;
  return (
    <div style={{
      ...typography.bodySmall,
      ...typography.mono,
      marginBottom: spacing.xs,
      padding: `${spacing.xs}px ${spacing.sm}px`,
      background:   colors.bg.secondary,
      borderRadius: 4,
      borderLeft:   `3px solid ${accent}`,
      display:      "flex",
      justifyContent: "space-between",
      alignItems:   "center",
    }}>
      <span style={{ color: colors.text.secondary }}>
        {symbol} × {qty}
      </span>
      <span style={pnlStyle(v)}>
        {v > 0 ? "+" : ""}₹{Math.round(v).toLocaleString("en-IN")}
      </span>
    </div>
  );
}

/* ── Contextual page header — replaces the static "Scalp Terminal" H1 ── */
const DAY_NAMES = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const MON_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function getSessionLabel() {
  const now  = new Date();
  const dow  = now.getDay();
  if (dow === 0 || dow === 6) return { label: "Weekend",    color: colors.text.muted,  bg: "rgba(100,116,139,0.12)" };
  const mins = now.getHours() * 60 + now.getMinutes();
  if (mins < 9 * 60 + 15)    return { label: "Pre-Market", color: colors.warning,      bg: colors.warningBg };
  if (mins < 15 * 60 + 30)   return { label: "Market Open",color: colors.success,      bg: colors.successBg };
  return                              { label: "Market Closed", color: colors.text.muted, bg: "rgba(100,116,139,0.12)" };
}

function DashboardHeader() {
  const [session, setSession] = useState(getSessionLabel);

  // Refresh session label every 30s so it transitions live
  useEffect(() => {
    const t = setInterval(() => setSession(getSessionLabel()), 30_000);
    return () => clearInterval(t);
  }, []);

  const now = new Date();
  const dateStr = `${DAY_NAMES[now.getDay()]}, ${now.getDate()} ${MON_NAMES[now.getMonth()]} ${now.getFullYear()}`;

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.lg, flexWrap: "wrap", gap: spacing.sm }}>
      <div>
        <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 3 }}>
          Dashboard
        </div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: colors.text.primary, lineHeight: 1.2 }}>
          {dateStr}
        </h1>
      </div>
      <span style={{
        fontSize: 12, fontWeight: 600, padding: "5px 14px",
        borderRadius: 20,
        background: session.bg,
        color:      session.color,
        border:     `1px solid ${session.color}40`,
        letterSpacing: "0.3px",
      }}>
        {session.label}
      </span>
    </div>
  );
}

/* ----------------------------------
   Dashboard
----------------------------------- */
export default function Dashboard() {
  const isMobile = useIsMobile();
  // ---- Global state ----
  const [zerodha,       setZerodha]       = useState(null);
  const [status,        setStatus]        = useState(null);
  const [backendHealth, setBackendHealth] = useState("BOOTING");
  const [loading,       setLoading]       = useState(true);
  const [globalConfig,  setGlobalConfig]  = useState(null);

  const [positions, setPositions] = useState({
    open: [],
    closed: [],
    totals: { realised: 0, unrealised: 0, total: 0 },
  });
  const [positionsLoading, setPositionsLoading] = useState(true);

  const [ltpMap,  setLtpMap]  = useState({});
  const [indices, setIndices] = useState({});

  // ---- Fast poll: status + zerodha (3s) ----
  useEffect(() => {
    async function loadFast() {
      try {
        const s = await getStatus();
        setStatus(s);
        if (s?.backend === "UP") {
          setBackendHealth("UP");
        } else {
          setBackendHealth("DOWN");
        }
      } catch {
        setBackendHealth((prev) => (prev === "UP" ? "DOWN" : prev));
      }

      try { setZerodha(await getZerodhaStatus()); } catch {}
    }

    loadFast();
    setLoading(false); // first fast load is enough to unblock the skeleton

    const fast = setInterval(loadFast, 3000);
    return () => clearInterval(fast);
  }, []);

  // ---- Slow poll: global config + today's positions (15s) ----
  useEffect(() => {
    async function loadSlow() {
      try { setGlobalConfig(await getGlobalConfig()); } catch {}

      try {
        setPositionsLoading(true);
        const p      = await getTodayPositions();
        const open   = p?.open   || [];
        const closed = p?.closed || [];

        const realised   = closed.reduce((s, x) => s + safeNum(x.pnl), 0);
        const unrealised = open.reduce((s, x) => s + safeNum(x.pnl), 0);

        setPositions({
          open,
          closed,
          totals: { realised, unrealised, total: realised + unrealised },
        });
      } catch {
        setPositions({
          open: [],
          closed: [],
          totals: { realised: 0, unrealised: 0, total: 0 },
        });
      } finally {
        setPositionsLoading(false);
      }
    }

    loadSlow();
    const slow = setInterval(loadSlow, 15000);
    return () => clearInterval(slow);
  }, []);

  // ---- LTP poll: 500ms ----
  useEffect(() => {
    let alive = true;

    async function pollLtp() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/ltp_snapshot`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") {
              const normalized = {};
              Object.entries(data).forEach(([symbol, price]) => {
                normalized[symbol.replace(/\s+/g, "").toUpperCase()] = price;
              });
              setLtpMap(normalized);
            }
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 500));
      }
    }

    pollLtp();
    return () => { alive = false; };
  }, []);

  // ---- Indices poll: 500ms ----
  useEffect(() => {
    let alive = true;

    async function pollIndices() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/market_indices`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") setIndices(data);
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 500));
      }
    }

    pollIndices();
    return () => { alive = false; };
  }, []);

  // ---- Derived ----
  const tradingEnabled = globalConfig?.trade_on === true;

  // ---- Loading gate ----
  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading dashboard..." />
      </>
    );
  }

  // ---- Render ----
  return (
    <div
      style={{
        padding: isMobile ? spacing.md : spacing.xxl,
        background: colors.bg.primary,
        color: colors.text.primary,
        minHeight: "100vh",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >

      {/* ---------- GLOBAL STATUS BAR ---------- */}
      <div style={{ marginBottom: spacing.xxl }}>
        <DashboardHeader />

        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>

            {/* Global system badges */}
            <StatusBadge 
              ok={zerodha?.connected === true} 
              warn={zerodha === null}
              danger={zerodha?.connected === false}
              text={
                zerodha === null 
                  ? "Checking..." 
                  : zerodha?.connected 
                    ? "Connected" 
                    : "Disconnected"
              } 
              icon={
                zerodha === null 
                  ? "◐" 
                  : zerodha?.connected 
                    ? "●" 
                    : "○"
              }
            />
            <StatusBadge
              ok={backendHealth === "UP" && status?.engine === "RUNNING"}
              warn={backendHealth === "BOOTING"}
              danger={backendHealth === "DOWN"}
              text={
                backendHealth === "BOOTING" ? "Backend Starting"
                : backendHealth === "DOWN"  ? "Backend Down"
                : status?.engine === "RUNNING" ? "Engine Running"
                : "Engine Paused"
              }
              icon="⚡"
            />
            <StatusBadge
              ok={tradingEnabled}
              warn={!tradingEnabled}
              text={tradingEnabled ? "Trading" : "Paused"}
              icon={tradingEnabled ? "▶" : "⏸"}
            />

            {/* Market index live tickers */}
            <MarketBadge name="NIFTY"     data={indices.NIFTY}     />
            <MarketBadge name="BANKNIFTY" data={indices.BANKNIFTY} />
          </div>
        </Card>
      </div>

      {/* ---------- STRATEGY PANELS ---------- */}
      {/*
        StrategyHost manages which strategies are active and their layout.
        ltpMap is the only global data strategies need — passed down here.
        Everything else (tradeState, selection, etc.) is owned per-panel.
      */}
      <div style={{ marginBottom: spacing.xxl }}>
        <StrategyHost ltpMap={ltpMap} />
      </div>

      {/* ---------- TODAY'S P&L ---------- */}
      <div>
        <h2 style={{ ...typography.headingLarge, color: colors.text.primary, marginBottom: spacing.md }}>
          Today's Performance
        </h2>

        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fit, minmax(300px, 1fr))", gap: spacing.md }}>

          {/* ── Summary — hero card ── */}
          {(() => {
            const total    = positions.totals.total;
            const isProfit = total > 0;
            const isLoss   = total < 0;
            const accent   = isProfit ? colors.profit : isLoss ? colors.loss : colors.border.light;
            return (
              <div style={{
                background:  colors.bg.secondary,
                border:      `1px solid ${colors.border.light}`,
                borderLeft:  `3px solid ${accent}`,
                borderRadius: 8,
                boxShadow:   `0 4px 6px -1px rgba(0,0,0,0.3)`,
                padding:     spacing.lg,
              }}>
                <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
                  Summary
                </div>
                {positionsLoading ? (
                  <CardSkeleton rows={3} />
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
                    <PnLRow label="Realised"   value={positions.totals.realised}   />
                    <PnLRow label="Unrealised" value={positions.totals.unrealised} />

                    {/* Hero total */}
                    <div style={{
                      marginTop: spacing.sm,
                      paddingTop: spacing.md,
                      borderTop: `1px solid ${colors.border.dark}`,
                    }}>
                      <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.xs }}>
                        Total P&L
                      </div>
                      <div style={{
                        ...typography.mono,
                        fontSize: 30,
                        fontWeight: 700,
                        ...pnlStyle(total),
                        lineHeight: 1.1,
                      }}>
                        {total >= 0 ? "+" : ""}₹{Math.round(Math.abs(total)).toLocaleString("en-IN")}
                      </div>
                      {/* Coloured background pill under the hero number */}
                      {total !== 0 && (
                        <div style={{
                          marginTop: spacing.sm,
                          display: "inline-block",
                          padding: "2px 10px",
                          borderRadius: 12,
                          fontSize: 11,
                          fontWeight: 600,
                          background: isProfit ? colors.successBg : colors.dangerBg,
                          color:      isProfit ? colors.profit    : colors.loss,
                          border:     `1px solid ${accent}30`,
                        }}>
                          {isProfit ? "▲ Profit" : "▼ Loss"} today
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Open Positions ── */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Open Positions
              {positions.open.length > 0 && (
                <span style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px", borderRadius: 10,
                  background: colors.warningBg, color: colors.warning,
                }}>
                  {positions.open.length}
                </span>
              )}
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {positions.open.length === 0 ? (
                  <EmptyState icon="🔭" title="No open positions" description="" />
                ) : (
                  positions.open.map((p, i) => (
                    <PositionRow
                      key={i}
                      symbol={p.tradingsymbol}
                      qty={p.quantity}
                      pnl={p.pnl}
                    />
                  ))
                )}
              </div>
            )}
          </Card>

          {/* ── Closed Positions ── */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Closed Positions
              {positions.closed.length > 0 && (
                <span style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px", borderRadius: 10,
                  background: colors.bg.tertiary, color: colors.text.muted,
                }}>
                  {positions.closed.length}
                </span>
              )}
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {positions.closed.length === 0 ? (
                  <EmptyState icon="🔭" title="No closed positions" description="" />
                ) : (
                  positions.closed.map((p, i) => (
                    <PositionRow
                      key={i}
                      symbol={p.tradingsymbol}
                      qty={p.day_buy_quantity}
                      pnl={p.pnl}
                    />
                  ))
                )}
              </div>
            )}
          </Card>

        </div>
      </div>

    </div>
  );
}

/* ----------------------------------
   Styles
----------------------------------- */
const styles = `
  @keyframes loading {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(400%);  }
  }
`;

if (typeof document !== "undefined" && !document.getElementById("dashboard-styles")) {
  const styleSheet     = document.createElement("style");
  styleSheet.id        = "dashboard-styles";
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}
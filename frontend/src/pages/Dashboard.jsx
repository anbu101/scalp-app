/**
 * DASHBOARD
 *
 * Intended path: src/pages/Dashboard.jsx
 *
 * Owns only global, cross-strategy concerns:
 *   - Zerodha connection status      (3s fast poll)
 *   - Backend / engine health        (3s fast poll)
 *   - trade_on global flag           (15s slow poll via getGlobalConfig)
 *   - Today's Zerodha positions/P&L  (15s slow poll for structure)
 *   - ltpMap                         (500ms live poll — passed into StrategyHost)
 *   - NIFTY / BANKNIFTY indices      (500ms live poll)
 *
 * P&L ARCHITECTURE (KEY FIX):
 *   - positions structure (entry price, qty, symbol) is fetched every 15s
 *   - unrealised P&L is COMPUTED LIVE from ltpMap (updates at 500ms)
 *   - realised P&L comes from closed positions returned by the API
 *   - This means "Today's Performance" updates at 500ms cadence, not API cadence
 *
 * Does NOT own:
 *   - selection, tradeState, tradeSideMode, strategyConfig — all in ScalpPanel
 *   - inTrade, executionMode badges — all in ScalpPanel
 *   - CE/PE switcher — in ScalpPanel
 *   - Audio alerts, toast on trade events — in ScalpPanel
 *   - DebugPanel — in ScalpPanel
 */

import { useEffect, useState, useRef, useMemo } from "react";
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
  const prevClose = typeof data?.prev_close === "number" ? data.prev_close : null;

  useEffect(() => {
    if (ltp === null) return;
    if (prevLtpRef.current !== null && prevLtpRef.current !== ltp) {
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 180);
      return () => clearTimeout(t);
    }
    prevLtpRef.current = ltp;
  }, [ltp]);

  if (ltp === null) return null;

  const hasChange = prevClose !== null && prevClose > 0;
  const change    = hasChange ? ltp - prevClose : null;
  const pct       = hasChange && prevClose !== 0 ? (change / prevClose) * 100 : null;
  const up        = change !== null ? change >= 0 : true;
  const bg        = up ? colors.successBg : colors.dangerBg;
  const color     = up ? colors.success   : colors.danger;

  const changePts = change !== null
    ? `${up ? "+" : ""}${change.toFixed(1)}`
    : null;

  const changePct = pct !== null
    ? `${up ? "+" : ""}${pct.toFixed(2)}%`
    : null;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 12px",
        minHeight: 28,
        borderRadius: 6,
        background: hasChange ? bg : colors.bg.tertiary,
        color: hasChange ? color : colors.text.secondary,
        border: `1px solid ${hasChange ? color : colors.border.light}40`,
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
      <span style={{ ...typography.mono, fontSize: 12 }}>
        {ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      {changePts !== null && changePct !== null && (
        <span style={{ ...typography.mono, fontSize: 11 }}>
          {up ? "▲" : "▼"} {changePts} ({changePct})
        </span>
      )}
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

/* ── Contextual page header ── */
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

  // ---- Positions RAW data (structure only — entry price, qty, symbol) ----
  // This is fetched from the API every 15s.
  // Do NOT compute P&L from this directly — use the useMemo below instead.
  const [positionsData, setPositionsData] = useState({
    open: [],        // raw open positions from Zerodha API
    closed: [],      // raw closed positions from Zerodha API
    realisedPnl: 0,  // sum of closed position P&L (from API — doesn't need LTP)
  });
  const [positionsLoading, setPositionsLoading] = useState(true);
  const posFirstLoad = useRef(true);

  const [ltpMap,  setLtpMap]  = useState({});
  const [indices, setIndices] = useState({});

  // ---- Fast poll: status + zerodha (3s) ----
  useEffect(() => {
    async function loadFast() {
      try {
        const s = await getStatus();
        setStatus(s);
        setBackendHealth(s?.backend === "UP" ? "UP" : "DOWN");
      } catch {
        setBackendHealth((prev) => (prev === "UP" ? "DOWN" : prev));
      }
      try { setZerodha(await getZerodhaStatus()); } catch {}
    }

    loadFast();
    setLoading(false);

    const t = setInterval(loadFast, 3000);
    return () => clearInterval(t);
  }, []);

  // ---- Slow poll: global config (15s) ----
  useEffect(() => {
    async function loadConfig() {
      try { setGlobalConfig(await getGlobalConfig()); } catch {}
    }
    loadConfig();
    const t = setInterval(loadConfig, 15000);
    return () => clearInterval(t);
  }, []);

  // ---- Positions structure poll (15s) ----------------------------------------
  //
  // WHY 15s AND NOT 3s:
  //   Zerodha's /positions REST endpoint updates its own pnl field at its own
  //   cadence (roughly every 30-60s). Polling at 3s hammers the API without
  //   getting fresher data for the Zerodha-computed pnl field.
  //
  //   We only need the STRUCTURE here: symbol, average_price, quantity.
  //   Unrealised P&L is re-computed every 500ms in the useMemo below using
  //   ltpMap, which is the authoritative live price source.
  //
  // ---------------------------------------------------------------------------
  useEffect(() => {
    async function loadPositions() {
      // Show skeleton ONLY on the very first fetch — subsequent polls are silent
      if (posFirstLoad.current) setPositionsLoading(true);

      try {
        const p      = await getTodayPositions();
        const open   = p?.open   || [];
        const closed = p?.closed || [];

        // Realised P&L from closed positions is stable — API value is correct
        const realisedPnl = closed.reduce((s, x) => s + safeNum(x.pnl), 0);

        setPositionsData({ open, closed, realisedPnl });
      } catch {
        // Silent: keep previous values on error — display stays live via ltpMap
      } finally {
        if (posFirstLoad.current) {
          setPositionsLoading(false);
          posFirstLoad.current = false;
        }
      }
    }

    loadPositions();
    const t = setInterval(loadPositions, 15000);
    return () => clearInterval(t);
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

  // ---- DERIVED POSITIONS with live P&L (updates at 500ms via ltpMap) ----------
  //
  // This is the core fix. Instead of waiting for the API to return fresh pnl
  // (which only happens every ~60s on Zerodha's end), we:
  //   1. Take the raw open positions (structure only: symbol, average_price, qty)
  //   2. Look up the live LTP from ltpMap for each symbol
  //   3. Compute unrealised P&L = (ltp - average_price) * quantity
  //
  // ltpMap updates every 500ms, so this memo re-runs every 500ms and the
  // "Today's Performance" numbers update smoothly in real time.
  //
  // Realised P&L (closed positions) doesn't need LTP — it comes from the API.
  //
  // -------------------------------------------------------------------------
  const positions = useMemo(() => {
    const { open, closed, realisedPnl } = positionsData;

    let unrealised = 0;

    const liveOpen = open.map((p) => {
      // Normalise the symbol to match ltpMap key format (uppercase, no spaces)
      const sym = (p.tradingsymbol || "").replace(/\s+/g, "").toUpperCase();
      const ltp = ltpMap[sym];

      // If we have a live LTP, compute P&L ourselves; otherwise fall back to
      // whatever Zerodha returned (which may be stale, but is better than 0)
      const livePnl =
        ltp != null
          ? (ltp - safeNum(p.average_price)) * safeNum(p.quantity)
          : safeNum(p.pnl);

      unrealised += livePnl;
      return { ...p, pnl: livePnl };
    });

    return {
      open: liveOpen,
      closed,
      totals: {
        realised:   realisedPnl,
        unrealised,
        total:      realisedPnl + unrealised,
      },
    };
  }, [positionsData, ltpMap]); // re-runs every time ltpMap changes (500ms)

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

            <MarketBadge name="NIFTY"     data={indices.NIFTY}     />
            <MarketBadge name="BANKNIFTY" data={indices.BANKNIFTY} />
          </div>
        </Card>
      </div>

      {/* ---------- STRATEGY PANELS ---------- */}
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
  const styleSheet      = document.createElement("style");
  styleSheet.id         = "dashboard-styles";
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}
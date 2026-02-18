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

/* ----------------------------------
   Design Tokens
----------------------------------- */
const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
};

const typography = {
  displayLarge:  { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge:     { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium:    { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall:     { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label:         { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: "0.5px", textTransform: "uppercase" },
  mono:          { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" },
};

const colors = {
  profit:    "#10b981",
  profitBg:  "rgba(16, 185, 129, 0.12)",
  loss:      "#ef4444",
  lossBg:    "rgba(239, 68, 68, 0.12)",
  neutral:   "#6b7280",
  primary:   "#3b82f6",
  success:   "#10b981",
  successBg: "rgba(16, 185, 129, 0.15)",
  warning:   "#f59e0b",
  warningBg: "rgba(245, 158, 11, 0.15)",
  danger:    "#ef4444",
  dangerBg:  "rgba(239, 68, 68, 0.15)",
  bg: {
    primary:   "#0a0f1e",
    secondary: "#111827",
    tertiary:  "#1f2937",
    elevated:  "#374151",
  },
  border: {
    light:  "#374151",
    medium: "#4b5563",
    dark:   "#1f2937",
  },
  text: {
    primary:   "#f9fafb",
    secondary: "#d1d5db",
    tertiary:  "#9ca3af",
    muted:     "#6b7280",
  },
};

/* ----------------------------------
   Shared helpers
----------------------------------- */
const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600,
});

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

/* ----------------------------------
   Dashboard
----------------------------------- */
export default function Dashboard() {
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
        padding: spacing.xxl,
        background: colors.bg.primary,
        color: colors.text.primary,
        minHeight: "100vh",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >

      {/* ---------- GLOBAL STATUS BAR ---------- */}
      <div style={{ marginBottom: spacing.xxl }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.lg }}>
          <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
            Scalp Terminal
          </h1>
          <div style={{ ...typography.label, color: colors.text.muted }}>
            Live Trading Dashboard
          </div>
        </div>

        <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap" }}>

            {/* Global system badges */}
            <StatusBadge
              ok={zerodha?.connected}
              danger={!zerodha?.connected}
              text={zerodha?.connected ? "Connected" : "Disconnected"}
              icon={zerodha?.connected ? "●" : "○"}
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

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: spacing.md }}>

          {/* Summary */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Summary
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
                <PnLRow label="Realised"   value={positions.totals.realised}   />
                <PnLRow label="Unrealised" value={positions.totals.unrealised} />
                <div style={{ borderTop: `1px solid ${colors.border.dark}`, marginTop: spacing.sm, paddingTop: spacing.sm }}>
                  <PnLRow label="Total P&L" value={positions.totals.total} large />
                </div>
              </div>
            )}
          </Card>

          {/* Open Positions */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Open Positions
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflow: "auto" }}>
                {positions.open.length === 0 ? (
                  <EmptyState icon="🔭" title="No open positions" description="" />
                ) : (
                  positions.open.map((p, i) => (
                    <div
                      key={i}
                      style={{
                        ...typography.bodySmall,
                        ...typography.mono,
                        marginBottom: spacing.xs,
                        padding: spacing.xs,
                        background: colors.bg.secondary,
                        borderRadius: 4,
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span style={{ color: colors.text.secondary }}>
                        {p.tradingsymbol} × {p.quantity}
                      </span>
                      <span style={pnlStyle(safeNum(p.pnl))}>
                        ₹{Math.round(safeNum(p.pnl)).toLocaleString("en-IN")}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )}
          </Card>

          {/* Closed Positions */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>
              Closed Positions
            </div>
            {positionsLoading ? (
              <CardSkeleton rows={3} />
            ) : (
              <div style={{ maxHeight: 200, overflow: "auto" }}>
                {positions.closed.length === 0 ? (
                  <EmptyState icon="🔭" title="No closed positions" description="" />
                ) : (
                  positions.closed.map((p, i) => (
                    <div
                      key={i}
                      style={{
                        ...typography.bodySmall,
                        ...typography.mono,
                        marginBottom: spacing.xs,
                        padding: spacing.xs,
                        background: colors.bg.secondary,
                        borderRadius: 4,
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span style={{ color: colors.text.secondary }}>
                        {p.tradingsymbol} × {p.day_buy_quantity}
                      </span>
                      <span style={pnlStyle(safeNum(p.pnl))}>
                        ₹{Math.round(safeNum(p.pnl)).toLocaleString("en-IN")}
                      </span>
                    </div>
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
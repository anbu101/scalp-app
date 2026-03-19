import { useEffect, useState, useRef } from "react";
import { getTodayTrades, getTodayPositions } from "../api";
import { LoadingAnimations, FullPageLoader, EmptyState, CardSkeleton } from "../components/LoadingStates";
import { useToast } from "../components/ToastNotifications";
import {
  exportToCSV,
  exportToExcel,
  formatTradesForExport,
  formatDetailedTradesForExport,
  formatTradeJournalForExport,
  formatPerformanceSummary,
  generateFilename,
  copyToClipboard
} from "../utils/export";

/* ─────────────────────────────────────────────
   Design System + Energy Colors
───────────────────────────────────────────── */

const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24 };

const typography = {
  displayLarge:  { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge:     { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium:    { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall:     { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label:         { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: '0.5px', textTransform: 'uppercase' },
  mono:          { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" }
};

const ENERGY = {
  profit:     { color: "#10b981", bg: "rgba(16, 185, 129, 0.1)",  border: "rgba(16, 185, 129, 0.3)" },
  loss:       { color: "#ef4444", bg: "rgba(239, 68, 68, 0.1)",   border: "rgba(239, 68, 68, 0.3)" },
  discipline: { color: "#f59e0b", bg: "rgba(245, 158, 11, 0.08)", border: "rgba(245, 158, 11, 0.25)" },
  execution:  { color: "#3b82f6", bg: "rgba(59, 130, 246, 0.08)", border: "rgba(59, 130, 246, 0.25)" },
  neutral:    { color: "#6b7280", bg: "rgba(107, 114, 128, 0.08)", border: "rgba(107, 114, 128, 0.2)" },
};

const colors = {
  profit:   ENERGY.profit.color,
  profitBg: ENERGY.profit.bg,
  loss:     ENERGY.loss.color,
  lossBg:   ENERGY.loss.bg,
  neutral:  ENERGY.neutral.color,
  primary:  ENERGY.execution.color,
  bg: {
    primary:   "#020817",
    secondary: "#0f172a",
    tertiary:  "#1e293b",
  },
  border: { light: "#334155", dark: "#1e293b" },
  text: {
    primary:   "#f8fafc",
    secondary: "#cbd5e1",
    tertiary:  "#94a3b8",
    muted:     "#64748b"
  }
};

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

/* ─────────────────────────────────────────────
   Normalise a raw trade row from /trades/today.
   The trades table stores:
     symbol        → map to tradingsymbol
     strategy_id   → map to strategy_name
     entry_price, exit_price, qty → compute pnl if missing
     state         → "CLOSED" | "PROTECTED" | "BUY_PLACED" etc.
───────────────────────────────────────────── */
function normaliseTrade(t) {
  const entryPrice = safeNum(t.entry_price);
  const exitPrice  = safeNum(t.exit_price);
  const qty        = safeNum(t.qty);

  // Prefer backend-computed pnl_value; fall back to manual calculation
  const pnl =
    t.pnl_value != null
      ? safeNum(t.pnl_value)
      : exitPrice > 0
      ? (exitPrice - entryPrice) * qty
      : 0;

  return {
    ...t,
    pnl,
    tradingsymbol: t.tradingsymbol ?? t.symbol ?? "",
    strategy_name: t.strategy_id  ?? t.strategy_name ?? "",
  };
}

/* ─────────────────────────────────────────────
   Market Hours Helper
───────────────────────────────────────────── */

function isMarketHours() {
  const d = new Date();
  const dow = d.getDay();
  if (dow === 0 || dow === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= 555 && m < 930;
}

/* ─────────────────────────────────────────────
   Card Component with Flash Animation
───────────────────────────────────────────── */

function Card({ children, style, elevated, flash }) {
  return (
    <div
      style={{
        background: elevated ? colors.bg.tertiary : colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: elevated ? "0 4px 6px -1px rgba(0, 0, 0, 0.3)" : "0 1px 3px rgba(0, 0, 0, 0.2)",
        position: "relative",
        overflow: "hidden",
        ...style
      }}
    >
      {flash && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: flash === "up" ? ENERGY.profit.bg : ENERGY.loss.bg,
            animation: "metricFlash 0.6s ease-out",
            pointerEvents: "none",
            zIndex: 0,
          }}
        />
      )}
      <div style={{ position: "relative", zIndex: 1 }}>{children}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Metric Card with Animation
───────────────────────────────────────────── */

function MetricCard({ label, value, subValue, energy, loading, flash }) {
  if (loading) {
    return (
      <Card elevated style={{ padding: spacing.lg }}>
        <CardSkeleton rows={2} />
      </Card>
    );
  }

  const e = ENERGY[energy] || ENERGY.neutral;

  return (
    <Card elevated flash={flash} style={{ padding: spacing.lg }}>
      <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: spacing.sm }}>
        <div style={{
          ...typography.headingLarge, fontSize: 24, ...typography.mono,
          color: e.color
        }}>
          {value}
        </div>
      </div>
      {subValue && (
        <div style={{ ...typography.bodySmall, color: colors.text.tertiary, marginTop: spacing.xs }}>
          {subValue}
        </div>
      )}
    </Card>
  );
}

/* ─────────────────────────────────────────────
   Hourly Heatmap
   Uses entry_time (unix seconds) from local DB.
───────────────────────────────────────────── */

function HourlyHeatmap({ trades }) {
  if (!trades || !Array.isArray(trades) || trades.length === 0) {
    return (
      <Card style={{ padding: spacing.lg }}>
        <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
          Hourly Performance
        </h3>
        <div style={{ padding: spacing.lg, textAlign: "center", color: colors.text.muted, fontSize: 12 }}>
          No trade data available for hourly breakdown
        </div>
      </Card>
    );
  }

  const hourBuckets = {};

  trades.forEach((t) => {
    if (!t) return;

    // Local DB uses unix seconds in entry_time; Zerodha uses ISO strings
    let ts = t.entry_time;
    if (!ts) return;

    let d;
    if (typeof ts === "number") {
      d = new Date(ts * 1000);         // unix seconds → ms
    } else {
      d = new Date(ts);                 // ISO string
    }
    if (isNaN(d.getTime())) return;

    const hour = d.getHours();
    if (hour < 9 || hour >= 16) return;

    if (!hourBuckets[hour]) hourBuckets[hour] = [];
    hourBuckets[hour].push(safeNum(t.pnl));
  });

  const hours = [9, 10, 11, 12, 13, 14, 15];
  const data = hours.map(h => {
    const hourTrades = hourBuckets[h] || [];
    const pnl   = hourTrades.reduce((sum, v) => sum + v, 0);
    const count = hourTrades.length;
    return { hour: h, pnl, count };
  });

  const maxAbsPnL = Math.max(...data.map(d => Math.abs(d.pnl)), 1);

  return (
    <Card style={{ padding: spacing.lg }}>
      <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
        Hourly Performance
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: spacing.sm }}>
        {data.map((d, i) => {
          const intensity = Math.abs(d.pnl) / maxAbsPnL;
          const isProfit  = d.pnl > 0;
          const bg = d.count === 0
            ? colors.bg.primary
            : isProfit
            ? `rgba(16, 185, 129, ${0.15 + intensity * 0.5})`
            : `rgba(239, 68, 68, ${0.15 + intensity * 0.5})`;

          return (
            <div
              key={i}
              style={{
                background: bg,
                border: `1px solid ${d.count > 0 ? (isProfit ? ENERGY.profit.border : ENERGY.loss.border) : colors.border.dark}`,
                borderRadius: 6,
                padding: spacing.sm,
                textAlign: "center",
                transition: "all 0.3s ease",
              }}
              title={`${d.count} trade${d.count !== 1 ? 's' : ''}`}
            >
              <div style={{ ...typography.label, fontSize: 10, color: colors.text.muted, marginBottom: 4 }}>
                {d.hour < 10 ? `0${d.hour}` : d.hour}:00
              </div>
              <div style={{
                ...typography.mono, fontSize: 13, fontWeight: 600,
                color: d.count === 0 ? colors.text.muted : (isProfit ? ENERGY.profit.color : ENERGY.loss.color)
              }}>
                {d.count === 0 ? "—" : `₹${Math.round(d.pnl)}`}
              </div>
              <div style={{ ...typography.bodySmall, fontSize: 10, color: colors.text.tertiary, marginTop: 2 }}>
                {d.count > 0 ? `${d.count} trade${d.count > 1 ? 's' : ''}` : "No trades"}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/* ─────────────────────────────────────────────
   Open Positions Panel
───────────────────────────────────────────── */

function OpenPositionsPanel({ positions, unrealisedPnL }) {
  const hasOpen = positions && Array.isArray(positions) && positions.length > 0;

  return (
    <Card style={{ padding: spacing.lg }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md }}>
        <h3 style={{ margin: 0, ...typography.headingMedium }}>
          Open Positions
        </h3>
        <div style={{
          ...typography.label, fontSize: 9,
          background: hasOpen ? ENERGY.execution.bg : ENERGY.neutral.bg,
          color: hasOpen ? ENERGY.execution.color : ENERGY.neutral.color,
          padding: "3px 8px", borderRadius: 12,
          border: `1px solid ${hasOpen ? ENERGY.execution.border : ENERGY.neutral.border}`,
        }}>
          {hasOpen ? `${positions.length} OPEN` : "NONE"}
        </div>
      </div>

      {!hasOpen ? (
        <div style={{ padding: spacing.lg, textAlign: "center", color: colors.text.muted, fontSize: 12 }}>
          No open positions
        </div>
      ) : (
        <>
          <div style={{
            background: unrealisedPnL >= 0 ? ENERGY.profit.bg : ENERGY.loss.bg,
            border: `1px solid ${unrealisedPnL >= 0 ? ENERGY.profit.border : ENERGY.loss.border}`,
            borderRadius: 6,
            padding: spacing.md,
            marginBottom: spacing.md,
          }}>
            <div style={{ ...typography.label, fontSize: 10, color: colors.text.muted, marginBottom: 4 }}>
              Unrealised P&L
            </div>
            <div style={{
              ...typography.mono, fontSize: 20, fontWeight: 700,
              color: unrealisedPnL >= 0 ? ENERGY.profit.color : ENERGY.loss.color
            }}>
              {unrealisedPnL >= 0 ? '+' : ''}₹{Math.round(unrealisedPnL).toLocaleString('en-IN')}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
            {positions.slice(0, 5).map((p, i) => {
              if (!p) return null;
              const pnl = safeNum(p.pnl);
              return (
                <div
                  key={i}
                  style={{
                    background: colors.bg.primary,
                    border: `1px solid ${colors.border.dark}`,
                    borderRadius: 6,
                    padding: spacing.sm,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ ...typography.mono, fontSize: 12, color: colors.text.secondary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.tradingsymbol || p.symbol || "Unknown"}
                    </div>
                    <div style={{ ...typography.bodySmall, fontSize: 10, color: colors.text.muted }}>
                      Qty: {p.qty || p.quantity || 0}
                    </div>
                  </div>
                  <div style={{
                    ...typography.mono, fontSize: 13, fontWeight: 600,
                    color: pnl >= 0 ? ENERGY.profit.color : ENERGY.loss.color
                  }}>
                    {pnl >= 0 ? '+' : ''}₹{Math.round(pnl)}
                  </div>
                </div>
              );
            })}
            {positions.length > 5 && (
              <div style={{ ...typography.bodySmall, color: colors.text.muted, textAlign: "center", paddingTop: spacing.xs }}>
                +{positions.length - 5} more position{positions.length - 5 > 1 ? 's' : ''}
              </div>
            )}
          </div>
        </>
      )}
    </Card>
  );
}

/* ─────────────────────────────────────────────
   Enhanced Line Chart with Gradient
───────────────────────────────────────────── */

function EquityCurveChart({ data, width = 550, height = 250 }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: colors.text.muted }}>No data</span>
      </div>
    );
  }

  const padding     = 40;
  const chartWidth  = width  - padding * 2;
  const chartHeight = height - padding * 2;

  const values = data.map(d => d.value);
  const min    = Math.min(...values, 0);
  const max    = Math.max(...values);
  const range  = max - min || 1;

  const points = data.map((d, i) => {
    const x = padding + (i / Math.max(data.length - 1, 1)) * chartWidth;
    const y = padding + chartHeight - ((d.value - min) / range) * chartHeight;
    return { x, y, value: d.value };
  });

  const pathD  = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const areaD  = `${pathD} L ${points[points.length - 1].x} ${padding + chartHeight} L ${padding} ${padding + chartHeight} Z`;
  const finalPnL   = points[points.length - 1]?.value || 0;
  const lineColor  = finalPnL >= 0 ? ENERGY.profit.color : ENERGY.loss.color;
  const gradientId = "equityGradient";

  return (
    <svg width={width} height={height} style={{ background: colors.bg.primary, borderRadius: 8 }}>
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%"   style={{ stopColor: lineColor, stopOpacity: 0.3 }} />
          <stop offset="100%" style={{ stopColor: lineColor, stopOpacity: 0.02 }} />
        </linearGradient>
      </defs>

      {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => {
        const y = padding + chartHeight * ratio;
        return (
          <g key={i}>
            <line x1={padding} y1={y} x2={width - padding} y2={y} stroke={colors.border.dark} strokeDasharray="3,3" />
            <text x={padding - 10} y={y + 4} fill={colors.text.muted} fontSize={10} textAnchor="end">
              ₹{Math.round(min + (1 - ratio) * range)}
            </text>
          </g>
        );
      })}

      <path d={areaD} fill={`url(#${gradientId})`} />
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={3.5} fill={lineColor} opacity={0.8} />
      ))}
    </svg>
  );
}

/* ─────────────────────────────────────────────
   Simple Bar Chart
───────────────────────────────────────────── */

function SimpleBarChart({ data, width = 550, height = 250 }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: colors.text.muted }}>No data</span>
      </div>
    );
  }

  const padding    = 40;
  const chartWidth = width  - padding * 2;
  const chartHeight= height - padding * 2;
  const barWidth   = Math.min((chartWidth / data.length) * 0.7, 80);
  const maxValue   = Math.max(...data.map(d => d.value), 1);

  return (
    <svg width={width} height={height} style={{ background: colors.bg.primary, borderRadius: 8 }}>
      {data.map((d, i) => {
        const barHeight = (d.value / maxValue) * chartHeight;
        const x = padding + (i * (chartWidth / data.length)) + ((chartWidth / data.length - barWidth) / 2);
        const y = padding + chartHeight - barHeight;
        return (
          <g key={i}>
            <rect x={x} y={y} width={barWidth} height={barHeight} fill={d.color} rx={4} />
            <text x={x + barWidth / 2} y={padding + chartHeight + 20} fill={colors.text.muted} fontSize={11} textAnchor="middle">
              {d.label}
            </text>
            <text x={x + barWidth / 2} y={y - 8} fill={colors.text.primary} fontSize={12} fontWeight={600} textAnchor="middle">
              {d.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ─────────────────────────────────────────────
   Analytics Page — Main Component
───────────────────────────────────────────── */

export default function Analytics() {
  const toast = useToast();
  const [loading,    setLoading]    = useState(true);
  const [trades,     setTrades]     = useState([]);
  const [positions,  setPositions]  = useState({ open: [], closed: [] });
  const [metrics,    setMetrics]    = useState(null);
  const [flashState, setFlashState] = useState({});

  const prevMetrics  = useRef(null);
  const pollInterval = useRef(null);

  useEffect(() => {
    loadData();
    startPolling();
    return () => stopPolling();
  }, []);

  function startPolling() {
    stopPolling();
    const interval = isMarketHours() ? 5000 : 30000;
    pollInterval.current = setInterval(loadData, interval);
  }

  function stopPolling() {
    if (pollInterval.current) clearInterval(pollInterval.current);
  }

  async function loadData() {
    try {
      // PRIMARY SOURCE: local trades DB via /trades/today
      // This works for both BB_V1 and SCALP_V1, LIVE and PAPER.
      // Zerodha positions API is NOT used — it misses NRML BB trades
      // and can't distinguish strategies.
      const raw    = await getTodayTrades();
      const allRaw = Array.isArray(raw) ? raw : [];

      // Also try Zerodha positions as a fallback for open position P&L
      // (unrealised pnl from broker is more accurate than our estimate)
      let zerodhaOpen = [];
      try {
        const pos = await getTodayPositions();
        zerodhaOpen = pos?.open || [];
      } catch {
        // non-fatal — local DB is sufficient
      }

      const closed = allRaw
        .filter(t => t.state === "CLOSED" && t.exit_price != null)
        .map(normaliseTrade);

      const open = allRaw
        .filter(t => t.state !== "CLOSED")
        .map(normaliseTrade);

      setTrades(allRaw.map(normaliseTrade));
      setPositions({ open, closed });
      calculateMetrics(closed, open);
    } catch (error) {
      console.error("Failed to load analytics:", error);
    } finally {
      setLoading(false);
    }
  }

  function calculateMetrics(closed, open) {
    const allTrades = [...closed];

    if (allTrades.length === 0) {
      setMetrics(null);
      prevMetrics.current = null;
      return;
    }

    const wins    = allTrades.filter(t => safeNum(t.pnl) > 0).length;
    const losses  = allTrades.filter(t => safeNum(t.pnl) < 0).length;
    const total   = allTrades.length;
    const winRate = total > 0 ? (wins / total) * 100 : 0;

    const totalPnL = allTrades.reduce((sum, t) => sum + safeNum(t.pnl), 0);
    const avgPnL   = total > 0 ? totalPnL / total : 0;

    const avgWin = wins > 0
      ? allTrades.filter(t => safeNum(t.pnl) > 0).reduce((sum, t) => sum + safeNum(t.pnl), 0) / wins
      : 0;
    const avgLoss = losses > 0
      ? allTrades.filter(t => safeNum(t.pnl) < 0).reduce((sum, t) => sum + safeNum(t.pnl), 0) / losses
      : 0;

    const bestTrade  = allTrades.reduce((max, t) => safeNum(t.pnl) > safeNum(max.pnl) ? t : max, allTrades[0] || {});
    const worstTrade = allTrades.reduce((min, t) => safeNum(t.pnl) < safeNum(min.pnl) ? t : min, allTrades[0] || {});

    const unrealisedPnL = open.reduce((sum, t) => sum + safeNum(t.pnl), 0);

    const grossProfit = allTrades.filter(t => safeNum(t.pnl) > 0).reduce((sum, t) => sum + safeNum(t.pnl), 0);
    const grossLoss   = Math.abs(allTrades.filter(t => safeNum(t.pnl) < 0).reduce((sum, t) => sum + safeNum(t.pnl), 0));
    const profitFactor= grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    // ── Streaks ──────────────────────────────────────────────────────────
    let runStreak = 0, bestWinStreak = 0, bestLossStreak = 0;
    allTrades.forEach((t) => {
      const p = safeNum(t.pnl);
      if      (p > 0) runStreak = runStreak > 0 ? runStreak + 1 : 1;
      else if (p < 0) runStreak = runStreak < 0 ? runStreak - 1 : -1;
      else            runStreak = 0;
      if (runStreak > bestWinStreak)  bestWinStreak  = runStreak;
      if (runStreak < bestLossStreak) bestLossStreak = runStreak;
    });
    const currentStreak = runStreak;

    // ── Max Drawdown ─────────────────────────────────────────────────────
    let peak = 0, maxDrawdown = 0, maxDrawdownPct = 0, equity = 0;
    allTrades.forEach((t) => {
      equity += safeNum(t.pnl);
      if (equity > peak) peak = equity;
      const dd = peak - equity;
      if (dd > maxDrawdown) {
        maxDrawdown    = dd;
        maxDrawdownPct = peak > 0 ? (dd / peak) * 100 : 0;
      }
    });

    // ── Expectancy ───────────────────────────────────────────────────────
    const lossRate       = total > 0 ? losses / total : 0;
    const winRateDecimal = total > 0 ? wins   / total : 0;
    const expectancy     = (winRateDecimal * avgWin) + (lossRate * avgLoss);

    const newMetrics = {
      totalTrades: total,
      wins,
      losses,
      winRate,
      totalPnL,
      avgPnL,
      avgWin,
      avgLoss,
      bestTrade,
      worstTrade,
      unrealisedPnL,
      profitFactor,
      currentStreak,
      bestWinStreak,
      bestLossStreak,
      maxDrawdown,
      maxDrawdownPct,
      expectancy,
    };

    // Flash detection
    if (prevMetrics.current) {
      const flash = {};
      if (newMetrics.totalPnL     > prevMetrics.current.totalPnL)     flash.totalPnL     = "up";
      if (newMetrics.totalPnL     < prevMetrics.current.totalPnL)     flash.totalPnL     = "down";
      if (newMetrics.winRate      > prevMetrics.current.winRate)      flash.winRate      = "up";
      if (newMetrics.winRate      < prevMetrics.current.winRate)      flash.winRate      = "down";
      if (newMetrics.avgPnL       > prevMetrics.current.avgPnL)       flash.avgPnL       = "up";
      if (newMetrics.avgPnL       < prevMetrics.current.avgPnL)       flash.avgPnL       = "down";
      if (newMetrics.profitFactor > prevMetrics.current.profitFactor) flash.profitFactor = "up";
      if (newMetrics.profitFactor < prevMetrics.current.profitFactor) flash.profitFactor = "down";
      if (Object.keys(flash).length > 0) {
        setFlashState(flash);
        setTimeout(() => setFlashState({}), 650);
      }
    }

    prevMetrics.current = newMetrics;
    setMetrics(newMetrics);
  }

  // ── Export handlers ──────────────────────────────────────────────────
  function handleExportTradesCSV() {
    exportToCSV(formatTradesForExport(positions.closed), generateFilename('trades', 'csv'));
    toast.success('Export Complete', 'Trades exported to CSV');
  }
  function handleExportTradesExcel() {
    exportToExcel(formatTradesForExport(positions.closed), generateFilename('trades', 'xlsx'));
    toast.success('Export Complete', 'Trades exported to Excel');
  }
  function handleExportDetailedTrades() {
    exportToCSV(formatDetailedTradesForExport(positions.closed), generateFilename('detailed_trades', 'csv'));
    toast.success('Export Complete', 'Detailed trades exported');
  }
  function handleExportTradeJournal() {
    exportToCSV(formatTradeJournalForExport(positions.closed), generateFilename('trade_journal', 'csv'));
    toast.success('Export Complete', 'Trade journal exported');
  }
  function handleExportSummary() {
    exportToCSV(formatPerformanceSummary(metrics, positions), generateFilename('performance_summary', 'csv'));
    toast.success('Export Complete', 'Summary exported');
  }
  async function handleCopyToClipboard() {
    const summary = formatPerformanceSummary(metrics, positions);
    const text    = summary.map(row => `${row.Metric}: ${row.Value}`).join('\n');
    const success = await copyToClipboard(text);
    if (success) toast.success('Copied!', 'Summary copied to clipboard');
    else         toast.error('Copy Failed', 'Could not copy to clipboard');
  }

  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading analytics..." />
      </>
    );
  }

  const hasTrades = positions.closed.length > 0;

  const equityCurveData = positions.closed.map((t, i) => ({
    label: `${i + 1}`,
    value: positions.closed.slice(0, i + 1).reduce((sum, tr) => sum + safeNum(tr.pnl), 0)
  }));

  const distributionData = metrics ? [
    { label: "Wins",   value: metrics.wins,   color: ENERGY.profit.color },
    { label: "Losses", value: metrics.losses,  color: ENERGY.loss.color  }
  ] : [];

  const pollRate = isMarketHours() ? "5s" : "30s";

  return (
    <div style={{
      padding: spacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    }}>
      {/* Header */}
      <div style={{ marginBottom: spacing.lg }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm }}>
          <div>
            <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
              Performance Analytics
            </h1>
            <div style={{ display: "flex", alignItems: "center", gap: spacing.md, marginTop: spacing.xs }}>
              <p style={{ margin: 0, ...typography.bodyMedium, color: colors.text.tertiary }}>
                Today's live performance · {positions.closed.length} closed · {positions.open.length} open
              </p>
              <div style={{
                ...typography.label, fontSize: 9,
                background: ENERGY.execution.bg,
                color: ENERGY.execution.color,
                padding: "3px 8px", borderRadius: 12,
                border: `1px solid ${ENERGY.execution.border}`,
                display: "flex", alignItems: "center", gap: 4,
              }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: ENERGY.execution.color, animation: "pulse 2s ease-in-out infinite" }} />
                Auto-refresh: {pollRate}
              </div>
            </div>
          </div>

          {hasTrades && (
            <div style={{ display: "flex", gap: spacing.sm, flexWrap: "wrap" }}>
              <button onClick={handleExportTradesCSV}      style={exportButtonStyle} title="Export to CSV">📄 CSV</button>
              <button onClick={handleExportTradesExcel}    style={exportButtonStyle} title="Export to Excel">📊 Excel</button>
              <button onClick={handleExportDetailedTrades} style={exportButtonStyle} title="Detailed export">📋 Detailed</button>
              <button onClick={handleExportTradeJournal}   style={exportButtonStyle} title="Trade journal">📖 Journal</button>
              <button onClick={handleExportSummary}        style={exportButtonStyle} title="Performance summary">📊 Summary</button>
              <button onClick={handleCopyToClipboard} style={{ ...exportButtonStyle, border: `1px solid ${ENERGY.execution.color}`, background: ENERGY.execution.color }} title="Copy to clipboard">📋 Copy</button>
            </div>
          )}
        </div>
      </div>

      {!hasTrades ? (
        <Card style={{ padding: spacing.xxl }}>
          <EmptyState
            icon="📊"
            title="No closed trades yet"
            description="Analytics will appear here once you have at least one closed trade today. Open positions are tracked separately."
          />
          {/* Show open positions count even when no closed trades */}
          {positions.open.length > 0 && (
            <div style={{ textAlign: "center", marginTop: spacing.lg, color: colors.text.muted, fontSize: 13 }}>
              {positions.open.length} open position{positions.open.length > 1 ? "s" : ""} currently active
            </div>
          )}
        </Card>
      ) : (
        <>
          {/* Key Metrics Grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: spacing.md,
            marginBottom: spacing.lg
          }}>
            <MetricCard
              label="Total P&L"
              value={`₹${Math.round(metrics.totalPnL).toLocaleString('en-IN')}`}
              energy={metrics.totalPnL >= 0 ? "profit" : "loss"}
              subValue={`${metrics.totalTrades} trades completed`}
              flash={flashState.totalPnL}
            />
            <MetricCard
              label="Win Rate"
              value={`${metrics.winRate.toFixed(1)}%`}
              energy={metrics.winRate >= 50 ? "profit" : "loss"}
              subValue={`${metrics.wins}W / ${metrics.losses}L`}
              flash={flashState.winRate}
            />
            <MetricCard
              label="Average P&L"
              value={`₹${Math.round(metrics.avgPnL).toLocaleString('en-IN')}`}
              energy={metrics.avgPnL >= 0 ? "profit" : "loss"}
              subValue="Per trade"
              flash={flashState.avgPnL}
            />
            <MetricCard
              label="Profit Factor"
              value={metrics.profitFactor === Infinity ? "∞" : metrics.profitFactor.toFixed(2)}
              energy={metrics.profitFactor > 1 ? "profit" : "loss"}
              subValue="Gross profit / loss"
              flash={flashState.profitFactor}
            />
            <MetricCard
              label="Expectancy"
              value={`₹${Math.round(metrics.expectancy).toLocaleString('en-IN')}`}
              energy={metrics.expectancy >= 0 ? "profit" : "loss"}
              subValue="Expected P&L per trade"
            />
            <MetricCard
              label="Max Drawdown"
              value={`₹${Math.round(metrics.maxDrawdown).toLocaleString('en-IN')}`}
              energy={metrics.maxDrawdown === 0 ? "neutral" : "loss"}
              subValue={metrics.maxDrawdownPct > 0 ? `${metrics.maxDrawdownPct.toFixed(1)}% of peak` : "No drawdown"}
            />
            <MetricCard
              label="Current Streak"
              value={
                metrics.currentStreak === 0 ? "—"
                : metrics.currentStreak > 0 ? `${metrics.currentStreak}W 🔥`
                : `${Math.abs(metrics.currentStreak)}L ❄️`
              }
              energy={metrics.currentStreak > 0 ? "profit" : metrics.currentStreak < 0 ? "loss" : "neutral"}
              subValue={`Best: ${metrics.bestWinStreak}W  Worst: ${Math.abs(metrics.bestLossStreak)}L`}
            />
          </div>

          {/* Two-Column Layout */}
          <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: spacing.md, marginBottom: spacing.lg }}>
            {/* Left: Charts */}
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
              <Card style={{ padding: spacing.lg }}>
                <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                  Equity Curve
                </h3>
                <div style={{ display: "flex", justifyContent: "center" }}>
                  <EquityCurveChart data={equityCurveData} width={550} height={250} />
                </div>
              </Card>

              <Card style={{ padding: spacing.lg }}>
                <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                  Trade Distribution
                </h3>
                <div style={{ display: "flex", justifyContent: "center" }}>
                  <SimpleBarChart data={distributionData} width={550} height={250} />
                </div>
              </Card>
            </div>

            {/* Right: Heatmap + Open Positions */}
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
              <HourlyHeatmap trades={trades} />
              <OpenPositionsPanel positions={positions.open} unrealisedPnL={metrics?.unrealisedPnL || 0} />
            </div>
          </div>

          {/* Bottom Row */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: spacing.md }}>
            {/* Best & Worst Trades */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Best & Worst Trades
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                <div style={{
                  padding: spacing.md, background: ENERGY.profit.bg,
                  borderRadius: 6, border: `1px solid ${ENERGY.profit.border}`
                }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>Best Trade</div>
                  <div style={{ ...typography.mono, fontSize: 13, color: colors.text.secondary, marginBottom: 4 }}>
                    {metrics.bestTrade.tradingsymbol || metrics.bestTrade.symbol || "N/A"}
                  </div>
                  <div style={{ ...typography.headingMedium, color: ENERGY.profit.color }}>
                    +₹{Math.round(safeNum(metrics.bestTrade.pnl)).toLocaleString('en-IN')}
                  </div>
                </div>

                <div style={{
                  padding: spacing.md, background: ENERGY.loss.bg,
                  borderRadius: 6, border: `1px solid ${ENERGY.loss.border}`
                }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>Worst Trade</div>
                  <div style={{ ...typography.mono, fontSize: 13, color: colors.text.secondary, marginBottom: 4 }}>
                    {metrics.worstTrade.tradingsymbol || metrics.worstTrade.symbol || "N/A"}
                  </div>
                  <div style={{ ...typography.headingMedium, color: ENERGY.loss.color }}>
                    ₹{Math.round(safeNum(metrics.worstTrade.pnl)).toLocaleString('en-IN')}
                  </div>
                </div>
              </div>
            </Card>

            {/* Average Performance */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Average Performance
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                <div>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>Average Win</div>
                  <div style={{ ...typography.headingMedium, ...typography.mono, color: ENERGY.profit.color }}>
                    +₹{Math.round(metrics.avgWin).toLocaleString('en-IN')}
                  </div>
                </div>
                <div>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>Average Loss</div>
                  <div style={{ ...typography.headingMedium, ...typography.mono, color: ENERGY.loss.color }}>
                    ₹{Math.round(metrics.avgLoss).toLocaleString('en-IN')}
                  </div>
                </div>
                <div style={{ paddingTop: spacing.md, borderTop: `1px solid ${colors.border.dark}` }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>Risk/Reward Ratio</div>
                  <div style={{ ...typography.headingMedium, ...typography.mono, color: colors.text.primary }}>
                    {metrics.avgLoss !== 0
                      ? `1:${Math.abs(metrics.avgWin / metrics.avgLoss).toFixed(2)}`
                      : "N/A"}
                  </div>
                </div>
              </div>
            </Card>
          </div>
        </>
      )}

      <style>{`
        @keyframes metricFlash {
          0%   { opacity: 0.8; }
          100% { opacity: 0; }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}

const exportButtonStyle = {
  padding: "8px 16px",
  borderRadius: 6,
  border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary,
  color: colors.text.primary,
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 6,
  transition: "all 0.2s ease"
};
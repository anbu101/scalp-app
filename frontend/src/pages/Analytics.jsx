import { useEffect, useState } from "react";
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

/* -------------------------
   Design System
-------------------------- */

const spacing = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24
};

const typography = {
  displayLarge: { fontSize: 28, fontWeight: 700, lineHeight: 1.2 },
  headingLarge: { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  headingMedium: { fontSize: 16, fontWeight: 600, lineHeight: 1.4 },
  bodyLarge: { fontSize: 14, fontWeight: 400, lineHeight: 1.5 },
  bodyMedium: { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall: { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label: { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: '0.5px', textTransform: 'uppercase' },
  mono: { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" }
};

const colors = {
  profit: "#10b981",
  profitBg: "rgba(16, 185, 129, 0.1)",
  loss: "#ef4444",
  lossBg: "rgba(239, 68, 68, 0.1)",
  neutral: "#6b7280",
  primary: "#2563eb",
  bg: {
    primary: "#020817",
    secondary: "#0f172a",
    tertiary: "#1e293b",
  },
  border: {
    light: "#334155",
    dark: "#1e293b"
  },
  text: {
    primary: "#f8fafc",
    secondary: "#cbd5e1",
    tertiary: "#94a3b8",
    muted: "#64748b"
  }
};

/* -------------------------
   Helper Functions
-------------------------- */

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);

/* -------------------------
   Card Component
-------------------------- */

function Card({ children, style, elevated }) {
  return (
    <div
      style={{
        background: elevated ? colors.bg.tertiary : colors.bg.secondary,
        border: `1px solid ${colors.border.light}`,
        borderRadius: 8,
        boxShadow: elevated ? "0 4px 6px -1px rgba(0, 0, 0, 0.3)" : "0 1px 3px rgba(0, 0, 0, 0.2)",
        ...style
      }}
    >
      {children}
    </div>
  );
}

/* -------------------------
   Metric Card
-------------------------- */

function MetricCard({ label, value, subValue, isPositive, loading }) {
  if (loading) {
    return (
      <Card elevated style={{ padding: spacing.lg }}>
        <CardSkeleton rows={2} />
      </Card>
    );
  }

  return (
    <Card elevated style={{ padding: spacing.lg }}>
      <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: spacing.sm }}>
        <div style={{
          ...typography.headingLarge,
          fontSize: 24,
          ...typography.mono,
          color: isPositive !== undefined 
            ? (isPositive ? colors.profit : colors.loss)
            : colors.text.primary
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

/* -------------------------
   Simple Line Chart (SVG)
-------------------------- */

function SimpleLineChart({ data, width = 600, height = 250, color = colors.primary }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: colors.text.muted }}>No data to display</span>
      </div>
    );
  }

  const padding = 40;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  const values = data.map(d => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * chartWidth;
    const y = padding + chartHeight - ((d.value - min) / range) * chartHeight;
    return { x, y, label: d.label, value: d.value };
  });

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <svg width={width} height={height} style={{ background: colors.bg.primary, borderRadius: 8 }}>
      {/* Grid lines */}
      {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => {
        const y = padding + chartHeight * ratio;
        return (
          <g key={i}>
            <line
              x1={padding}
              y1={y}
              x2={width - padding}
              y2={y}
              stroke={colors.border.dark}
              strokeDasharray="3,3"
            />
            <text
              x={padding - 10}
              y={y + 4}
              fill={colors.text.muted}
              fontSize={10}
              textAnchor="end"
            >
              ₹{Math.round(min + (1 - ratio) * range)}
            </text>
          </g>
        );
      })}

      {/* Line */}
      <path
        d={pathD}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Points */}
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r={4} fill={color} />
          {i % Math.ceil(points.length / 8) === 0 && (
            <text
              x={p.x}
              y={height - padding + 20}
              fill={colors.text.muted}
              fontSize={10}
              textAnchor="middle"
            >
              {p.label}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

/* -------------------------
   Simple Bar Chart
-------------------------- */

function SimpleBarChart({ data, width = 600, height = 250 }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: colors.text.muted }}>No data to display</span>
      </div>
    );
  }

  const padding = 40;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;
  const barWidth = chartWidth / data.length - 20;
  const maxValue = Math.max(...data.map(d => d.value));

  return (
    <svg width={width} height={height} style={{ background: colors.bg.primary, borderRadius: 8 }}>
      {data.map((d, i) => {
        const barHeight = (d.value / maxValue) * chartHeight;
        const x = padding + i * (chartWidth / data.length) + 10;
        const y = padding + chartHeight - barHeight;

        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={barHeight}
              fill={d.color || colors.primary}
              rx={6}
            />
            <text
              x={x + barWidth / 2}
              y={height - padding + 20}
              fill={colors.text.muted}
              fontSize={12}
              textAnchor="middle"
            >
              {d.label}
            </text>
            <text
              x={x + barWidth / 2}
              y={y - 8}
              fill={colors.text.primary}
              fontSize={12}
              fontWeight={600}
              textAnchor="middle"
            >
              {d.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* -------------------------
   Analytics Page
-------------------------- */

export default function Analytics() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [trades, setTrades] = useState([]);
  const [positions, setPositions] = useState({ open: [], closed: [] });
  const [metrics, setMetrics] = useState(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  async function loadData() {
    try {
      const [tradesData, positionsData] = await Promise.all([
        getTodayTrades(),
        getTodayPositions()
      ]);

      setTrades(tradesData || []);
      setPositions(positionsData || { open: [], closed: [] });
      
      calculateMetrics(positionsData?.closed || [], positionsData?.open || []);
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
      return;
    }

    const wins = allTrades.filter(t => safeNum(t.pnl) > 0).length;
    const losses = allTrades.filter(t => safeNum(t.pnl) < 0).length;
    const total = allTrades.length;
    const winRate = total > 0 ? (wins / total) * 100 : 0;

    const totalPnL = allTrades.reduce((sum, t) => sum + safeNum(t.pnl), 0);
    const avgPnL = total > 0 ? totalPnL / total : 0;
    const avgWin = wins > 0 
      ? allTrades.filter(t => safeNum(t.pnl) > 0).reduce((sum, t) => sum + safeNum(t.pnl), 0) / wins 
      : 0;
    const avgLoss = losses > 0
      ? allTrades.filter(t => safeNum(t.pnl) < 0).reduce((sum, t) => sum + safeNum(t.pnl), 0) / losses
      : 0;

    const bestTrade = allTrades.reduce((max, t) => 
      safeNum(t.pnl) > safeNum(max.pnl) ? t : max, allTrades[0] || {});
    const worstTrade = allTrades.reduce((min, t) => 
      safeNum(t.pnl) < safeNum(min.pnl) ? t : min, allTrades[0] || {});

    const unrealisedPnL = open.reduce((sum, t) => sum + safeNum(t.pnl), 0);

    const grossProfit = allTrades.filter(t => safeNum(t.pnl) > 0).reduce((sum, t) => sum + safeNum(t.pnl), 0);
    const grossLoss = Math.abs(allTrades.filter(t => safeNum(t.pnl) < 0).reduce((sum, t) => sum + safeNum(t.pnl), 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    setMetrics({
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
      profitFactor
    });
  }

  // Export handlers
  function handleExportTradesCSV() {
    const formattedTrades = formatTradesForExport(positions.closed);
    exportToCSV(formattedTrades, generateFilename('trades', 'csv'));
    toast.success('Export Complete', 'Trades exported to CSV');
  }

  function handleExportTradesExcel() {
    const formattedTrades = formatTradesForExport(positions.closed);
    exportToExcel(formattedTrades, generateFilename('trades', 'xlsx'));
    toast.success('Export Complete', 'Trades exported to Excel');
  }

  function handleExportDetailedTrades() {
    const detailedTrades = formatDetailedTradesForExport(positions.closed);
    exportToCSV(detailedTrades, generateFilename('detailed_trades', 'csv'));
    toast.success('Export Complete', 'Detailed trades exported with individual legs');
  }

  function handleExportTradeJournal() {
    const journal = formatTradeJournalForExport(positions.closed);
    exportToCSV(journal, generateFilename('trade_journal', 'csv'));
    toast.success('Export Complete', 'Trade journal exported with all entries/exits');
  }

  function handleExportSummary() {
    const summary = formatPerformanceSummary(metrics, positions);
    exportToCSV(summary, generateFilename('performance_summary', 'csv'));
    toast.success('Export Complete', 'Summary exported to CSV');
  }

  async function handleCopyToClipboard() {
    const summary = formatPerformanceSummary(metrics, positions);
    const text = summary.map(row => `${row.Metric}: ${row.Value}`).join('\n');
    const success = await copyToClipboard(text);
    if (success) {
      toast.success('Copied!', 'Summary copied to clipboard');
    } else {
      toast.error('Copy Failed', 'Could not copy to clipboard');
    }
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

  // Prepare chart data
  const equityCurveData = positions.closed.map((t, i) => ({
    label: `${i + 1}`,
    value: positions.closed.slice(0, i + 1).reduce((sum, tr) => sum + safeNum(tr.pnl), 0)
  }));

  const distributionData = metrics ? [
    { label: "Wins", value: metrics.wins, color: colors.profit },
    { label: "Losses", value: metrics.losses, color: colors.loss }
  ] : [];

  return (
    <div style={{
      padding: spacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    }}>
      {/* Header */}
      <div style={{ marginBottom: spacing.xxl }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md }}>
          <div>
            <h1 style={{ margin: 0, ...typography.displayLarge, color: colors.text.primary }}>
              Performance Analytics
            </h1>
            <p style={{ margin: 0, marginTop: spacing.xs, ...typography.bodyMedium, color: colors.text.tertiary }}>
              Comprehensive trading performance metrics and insights
            </p>
          </div>
          
          {hasTrades && (
            <div style={{ display: "flex", gap: spacing.sm, flexWrap: "wrap" }}>
              <button
                onClick={handleExportTradesCSV}
                style={{
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
                }}
                onMouseEnter={(e) => e.target.style.background = colors.bg.tertiary}
                onMouseLeave={(e) => e.target.style.background = colors.bg.secondary}
                title="Export summarized trades"
              >
                📄 CSV
              </button>
              
              <button
                onClick={handleExportTradesExcel}
                style={{
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
                }}
                onMouseEnter={(e) => e.target.style.background = colors.bg.tertiary}
                onMouseLeave={(e) => e.target.style.background = colors.bg.secondary}
                title="Export to Excel"
              >
                📊 Excel
              </button>
              
              <button
                onClick={handleExportDetailedTrades}
                style={{
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
                }}
                onMouseEnter={(e) => e.target.style.background = colors.bg.tertiary}
                onMouseLeave={(e) => e.target.style.background = colors.bg.secondary}
                title="Export with individual trade legs"
              >
                📋 Detailed
              </button>
              
              <button
                onClick={handleExportTradeJournal}
                style={{
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
                }}
                onMouseEnter={(e) => e.target.style.background = colors.bg.tertiary}
                onMouseLeave={(e) => e.target.style.background = colors.bg.secondary}
                title="Export complete trade journal with all entries/exits"
              >
                📖 Journal
              </button>
              
              <button
                onClick={handleExportSummary}
                style={{
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
                }}
                onMouseEnter={(e) => e.target.style.background = colors.bg.tertiary}
                onMouseLeave={(e) => e.target.style.background = colors.bg.secondary}
                title="Export performance summary"
              >
                📊 Summary
              </button>
              
              <button
                onClick={handleCopyToClipboard}
                style={{
                  padding: "8px 16px",
                  borderRadius: 6,
                  border: `1px solid ${colors.primary}`,
                  background: colors.primary,
                  color: colors.text.primary,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "all 0.2s ease"
                }}
                onMouseEnter={(e) => e.target.style.opacity = 0.9}
                onMouseLeave={(e) => e.target.style.opacity = 1}
                title="Copy summary to clipboard"
              >
                📋 Copy
              </button>
            </div>
          )}
        </div>
      </div>

      {!hasTrades ? (
        <Card style={{ padding: spacing.xxl }}>
          <EmptyState
            icon="📊"
            title="No trades yet"
            description="Analytics will appear here once you complete your first trade of the day."
          />
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
              isPositive={metrics.totalPnL > 0}
              subValue={`${metrics.totalTrades} trades completed`}
            />
            <MetricCard
              label="Win Rate"
              value={`${metrics.winRate.toFixed(1)}%`}
              isPositive={metrics.winRate >= 50}
              subValue={`${metrics.wins}W / ${metrics.losses}L`}
            />
            <MetricCard
              label="Average P&L"
              value={`₹${Math.round(metrics.avgPnL).toLocaleString('en-IN')}`}
              isPositive={metrics.avgPnL > 0}
              subValue="Per trade"
            />
            <MetricCard
              label="Profit Factor"
              value={metrics.profitFactor === Infinity ? "∞" : metrics.profitFactor.toFixed(2)}
              isPositive={metrics.profitFactor > 1}
              subValue="Gross profit / loss"
            />
          </div>

          {/* Charts Row */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: spacing.md,
            marginBottom: spacing.lg
          }}>
            {/* Equity Curve */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Equity Curve
              </h3>
              <div style={{ display: "flex", justifyContent: "center" }}>
                <SimpleLineChart 
                  data={equityCurveData}
                  width={550}
                  height={250}
                  color={colors.primary}
                />
              </div>
            </Card>

            {/* Win/Loss Distribution */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Trade Distribution
              </h3>
              <div style={{ display: "flex", justifyContent: "center" }}>
                <SimpleBarChart 
                  data={distributionData}
                  width={550}
                  height={250}
                />
              </div>
            </Card>
          </div>

          {/* Additional Metrics */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: spacing.md
          }}>
            {/* Best & Worst Trades */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Best & Worst Trades
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                <div style={{
                  padding: spacing.md,
                  background: colors.profitBg,
                  borderRadius: 6,
                  border: `1px solid ${colors.profit}40`
                }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>
                    Best Trade
                  </div>
                  <div style={{ ...typography.mono, fontSize: 13, color: colors.text.secondary, marginBottom: 4 }}>
                    {metrics.bestTrade.tradingsymbol || "N/A"}
                  </div>
                  <div style={{ ...typography.headingMedium, color: colors.profit }}>
                    +₹{Math.round(safeNum(metrics.bestTrade.pnl)).toLocaleString('en-IN')}
                  </div>
                </div>

                <div style={{
                  padding: spacing.md,
                  background: colors.lossBg,
                  borderRadius: 6,
                  border: `1px solid ${colors.loss}40`
                }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>
                    Worst Trade
                  </div>
                  <div style={{ ...typography.mono, fontSize: 13, color: colors.text.secondary, marginBottom: 4 }}>
                    {metrics.worstTrade.tradingsymbol || "N/A"}
                  </div>
                  <div style={{ ...typography.headingMedium, color: colors.loss }}>
                    ₹{Math.round(safeNum(metrics.worstTrade.pnl)).toLocaleString('en-IN')}
                  </div>
                </div>
              </div>
            </Card>

            {/* Average Win/Loss */}
            <Card style={{ padding: spacing.lg }}>
              <h3 style={{ margin: 0, marginBottom: spacing.md, ...typography.headingMedium }}>
                Average Performance
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
                <div>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>
                    Average Win
                  </div>
                  <div style={{ ...typography.headingMedium, ...typography.mono, color: colors.profit }}>
                    +₹{Math.round(metrics.avgWin).toLocaleString('en-IN')}
                  </div>
                </div>

                <div>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>
                    Average Loss
                  </div>
                  <div style={{ ...typography.headingMedium, ...typography.mono, color: colors.loss }}>
                    ₹{Math.round(metrics.avgLoss).toLocaleString('en-IN')}
                  </div>
                </div>

                <div style={{
                  paddingTop: spacing.md,
                  borderTop: `1px solid ${colors.border.dark}`
                }}>
                  <div style={{ ...typography.bodySmall, color: colors.text.muted, marginBottom: spacing.xs }}>
                    Risk/Reward Ratio
                  </div>
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
    </div>
  );
}
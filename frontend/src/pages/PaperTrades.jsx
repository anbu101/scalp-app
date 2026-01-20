import { useEffect, useState } from "react";
import { LoadingAnimations, FullPageLoader, EmptyState } from "../components/LoadingStates";
import { useToast } from "../components/ToastNotifications";
import { exportToCSV, generateFilename } from "../utils/export";

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
  success: "#059669",
  successBg: "rgba(5, 150, 105, 0.12)",
  warning: "#d97706",
  warningBg: "rgba(217, 119, 6, 0.12)",
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

function isWithinDateRange(ts, startDate, endDate) {
    if (!ts) return false;
  
    const tradeDate = new Date(ts * 1000)
      .toISOString()
      .slice(0, 10);
  
    return tradeDate >= startDate && tradeDate <= endDate;
  }
  
  function getTodayRange() {
    const today = new Date().toISOString().slice(0, 10);
    return { start: today, end: today };
  }
function getThisMonthRange() {
const now = new Date();
const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);

return {
    start: firstDay.toISOString().slice(0, 10),
    end: now.toISOString().slice(0, 10),
};
}
  
function getThisWeekRange() {
const now = new Date();
const day = now.getDay(); // 0=Sun, 1=Mon
const diff = day === 0 ? 6 : day - 1; // Monday as start
const monday = new Date(now);
monday.setDate(now.getDate() - diff);

return {
    start: monday.toISOString().slice(0, 10),
    end: now.toISOString().slice(0, 10),
};
}

function getAllTimeRange() {
return {
    start: "2000-01-01",
    end: "2100-12-31",
};
}
  
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
   Status Badge
-------------------------- */

function StatusBadge({ type, text }) {
  const styles = {
    open: { bg: colors.warningBg, color: colors.warning, border: colors.warning },
    closed: { bg: colors.bg.tertiary, color: colors.text.muted, border: colors.border.light }
  };

  const style = styles[type] || styles.closed;

  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 6,
        fontSize: 11,
        fontWeight: 600,
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}40`,
        display: "inline-block",
        textTransform: "uppercase",
        letterSpacing: "0.3px"
      }}
    >
      {text}
    </span>
  );
}

/* -------------------------
   Helper Functions
-------------------------- */

function formatTimestamp(timestamp) {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000); // Unix timestamp to JS Date
  return date.toLocaleString('en-IN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
}

const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600
});

const BROKERAGE_PER_TRADE = 40;

function scaleTrade(trade, selectedLots) {
  if (!trade || !trade.lots || trade.lots === 0) return trade;

  const scale = selectedLots / trade.lots;

  const gross = (trade.pnl_value || 0) * scale;

  const totalCharges = trade.total_charges || 0;

  const variableCharges = Math.max(
    totalCharges - BROKERAGE_PER_TRADE,
    0
  );

  const scaledCharges =
    BROKERAGE_PER_TRADE + (variableCharges * scale);

  return {
    ...trade,
    _scale: scale,

    scaled_pnl_value: gross,
    scaled_charges: scaledCharges,
    scaled_net_pnl: gross - scaledCharges,
  };
}

/* -------------------------
   Paper Trades Page
-------------------------- */

export default function PaperTrades() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [paperTrades, setPaperTrades] = useState({ open: [], closed: [] });
  const [selectedLots, setSelectedLots] = useState(1);
  const todayStr = new Date().toISOString().slice(0, 10);

  const [startDate, setStartDate] = useState(todayStr);
  const [endDate, setEndDate] = useState(todayStr);

  const [activeQuickFilter, setActiveQuickFilter] = useState("TODAY");

  function applyQuickFilter(type) {
    let range;
  
    if (type === "TODAY") range = getTodayRange();
    else if (type === "WEEK") range = getThisWeekRange();
    else if (type === "MONTH") range = getThisMonthRange();
    else range = getAllTimeRange();
  
    setStartDate(range.start);
    setEndDate(range.end);
    setActiveQuickFilter(type);
  }
  
  

  useEffect(() => {
    loadPaperTrades();
    const interval = setInterval(loadPaperTrades, 10000); // Refresh every 10s
    return () => clearInterval(interval);
  }, []);

  async function loadPaperTrades() {
    try {
      const response = await fetch('http://localhost:8000/paper_trades');
      if (!response.ok) throw new Error('Failed to fetch paper trades');
      const data = await response.json();
      console.log("PAPER TRADES RAW API DATA", data);

      setPaperTrades({
        open: Array.isArray(data.open) ? data.open : [],
        closed: Array.isArray(data.closed) ? data.closed : [],
      });

    } catch (error) {
      console.error('Error loading paper trades:', error);
      setPaperTrades({ open: [], closed: [] });
      toast.error('Load Failed', 'Could not load paper trades');
    } finally {
      setLoading(false);
    }
  }

  function handleExportCSV() {
    if (allTrades.length === 0) {
      toast.warning("No Data", "No paper trades to export");
      return;
    }
  
    const grossPnL = Math.round(totalGrossPnL);
    const netPnL = Math.round(totalNetPnL);
    const totalCharges = Math.round(
      scaledClosedTrades.reduce((s, t) => s + (t.scaled_charges || 0), 0)
    );
  
    // -----------------------------
    // CSV rows (SAME columns)
    // -----------------------------
    const csvRows = [
      {
        Strategy: "SUMMARY",
        Symbol: "",
        Side: "",
        Entry_Time: `Start Date: ${startDate}`,
        Exit_Time: `End Date: ${endDate}`,
        Lots: selectedLots,
        Gross_PnL: grossPnL,
        Charges: totalCharges,
        Net_PnL: netPnL,
        State: "",
      },
      {}, // blank row
      {
        Strategy: "Strategy",
        Symbol: "Symbol",
        Side: "Side",
        Entry_Time: "Entry Time",
        Exit_Time: "Exit Time",
        Lots: "Lots",
        Gross_PnL: "Gross P/L",
        Charges: "Charges",
        Net_PnL: "Net P/L",
        State: "State",
      },
    ];
  
    // -----------------------------
    // Trade rows
    // -----------------------------
    allTrades.forEach(trade => {
      csvRows.push({
        Strategy: trade.strategy_name || "",
        Symbol: trade.symbol || "",
        Side: trade.side || "",
        Entry_Time: formatTimestamp(trade.entry_time),
        Exit_Time: trade.exit_time ? formatTimestamp(trade.exit_time) : "",
        Lots: selectedLots,
        Gross_PnL: Math.round(trade.scaled_pnl_value || 0),
        Charges: Math.round(trade.scaled_charges || 0),
        Net_PnL: Math.round(trade.scaled_net_pnl || 0),
        State: trade.state || "",
      });
    });
  
    exportToCSV(csvRows, generateFilename("paper_trades", "csv"));
  
    toast.success(
      "Export Complete",
      `${allTrades.length} paper trades exported`
    );
  }
  
  

  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading paper trades..." />
      </>
    );
  }
  
  /* -------------------------
     Scaled Trades (UI-SCALE)
  -------------------------- */
  
  // 1️⃣ Filter by selected date range (entry_time)
  const filteredOpenTrades = Array.isArray(paperTrades.open)
  ? paperTrades.open.filter(t =>
      isWithinDateRange(t.entry_time, startDate, endDate)
    )
  : [];

  const filteredClosedTrades = Array.isArray(paperTrades.closed)
  ? paperTrades.closed.filter(t =>
      isWithinDateRange(t.entry_time, startDate, endDate)
    )
  : [];

  // 2️⃣ Apply UI lot scaling AFTER filtering
  const scaledOpenTrades = filteredOpenTrades.map(t =>
  scaleTrade(t, selectedLots)
  );

  const scaledClosedTrades = filteredClosedTrades.map(t =>
  scaleTrade(t, selectedLots)
  );
  
  const allTrades = [...scaledOpenTrades, ...scaledClosedTrades];
  const hasTrades = allTrades.length > 0;
  
  
  // Calculate summary stats
  const totalGrossPnL = scaledClosedTrades.reduce(
    (sum, t) => sum + (t.scaled_pnl_value || 0),
    0
  );
  
  const totalNetPnL = scaledClosedTrades.reduce(
    (sum, t) => sum + (t.scaled_net_pnl || 0),
    0
  );
  
 
  const wins = filteredClosedTrades.filter(
    t => (t.pnl_value ?? 0) > 0
  ).length;
  
  const losses = filteredClosedTrades.filter(
    t => (t.pnl_value ?? 0) < 0
  ).length;
  
  const decisiveTrades = wins + losses;
  
  const winRate = decisiveTrades > 0
    ? (wins / decisiveTrades) * 100
    : 0;  
  


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
        <div
            style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                marginBottom: spacing.md,
                gap: spacing.lg
            }}
            >
            {/* Left: Title */}
            <div>
                <h1
                style={{
                    margin: 0,
                    ...typography.displayLarge,
                    color: colors.text.primary
                }}
                >
                📝 Paper Trades
                </h1>
                <p
                style={{
                    margin: 0,
                    marginTop: spacing.xs,
                    ...typography.bodyMedium,
                    color: colors.text.tertiary
                }}
                >
                Simulated trades for testing strategies without risk
                </p>
            </div>

            {/* Right: Controls */}
            <div
            style={{
                display: "flex",
                alignItems: "center",
                gap: spacing.sm
            }}
            >

                {/* Quick Filters */}
                <div style={{ display: "flex", gap: 6 }}>
                {[
                    { id: "TODAY", label: "Today" },
                    { id: "WEEK", label: "This Week" },
                    { id: "MONTH", label: "This Month" },
                    { id: "ALL", label: "All Time" },
                ].map(btn => (
                    <button
                    key={btn.id}
                    onClick={() => applyQuickFilter(btn.id)}
                    style={{
                        padding: "6px 10px",
                        borderRadius: 6,
                        fontSize: 12,
                        fontWeight: 600,
                        border: `1px solid ${colors.border.light}`,
                        background:
                        activeQuickFilter === btn.id
                            ? colors.bg.tertiary
                            : colors.bg.secondary,
                        color: colors.text.primary,
                        cursor: "pointer",
                    }}
                    >
                    {btn.label}
                    </button>
                ))}
                </div>


                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: 12,
                        color: colors.text.tertiary
                    }}
                    >
                    <input
                        type="date"
                        value={startDate}
                        onChange={(e) => {
                            setStartDate(e.target.value);
                            setActiveQuickFilter("CUSTOM");
                          }}                          
                        style={{
                        padding: "6px 8px",
                        borderRadius: 6,
                        border: `1px solid ${colors.border.light}`,
                        background: colors.bg.secondary,
                        color: colors.text.primary
                        }}
                    />

                    <span>→</span>

                    <input
                        type="date"
                        value={endDate}
                        onChange={(e) => setEndDate(e.target.value)}
                        style={{
                        padding: "6px 8px",
                        borderRadius: 6,
                        border: `1px solid ${colors.border.light}`,
                        background: colors.bg.secondary,
                        color: colors.text.primary
                        }}
                    />
                    </div>

                {/* Lots selector */}
                <div
                    style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 12,
                    color: colors.text.tertiary
                    }}
                >
                    <span style={{ fontWeight: 500 }}>Lots</span>
                    <select
                    value={selectedLots}
                    onChange={(e) => setSelectedLots(Number(e.target.value))}
                    style={{
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: `1px solid ${colors.border.light}`,
                        background: colors.bg.secondary,
                        color: colors.text.primary,
                        fontSize: 13,
                        fontWeight: 600,
                        cursor: "pointer"
                    }}
                    >
                    {Array.from({ length: 10 }, (_, i) => i + 1).map((lot) => (
                        <option key={lot} value={lot}>
                        {lot}
                        </option>
                    ))}
                    </select>
                </div>

                {/* Download CSV */}
                <button
                    onClick={handleExportCSV}
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
                    onMouseEnter={(e) =>
                    (e.currentTarget.style.background = colors.bg.tertiary)
                    }
                    onMouseLeave={(e) =>
                    (e.currentTarget.style.background = colors.bg.secondary)
                    }
                >
                    📄 Download CSV
                </button>
                </div>
            
            
            </div>

      </div>

        {/* Summary Stats */}
        <div
        style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: spacing.md,
            marginBottom: spacing.lg
        }}
        >

          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
                Gross P/L
            </div>
            <div style={{
                ...typography.headingLarge,
                fontSize: 24,
                ...typography.mono,
                ...pnlStyle(totalGrossPnL)
            }}>
                {totalGrossPnL > 0 ? '+' : ''}₹{Math.round(totalGrossPnL).toLocaleString('en-IN')}
            </div>
            </Card>

          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
                Net P/L (After Charges)
            </div>
            <div style={{
                ...typography.headingLarge,
                fontSize: 24,
                ...typography.mono,
                ...pnlStyle(totalNetPnL)
            }}>
                {totalNetPnL > 0 ? '+' : ''}₹{Math.round(totalNetPnL).toLocaleString('en-IN')}
            </div>
          </Card>


          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>Win Rate</div>
            <div style={{ 
              ...typography.headingLarge, 
              fontSize: 24,
              color: winRate >= 50 ? colors.profit : colors.loss
            }}>
              {winRate.toFixed(1)}%
            </div>
            <div style={{ ...typography.bodySmall, color: colors.text.tertiary, marginTop: spacing.xs }}>
              {wins}W / {losses}L
            </div>
          </Card>

          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>Total Trades</div>
            <div style={{ ...typography.headingLarge, fontSize: 24, color: colors.text.primary }}>
            {filteredOpenTrades.length + filteredClosedTrades.length}
            </div>
            <div style={{ ...typography.bodySmall, color: colors.text.tertiary, marginTop: spacing.xs }}>
              {filteredOpenTrades.length} open / {filteredClosedTrades.length} closed
            </div>
          </Card>
        </div>
      

      {/* Trades Table */}
      <Card>
        {!hasTrades ? (
          <div style={{ padding: spacing.xxl }}>
            <EmptyState
              icon="📝"
              title="No paper trades yet"
              description="Paper trades will appear here once you switch to Paper Trading mode in Settings and start trading."
            />
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{
              width: "100%",
              borderCollapse: "collapse",
              ...typography.bodyMedium
            }}>
              <thead style={{ background: colors.bg.tertiary }}>
                <tr>
                  <th style={th}>Strategy</th>
                  <th style={th}>Symbol</th>
                  <th style={th}>Side</th>
                  <th style={th}>Entry Time</th>
                  <th style={{ ...th, textAlign: "right" }}>Entry Price</th>
                  <th style={{ ...th, textAlign: "right" }}>SL</th>
                  <th style={{ ...th, textAlign: "right" }}>TP</th>
                  <th style={th}>Exit Time</th>
                  <th style={{ ...th, textAlign: "right" }}>Exit Price</th>
                  <th style={th}>Exit Reason</th>
                  <th style={{ ...th, textAlign: "right" }}>P/L Points</th>
                  <th style={{ ...th, textAlign: "right" }}>P/L Value</th>
                  <th style={{ ...th, textAlign: "right" }}>Charges</th>
                  <th style={{ ...th, textAlign: "right" }}>Net P/L</th>
                  <th style={{ ...th, textAlign: "center" }}>State</th>

                </tr>
              </thead>
              <tbody>
                {allTrades.map((trade, i) => {
                  const pnlValue = trade.scaled_pnl_value || 0;
                  const charges = trade.scaled_charges || 0;
                  const netPnl = trade.scaled_net_pnl || 0;
                  
                  const pnlPoints = trade.pnl_points || 0;

                  return (
                    <tr 
                      key={trade.paper_trade_id || i}
                      style={{ 
                        background: i % 2 ? colors.bg.secondary : colors.bg.primary,
                        borderTop: `1px solid ${colors.border.dark}`,
                        transition: "background 0.15s ease"
                      }}
                      onMouseEnter={(e) => e.currentTarget.style.background = colors.bg.tertiary}
                      onMouseLeave={(e) => e.currentTarget.style.background = i % 2 ? colors.bg.secondary : colors.bg.primary}
                    >
                      <td style={td}>{trade.strategy_name || "—"}</td>
                      <td style={{ ...td, ...typography.mono, fontWeight: 600 }}>
                        {trade.symbol || "—"}
                      </td>
                      <td style={td}>
                        <span style={{
                          padding: "2px 8px",
                          borderRadius: 4,
                          background: trade.side === "CE" ? colors.successBg : colors.lossBg,
                          color: trade.side === "CE" ? colors.success : colors.loss,
                          fontSize: 11,
                          fontWeight: 600
                        }}>
                          {trade.side || "—"}
                        </span>
                      </td>
                      <td style={{ ...td, ...typography.mono, fontSize: 11 }}>
                        {formatTimestamp(trade.entry_time)}
                      </td>
                      <td style={{ ...td, ...typography.mono, textAlign: "right" }}>
                        {trade.entry_price ? trade.entry_price.toFixed(2) : "—"}
                      </td>
                      <td style={{ ...td, ...typography.mono, textAlign: "right", color: colors.text.tertiary }}>
                        {trade.sl_price ? trade.sl_price.toFixed(2) : "—"}
                      </td>
                      <td style={{ ...td, ...typography.mono, textAlign: "right", color: colors.text.tertiary }}>
                        {trade.tp_price ? trade.tp_price.toFixed(2) : "—"}
                      </td>
                      <td style={{ ...td, ...typography.mono, fontSize: 11 }}>
                        {trade.exit_time ? formatTimestamp(trade.exit_time) : "—"}
                      </td>
                      <td style={{ ...td, ...typography.mono, textAlign: "right" }}>
                        {trade.exit_price ? trade.exit_price.toFixed(2) : "—"}
                      </td>
                      <td style={td}>
                        {trade.exit_reason ? (
                          <span style={{
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: trade.exit_reason === "TP" ? colors.successBg : colors.lossBg,
                            fontSize: 11,
                            fontWeight: 600
                          }}>
                            {trade.exit_reason}
                          </span>
                        ) : "—"}
                      </td>
                        {/* P/L Points */}
                        <td style={{ 
                        ...td, 
                        ...typography.mono, 
                        textAlign: "right", 
                        ...pnlStyle(pnlPoints) 
                        }}>
                        {trade.state === "CLOSED" && pnlPoints !== 0
                            ? `${pnlPoints > 0 ? '+' : ''}${pnlPoints.toFixed(2)}`
                            : "—"}
                        </td>

                        {/* P/L Value (Gross) */}
                        <td style={{
                        ...td,
                        ...typography.mono,
                        textAlign: "right",
                        ...pnlStyle(pnlValue),
                        }}>
                        {trade.state === "CLOSED"
                            ? `${pnlValue > 0 ? '+' : ''}₹${Math.round(pnlValue).toLocaleString('en-IN')}`
                            : "—"}
                        </td>

                        {/* Charges */}
                        <td style={{
                        ...td,
                        ...typography.mono,
                        textAlign: "right",
                        color: colors.text.tertiary
                        }}>
                        {trade.state === "CLOSED"
                            ? `₹${Math.round(charges).toLocaleString('en-IN')}`
                            : "—"}

                        </td>

                        {/* Net P/L */}
                        <td style={{
                        ...td,
                        ...typography.mono,
                        textAlign: "right",
                        fontWeight: 600,
                        color: trade.net_pnl > 0 ? colors.profit : colors.loss,
                        background: trade.state === "CLOSED"
                            ? (trade.net_pnl > 0 ? colors.profitBg : colors.lossBg)
                            : "transparent"
                        }}>
                        {trade.state === "CLOSED"
                            ? `${netPnl > 0 ? '+' : ''}₹${Math.round(netPnl).toLocaleString('en-IN')}`
                            : "—"}

                        </td>

                        {/* State */}
                        <td style={{ ...td, textAlign: "center" }}>
                        <StatusBadge
                            type={trade.state === "OPEN" ? "open" : "closed"}
                            text={trade.state || "UNKNOWN"}
                        />
                        </td>


                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/* -------------------------
   Styles
-------------------------- */

const th = {
  padding: "12px 12px",
  textAlign: "left",
  ...typography.label,
  color: colors.text.muted,
  borderBottom: `2px solid ${colors.border.light}`,
  fontWeight: 600
};

const td = { 
  padding: "12px 12px",
  ...typography.bodyMedium
};
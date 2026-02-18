import { useEffect, useState, useMemo, useCallback } from "react";
import { LoadingAnimations, FullPageLoader, EmptyState } from "../components/LoadingStates";
import { useToast } from "../components/ToastNotifications";
import { exportToCSV, generateFilename } from "../utils/export";
import { getApiBase } from "../api/base";

/* ─────────────────────────────────────────────
   Design tokens
───────────────────────────────────────────── */

const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24 };

const typography = {
  headingLarge:  { fontSize: 18, fontWeight: 600, lineHeight: 1.4 },
  bodyMedium:    { fontSize: 13, fontWeight: 400, lineHeight: 1.5 },
  bodySmall:     { fontSize: 12, fontWeight: 400, lineHeight: 1.4 },
  label:         { fontSize: 11, fontWeight: 500, lineHeight: 1.3, letterSpacing: "0.5px", textTransform: "uppercase" },
  mono:          { fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontVariantNumeric: "tabular-nums" },
};

const colors = {
  profit:    "#10b981",  profitBg:  "rgba(16, 185, 129, 0.10)",
  loss:      "#ef4444",  lossBg:    "rgba(239, 68, 68, 0.10)",
  neutral:   "#6b7280",
  primary:   "#2563eb",  primaryBg: "rgba(37, 99, 235, 0.12)",
  success:   "#059669",  successBg: "rgba(5, 150, 105, 0.12)",
  warning:   "#d97706",  warningBg: "rgba(217, 119, 6, 0.12)",
  bg:        { primary: "#020817", secondary: "#0f172a", tertiary: "#1e293b" },
  border:    { light: "#334155", dark: "#1e293b" },
  text:      { primary: "#f8fafc", secondary: "#cbd5e1", tertiary: "#94a3b8", muted: "#64748b" },
};

/* ─────────────────────────────────────────────
   Table style constants
───────────────────────────────────────────── */
const TH = {
  padding: "11px 12px",
  textAlign: "left",
  ...typography.label,
  color: colors.text.muted,
  borderBottom: `2px solid ${colors.border.light}`,
  fontWeight: 600,
  whiteSpace: "nowrap",
};
const TD = { padding: "10px 12px", ...typography.bodyMedium };

/* ─────────────────────────────────────────────
   Helpers
───────────────────────────────────────────── */

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

// Display exactly what the backend sends — mapped to friendly names where known.
const STRATEGY_DISPLAY = {
  "SCALP_V1": "SCALP V1",
  "SCALP V1": "SCALP V1",
  "1M_SCALP":  "SCALP V1",  // legacy backend ID — old name for SCALP strategy
  "BB_V1":     "BB",
  "BB":        "BB",
};

function displayStrategyName(rawName) {
  if (!rawName) return "—";
  return STRATEGY_DISPLAY[rawName] ?? rawName;
}

// All IDs that map to SCALP get the lot-multiplier treatment.
const SCALP_STRATEGY_IDS = new Set(["SCALP_V1", "SCALP V1", "1M_SCALP"]);
const isScalpStrategy = (name) => SCALP_STRATEGY_IDS.has(name || "");

/* ─────────────────────────────────────────────
   NSE Index Options charges (Zerodha, post Apr 1 2026)
   STT updated to 0.15% on sell premium (was 0.0625%)
   Buyer-side STT: nil (only seller pays STT)
   entry/exit price in ₹, qty = total shares
───────────────────────────────────────────── */
function calcCharges(entryPrice, exitPrice, qty) {
  if (!entryPrice || !exitPrice || !qty) return 0;
  const buyVal  = entryPrice * qty;
  const sellVal = exitPrice  * qty;
  const turnover = buyVal + sellVal;
  const brokerage      = 40;                          // ₹20 × 2 legs
  const stt            = sellVal  * 0.0015;           // 0.15% sell premium (post Apr 1 2026)
  const exchangeCharge = turnover * 0.00053;          // 0.053% NSE F&O
  const gst            = (brokerage + exchangeCharge) * 0.18;
  const sebi           = turnover  * 0.000001;        // ₹10/crore
  const stampDuty      = buyVal    * 0.00003;         // 0.003% buy side
  return Math.round((brokerage + stt + exchangeCharge + gst + sebi + stampDuty) * 100) / 100;
}

function formatTimestamp(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  if (isToday) {
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }
  return d.toLocaleString("en-IN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

const pnlStyle = (v) => ({
  color: v > 0 ? colors.profit : v < 0 ? colors.loss : colors.neutral,
  fontWeight: 600,
});

/* ─────────────────────────────────────────────
   Small reusable components
───────────────────────────────────────────── */

function Card({ children, style, elevated }) {
  return (
    <div style={{
      background:  elevated ? colors.bg.tertiary : colors.bg.secondary,
      border:      `1px solid ${colors.border.light}`,
      borderRadius: 8,
      boxShadow:   elevated ? "0 4px 6px -1px rgba(0,0,0,0.3)" : "0 1px 3px rgba(0,0,0,0.2)",
      ...style,
    }}>
      {children}
    </div>
  );
}

function SideBadge({ side }) {
  if (!side) return <span style={{ color: colors.text.muted }}>—</span>;
  const isCE = side === "CE";
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600,
      background: isCE ? colors.successBg : colors.lossBg,
      color:      isCE ? colors.success    : colors.loss,
    }}>
      {side}
    </span>
  );
}

function StrategyChip({ name }) {
  const isBB = (name || "").toUpperCase().includes("BB");
  return (
    <span style={{
      padding: "2px 7px", borderRadius: 4, fontSize: 10, fontWeight: 600,
      background: isBB ? colors.primaryBg : colors.warningBg,
      color:      isBB ? colors.primary   : colors.warning,
      letterSpacing: "0.3px", textTransform: "uppercase",
    }}>
      {displayStrategyName(name)}
    </span>
  );
}

function ExitReasonBadge({ reason }) {
  if (!reason) return <span style={{ color: colors.text.muted }}>—</span>;
  const isGood = reason === "TP" || reason === "TARGET";
  return (
    <span style={{
      padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
      background: isGood ? colors.successBg : colors.lossBg,
      color:      isGood ? colors.success   : colors.loss,
    }}>
      {reason}
    </span>
  );
}

/* ─────────────────────────────────────────────
   Strategy filter tabs
───────────────────────────────────────────── */

function StrategyTabs({ options, active, onChange }) {
  return (
    <div style={{
      display: "inline-flex", gap: 3,
      background: colors.bg.secondary,
      padding: 3, borderRadius: 8,
      border: `1px solid ${colors.border.dark}`,
    }}>
      {options.map((opt) => {
        const isActive = opt.value === active;
        return (
          <button key={opt.value} onClick={() => onChange(opt.value)}
            style={{
              padding: "6px 16px", borderRadius: 5, border: "none",
              background: isActive ? colors.bg.tertiary : "transparent",
              color:      isActive ? colors.text.primary : colors.text.muted,
              fontSize: 12, fontWeight: isActive ? 600 : 400, cursor: "pointer",
              boxShadow: isActive ? "0 1px 3px rgba(0,0,0,0.3)" : "none",
              transition: "all 0.15s ease",
              borderBottom: isActive ? `2px solid ${colors.primary}` : "2px solid transparent",
            }}
          >
            {opt.label}
            {opt.count != null && (
              <span style={{
                marginLeft: 6, fontSize: 10, padding: "1px 5px", borderRadius: 10,
                background: isActive ? colors.primaryBg : "rgba(255,255,255,0.06)",
                color:      isActive ? colors.primary   : colors.text.muted,
              }}>
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────
   Lot multiplier control (SCALP only)
───────────────────────────────────────────── */

function LotMultiplier({ value, onChange }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: spacing.sm,
      padding: "6px 14px", borderRadius: 8,
      background: colors.bg.secondary,
      border: `1px solid ${colors.border.light}`,
    }}>
      <span style={{ fontSize: 11, color: colors.text.muted, fontWeight: 500, whiteSpace: "nowrap" }}>
        Simulate lots:
      </span>
      <button onClick={() => onChange(Math.max(1, value - 1))}
        style={{ ...btnBase, width: 22, height: 22, borderRadius: 4, fontSize: 14 }}>−</button>
      <span style={{ ...typography.mono, fontSize: 14, fontWeight: 700, color: colors.text.primary, minWidth: 16, textAlign: "center" }}>
        {value}
      </span>
      <button onClick={() => onChange(Math.min(10, value + 1))}
        style={{ ...btnBase, width: 22, height: 22, borderRadius: 4, fontSize: 14 }}>+</button>
      {value > 1 && (
        <span style={{ fontSize: 10, color: colors.primary, fontWeight: 500 }}>
          ×{value} applied to P&L
        </span>
      )}
    </div>
  );
}

const btnBase = {
  background: colors.bg.tertiary, border: `1px solid ${colors.border.light}`,
  color: colors.text.secondary, cursor: "pointer", display: "flex",
  alignItems: "center", justifyContent: "center", fontWeight: 700,
  transition: "background 0.1s",
};

/* ─────────────────────────────────────────────
   LTP distance display (for open trades)
───────────────────────────────────────────── */

function LtpCell({ ltp, entryPrice, slPrice, tpPrice, isOpen }) {
  if (!isOpen || ltp == null) {
    return <span style={{ color: colors.text.muted }}>—</span>;
  }

  const toSl = slPrice != null ? (ltp - slPrice).toFixed(2) : null;
  const toTp = tpPrice != null ? (tpPrice - ltp).toFixed(2) : null;
  const profit = entryPrice != null ? ltp - entryPrice : null;
  const ltpColor = profit == null ? colors.text.primary
    : profit > 0 ? colors.profit
    : profit < 0 ? colors.loss
    : colors.neutral;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ ...typography.mono, fontSize: 13, fontWeight: 700, color: ltpColor }}>
        {ltp.toFixed(2)}
      </span>
      <div style={{ display: "flex", gap: 6 }}>
        {toSl != null && (
          <span style={{ fontSize: 9, color: Number(toSl) < 0 ? colors.loss : colors.text.muted, fontWeight: 500 }}>
            SL {Number(toSl) > 0 ? "+" : ""}{toSl}
          </span>
        )}
        {toTp != null && (
          <span style={{ fontSize: 9, color: Number(toTp) < 0 ? colors.profit : colors.text.muted, fontWeight: 500 }}>
            TP {Number(toTp) > 0 ? "+" : ""}{toTp}
          </span>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Date range helpers
───────────────────────────────────────────── */

// Returns { from: Date, to: Date } for a given preset
function getPresetRange(preset) {
  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()); // midnight today

  if (preset === "today") {
    return { from: today, to: now };
  }
  if (preset === "week") {
    // Monday of current week
    const day = today.getDay(); // 0=Sun
    const diffToMon = day === 0 ? -6 : 1 - day;
    const monday = new Date(today);
    monday.setDate(today.getDate() + diffToMon);
    return { from: monday, to: now };
  }
  if (preset === "month") {
    const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    return { from: firstOfMonth, to: now };
  }
  return null; // "all" or "custom"
}

// Returns true if trade's entry_time falls within [from, to]
function inDateRange(tradeUnixSec, from, to) {
  if (!from && !to) return true;
  const t = tradeUnixSec * 1000; // ms
  if (from && t < from.getTime()) return false;
  if (to   && t > to.getTime())   return false;
  return true;
}

// Format a Date to YYYY-MM-DD for <input type="date"> value
function toInputDate(d) {
  if (!d) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const DATE_PRESETS = [
  { value: "today",  label: "Today"      },
  { value: "week",   label: "This Week"  },
  { value: "month",  label: "This Month" },
  { value: "custom", label: "Custom"     },
  { value: "all",    label: "All Time"   },
];

/* ─────────────────────────────────────────────
   DateRangePicker component
───────────────────────────────────────────── */

function DateRangePicker({ preset, customFrom, customTo, onPreset, onCustomFrom, onCustomTo }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" }}>

      {/* Preset buttons */}
      <div style={{
        display: "inline-flex", gap: 2,
        background: colors.bg.secondary, padding: 3, borderRadius: 8,
        border: `1px solid ${colors.border.dark}`,
      }}>
        {DATE_PRESETS.map((p) => {
          const isActive = p.value === preset;
          return (
            <button key={p.value} onClick={() => onPreset(p.value)}
              style={{
                padding: "5px 13px", borderRadius: 5, border: "none",
                background:  isActive ? colors.bg.tertiary : "transparent",
                color:       isActive ? colors.text.primary : colors.text.muted,
                fontSize: 12, fontWeight: isActive ? 600 : 400, cursor: "pointer",
                borderBottom: isActive ? `2px solid ${colors.warning}` : "2px solid transparent",
                transition: "all 0.15s ease",
              }}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      {/* Custom date inputs — only when preset === "custom" */}
      {preset === "custom" && (
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
          <input
            type="date"
            value={customFrom}
            onChange={(e) => onCustomFrom(e.target.value)}
            style={dateInputStyle}
          />
          <span style={{ color: colors.text.muted, fontSize: 12 }}>→</span>
          <input
            type="date"
            value={customTo}
            onChange={(e) => onCustomTo(e.target.value)}
            style={dateInputStyle}
          />
        </div>
      )}
    </div>
  );
}

const dateInputStyle = {
  padding: "5px 10px",
  borderRadius: 6,
  border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary,
  color: colors.text.primary,
  fontSize: 12,
  fontFamily: "'Inter', sans-serif",
  cursor: "pointer",
  outline: "none",
};

/* ─────────────────────────────────────────────
   PaperTrades
───────────────────────────────────────────── */

export default function PaperTrades() {
  const toast = useToast();

  const [loading,     setLoading]     = useState(true);
  const [paperTrades, setPaperTrades] = useState({ open: [], closed: [] });
  const [ltpMap,      setLtpMap]      = useState({});
  const [stratFilter, setStratFilter] = useState("ALL");
  const [scalpLots,   setScalpLots]   = useState(1);

  // Date range filter
  const [datePreset,  setDatePreset]  = useState("today");
  const [customFrom,  setCustomFrom]  = useState(() => toInputDate(new Date()));
  const [customTo,    setCustomTo]    = useState(() => toInputDate(new Date()));

  /* ── Data loading ─────────────────────────── */
  const loadPaperTrades = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/paper_trades`);
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      setPaperTrades({
        open:   Array.isArray(data?.open)   ? data.open   : [],
        closed: Array.isArray(data?.closed) ? data.closed : [],
      });
    } catch {
      setPaperTrades({ open: [], closed: [] });
      toast.error("Load Failed", "Could not load paper trades");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const loadLtp = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/ltp_snapshot`);
      if (!res.ok) return;
      const data = await res.json();
      // ltp_snapshot returns { symbol: price, ... }
      setLtpMap(data || {});
    } catch { /* LTP is best-effort */ }
  }, []);

  useEffect(() => {
    loadPaperTrades();
    loadLtp();
    const t1 = setInterval(loadPaperTrades, 10_000);
    const t2 = setInterval(loadLtp, 3_000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [loadPaperTrades, loadLtp]);

  /* ── Derived ──────────────────────────────── */
  const allTrades = useMemo(() =>
    [...paperTrades.open, ...paperTrades.closed],
    [paperTrades]
  );

  // Discover unique CANONICAL strategy names (e.g. "SCALP V1", "BB")
  // Multiple raw IDs (1M_SCALP, SCALP_V1) collapse into one canonical name
  const strategies = useMemo(() => {
    const seen = new Set();
    allTrades.forEach((t) => {
      if (t.strategy_name) seen.add(displayStrategyName(t.strategy_name));
    });
    return Array.from(seen).sort();
  }, [allTrades]);

  // Match trade against a canonical strategy filter name
  const matchesStrategy = (trade, canonicalName) =>
    displayStrategyName(trade.strategy_name) === canonicalName;

  // Compute active date range
  const activeDateRange = useMemo(() => {
    if (datePreset === "all") return null;
    if (datePreset === "custom") {
      const from = customFrom ? new Date(customFrom + "T00:00:00") : null;
      const to   = customTo   ? new Date(customTo   + "T23:59:59") : null;
      return (from || to) ? { from, to } : null;
    }
    return getPresetRange(datePreset);
  }, [datePreset, customFrom, customTo]);

  // Date-filtered trades
  const dateFiltered = useMemo(() =>
    allTrades.filter((t) =>
      inDateRange(t.entry_time, activeDateRange?.from, activeDateRange?.to)
    ),
    [allTrades, activeDateRange]
  );

  // Build tab options — one tab per canonical name, count reflects date window
  const tabOptions = useMemo(() => [
    { value: "ALL", label: "All", count: dateFiltered.length },
    ...strategies.map((s) => ({
      value: s,   // canonical name IS the value now
      label: s,
      count: dateFiltered.filter((t) => matchesStrategy(t, s)).length,
    })),
  ], [dateFiltered, strategies]);

  // Final filtered trades — match by canonical name
  const filtered = useMemo(() =>
    stratFilter === "ALL"
      ? dateFiltered
      : dateFiltered.filter((t) => matchesStrategy(t, stratFilter)),
    [dateFiltered, stratFilter]
  );

  // stratFilter is now a canonical display name ("SCALP V1", "BB") or "ALL"
  const showingScalp = stratFilter === "ALL" || stratFilter === "SCALP V1";
  // Side column is only meaningful for SCALP; hidden when viewing BB-only
  const showSideCol  = showingScalp;
  // Show lot multiplier when SCALP trades actually exist in data
  const hasScalpTrades = allTrades.some((t) => isScalpStrategy(t.strategy_name));
  const showLotMultiplier = showingScalp && hasScalpTrades;

  // Summary stats (filtered closed trades)
  const filteredClosed = filtered.filter((t) => t.state === "CLOSED");

  const { grossPnL, netPnL, totalCharges, wins, losses, winRate } = useMemo(() => {
    let gross = 0, net = 0, charges = 0;
    filteredClosed.forEach((t) => {
      const base    = t.pnl_value || 0;
      const lots    = isScalpStrategy(t.strategy_name) ? scalpLots : 1;
      const qty     = (t.qty || 1) * lots;
      const chg     = calcCharges(t.entry_price, t.exit_price, qty);
      gross   += base * lots;
      charges += chg;
      net     += base * lots - chg;
    });
    const w  = filteredClosed.filter((t) => (t.pnl_value || 0) > 0).length;
    const l  = filteredClosed.filter((t) => (t.pnl_value || 0) < 0).length;
    const wr = filteredClosed.length > 0 ? (w / filteredClosed.length) * 100 : 0;
    return { grossPnL: gross, netPnL: net, totalCharges: charges, wins: w, losses: l, winRate: wr };
  }, [filteredClosed, scalpLots]);

  /* ── Export ───────────────────────────────── */
  function handleExportCSV() {
    if (filtered.length === 0) { toast.warning("No Data", "No trades to export"); return; }
    const rows = filtered.map((t) => {
      const scalp   = isScalpStrategy(t.strategy_name);
      const lots    = scalp ? scalpLots : 1;
      const qty     = (t.qty || 1) * lots;
      const grossV  = (t.pnl_value || 0) * lots;
      const chg     = t.state === "CLOSED" ? calcCharges(t.entry_price, t.exit_price, qty) : 0;
      return {
        "Strategy":    displayStrategyName(t.strategy_name),
        "Symbol":      t.symbol || "",
        "Side":        scalp ? (t.side || "") : "N/A",
        "Entry Time":  formatTimestamp(t.entry_time),
        "Entry Price": t.entry_price || 0,
        "SL":          t.sl_price || 0,
        "TP":          t.tp_price || 0,
        "Exit Time":   t.exit_time ? formatTimestamp(t.exit_time) : "",
        "Exit Price":  t.exit_price || "",
        "Exit Reason": t.exit_reason || "",
        "Lots/Qty":    scalp ? scalpLots : (t.qty || 1),
        "Gross P/L":   grossV,
        "Charges":     chg ? `-${chg.toFixed(2)}` : "",
        "Net P/L":     grossV - chg,
        "State":       t.state || "",
      };
    });
    exportToCSV(rows, generateFilename("paper_trades", "csv"));
    toast.success("Export Complete", `${filtered.length} trades exported`);
  }

  /* ── Loading screen ───────────────────────── */
  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading paper trades…" />
      </>
    );
  }

  /* ─────────────────────────────────────────── */
  return (
    <div style={{
      padding: spacing.xxl,
      background: colors.bg.primary,
      color: colors.text.primary,
      minHeight: "100vh",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      paddingBottom: 56, // room for StatusBar
    }}>

      {/* ── Page header ─────────────────────── */}
      <div style={{ marginBottom: spacing.xl }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, color: colors.text.primary }}>
              Paper Trades
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: colors.text.muted }}>
              Simulated trades — no real money at risk
            </p>
          </div>

          {filtered.length > 0 && (
            <button onClick={handleExportCSV}
              style={{
                padding: "8px 16px", borderRadius: 6,
                border:  `1px solid ${colors.border.light}`,
                background: colors.bg.secondary, color: colors.text.primary,
                fontSize: 13, fontWeight: 600, cursor: "pointer",
                display: "flex", alignItems: "center", gap: 6,
                transition: "background 0.2s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = colors.bg.tertiary)}
              onMouseLeave={(e) => (e.currentTarget.style.background = colors.bg.secondary)}
            >
              📄 Download CSV
            </button>
          )}
        </div>
      </div>

      {/* ── Filters row ─────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", gap: spacing.md,
        flexWrap: "wrap", marginBottom: spacing.xl,
      }}>

        {/* Date range picker — always shown */}
        <DateRangePicker
          preset={datePreset}
          customFrom={customFrom}
          customTo={customTo}
          onPreset={setDatePreset}
          onCustomFrom={setCustomFrom}
          onCustomTo={setCustomTo}
        />

        {/* Divider */}
        <div style={{ width: 1, height: 24, background: colors.border.light, flexShrink: 0 }} />

        {/* Strategy tabs */}
        {strategies.length > 0 && (
          <StrategyTabs
            options={tabOptions}
            active={stratFilter}
            onChange={(v) => setStratFilter(v)}
          />
        )}

        {/* SCALP lot multiplier */}
        {showLotMultiplier && (
          <LotMultiplier value={scalpLots} onChange={setScalpLots} />
        )}
      </div>

      {/* Active window label */}
      {activeDateRange && (
        <div style={{
          marginBottom: spacing.md, fontSize: 11,
          color: colors.text.muted, display: "flex", alignItems: "center", gap: 6,
        }}>
          <span>📅</span>
          <span>
            Showing trades from{" "}
            <span style={{ color: colors.text.secondary, fontWeight: 500 }}>
              {activeDateRange.from
                ? activeDateRange.from.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
                : "the beginning"}
            </span>
            {" "}to{" "}
            <span style={{ color: colors.text.secondary, fontWeight: 500 }}>
              {activeDateRange.to
                ? activeDateRange.to.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
                : "now"}
            </span>
            {" "}·{" "}
            <span style={{ color: colors.primary }}>{filtered.length} trade{filtered.length !== 1 ? "s" : ""}</span>
          </span>
        </div>
      )}

      {/* ── Summary stats ───────────────────── */}
      {/* ── Summary stats — shown whenever there are any trades in view ── */}
      {filtered.length > 0 && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: spacing.md, marginBottom: spacing.xl,
        }}>
          {/* Gross P/L */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
              Gross P&L <span style={{ opacity: 0.5, textTransform: "none", fontSize: 10 }}>(before charges)</span>
            </div>
            <div style={{ fontSize: 24, fontWeight: 700, ...typography.mono, ...pnlStyle(grossPnL) }}>
              {grossPnL !== 0 ? `${grossPnL > 0 ? "+" : ""}₹${Math.round(grossPnL).toLocaleString("en-IN")}` : "—"}
            </div>
            <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3 }}>
              {filteredClosed.length} closed · {filtered.filter(t => t.state === "OPEN").length} open
            </div>
          </Card>

          {/* Charges */}
          {filteredClosed.length > 0 && (
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
                Charges <span style={{ opacity: 0.5, textTransform: "none", fontSize: 10 }}>(est.)</span>
              </div>
              <div style={{ fontSize: 24, fontWeight: 700, ...typography.mono, color: colors.loss }}>
                −₹{Math.round(totalCharges).toLocaleString("en-IN")}
              </div>
              <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 3 }}>
                Brokerage · STT · GST · Exchange
              </div>
            </Card>
          )}

          {/* Net P/L — scaled by lot multiplier; only show when SCALP trades visible */}
          {showLotMultiplier && (
            <Card elevated style={{ padding: spacing.lg, border: scalpLots > 1 ? `1px solid ${colors.primary}50` : undefined }}>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>
                Net P&L
                {scalpLots > 1 && <span style={{ marginLeft: 5, color: colors.primary }}>×{scalpLots} lots</span>}
              </div>
              <div style={{ fontSize: 24, fontWeight: 700, ...typography.mono, ...pnlStyle(netPnL) }}>
                {netPnL !== 0 ? `${netPnL > 0 ? "+" : ""}₹${Math.round(netPnL).toLocaleString("en-IN")}` : "—"}
              </div>
              {filteredClosed.length > 0 && (
                <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3, ...typography.mono }}>
                  after −₹{Math.round(totalCharges).toLocaleString("en-IN")} charges
                </div>
              )}
            </Card>
          )}

          {/* Net P&L when lot multiplier not shown (BB-only or no SCALP trades) */}
          {!showLotMultiplier && filteredClosed.length > 0 && (
            <Card elevated style={{ padding: spacing.lg }}>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>Net P&L</div>
              <div style={{ fontSize: 24, fontWeight: 700, ...typography.mono, ...pnlStyle(netPnL) }}>
                {netPnL !== 0 ? `${netPnL > 0 ? "+" : ""}₹${Math.round(netPnL).toLocaleString("en-IN")}` : "—"}
              </div>
              <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 3, ...typography.mono }}>
                after −₹{Math.round(totalCharges).toLocaleString("en-IN")} charges
              </div>
            </Card>
          )}

          {/* Win Rate — only meaningful when there are closed trades */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>Win Rate</div>
            {filteredClosed.length > 0 ? (
              <>
                <div style={{ fontSize: 24, fontWeight: 700, color: winRate >= 50 ? colors.profit : colors.loss }}>
                  {winRate.toFixed(1)}%
                </div>
                <div style={{ fontSize: 12, color: colors.text.tertiary, marginTop: 3 }}>
                  {wins}W / {losses}L
                </div>
              </>
            ) : (
              <div style={{ fontSize: 20, fontWeight: 700, color: colors.text.muted }}>—</div>
            )}
          </Card>

          {/* Total Trades */}
          <Card elevated style={{ padding: spacing.lg }}>
            <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.sm }}>Trades</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: colors.text.primary }}>
              {filtered.length}
            </div>
            <div style={{ fontSize: 12, color: colors.text.tertiary, marginTop: 3 }}>
              {filteredClosed.length} closed · {filtered.filter(t => t.state === "OPEN").length} open
            </div>
          </Card>
        </div>
      )}

      {/* ── Trades table ─────────────────────── */}
      <Card>
        {filtered.length === 0 ? (
          <div style={{ padding: spacing.xxl }}>
            <EmptyState
              icon="📋"
              title={
                datePreset === "today" && stratFilter === "ALL"
                  ? "No trades today"
                  : stratFilter !== "ALL"
                  ? `No ${stratFilter} trades in this period`
                  : "No trades in this period"
              }
              description={
                datePreset === "today"
                  ? "Paper trades taken today will appear here. Switch to a wider date range to see historical trades."
                  : "No paper trades match the selected date range and strategy filter."
              }
            />
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
              <thead style={{ background: colors.bg.tertiary }}>
                <tr>
                  {stratFilter === "ALL" && <th style={TH}>Strategy</th>}
                  <th style={TH}>Symbol</th>
                  {showSideCol && <th style={{ ...TH, textAlign: "center" }}>Side</th>}
                  <th style={TH}>Entry Time</th>
                  <th style={{ ...TH, textAlign: "right" }}>Entry</th>
                  <th style={{ ...TH, textAlign: "right" }}>SL</th>
                  <th style={{ ...TH, textAlign: "right" }}>TP</th>
                  <th style={{ ...TH, textAlign: "right" }}>LTP</th>
                  <th style={TH}>Exit Time</th>
                  <th style={{ ...TH, textAlign: "right" }}>Exit</th>
                  <th style={TH}>Reason</th>
                  <th style={{ ...TH, textAlign: "center" }}>Lots / Qty</th>
                  <th style={{ ...TH, textAlign: "right" }}>Gross P/L</th>
                  <th style={{ ...TH, textAlign: "right", color: colors.loss }}>Charges</th>
                  <th style={{ ...TH, textAlign: "right", color: colors.primary }}>Net P/L</th>
                  <th style={{ ...TH, textAlign: "center" }}>State</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((trade, i) => {
                  const isScalp   = isScalpStrategy(trade.strategy_name);
                  const isOpen    = trade.state === "OPEN";
                  const isClosed  = trade.state === "CLOSED";
                  const ltp       = ltpMap[normalizeSymbol(trade.symbol)];
                  const lots      = isScalp ? scalpLots : 1;
                  const rowQty    = (trade.qty || 1) * lots;

                  // Gross = pnl_value × lots
                  const grossVal  = (trade.pnl_value  || 0) * lots;
                  const grossPts  = (trade.pnl_points || 0) * lots;
                  // Charges — only on closed trades with known entry/exit
                  const rowCharges = isClosed
                    ? calcCharges(trade.entry_price, trade.exit_price, rowQty)
                    : 0;
                  // Net = gross − charges
                  const netVal    = grossVal - rowCharges;

                  return (
                    <tr key={trade.paper_trade_id || i}
                      style={{
                        background:  i % 2 ? colors.bg.secondary : colors.bg.primary,
                        borderTop:   `1px solid ${colors.border.dark}`,
                        transition:  "background 0.15s ease",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = colors.bg.tertiary)}
                      onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 ? colors.bg.secondary : colors.bg.primary)}
                    >
                      {/* Strategy chip — only in ALL view */}
                      {stratFilter === "ALL" && (
                        <td style={TD}><StrategyChip name={trade.strategy_name} /></td>
                      )}

                      {/* Symbol */}
                      <td style={{ ...TD, ...typography.mono, fontWeight: 600, color: colors.text.primary, whiteSpace: "nowrap" }}>
                        {trade.symbol || "—"}
                      </td>

                      {/* Side — hidden for BB-only view */}
                      {showSideCol && (
                        <td style={{ ...TD, textAlign: "center" }}>
                          {isScalp ? <SideBadge side={trade.side} /> : <span style={{ color: colors.text.muted }}>—</span>}
                        </td>
                      )}

                      {/* Entry time */}
                      <td style={{ ...TD, ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>
                        {formatTimestamp(trade.entry_time)}
                      </td>

                      {/* Entry price */}
                      <td style={{ ...TD, ...typography.mono, textAlign: "right" }}>
                        {trade.entry_price != null ? trade.entry_price.toFixed(2) : "—"}
                      </td>

                      {/* SL */}
                      <td style={{ ...TD, ...typography.mono, textAlign: "right", color: colors.loss }}>
                        {trade.sl_price != null ? trade.sl_price.toFixed(2) : "—"}
                      </td>

                      {/* TP */}
                      <td style={{ ...TD, ...typography.mono, textAlign: "right", color: colors.profit }}>
                        {trade.tp_price != null ? trade.tp_price.toFixed(2) : "—"}
                      </td>

                      {/* LTP — live for open trades */}
                      <td style={{ ...TD, textAlign: "right" }}>
                        <LtpCell
                          ltp={ltp}
                          entryPrice={trade.entry_price}
                          slPrice={trade.sl_price}
                          tpPrice={trade.tp_price}
                          isOpen={isOpen}
                        />
                      </td>

                      {/* Exit time */}
                      <td style={{ ...TD, ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>
                        {trade.exit_time ? formatTimestamp(trade.exit_time) : "—"}
                      </td>

                      {/* Exit price */}
                      <td style={{ ...TD, ...typography.mono, textAlign: "right" }}>
                        {trade.exit_price != null ? Number(trade.exit_price).toFixed(2) : "—"}
                      </td>

                      {/* Exit reason */}
                      <td style={TD}>
                        <ExitReasonBadge reason={trade.exit_reason} />
                      </td>

                      {/* Lots / Qty */}
                      <td style={{ ...TD, textAlign: "center" }}>
                        {isScalp ? (
                          <span style={{
                            ...typography.mono, fontSize: 12, fontWeight: 700,
                            color: scalpLots > 1 ? colors.primary : colors.text.secondary,
                          }}>
                            {scalpLots}{scalpLots > 1 && <span style={{ fontSize: 9, color: colors.primary, marginLeft: 2 }}>×</span>}
                          </span>
                        ) : (
                          <span style={{ ...typography.mono, fontSize: 12, color: colors.text.secondary }}>
                            {trade.qty != null ? trade.qty : "—"}
                          </span>
                        )}
                      </td>

                      {/* Gross P/L */}
                      <td style={{
                        ...TD, ...typography.mono, textAlign: "right", ...pnlStyle(grossVal), fontSize: 13,
                        background: isClosed && grossVal !== 0
                          ? grossVal > 0 ? colors.profitBg : colors.lossBg : "transparent",
                      }}>
                        {isClosed && grossVal !== 0
                          ? `${grossVal > 0 ? "+" : ""}₹${Math.round(grossVal).toLocaleString("en-IN")}` : "—"}
                      </td>

                      {/* Charges */}
                      <td style={{ ...TD, ...typography.mono, textAlign: "right", color: isClosed ? colors.loss : colors.text.muted, fontSize: 12 }}>
                        {isClosed && rowCharges > 0
                          ? `−₹${Math.round(rowCharges).toLocaleString("en-IN")}` : "—"}
                      </td>

                      {/* Net P/L */}
                      {showLotMultiplier && (
                        <td style={{
                          ...TD, ...typography.mono, textAlign: "right",
                          ...pnlStyle(netVal), fontSize: 13, fontWeight: 700,
                          background: isClosed && netVal !== 0
                            ? netVal > 0 ? "rgba(16,185,129,0.18)" : "rgba(239,68,68,0.18)" : "transparent",
                        }}>
                          {isClosed && grossVal !== 0 ? (
                            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
                              <span>{netVal > 0 ? "+" : ""}₹{Math.round(netVal).toLocaleString("en-IN")}</span>
                              {isScalp && scalpLots > 1 && (
                                <span style={{ fontSize: 9, color: colors.primary, fontWeight: 500 }}>×{scalpLots} lots</span>
                              )}
                            </div>
                          ) : "—"}
                        </td>
                      )}
                      {!showLotMultiplier && (
                        <td style={{
                          ...TD, ...typography.mono, textAlign: "right",
                          ...pnlStyle(netVal), fontSize: 13, fontWeight: 700,
                          background: isClosed && netVal !== 0
                            ? netVal > 0 ? "rgba(16,185,129,0.18)" : "rgba(239,68,68,0.18)" : "transparent",
                        }}>
                          {isClosed && grossVal !== 0
                            ? `${netVal > 0 ? "+" : ""}₹${Math.round(netVal).toLocaleString("en-IN")}` : "—"}
                        </td>
                      )}

                      {/* State */}
                      <td style={{ ...TD, textAlign: "center" }}>
                        <span style={{
                          padding: "3px 10px", borderRadius: 5, fontSize: 11, fontWeight: 600,
                          background: isOpen ? colors.warningBg : colors.bg.tertiary,
                          color:      isOpen ? colors.warning   : colors.text.muted,
                          border:     `1px solid ${isOpen ? colors.warning : colors.border.light}40`,
                          textTransform: "uppercase", letterSpacing: "0.3px",
                        }}>
                          {trade.state || "—"}
                        </span>
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
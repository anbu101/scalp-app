import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import { LoadingAnimations, FullPageLoader, EmptyState } from "../components/LoadingStates";
import { useToast } from "../components/ToastNotifications";
import { exportToCSV, generateFilename } from "../utils/export";
import { getApiBase } from "../api/base";
import { colors, spacing, typography, pnlStyle } from "../tokens";

/* ─────────────────────────────────────────────
   Table style constants
───────────────────────────────────────────── */
const TH = {
  padding: "9px 8px",
  textAlign: "left",
  ...typography.label,
  color: colors.text.muted,
  borderBottom: `2px solid ${colors.border.light}`,
  fontWeight: 600,
  whiteSpace: "nowrap",
};
const TD = { padding: "8px 8px", ...typography.bodyMedium, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
// NIFTY options lot size (used to derive actual lots from stored qty).
const LOT_SIZE = 65;
const TH_COMPACT = { ...TH, width: "1px" };

/* ─────────────────────────────────────────────
   Helpers
───────────────────────────────────────────── */

function normalizeSymbol(sym) {
  if (!sym) return sym;
  return sym.replace(/\s+/g, "").toUpperCase();
}

// ── CHANGE 1: added HA_V1 and HA mappings ──
const STRATEGY_DISPLAY = {
  "SCALP_V1": "SCALP V1",
  "SCALP V1": "SCALP V1",
  "1M_SCALP":  "SCALP V1",
  "SCALP V2":  "SCALP V2",
  "SCALP_V3":  "SCALP V3",
  "SCALP V3":  "SCALP V3",
  "SCALP_V4":  "SCALP V4",
  "SCALP V4":  "SCALP V4",
  "SCALP_V5":  "SCALP V5",
  "SCALP V5":  "SCALP V5",
  "IC_V1":     "IC V1",
  "IC V1":     "IC V1",
  "PST_SELL":  "PST SELL",
  "PST_HEDGE": "PST HEDGE",
  "BB_V1":     "BB",
  "BB_V2":     "BB V2",    // ← add — separate tab, or use "BB" to merge
  "BB":        "BB",
  "BB V2":     "BB V2",    // ← add variant
  "HA_V1":     "HA",
  "HA":        "HA",
  "TMA_V1":    "TMA V1",   // ── TMA_V1 ──
  "TMA V1":    "TMA V1",   // ── TMA_V1 ──
};

function displayStrategyName(rawName) {
  if (!rawName) return "—";
  return STRATEGY_DISPLAY[rawName] ?? rawName;
}

const SCALP_STRATEGY_IDS = new Set(["SCALP_V1", "SCALP V1", "1M_SCALP"]);
const isScalpStrategy = (name) => SCALP_STRATEGY_IDS.has(name || "");

// ── CHANGE 2: HA also trades CE/PE sides ──
// Used to decide whether to show the Side column and SideBadge.
const SIDE_STRATEGY_IDS = new Set([
  "SCALP_V1", "SCALP V1", "1M_SCALP",
  "SCALP_V3", "SCALP V3",
  "SCALP_V4", "SCALP V4",
  "SCALP_V5", "SCALP V5",
  "HA_V1", "HA",           // ← NEW
  "IC_V1", "IC V1",        // ← IC legs carry side CE/PE (L1/L3=CE, L2/L4=PE)
  "TMA_V1", "TMA V1",      // ── TMA_V1 ── both legs carry side CE/PE
  "PST_SELL", "PST_HEDGE",  // ← PST rows carry the HELD side CE/PE
]);
const hasSideColumn = (name) => SIDE_STRATEGY_IDS.has(name || "");

/* ─────────────────────────────────────────────
   NSE Index Options charges (Zerodha)
   LOCKED v4 — verified against https://zerodha.com/charges/ on 06-Jun-2026
   (F&O – Options, NSE column)

   MUST stay identical to backend zerodha_charges.py:
     - STT sell        0.0015      (0.15% of SELL-LEG premium, post 01-Apr-2026)
     - Exchange (txn)  0.0003553   (NSE options 0.03553% of premium turnover)
     - SEBI            0.000001    (₹10 / crore)
     - Stamp (buy)     0.00003     (0.003% of buy premium)
     - GST             0.18 × (brokerage + exchange + SEBI)
     - Brokerage       ₹40         (₹20 × 2)

   DIRECTION (v4): STT is on the SELL leg of the round trip.
     - LONG  (buyer:  BB, BB V2, HA)   -> sells to close -> STT on exit_price
     - SHORT (seller: SCALP V1/V2)     -> sold first     -> STT on entry_price
   Exchange / SEBI / stamp / GST are turnover-based and direction-neutral.
───────────────────────────────────────────── */
const ZCHARGES = {
  BROKERAGE: 40,        // ₹20 × 2
  STT_SELL:  0.0015,    // sell-leg premium
  EXCHANGE:  0.0003553, // turnover (buy + sell premium)
  SEBI:      0.000001,  // turnover
  STAMP:     0.00003,   // buy premium
  GST:       0.18,      // on (brokerage + exchange + SEBI)
};

// direction: "SHORT" => STT on entry leg; anything else ("LONG"/undefined) => STT on exit leg.
function calcCharges(entryPrice, exitPrice, qty, direction = "LONG") {
  if (!entryPrice || !exitPrice || !qty) return 0;
  const buyVal   = entryPrice * qty;
  const sellVal  = exitPrice  * qty;
  const turnover = buyVal + sellVal;

  // STT on the sell leg: SHORT sold at entry, LONG sells at exit.
  const sttLegVal = (direction === "SHORT" ? entryPrice : exitPrice) * qty;

  const brokerage      = ZCHARGES.BROKERAGE;
  const stt            = ZCHARGES.STT_SELL * sttLegVal;
  const exchangeCharge = ZCHARGES.EXCHANGE * turnover;
  const sebi           = ZCHARGES.SEBI     * turnover;
  const stampDuty      = ZCHARGES.STAMP    * buyVal;
  const gst            = ZCHARGES.GST * (brokerage + exchangeCharge + sebi);

  return Math.round(
    (brokerage + stt + exchangeCharge + sebi + stampDuty + gst) * 100
  ) / 100;
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

// ── CHANGE 3: HA gets amber colour in strategy chip ──
function StrategyChip({ name }) {
  const display = displayStrategyName(name);
  const isBB    = display === "BB V1";
  const isBBV2 = display === "BB V2"; 
  const isHA    = display === "HA";   // ← NEW

  let bg, color;
  if (isBBV2) {
    bg    = "rgba(139,92,246,0.15)";    // violet to distinguish from BB_V1
    color = "#8b5cf6";
  } else if (isBB) {
    bg    = colors.primaryBg;
    color = colors.primary;
  } else if (isHA) {                  // ← NEW
    bg    = colors.warningBg;         // ← NEW  amber
    color = colors.warning;           // ← NEW
  } else {
    bg    = colors.warningBg;
    color = colors.warning;
  }

  return (
    <span style={{
      padding: "2px 7px", borderRadius: 4, fontSize: 10, fontWeight: 600,
      background: bg, color,
      letterSpacing: "0.3px", textTransform: "uppercase",
    }}>
      {display}
    </span>
  );
}

// ── CHANGE 4: EOD_SQUARE_OFF is amber (neutral exit), not red ──
function ExitReasonBadge({ reason }) {
  if (!reason) return <span style={{ color: colors.text.muted }}>—</span>;

  const isGood    = reason === "TP" || reason === "TARGET" || reason === "GTT_TP";
  const isEOD     = reason === "EOD_SQUARE_OFF";   // ← NEW
  const isSL      = reason === "SL" || reason === "GTT_SL";

  const bg    = isGood ? colors.successBg
              : isEOD  ? colors.warningBg   // ← NEW
              : colors.lossBg;
  const color = isGood ? colors.success
              : isEOD  ? colors.warning     // ← NEW
              : colors.loss;

  return (
    <span style={{
      padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
      background: bg, color,
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

function LtpCell({ ltp, entryPrice, slPrice, tpPrice, isOpen, tradeDirection }) {
  if (!isOpen || ltp == null) {
    return <span style={{ color: colors.text.muted }}>—</span>;
  }

  const toSl = slPrice != null ? (ltp - slPrice).toFixed(2) : null;
  const toTp = tpPrice != null ? (tpPrice - ltp).toFixed(2) : null;
  const profit = entryPrice != null
  ? (tradeDirection === "SHORT" ? entryPrice - ltp : ltp - entryPrice)
  : null;
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

function getPresetRange(preset) {
  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  if (preset === "today") {
    return { from: today, to: null };
  }
  if (preset === "week") {
    const day = today.getDay();
    const diffToMon = day === 0 ? -6 : 1 - day;
    const monday = new Date(today);
    monday.setDate(today.getDate() + diffToMon);
    return { from: monday, to: null };
  }
  if (preset === "month") {
    const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    return { from: firstOfMonth, to: null };
  }
  return null;
}

function inDateRange(tradeUnixSec, from, to) {
  if (!from && !to) return true;
  const t = tradeUnixSec * 1000;
  if (from && t < from.getTime()) return false;
  if (to   && t > to.getTime())   return false;
  return true;
}

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
   TradeCard — compact mobile representation
───────────────────────────────────────────── */

function TradeCard({ trade, ltpMap, scalpLots, isNew }) {
  const isScalp  = isScalpStrategy(trade.strategy_name);
  const isOpen   = trade.state === "OPEN";
  const lots     = isScalp ? scalpLots : 1;
  const qty      = (trade.qty || 1) * lots;
  const gross    = (trade.pnl_value || 0) * lots;
  const charges  = !isOpen ? calcCharges(trade.entry_price, trade.exit_price, qty) : 0;
  const net      = gross - charges;
  const ltp      = ltpMap[normalizeSymbol(trade.symbol)];

  const accent   = isOpen
    ? colors.warning
    : net > 0 ? colors.profit : net < 0 ? colors.loss : colors.border.light;

  // ── CHANGE 5: show side for HA on mobile cards too ──
  const showSide = hasSideColumn(trade.strategy_name);

  return (
    <div
      className={isNew ? "pt-new-row" : undefined}
      style={{
        background:   colors.bg.secondary,
        borderRadius: 8,
        borderLeft:   `3px solid ${accent}`,
        padding:      `${spacing.md}px`,
        marginBottom: spacing.sm,
        border:       `1px solid ${colors.border.dark}`,
        borderLeftColor: accent,
        borderLeftWidth: 3,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.xs }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
          {showSide && trade.side && <SideBadge side={trade.side} />}
          <span style={{ ...typography.mono, fontWeight: 700, fontSize: 13, color: colors.text.primary }}>
            {trade.symbol || "—"}
          </span>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5,
          background: isOpen ? colors.warningBg : colors.bg.tertiary,
          color:      isOpen ? colors.warning   : colors.text.muted,
          border:     `1px solid ${isOpen ? colors.warning : colors.border.dark}40`,
          textTransform: "uppercase",
        }}>
          {trade.state}
        </span>
      </div>

      <div style={{ display: "flex", gap: spacing.md, marginBottom: spacing.xs }}>
        <span style={{ ...typography.mono, fontSize: 12, color: colors.text.tertiary }}>
          Entry <span style={{ color: colors.text.secondary }}>{trade.entry_price?.toFixed(2) ?? "—"}</span>
        </span>
        {!isOpen && (
          <span style={{ ...typography.mono, fontSize: 12, color: colors.text.tertiary }}>
            Exit <span style={{ color: colors.text.secondary }}>{trade.exit_price?.toFixed(2) ?? "—"}</span>
          </span>
        )}
        {isOpen && ltp && (
          <span style={{ ...typography.mono, fontSize: 12, color: colors.text.tertiary }}>
            LTP <span style={{ color: colors.text.primary }}>{ltp.toFixed(2)}</span>
          </span>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ ...typography.mono, fontSize: 14, fontWeight: 700, ...pnlStyle(isOpen ? 0 : net) }}>
          {isOpen
            ? <span style={{ color: colors.text.muted, fontSize: 12 }}>Open position</span>
            : `${net >= 0 ? "+" : ""}₹${Math.round(net).toLocaleString("en-IN")}`
          }
        </span>
        <span style={{ fontSize: 11, color: colors.text.muted }}>
          {formatTimestamp(trade.entry_time)}
        </span>
      </div>
    </div>
  );
}
/* ─────────────────────────────────────────────
   PaperTrades
───────────────────────────────────────────── */

export default function PaperTrades() {
  const toast    = useToast();
  const isMobile = useIsMobile();

  const [loading,     setLoading]     = useState(true);
  const [paperTrades, setPaperTrades] = useState({ open: [], closed: [] });
  const [lastUpdate,  setLastUpdate]  = useState(Date.now());
  const [ltpMap,      setLtpMap]      = useState({});
  const [stratFilter, setStratFilter] = useState("ALL");
  const [scalpLots,   setScalpLots]   = useState(1);

  const [newRowIds,   setNewRowIds]   = useState(() => new Set());
  const knownIdsRef = useRef(new Set());

  const [updatePulse, setUpdatePulse] = useState(false);

  const [datePreset,  setDatePreset]  = useState("today");
  const [customFrom,  setCustomFrom]  = useState(() => toInputDate(new Date()));
  const [customTo,    setCustomTo]    = useState(() => toInputDate(new Date()));

  /* ── Data loading ─────────────────────────── */
  const loadPaperTrades = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/paper_trades`);
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();

      const open   = Array.isArray(data?.open)   ? [...data.open]   : [];
      const closed = Array.isArray(data?.closed) ? [...data.closed] : [];

      setPaperTrades({ open, closed });

      const getId = (t) => t.paper_trade_id ?? `${t.entry_time}_${t.symbol}`;
      const incomingIds = new Set([...open, ...closed].map(getId));

      if (knownIdsRef.current.size > 0) {
        const brandNew = [...incomingIds].filter(id => !knownIdsRef.current.has(id));
        if (brandNew.length > 0) {
          setNewRowIds(new Set(brandNew));
          setTimeout(() => setNewRowIds(new Set()), 1500);
        }
      }
      knownIdsRef.current = incomingIds;

      setLastUpdate(Date.now());
      setUpdatePulse(true);
      setTimeout(() => setUpdatePulse(false), 600);
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

  const strategies = useMemo(() => {
    const seen = new Set();
    allTrades.forEach((t) => {
      if (t.strategy_name) seen.add(displayStrategyName(t.strategy_name));
    });
    return Array.from(seen).sort();
  }, [allTrades]);

  const matchesStrategy = (trade, canonicalName) =>
    displayStrategyName(trade.strategy_name) === canonicalName;

  const activeDateRange = useMemo(() => {
    if (datePreset === "all") return null;
    if (datePreset === "custom") {
      const from = customFrom ? new Date(customFrom + "T00:00:00") : null;
      const to   = customTo   ? new Date(customTo   + "T23:59:59") : null;
      return (from || to) ? { from, to } : null;
    }
    return getPresetRange(datePreset);
  }, [datePreset, customFrom, customTo, lastUpdate]);

  const dateFiltered = useMemo(() =>
    allTrades.filter((t) =>
      inDateRange(t.entry_time, activeDateRange?.from, activeDateRange?.to)
    ),
    [allTrades, activeDateRange]
  );

  const tabOptions = useMemo(() => [
    { value: "ALL", label: "All", count: dateFiltered.length },
    ...strategies.map((s) => ({
      value: s,
      label: s,
      count: dateFiltered.filter((t) => matchesStrategy(t, s)).length,
    })),
  ], [dateFiltered, strategies]);

  const filtered = useMemo(() =>
    stratFilter === "ALL"
      ? dateFiltered
      : dateFiltered.filter((t) => matchesStrategy(t, stratFilter)),
    [dateFiltered, stratFilter]
  );

  const showingScalp = stratFilter === "ALL" || stratFilter === "SCALP V1";
  // ── CHANGE 5 (desktop): Side column visible for SCALP and HA ──
  const showSideCol  = stratFilter === "ALL"
    || stratFilter === "SCALP V1"
    || stratFilter === "SCALP V2"
    || stratFilter === "SCALP V3"
    || stratFilter === "SCALP V4"
    || stratFilter === "SCALP V5"
    || stratFilter === "HA";

  const hasScalpTrades = allTrades.some((t) => isScalpStrategy(t.strategy_name));
  const showLotMultiplier = showingScalp && hasScalpTrades;

  const filteredClosed = filtered.filter((t) => t.state === "CLOSED");
  const filteredOpen   = filtered.filter((t) => t.state === "OPEN");

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
      // ── show side for HA in CSV export too ──
      const showSide = hasSideColumn(t.strategy_name);

      // Actual lots the trade ran, derived from stored qty (matches desktop table). This should work.
      const actualLots =
        t.qty != null && LOT_SIZE > 0 && t.qty % LOT_SIZE === 0
          ? t.qty / LOT_SIZE
          : (t.qty ?? 1);

      return {
        "Strategy":       displayStrategyName(t.strategy_name),
        "Symbol":         t.symbol || "",
        "Side":           showSide ? (t.side || "") : "N/A",
        "Entry Time":     formatTimestamp(t.entry_time),
        "Entry Price":    t.entry_price || 0,
        "SL":             t.sl_price || 0,
        "TP":             t.tp_price || 0,
        "Exit Time":      t.exit_time ? formatTimestamp(t.exit_time) : "",
        "Exit Price":     t.exit_price || "",
        "Exit Reason":    t.exit_reason || "",
        "Actual Lots":    actualLots,
        "Sim Multiplier": scalp ? scalpLots : 1,
        "Qty":            t.qty ?? "",
        "Gross P/L":      grossV,
        "Charges":        chg ? `-${chg.toFixed(2)}` : "",
        "Net P/L":        grossV - chg,
        "State":          t.state || "",
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
      padding:       isMobile ? spacing.sm : spacing.xxl,
      background:    colors.bg.primary,
      color:         colors.text.primary,
      minHeight:     "100vh",
      fontFamily:    "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      paddingBottom: isMobile ? 76 : 56,
    }}>

      {/* ── Page header ─────────────────────── */}
      <div style={{ marginBottom: spacing.xl }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, color: colors.text.primary }}>
              Paper Trades
            </h1>
            <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, marginTop: 4 }}>
              <p style={{ margin: 0, fontSize: 12, color: colors.text.muted }}>
                Simulated trades — no real money at risk
              </p>
              <span style={{
                display: "inline-flex", alignItems: "center", gap: 5,
                fontSize: 10, fontWeight: 500,
                color:      updatePulse ? colors.success    : colors.text.muted,
                padding:    "2px 8px",
                borderRadius: 4,
                background: updatePulse ? colors.successBg  : colors.bg.tertiary,
                border:    `1px solid ${updatePulse ? colors.success + "50" : colors.border.dark}`,
                transition: "all 0.4s ease",
              }}>
                <span style={{
                  width: 5, height: 5, borderRadius: "50%", flexShrink: 0,
                  background: updatePulse ? colors.success : colors.text.muted,
                  boxShadow:  updatePulse ? `0 0 6px ${colors.success}` : "none",
                  transition: "all 0.4s ease",
                }} />
                {new Date(lastUpdate).toLocaleTimeString("en-IN", {
                  hour: "2-digit", minute: "2-digit", second: "2-digit"
                })}
              </span>
            </div>
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
        <DateRangePicker
          preset={datePreset}
          customFrom={customFrom}
          customTo={customTo}
          onPreset={setDatePreset}
          onCustomFrom={setCustomFrom}
          onCustomTo={setCustomTo}
        />

        <div style={{ width: 1, height: 24, background: colors.border.light, flexShrink: 0 }} />

        {strategies.length > 0 && (
          <StrategyTabs
            options={tabOptions}
            active={stratFilter}
            onChange={(v) => setStratFilter(v)}
          />
        )}

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
      {filtered.length > 0 && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: spacing.md, marginBottom: spacing.xl,
        }}>
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

      {/* ── Trades — card list on mobile, table on desktop ── */}
      {isMobile ? (
        <div>
          {filtered.length === 0 ? (
            <Card style={{ padding: spacing.xxl }}>
              <EmptyState
                icon="📋"
                title={datePreset === "today" && stratFilter === "ALL" ? "No trades today" : "No trades in this period"}
                description="Adjust your date range or strategy filter."
              />
            </Card>
          ) : (
            <>
              {filteredOpen.length > 0 && (
                <div style={{ marginBottom: spacing.lg }}>
                  <div style={{
                    display: "flex", alignItems: "center", gap: spacing.sm,
                    marginBottom: spacing.sm, padding: `${spacing.xs}px 0`,
                  }}>
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: colors.warning, boxShadow: `0 0 6px ${colors.warning}99`, animation: "sectionPulse 2s ease-in-out infinite" }} />
                    <span style={{ ...typography.label, fontSize: 10, color: colors.warning }}>OPEN</span>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 10, background: colors.warningBg, color: colors.warning }}>
                      {filteredOpen.length}
                    </span>
                  </div>
                  {filteredOpen.map((trade) => (
                    <TradeCard
                      key={trade.paper_trade_id || trade.entry_time}
                      trade={trade}
                      ltpMap={ltpMap}
                      scalpLots={scalpLots}
                      isNew={newRowIds.has(trade.paper_trade_id ?? `${trade.entry_time}_${trade.symbol}`)}
                    />
                  ))}
                </div>
              )}
              {filteredClosed.length > 0 && (
                <div>
                  <div style={{
                    display: "flex", alignItems: "center", gap: spacing.sm,
                    marginBottom: spacing.sm, padding: `${spacing.xs}px 0`,
                  }}>
                    <span style={{ ...typography.label, fontSize: 10, color: colors.text.muted }}>CLOSED</span>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 10, background: colors.bg.tertiary, color: colors.text.muted }}>
                      {filteredClosed.length}
                    </span>
                  </div>
                  {filteredClosed.map((trade) => (
                    <TradeCard
                      key={trade.paper_trade_id || trade.entry_time}
                      trade={trade}
                      ltpMap={ltpMap}
                      scalpLots={scalpLots}
                      isNew={newRowIds.has(trade.paper_trade_id ?? `${trade.entry_time}_${trade.symbol}`)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      ) : (
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
            <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", ...typography.bodyMedium }}>
              <thead style={{ background: colors.bg.tertiary }}>
                <tr>
                  {/* tableLayout:"fixed" — widths below are proportional and
                      the browser normalizes them, so the conditional Strategy/
                      Side columns just reflow the remainder evenly. */}
                  {stratFilter === "ALL" && <th style={{ ...TH, width: "7%" }}>Strategy</th>}
                  <th style={{ ...TH, width: "14%" }}>Symbol</th>
                  {showSideCol && <th style={{ ...TH, width: "4%", textAlign: "center" }}>Side</th>}
                  <th style={{ ...TH, width: "7%" }}>Entry Time</th>
                  <th style={{ ...TH, width: "6%", textAlign: "right" }}>Entry</th>
                  <th style={{ ...TH, width: "5%", textAlign: "right" }}>SL</th>
                  <th style={{ ...TH, width: "5%", textAlign: "right" }}>TP</th>
                  <th style={{ ...TH, width: "5%", textAlign: "right" }}>LTP</th>
                  <th style={{ ...TH, width: "7%" }}>Exit Time</th>
                  <th style={{ ...TH, width: "6%", textAlign: "right" }}>Exit</th>
                  <th style={{ ...TH, width: "9%" }}>Reason</th>
                  <th style={{ ...TH, width: "6%", textAlign: "center" }}>Lots / Qty</th>
                  <th style={{ ...TH, width: "7%", textAlign: "right" }}>Gross P/L</th>
                  <th style={{ ...TH, width: "6%", textAlign: "right", color: colors.loss }}>Charges</th>
                  <th style={{ ...TH, width: "7%", textAlign: "right", color: colors.primary }}>Net P/L</th>
                  <th style={{ ...TH, width: "6%", textAlign: "center" }}>State</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { label: "Open",   trades: filteredOpen,   isOpenGroup: true  },
                  { label: "Closed", trades: filteredClosed, isOpenGroup: false },
                ].map(({ label, trades: groupTrades, isOpenGroup }) => {
                  if (groupTrades.length === 0) return null;

                  const colCount = (stratFilter === "ALL" ? 1 : 0) + (showSideCol ? 1 : 0) + (showLotMultiplier ? 1 : 0) + 14;

                  return (
                    <React.Fragment key={label}>
                      <tr>
                        <td
                          colSpan={colCount}
                          style={{
                            padding:     "6px 12px",
                            background:  isOpenGroup ? "rgba(245,158,11,0.07)" : colors.bg.tertiary,
                            borderTop:   `2px solid ${isOpenGroup ? colors.warning : colors.border.light}`,
                            borderBottom:`1px solid ${isOpenGroup ? `${colors.warning}40` : colors.border.dark}`,
                          }}
                        >
                          <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
                            {isOpenGroup && (
                              <span style={{
                                width: 7, height: 7, borderRadius: "50%",
                                background: colors.warning,
                                boxShadow: `0 0 6px ${colors.warning}99`,
                                flexShrink: 0,
                                animation: "sectionPulse 2s ease-in-out infinite",
                              }} />
                            )}
                            <span style={{
                              ...typography.label,
                              fontSize: 10,
                              color: isOpenGroup ? colors.warning : colors.text.muted,
                              letterSpacing: "0.6px",
                            }}>
                              {label}
                            </span>
                            <span style={{
                              fontSize: 10, fontWeight: 700,
                              padding: "1px 6px", borderRadius: 10,
                              background: isOpenGroup ? colors.warningBg : colors.bg.secondary,
                              color:      isOpenGroup ? colors.warning   : colors.text.muted,
                              border:     `1px solid ${isOpenGroup ? `${colors.warning}40` : colors.border.dark}`,
                            }}>
                              {groupTrades.length}
                            </span>
                          </div>
                        </td>
                      </tr>

                      {groupTrades.map((trade, i) => {
                        const isScalp   = isScalpStrategy(trade.strategy_name);
                        const isOpen    = trade.state === "OPEN";
                        const isClosed  = trade.state === "CLOSED";
                        const ltp       = ltpMap[normalizeSymbol(trade.symbol)];
                        const lots      = isScalp ? scalpLots : 1;
                        const rowQty    = (trade.qty || 1) * lots;

                        // Infer direction from stored SL: if SL > entry, this is a SHORT trade
                        const inferredDirection =
                          trade.sl_price != null &&
                          trade.entry_price != null &&
                          trade.sl_price > trade.entry_price
                            ? "SHORT"
                            : "LONG";

                        const livePnlVal = isOpen && ltp != null && trade.entry_price != null
                          ? (inferredDirection === "SHORT"
                              ? (trade.entry_price - ltp) * rowQty
                              : (ltp - trade.entry_price) * rowQty)
                          : null;
                        const grossVal   = isOpen && livePnlVal != null
                          ? livePnlVal
                          : (trade.pnl_value || 0) * lots;
                        const rowCharges = isClosed
                          ? calcCharges(trade.entry_price, trade.exit_price, rowQty)
                          : 0;
                        const netVal    = grossVal - rowCharges;

                        const tradeId  = trade.paper_trade_id ?? `${trade.entry_time}_${trade.symbol}`;
                        const isNewRow = newRowIds.has(tradeId);

                        const rowBg      = i % 2 ? colors.bg.secondary : colors.bg.primary;
                        const openAccent = isOpen ? `3px solid ${colors.warning}` : "3px solid transparent";

                        // ── CHANGE 5: show side badge for HA in table too ──
                        const tradeSide = hasSideColumn(trade.strategy_name);

                        return (
                          <tr key={trade.paper_trade_id || i}
                            className={isNewRow ? "pt-new-row" : undefined}
                            style={{
                              background: rowBg,
                              borderTop:  `1px solid ${colors.border.dark}`,
                              transition: "background 0.15s ease",
                              borderLeft: openAccent,
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = colors.bg.tertiary)}
                            onMouseLeave={(e) => (e.currentTarget.style.background = rowBg)}
                          >
                            {stratFilter === "ALL" && (
                              <td style={TD}><StrategyChip name={trade.strategy_name} /></td>
                            )}

                            <td style={{ ...TD, ...typography.mono, fontWeight: 600, color: colors.text.primary, whiteSpace: "nowrap" }}>
                              {trade.symbol || "—"}
                            </td>

                            {showSideCol && (
                              <td style={{ ...TD, textAlign: "center" }}>
                                {tradeSide
                                  ? <SideBadge side={trade.side} />
                                  : <span style={{ color: colors.text.muted }}>—</span>}
                              </td>
                            )}

                            <td style={{ ...TD, ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>
                              {formatTimestamp(trade.entry_time)}
                            </td>

                            <td style={{ ...TD, ...typography.mono, textAlign: "right" }}>
                              {trade.entry_price != null ? trade.entry_price.toFixed(2) : "—"}
                            </td>

                            <td style={{ ...TD, ...typography.mono, textAlign: "right", color: colors.loss }}>
                              {trade.sl_price != null ? trade.sl_price.toFixed(2) : "—"}
                            </td>

                            <td style={{ ...TD, ...typography.mono, textAlign: "right", color: colors.profit }}>
                              {trade.tp_price != null ? trade.tp_price.toFixed(2) : "—"}
                            </td>

                            <td style={{ ...TD, textAlign: "right" }}>
                              <LtpCell
                                ltp={ltp}
                                entryPrice={trade.entry_price}
                                slPrice={trade.sl_price}
                                tpPrice={trade.tp_price}
                                isOpen={isOpen}
                                tradeDirection={inferredDirection}
                              />
                            </td>

                            <td style={{ ...TD, ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>
                              {trade.exit_time ? formatTimestamp(trade.exit_time) : "—"}
                            </td>

                            <td style={{ ...TD, ...typography.mono, textAlign: "right" }}>
                              {trade.exit_price != null ? Number(trade.exit_price).toFixed(2) : "—"}
                            </td>

                            <td style={TD}>
                              <ExitReasonBadge reason={trade.exit_reason} />
                            </td>

                            <td style={{ ...TD, textAlign: "center" }}>
                              {(() => {
                                // Actual lots the trade used, derived from stored qty.
                                const actualLots =
                                  trade.qty != null && LOT_SIZE > 0 && trade.qty % LOT_SIZE === 0
                                    ? trade.qty / LOT_SIZE
                                    : null;
                                return (
                                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 1 }}>
                                    <span style={{ ...typography.mono, fontSize: 12, fontWeight: 700, color: colors.text.secondary }}>
                                      {actualLots != null
                                        ? `${actualLots} lot${actualLots !== 1 ? "s" : ""}`
                                        : (trade.qty != null ? trade.qty : "—")}
                                    </span>
                                    {isScalp && scalpLots > 1 && (
                                      <span style={{ fontSize: 9, color: colors.primary, fontWeight: 500 }}>
                                        ×{scalpLots} sim
                                      </span>
                                    )}
                                  </div>
                                );
                              })()}
                            </td>

                            <td style={{
                              ...TD, ...typography.mono, textAlign: "right", ...pnlStyle(grossVal), fontSize: 13,
                              background: (isClosed || (isOpen && livePnlVal != null)) && grossVal !== 0
                                ? grossVal > 0 ? colors.profitBg : colors.lossBg : "transparent",
                            }}>
                              {isClosed && grossVal !== 0
                                ? `${grossVal > 0 ? "+" : ""}₹${Math.round(grossVal).toLocaleString("en-IN")}`
                                : isOpen && livePnlVal != null
                                ? <>{livePnlVal > 0 ? "+" : ""}₹{Math.round(livePnlVal).toLocaleString("en-IN")} <span style={{ color: colors.text.muted, fontSize: 9, fontWeight: 400 }}>LIVE</span></>
                                : "—"}
                            </td>

                            <td style={{ ...TD, ...typography.mono, textAlign: "right", color: isClosed ? colors.loss : colors.text.muted, fontSize: 12 }}>
                              {isClosed && rowCharges > 0
                                ? `−₹${Math.round(rowCharges).toLocaleString("en-IN")}` : "—"}
                            </td>

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
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      )}

      <style>{`
        @keyframes ptNewRow {
          0%   { background: rgba(16, 185, 129, 0.22); box-shadow: inset 0 0 0 1px rgba(16,185,129,0.5); }
          60%  { background: rgba(16, 185, 129, 0.10); box-shadow: inset 0 0 0 1px rgba(16,185,129,0.2); }
          100% { background: transparent; box-shadow: none; }
        }
        .pt-new-row {
          animation: ptNewRow 1.5s ease-out forwards;
        }
        @keyframes sectionPulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
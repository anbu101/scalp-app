/**
 * BBPanel — src/strategies/bb_v1/BBPanel.jsx
 *
 * Supports both BB_V1 and BB_V2 via the `strategyId` prop.
 *
 * DB column routing (strategyId-aware):
 *   BB_V1 reads: supertrend, st_direction, signal_action, signal_reason, rejection_reason
 *   BB_V2 reads: supertrend_v2, st_direction_v2, signal_action_v2, signal_reason_v2, rejection_reason_v2
 *   Shared:      bb_upper, bb_middle, bb_lower, rsi_raw, rsi_smooth, r1, s1, r2, pp, s2, s3
 *
 * v4 changes (chart upgrade):
 *   1. Candles are SESSION-FILTERED to 09:15–15:30 IST before any rendering,
 *      so off-session candles (and any spurious CE/PE markers attached to
 *      them) never appear. Applied once at fetch time.
 *   2. Multi-day history: fetch up to ~1 month of 3m candles. Render stays
 *      fluid because only the candles inside the current viewport are mapped
 *      and drawn — total retained count does not affect interaction cost.
 *      Day boundaries are marked with subtle separators + date labels.
 *   3. Per-strategy OPEN P&L shown in BOLD in the panel header and info strip.
 *      P&L is for THIS strategy only (filtered by strategy_id), computed LONG:
 *      (ltp - entry) * qty, summed over this strategy's open positions, using
 *      a live /ltp_snapshot poll. BB_V1 / BB_V2 are both LONG (option buyers),
 *      so no short inversion is applied here.
 *   4. The ARMED/IDLE badge is removed.
 *   5. Y-axis interaction improved: drag the left price gutter to pan Y, a
 *      dedicated Y-zoom rail on the right, shift+wheel / shift+drag retained,
 *      double-click anywhere to auto-fit.
 */

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { getApiBase } from "../../api/base";
import { getStrategyConfig, getTradeState, getTodayPositions } from "../../api";

/* ─── Design tokens ────────────────────────────────────────────── */
const C = {
  bg:        "#020817",
  bgCard:    "#0f172a",
  bgSurface: "#1e293b",
  border:    "#334155",
  borderDim: "#1a2540",
  text:      "#f1f5f9",
  textSec:   "#94a3b8",
  textMuted: "#4b6280",
  green:     "#10b981",
  greenDim:  "rgba(16,185,129,0.12)",
  red:       "#ef4444",
  redDim:    "rgba(239,68,68,0.12)",
  amber:     "#f59e0b",
  amberDim:  "rgba(245,158,11,0.12)",
  blue:      "#3b82f6",
  blueDim:   "rgba(59,130,246,0.12)",
  violet:    "#8b5cf6",
  teal:      "#14b8a6",
  wick:      "#475569",
  bbFill:    "rgba(59,130,246,0.07)",
  bbUpper:   "rgba(59,130,246,0.8)",
  bbLower:   "rgba(59,130,246,0.8)",
  bbMiddle:  "rgba(148,163,184,0.5)",
  stUp:      "#10b981",
  stDown:    "#ef4444",
  r1:        "#f59e0b",
  s1:        "#14b8a6",
  rsiFill:   "rgba(139,92,246,0.15)",
  rsiLine:   "#8b5cf6",
  sigEnterCE: "#06b6d4",
  sigEnterPE: "#f97316",
  sigExitCE:  "#facc15",
  sigExitPE:  "#e879f9",
  daySep:    "rgba(148,163,184,0.18)",
};

const FONT = "'Inter', -apple-system, sans-serif";
const MONO = "'JetBrains Mono','Fira Code',monospace";

/* ─── Session window (IST: 09:15–15:30) ─────────────────────────
   The app runs in IST; timestamps are epoch seconds. We compute the
   minute-of-day in the LOCAL timezone (which is IST on the trading
   machine) to decide session membership. 09:15 = 555, 15:30 = 930.
─────────────────────────────────────────────────────────────── */
const SESSION_START_MIN = 9 * 60 + 15;   // 555
const SESSION_END_MIN   = 15 * 60 + 30;  // 930

function minuteOfDay(ts) {
  const d = new Date(ts * 1000);
  return d.getHours() * 60 + d.getMinutes();
}

/* Day key (local) used for grouping candles into trading days. */
function dayKey(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/* Keep only candles inside the trading session, on weekdays.
   This is the single gate for BOTH "no off-session candles" and
   "no off-session signal markers" — markers live on candles, so once
   the candle is gone the marker is gone. */
function filterToSession(candles) {
  if (!Array.isArray(candles)) return [];
  return candles.filter((c) => {
    if (c == null || c.ts == null) return false;
    const d = new Date(c.ts * 1000);
    const dow = d.getDay();
    if (dow === 0 || dow === 6) return false;       // weekend guard
    const m = minuteOfDay(c.ts);
    return m >= SESSION_START_MIN && m < SESSION_END_MIN;
  });
}

/* ─── Helpers ───────────────────────────────────────────────── */
function isMarketHours() {
  const d = new Date();
  if (d.getDay() === 0 || d.getDay() === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= 555 && m < 930;
}
function fmtPrice(v) {
  if (v == null) return "—";
  return Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtInr(v) {
  if (v == null) return "—";
  const abs = Math.abs(Math.round(v));
  return `₹${abs.toLocaleString("en-IN")}`;
}
function fmtTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleTimeString("en-IN", {
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch { return ""; }
}
function fmtDayLabel(ts) {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  } catch { return ""; }
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

/* ─── Per-strategy OPEN P&L (LONG only — both BB variants buy) ───
   positions.open carries strategy_id, entry_price, qty, symbol.
   Unrealised = (ltp - entry) * qty, summed over this strategy's
   open positions. Returns { value, count, hasTick }. */
function computeStrategyOpenPnl(positions, strategyId, ltpMap) {
  const open = positions?.open || [];
  const mine = open.filter((p) => (p.strategy_id || p.strategyId) === strategyId);
  if (!mine.length) return { value: 0, count: 0, hasTick: false };
  let total = 0;
  let hasTick = false;
  for (const p of mine) {
    const sym   = (p.symbol || p.tradingsymbol || "").toUpperCase().replace(/\s+/g, "");
    const entry = Number(p.entry_price) || 0;
    const qty   = Number(p.qty) || 0;
    const ltp   = ltpMap[sym];
    if (ltp == null || !entry || !qty) continue;
    hasTick = true;
    total += (ltp - entry) * qty;   // LONG: (ltp - entry) * qty
  }
  return { value: total, count: mine.length, hasTick };
}

/* ─── Column key resolver ─────────────────────────────────────── */
function getColKeys(strategyId) {
  const isV2 = strategyId === "BB_V2";
  return {
    supertrend:       isV2 ? "supertrend_v2"       : "supertrend",
    st_direction:     isV2 ? "st_direction_v2"     : "st_direction",
    signal_action:    isV2 ? "signal_action_v2"    : "signal_action",
    signal_reason:    isV2 ? "signal_reason_v2"    : "signal_reason",
    rejection_reason: isV2 ? "rejection_reason_v2" : "rejection_reason",
  };
}

/* ─── Chart geometry ────────────────────────────────────────── */
const MARGIN       = { top: 12, right: 10, bottom: 28, left: 68 };
const RSI_GAP      = 10;
const DEFAULT_VIEW = 80;
const MIN_VIEW     = 10;

/* Preferred candle fetch size for multi-day history (≈8 sessions of 3m
   candles). If the backend rejects this (e.g. a server-side `limit` ceiling
   returns 422), fetchCandles falls back to limit=200, then the default.
   Bump this once the endpoint's real maximum is confirmed. */
const CANDLE_LIMIT = 1000;

function computePanes(totalH) {
  const inner = totalH - MARGIN.top - MARGIN.bottom - RSI_GAP;
  const mainH = Math.max(100, Math.round(inner * 0.77));
  const rsiH  = Math.max(40, inner - mainH);
  return { mainH, rsiH, totalH };
}

/* ─── Small atoms ───────────────────────────────────────────── */
function Pill({ label, color, bg, border, icon }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
      padding: "2px 8px", borderRadius: 4,
      background: bg, color, border: `1px solid ${border}`,
    }}>
      {icon && <span style={{ fontSize: 9 }}>{icon}</span>}
      {label}
    </span>
  );
}

function SignalBadge({ action }) {
  if (!action) return null;
  const map = {
    ENTER_CE: { label: "▲ CE", bg: "rgba(6,182,212,0.15)",   color: C.sigEnterCE, border: C.sigEnterCE },
    ENTER_PE: { label: "▼ PE", bg: "rgba(249,115,22,0.15)",  color: C.sigEnterPE, border: C.sigEnterPE },
    EXIT_CE:  { label: "✕ CE", bg: "rgba(250,204,21,0.12)",  color: C.sigExitCE,  border: C.sigExitCE  },
    EXIT_PE:  { label: "✕ PE", bg: "rgba(232,121,249,0.12)", color: C.sigExitPE,  border: C.sigExitPE  },
  };
  const s = map[action];
  if (!s) return <span style={{ fontSize: 11, color: C.textMuted }}>{action}</span>;
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.4px",
      padding: "2px 7px", borderRadius: 4,
      background: s.bg, color: s.color, border: `1px solid ${s.border}`,
    }}>{s.label}</span>
  );
}

/* ─── Open P&L pill (bold, per-strategy) ────────────────────── */
function OpenPnlPill({ pnl, compact }) {
  if (!pnl || pnl.count === 0) return null;
  const { value, count, hasTick } = pnl;
  const up = value >= 0;
  const color = !hasTick ? C.textMuted : up ? C.green : C.red;
  const bg    = !hasTick ? "transparent" : up ? C.greenDim : C.redDim;
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: compact ? "2px 8px" : "3px 10px", borderRadius: 6,
      background: bg, border: `1px solid ${color}55`,
    }}>
      <span style={{
        fontSize: 8, fontWeight: 700, color: C.textMuted,
        textTransform: "uppercase", letterSpacing: "0.5px", whiteSpace: "nowrap",
      }}>
        Open P&L · {count}
      </span>
      <span style={{
        fontSize: compact ? 13 : 15, fontWeight: 800, fontFamily: MONO,
        color, whiteSpace: "nowrap",
      }}>
        {hasTick ? `${up ? "+" : "−"}${fmtInr(value)}` : "—"}
      </span>
    </div>
  );
}

/* ─── Info strip ────────────────────────────────────────────── */
function InfoStrip({ config, positions, activeSymbol, onOpenSettings, strategyId, openPnl }) {
  if (!config) return null;

  const isV2   = strategyId === "BB_V2";
  const mode   = config.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";

  const ceOpen   = positions?.open?.filter(p => p.symbol?.includes("CE"))?.length   ?? 0;
  const peOpen   = positions?.open?.filter(p => p.symbol?.includes("PE"))?.length   ?? 0;
  const ceClosed = positions?.closed?.filter(p => p.symbol?.includes("CE"))?.length ?? 0;
  const peClosed = positions?.closed?.filter(p => p.symbol?.includes("PE"))?.length ?? 0;

  const statStyle = { display: "flex", flexDirection: "column", gap: 1, minWidth: 0 };
  const statLabel = {
    fontSize: 8, fontWeight: 600, color: C.textMuted,
    letterSpacing: "0.6px", textTransform: "uppercase", whiteSpace: "nowrap",
  };
  const statVal = { fontSize: 12, fontWeight: 700, color: C.text, fontFamily: MONO, whiteSpace: "nowrap" };

  const Stat = ({ label, value, color }) => (
    <div style={statStyle}>
      <span style={statLabel}>{label}</span>
      <span style={{ ...statVal, color: color || C.text }}>{value ?? "—"}</span>
    </div>
  );
  const divider = (
    <div style={{ width: 1, background: C.borderDim, alignSelf: "stretch", margin: "0 2px" }} />
  );

  return (
    <div style={{ borderBottom: `1px solid ${C.borderDim}`, background: C.bgCard }}>
      {/* Row 1 — ARMED badge removed; Open P&L pill added */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 14px 5px" }}>
        <Pill
          label={mode}
          color={isLive ? C.red : C.green}
          bg={isLive ? C.redDim : C.greenDim}
          border={isLive ? C.red : C.green}
          icon={isLive ? "⚡" : "✎"}
        />
        <OpenPnlPill pnl={openPnl} />
        <span style={{ fontSize: 10, color: C.textMuted, marginLeft: 4 }}>
          {activeSymbol} · {isV2 ? "BB V2 OPTIONS" : "BB OPTIONS"} · 3M
        </span>
        <button
          onClick={onOpenSettings}
          style={{
            marginLeft: "auto", background: "none", border: "none",
            color: C.textMuted, cursor: "pointer", fontSize: 10,
            fontFamily: FONT, padding: 0,
          }}
        >
          EDIT IN SETTINGS ›
        </button>
      </div>

      {/* Row 2: stats */}
      <div style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "4px 14px 8px", overflowX: "auto",
      }}>
        <Stat label="SL %" value={config.sl_pct != null ? `${config.sl_pct}%` : "—"} />
        {divider}
        <Stat label="TP %" value={config.tp_pct != null ? `${config.tp_pct}%` : "—"} />
        {divider}
        <Stat label="Max Premium" value={config.max_premium != null ? `₹${config.max_premium}` : "—"} />
        {divider}
        <Stat label="Max Trades/Side" value={config.max_trades_per_side} />
        {isV2 && (
          <>
            {divider}
            <Stat label="ST Mult" value="1.5" color={C.amber} />
            {divider}
            <Stat label="Pivots" value="R2→S3" color={C.teal} />
          </>
        )}
        {divider}
        <Stat label="CE Lots" value={config.ce_lots ?? config.lots} />
        {divider}
        <Stat label="PE Lots" value={config.pe_lots ?? config.lots} />
        {divider}
        <Stat
          label="Session"
          value={
            config.session_start && config.session_end
              ? `${config.session_start} – ${config.session_end}`
              : "—"
          }
        />
        {divider}
        <Stat label="Square-off" value={config.auto_square_off_time || "—"} />
        {divider}
        <Stat label="Today CE" value={ceClosed} color={ceClosed > 0 ? C.sigEnterCE : C.textMuted} />
        {divider}
        <Stat label="Today PE" value={peClosed} color={peClosed > 0 ? C.sigEnterPE : C.textMuted} />
        {divider}
        <Stat
          label="Open"
          value={`CE:${ceOpen}  PE:${peOpen}`}
          color={(ceOpen + peOpen) > 0 ? C.amber : C.textMuted}
        />
      </div>
    </div>
  );
}

/* ─── Panel header ──────────────────────────────────────────── */
function PanelHeader({
  candles, isPrimary, onBecomePrimary, activeSymbol,
  isFullscreen, onToggleFullscreen, config, strategyId, openPnl,
}) {
  const last = candles[candles.length - 1];
  if (!last) return null;

  const keys   = getColKeys(strategyId);
  const prev   = candles[candles.length - 2];
  const change = prev ? last.close - prev.close : 0;
  const isUp   = change >= 0;

  let bbPos = null;
  if (last.bb_upper != null && last.bb_lower != null) {
    if (last.close > last.bb_upper)      bbPos = { label: "ABOVE BB", color: C.amber };
    else if (last.close < last.bb_lower) bbPos = { label: "BELOW BB", color: C.amber };
    else                                  bbPos = { label: "IN BAND",  color: C.textMuted };
  }

  const stDir      = last[keys.st_direction]?.toUpperCase();
  const lastSignal = [...candles].reverse().find(c => c[keys.signal_action]);

  const mode   = config?.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";
  const label  = strategyId === "BB_V2" ? "BB V2" : "BB";

  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: isPrimary ? "9px 14px 7px" : "7px 10px",
        borderBottom: `1px solid ${C.borderDim}`,
        cursor: isPrimary ? "default" : "pointer",
        flexWrap: "wrap", flexShrink: 0,
      }}
      onClick={!isPrimary ? onBecomePrimary : undefined}
    >
      <div style={{ fontSize: 11, fontWeight: 700, color: C.blue, letterSpacing: "0.8px" }}>
        {label}
      </div>

      {!isPrimary && config && (
        <Pill
          label={mode}
          color={isLive ? C.red : C.green}
          bg={isLive ? C.redDim : C.greenDim}
          border={isLive ? C.red : C.green}
          icon={isLive ? "⚡" : "✎"}
        />
      )}

      <div style={{ fontSize: 11, color: C.textMuted, letterSpacing: "0.5px" }}>
        {activeSymbol} 3m
      </div>
      <div style={{
        fontSize: isPrimary ? 17 : 13, fontWeight: 700,
        fontFamily: MONO, color: isUp ? C.green : C.red, marginLeft: 2,
      }}>
        {fmtPrice(last.close)}
      </div>
      <div style={{ fontSize: 11, fontFamily: MONO, color: isUp ? C.green : C.red }}>
        {isUp ? "+" : ""}{change.toFixed(2)}
      </div>

      {/* Per-strategy OPEN P&L — bold, right next to price */}
      <OpenPnlPill pnl={openPnl} compact={!isPrimary} />

      {stDir && (
        <div style={{
          fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
          background: stDir === "UP" ? C.greenDim : C.redDim,
          color:      stDir === "UP" ? C.green    : C.red,
          border: `1px solid ${stDir === "UP" ? C.green : C.red}`,
        }}>
          ST {stDir}
        </div>
      )}

      {bbPos && <div style={{ fontSize: 10, color: bbPos.color }}>{bbPos.label}</div>}

      {(last.rsi_raw != null || last.rsi_smooth != null) && (
        <div style={{
          fontSize: 10, fontFamily: MONO,
          color: (last.rsi_raw ?? last.rsi_smooth) > 70 ? C.red
               : (last.rsi_raw ?? last.rsi_smooth) < 35 ? C.green
               : C.violet,
        }}>
          RSI {(last.rsi_raw ?? last.rsi_smooth)?.toFixed(1)}
        </div>
      )}

      {isPrimary && lastSignal?.[keys.signal_action] && (
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: C.textMuted }}>Last:</span>
          <SignalBadge action={lastSignal[keys.signal_action]} />
          <span style={{ fontSize: 10, color: C.textMuted, fontFamily: MONO }}>
            {fmtTime(lastSignal.ts)}
          </span>
        </div>
      )}

      {isPrimary && (
        <button
          onClick={(e) => { e.stopPropagation(); onToggleFullscreen(); }}
          title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          style={{
            marginLeft: "auto", background: "none",
            border: `1px solid ${C.border}`,
            borderRadius: 4, color: C.textMuted, cursor: "pointer",
            padding: "3px 7px", fontSize: 13, lineHeight: 1,
            display: "flex", alignItems: "center", justifyContent: "center",
            transition: "border-color 0.15s, color 0.15s",
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.color = C.blue; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.textMuted; }}
        >
          {isFullscreen ? "✕" : "⛶"}
        </button>
      )}
    </div>
  );
}

/* ─── SVG Chart ─────────────────────────────────────────────── */
function CandleChart({ candles, width, chartHeight = 450, instanceId = "main", strategyId = "BB_V1" }) {
  const { mainH: MAIN_H, rsiH: RSI_H, totalH: TOTAL_H } = computePanes(chartHeight);

  const keys = getColKeys(strategyId);

  const totalCandles = candles.length;
  const [viewCount, setViewCount]   = useState(() => Math.min(DEFAULT_VIEW, totalCandles));
  const [viewOffset, setViewOffset] = useState(() => Math.max(0, totalCandles - Math.min(DEFAULT_VIEW, totalCandles)));
  const atTailRef   = useRef(true);
  const prevTotalRef = useRef(totalCandles);
  const dragRef     = useRef({ active: false, startX: 0, startOffset: 0, startY: 0, startYOffset: 0, isY: false });
  const [isDragging, setIsDragging] = useState(false);
  const [tooltip, setTooltip]       = useState(null);
  const [yZoom,   setYZoom]         = useState(1);
  const [yOffset, setYOffset]       = useState(0);
  const yAutoFit = () => { setYZoom(1); setYOffset(0); };
  const rangePRef = useRef(1);

  const futureSlots = (() => {
    if (!totalCandles) return 0;
    const lastTs  = candles[totalCandles - 1].ts;
    const closeMs = new Date(lastTs * 1000);
    closeMs.setHours(15, 30, 0, 0);
    const fromMs  = Math.max(Date.now(), lastTs * 1000 + 180_000);
    return Math.ceil(Math.max(0, closeMs - fromMs) / (3 * 60 * 1000));
  })();
  const totalSlots = totalCandles + futureSlots;

  useEffect(() => {
    if (totalSlots === 0) return;
    // Do not reposition the viewport while the user is actively dragging —
    // a candle poll mid-drag must not yank the view. And only follow new
    // candles to the tail when the user is already pinned at the tail;
    // otherwise keep their historical position stable as data appends.
    if (dragRef.current.active) return;
    setViewCount(prev => {
      const vc = clamp(prev, MIN_VIEW, totalSlots);
      setViewOffset(prevOff => {
        if (atTailRef.current) return Math.max(0, totalCandles - vc);
        return clamp(prevOff, 0, Math.max(0, totalSlots - vc));
      });
      return vc;
    });
  }, [totalSlots]);

  const safeCount  = clamp(viewCount,  MIN_VIEW, Math.max(totalSlots, 1));
  const safeOffset = clamp(viewOffset, 0, Math.max(0, totalSlots - safeCount));

  // PERF: only the candles inside the viewport are ever sliced/mapped/drawn.
  // Retaining a month of history therefore does not slow interaction — the
  // work per frame is bounded by `safeCount`, not by total candle count.
  const visible            = candles.slice(safeOffset, Math.min(safeOffset + safeCount, totalCandles));
  const visibleFutureSlots = Math.max(0, (safeOffset + safeCount) - totalCandles);
  const visibleSlots       = safeCount;

  useEffect(() => { atTailRef.current = (safeOffset + safeCount >= totalSlots); }, [safeOffset, safeCount, totalSlots]);

  useEffect(() => {
    if (totalSlots === 0) return;
    // When new candles append at the tail while the user is scrolled back
    // into history (not at tail, not dragging), shift the offset by the same
    // amount so the SAME candles stay in view instead of drifting.
    const grew = totalCandles - prevTotalRef.current;
    if (grew > 0 && !atTailRef.current && !dragRef.current.active) {
      setViewOffset(prevOff => clamp(prevOff + grew, 0, Math.max(0, totalSlots - safeCount)));
    }
    prevTotalRef.current = totalCandles;
  }, [totalCandles, totalSlots, safeCount]);

  const svgRef = useRef(null);

  // Live mirrors of viewport state so the global pointer/wheel listeners
  // (registered once) always read CURRENT values without being torn down
  // and re-added on every poll. This is what fixes the dead drag: the
  // listeners never go stale, and panning is pixel-accurate (fractional
  // slot movement accumulates, so even tiny drags move the chart).
  const liveRef = useRef({
    safeCount, safeOffset, totalSlots, totalCandles, width, yOffset, yZoom,
  });
  liveRef.current = { safeCount, safeOffset, totalSlots, totalCandles, width, yOffset, yZoom };

  const jumpToLatest = () => {
    setViewOffset(Math.max(0, liveRef.current.totalCandles - liveRef.current.safeCount));
    atTailRef.current = true;
  };

  // Register ALL pointer + wheel listeners ONCE on the SVG element.
  // They read from liveRef so they're never stale, and they attach the
  // move/up listeners to WINDOW on press — so a drag keeps tracking even
  // if the cursor leaves the SVG, and child <g> candles can't swallow it.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;

    const chartWNow = () => Math.max(1, liveRef.current.width - MARGIN.left - MARGIN.right);

    function onWheel(e) {
      e.preventDefault();
      const L = liveRef.current;
      const factor = e.deltaY > 0 ? 1.12 : 0.88;
      if (e.shiftKey) {
        setYZoom(prev => clamp(prev / factor, 0.2, 20));
        return;
      }
      const rect       = el.getBoundingClientRect();
      const mouseX     = e.clientX - rect.left - MARGIN.left;
      const cursorFrac = clamp(mouseX / chartWNow(), 0, 1);
      setViewCount(prevCount => {
        const newCount   = Math.round(clamp(prevCount * factor, MIN_VIEW, L.totalSlots));
        const cursorSlot = L.safeOffset + cursorFrac * prevCount;
        const newOffset  = Math.round(clamp(cursorSlot - cursorFrac * newCount, 0, L.totalSlots - newCount));
        setViewOffset(newOffset);
        atTailRef.current = (newOffset + newCount >= L.totalSlots);
        return newCount;
      });
    }

    function onPointerMove(e) {
      const d = dragRef.current;
      if (!d.active) return;
      const L = liveRef.current;
      if (d.isY) {
        const dy = e.clientY - d.startY;
        const pxPerPrice = MAIN_H / (rangePRef.current / L.yZoom || 1);
        setYOffset(d.startYOffset + dy / pxPerPrice);
      } else {
        // Pixel-accurate X pan: convert total drag distance to a fractional
        // slot delta from the offset captured at drag start. No rounding
        // dead-zone — any movement pans proportionally.
        const slotW      = chartWNow() / Math.max(1, L.safeCount);
        const dx         = e.clientX - d.startX;
        const deltaSlots = -dx / slotW;
        const maxOff     = Math.max(0, L.totalSlots - L.safeCount);
        const newOffset  = clamp(Math.round(d.startOffset + deltaSlots), 0, maxOff);
        setViewOffset(newOffset);
        atTailRef.current = (newOffset + L.safeCount >= L.totalSlots);
      }
    }

    function onPointerUp() {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      setIsDragging(false);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    }

    function onPointerDown(e) {
      if (e.button !== 0) return;
      const rect   = el.getBoundingClientRect();
      const localX = e.clientX - rect.left;
      const isY    = e.shiftKey || localX < MARGIN.left;  // gutter = Y pan
      dragRef.current = {
        active: true,
        startX: e.clientX, startOffset: liveRef.current.safeOffset,
        startY: e.clientY, startYOffset: liveRef.current.yOffset,
        isY,
      };
      setIsDragging(true);
      setTooltip(null);
      // Track on window so the drag survives leaving the SVG bounds.
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    }

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onPointerDown);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [MAIN_H]);  // MAIN_H changes only on resize; listeners otherwise stable

  // Y-zoom rail buttons
  const yZoomIn  = () => setYZoom(z => clamp(z * 1.25, 0.2, 20));
  const yZoomOut = () => setYZoom(z => clamp(z / 1.25, 0.2, 20));

  if (!candles || candles.length < 2) {
    return (
      <div style={{ height: chartHeight, display: "flex", alignItems: "center", justifyContent: "center", color: C.textMuted, fontSize: 12 }}>
        Waiting for candle data…
      </div>
    );
  }

  const chartW  = Math.max(0, width - MARGIN.left - MARGIN.right);
  const mainTop = MARGIN.top;
  const rsiTop  = mainTop + MAIN_H + RSI_GAP;
  const slotW   = visibleSlots > 0 ? chartW / visibleSlots : chartW;
  const candleW = Math.max(1.5, slotW * 0.72);
  const candleX = (i) => MARGIN.left + i * slotW + slotW / 2;

  const priceSource = visible.length > 0 ? visible : candles.slice(-1);
  const prices = priceSource.flatMap(c =>
    [c.open, c.high, c.low, c.close, c.bb_upper, c.bb_lower, c.r1, c.s1, c[keys.supertrend]]
      .filter(v => v != null && isFinite(v))
  );
  const rawMin   = prices.length ? Math.min(...prices) : 50000;
  const rawMax   = prices.length ? Math.max(...prices) : 51000;
  const pad      = (rawMax - rawMin) * 0.06 || 10;
  const autoMinP = rawMin - pad;
  const autoMaxP = rawMax + pad;
  const rangeP   = (autoMaxP - autoMinP) || 1;
  rangePRef.current = rangeP;

  const centerP   = (autoMinP + autoMaxP) / 2 - yOffset;
  const halfR     = (rangeP / 2) / yZoom;
  const minP      = centerP - halfR;
  const maxP      = centerP + halfR;
  const viewRange = (maxP - minP) || 1;

  const py   = (price) => mainTop + MAIN_H * (1 - (price - minP) / viewRange);
  const rsiY = (v)     => rsiTop + RSI_H * (1 - clamp(v, 0, 100) / 100);

  const tickCount  = 6;
  const priceTicks = Array.from({ length: tickCount + 1 }, (_, i) => minP + (viewRange / tickCount) * i);

  const lastCandleTs = totalCandles ? candles[totalCandles - 1].ts : 0;
  const timeStep     = Math.max(1, Math.floor(visibleSlots / 8));
  const timeTicks    = (() => {
    const ticks = [];
    visible.forEach((c, i) => { if (i % timeStep === 0) ticks.push({ i, ts: c.ts, future: false }); });
    for (let j = 0; j < visibleFutureSlots; j++) {
      const slotIdx = visible.length + j;
      if (slotIdx % timeStep === 0) {
        const futureSlotNum = safeOffset + slotIdx - totalCandles;
        ticks.push({ i: slotIdx, ts: lastCandleTs + (futureSlotNum + 1) * 180, future: true });
      }
    }
    return ticks;
  })();

  // Day boundaries within the visible window — draw a separator + date label
  // wherever the trading day changes between adjacent visible candles.
  const dayBoundaries = [];
  for (let i = 1; i < visible.length; i++) {
    if (dayKey(visible[i].ts) !== dayKey(visible[i - 1].ts)) {
      dayBoundaries.push({ i, ts: visible[i].ts });
    }
  }
  // Also label the first visible candle's day at the left edge.
  const firstDayTs = visible.length ? visible[0].ts : null;

  // SuperTrend segments
  const stSegments = [];
  let seg = null;
  visible.forEach((c, i) => {
    const stVal = c[keys.supertrend];
    const stDir = c[keys.st_direction]?.toUpperCase();
    if (stVal == null) { seg = null; return; }
    const pt = { x: candleX(i), y: py(stVal) };
    if (!seg || seg.dir !== stDir) {
      if (seg) { seg.points.push(pt); stSegments.push(seg); }
      seg = { dir: stDir, points: [pt] };
    } else {
      seg.points.push(pt);
    }
  });
  if (seg) stSegments.push(seg);

  const bbUpperPts = visible.map((c, i) => c.bb_upper  != null ? { x: candleX(i), y: py(c.bb_upper)  } : null).filter(Boolean);
  const bbLowerPts = visible.map((c, i) => c.bb_lower  != null ? { x: candleX(i), y: py(c.bb_lower)  } : null).filter(Boolean);
  const bbMidPts   = visible.map((c, i) => c.bb_middle != null ? { x: candleX(i), y: py(c.bb_middle) } : null).filter(Boolean);
  const rsiPts     = visible.map((c, i) => c.rsi_smooth != null ? { x: candleX(i), y: rsiY(c.rsi_smooth) } : null).filter(Boolean);

  let bbAreaPath = "";
  if (bbUpperPts.length > 1 && bbLowerPts.length > 1) {
    const upper    = bbUpperPts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
    const lowerRev = [...bbLowerPts].reverse().map(p => `L ${p.x} ${p.y}`).join(" ");
    bbAreaPath = `${upper} ${lowerRev} Z`;
  }
  const linePath = (pts) => pts.length < 2 ? "" : pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");

  const lastR1 = [...visible].reverse().find(c => c.r1 != null)?.r1;
  const lastS1 = [...visible].reverse().find(c => c.s1 != null)?.s1;
  const lastR2 = strategyId === "BB_V2" ? [...visible].reverse().find(c => c.r2 != null)?.r2 : null;
  const lastPP = strategyId === "BB_V2" ? [...visible].reverse().find(c => c.pp != null)?.pp : null;
  const lastS2 = strategyId === "BB_V2" ? [...visible].reverse().find(c => c.s2 != null)?.s2 : null;
  const lastS3 = strategyId === "BB_V2" ? [...visible].reverse().find(c => c.s3 != null)?.s3 : null;

  const mainClipId = `mainClip_${instanceId}`;
  const rsiClipId  = `rsiClip_${instanceId}`;
  const arrowSize  = Math.max(6, Math.min(10, slotW * 0.8));

  function PivotLine({ value, color, labelText, yLabelOffset = -3 }) {
    if (value == null) return null;
    const yVal = py(value);
    if (yVal < mainTop || yVal > mainTop + MAIN_H) return null;
    return (
      <>
        <line x1={MARGIN.left} y1={yVal} x2={MARGIN.left + chartW} y2={yVal}
          stroke={color} strokeWidth={1} strokeDasharray="6 4" opacity={0.7} />
        <text x={MARGIN.left + chartW - 2} y={yVal + yLabelOffset}
          textAnchor="end" fontSize={9} fill={color} fontFamily={MONO}>
          {labelText} {Math.round(value)}
        </text>
      </>
    );
  }

  return (
    <div style={{ position: "relative", userSelect: "none" }}>
      <svg
        ref={svgRef}
        width={width}
        height={TOTAL_H}
        onDoubleClick={yAutoFit}
        style={{ display: "block", fontFamily: FONT, cursor: isDragging ? "grabbing" : "crosshair", touchAction: "none" }}
        onMouseLeave={() => setTooltip(null)}
      >
        <defs>
          <clipPath id={mainClipId}>
            <rect x={MARGIN.left} y={mainTop} width={Math.max(0, chartW)} height={MAIN_H} />
          </clipPath>
          <clipPath id={rsiClipId}>
            <rect x={MARGIN.left} y={rsiTop} width={Math.max(0, chartW)} height={RSI_H} />
          </clipPath>
          <filter id={`glowCE_${instanceId}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          <filter id={`glowPE_${instanceId}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>

        {/* Left price gutter — visual affordance that it is the Y-drag zone */}
        <rect x={0} y={mainTop} width={MARGIN.left} height={MAIN_H}
          fill="transparent" style={{ cursor: "ns-resize" }} />

        {/* Grid */}
        {priceTicks.map((p, i) => (
          <line key={i} x1={MARGIN.left} y1={py(p)} x2={MARGIN.left + chartW} y2={py(p)}
            stroke={C.borderDim} strokeWidth={0.5} />
        ))}
        {priceTicks.map((p, i) => (
          <text key={i} x={MARGIN.left - 6} y={py(p) + 4}
            textAnchor="end" fontSize={9} fill={C.textMuted} fontFamily={MONO}>
            {Math.round(p)}
          </text>
        ))}

        {/* Day separators + labels (multi-day context) */}
        <g clipPath={`url(#${mainClipId})`}>
          {dayBoundaries.map((b, bi) => {
            const x = MARGIN.left + b.i * slotW;
            return (
              <g key={bi}>
                <line x1={x} y1={mainTop} x2={x} y2={rsiTop + RSI_H}
                  stroke={C.daySep} strokeWidth={1} strokeDasharray="2 4" />
                <text x={x + 3} y={mainTop + 10} fontSize={8.5} fill={C.textSec}
                  fontFamily={MONO} opacity={0.8}>
                  {fmtDayLabel(b.ts)}
                </text>
              </g>
            );
          })}
        </g>
        {/* First-day label at the left edge */}
        {firstDayTs != null && (
          <text x={MARGIN.left + 3} y={mainTop + 10} fontSize={8.5} fill={C.textSec}
            fontFamily={MONO} opacity={0.6}>
            {fmtDayLabel(firstDayTs)}
          </text>
        )}

        {/* BB bands */}
        {bbAreaPath && <path d={bbAreaPath} fill={C.bbFill} clipPath={`url(#${mainClipId})`} />}
        {bbUpperPts.length > 1 && <path d={linePath(bbUpperPts)} fill="none" stroke={C.bbUpper} strokeWidth={1} strokeDasharray="4 3" clipPath={`url(#${mainClipId})`} />}
        {bbMidPts.length > 1   && <path d={linePath(bbMidPts)}   fill="none" stroke={C.bbMiddle} strokeWidth={0.8} strokeDasharray="3 4" clipPath={`url(#${mainClipId})`} />}
        {bbLowerPts.length > 1 && <path d={linePath(bbLowerPts)} fill="none" stroke={C.bbLower} strokeWidth={1} strokeDasharray="4 3" clipPath={`url(#${mainClipId})`} />}

        {/* Pivot lines */}
        {strategyId === "BB_V2" && (
          <>
            <PivotLine value={lastR2} color="#a78bfa" labelText="R2" yLabelOffset={-3} />
            <PivotLine value={lastPP} color="#64748b" labelText="PP" yLabelOffset={-3} />
            <PivotLine value={lastS2} color="#06b6d4" labelText="S2" yLabelOffset={10}  />
            <PivotLine value={lastS3} color="#0891b2" labelText="S3" yLabelOffset={10}  />
          </>
        )}
        <PivotLine value={lastR1} color={C.r1} labelText="R1" yLabelOffset={-3} />
        <PivotLine value={lastS1} color={C.s1} labelText="S1" yLabelOffset={10}  />

        {/* SuperTrend */}
        <g clipPath={`url(#${mainClipId})`}>
          {stSegments.map((seg, si) => (
            <path key={si} d={linePath(seg.points)} fill="none"
              stroke={seg.dir === "UP" ? C.stUp : C.stDown}
              strokeWidth={1.8} opacity={0.9} />
          ))}
        </g>

        {/* Candles */}
        <g clipPath={`url(#${mainClipId})`}>
          {visible.map((c, i) => {
            const x       = candleX(i);
            const isGreen = c.close >= c.open;
            const fill    = isGreen ? C.green : C.red;
            const bodyTop = py(Math.max(c.open, c.close));
            const bodyBot = py(Math.min(c.open, c.close));
            const bodyH   = Math.max(1, bodyBot - bodyTop);
            return (
              <g key={i} onMouseEnter={() => {
                if (!dragRef.current.active)
                  setTooltip({ x: Math.min(x, width - 160), y: mainTop, candle: c });
              }}>
                <line x1={x} y1={py(c.high)} x2={x} y2={py(c.low)} stroke={C.wick} strokeWidth={1} />
                <rect x={x - candleW / 2} y={bodyTop} width={candleW} height={bodyH} fill={fill} opacity={0.85} />
              </g>
            );
          })}
        </g>

        {/* Signal markers */}
        <g clipPath={`url(#${mainClipId})`}>
          {visible.map((c, i) => {
            const action = c[keys.signal_action];
            if (!action) return null;
            const x  = candleX(i);
            const hw = arrowSize;
            const ah = arrowSize * 1.1;
            const gap = 6;

            if (action === "ENTER_CE") {
              const wickBottom = py(c.low);
              const arrowTip   = wickBottom + gap;
              const arrowBase  = arrowTip + ah;
              const stemStart  = arrowBase + 2;
              const stemEnd    = stemStart + 8;
              return (
                <g key={i} filter={`url(#glowCE_${instanceId})`}>
                  <line x1={x} y1={stemStart} x2={x} y2={stemEnd} stroke={C.sigEnterCE} strokeWidth={1.5} opacity={0.5} />
                  <polygon points={`${x},${arrowTip} ${x - hw},${arrowBase} ${x + hw},${arrowBase}`} fill={C.sigEnterCE} opacity={0.95} />
                  <text x={x} y={stemEnd + 9} textAnchor="middle" fontSize={7.5} fill={C.sigEnterCE} fontWeight="800" fontFamily={MONO}>CE</text>
                </g>
              );
            }
            if (action === "ENTER_PE") {
              const wickTop   = py(c.high);
              const arrowTip  = wickTop - gap;
              const arrowBase = arrowTip - ah;
              const stemStart = arrowBase - 2;
              const stemEnd   = stemStart - 8;
              return (
                <g key={i} filter={`url(#glowPE_${instanceId})`}>
                  <line x1={x} y1={stemStart} x2={x} y2={stemEnd} stroke={C.sigEnterPE} strokeWidth={1.5} opacity={0.5} />
                  <polygon points={`${x},${arrowTip} ${x - hw},${arrowBase} ${x + hw},${arrowBase}`} fill={C.sigEnterPE} opacity={0.95} />
                  <text x={x} y={stemEnd - 3} textAnchor="middle" fontSize={7.5} fill={C.sigEnterPE} fontWeight="800" fontFamily={MONO}>PE</text>
                </g>
              );
            }
            if (action === "EXIT_CE") {
              const y = py(c.close); const r = arrowSize * 0.9;
              return (
                <g key={i}>
                  <circle cx={x} cy={y} r={r} fill="rgba(250,204,21,0.18)" stroke={C.sigExitCE} strokeWidth={1.8} />
                  <line x1={x - r*0.55} y1={y - r*0.55} x2={x + r*0.55} y2={y + r*0.55} stroke={C.sigExitCE} strokeWidth={1.6} />
                  <line x1={x + r*0.55} y1={y - r*0.55} x2={x - r*0.55} y2={y + r*0.55} stroke={C.sigExitCE} strokeWidth={1.6} />
                </g>
              );
            }
            if (action === "EXIT_PE") {
              const y = py(c.close); const r = arrowSize * 0.9;
              return (
                <g key={i}>
                  <circle cx={x} cy={y} r={r} fill="rgba(232,121,249,0.18)" stroke={C.sigExitPE} strokeWidth={1.8} />
                  <line x1={x - r*0.55} y1={y - r*0.55} x2={x + r*0.55} y2={y + r*0.55} stroke={C.sigExitPE} strokeWidth={1.6} />
                  <line x1={x + r*0.55} y1={y - r*0.55} x2={x - r*0.55} y2={y + r*0.55} stroke={C.sigExitPE} strokeWidth={1.6} />
                </g>
              );
            }
            return null;
          })}
        </g>

        {/* Future zone */}
        {visibleFutureSlots > 0 && (() => {
          const futureStartX = MARGIN.left + visible.length * slotW;
          const futureW      = visibleFutureSlots * slotW;
          const closeSlotRel = (totalCandles + futureSlots - 1) - safeOffset;
          const closeX       = MARGIN.left + closeSlotRel * slotW + slotW / 2;
          const showClose    = closeX > MARGIN.left && closeX < MARGIN.left + chartW;
          return (
            <>
              <rect x={futureStartX} y={mainTop} width={futureW} height={MAIN_H} fill="rgba(255,255,255,0.01)" />
              {showClose && (
                <>
                  <line x1={closeX} y1={mainTop} x2={closeX} y2={rsiTop + RSI_H} stroke={C.amber} strokeWidth={0.8} strokeDasharray="4 3" opacity={0.35} />
                  <text x={closeX + 3} y={mainTop + 11} fontSize={8} fill={C.amber} opacity={0.45}>15:30</text>
                </>
              )}
            </>
          );
        })()}

        <rect x={MARGIN.left} y={mainTop} width={chartW} height={MAIN_H} fill="none" stroke={C.border} strokeWidth={0.5} />

        {/* RSI pane */}
        <rect x={MARGIN.left} y={rsiTop} width={chartW} height={RSI_H} fill={C.bgSurface} opacity={0.3} />
        <rect x={MARGIN.left} y={rsiY(100)} width={chartW} height={rsiY(70) - rsiY(100)} fill="rgba(239,68,68,0.08)" clipPath={`url(#${rsiClipId})`} />
        <rect x={MARGIN.left} y={rsiY(35)}  width={chartW} height={rsiY(0)  - rsiY(35)}  fill="rgba(16,185,129,0.08)" clipPath={`url(#${rsiClipId})`} />
        <g clipPath={`url(#${rsiClipId})`}>
          {rsiPts.length > 1 && (
            <>
              <path d={`${linePath(rsiPts)} L ${rsiPts[rsiPts.length-1].x} ${rsiTop+RSI_H} L ${rsiPts[0].x} ${rsiTop+RSI_H} Z`} fill={C.rsiFill} />
              <path d={linePath(rsiPts)} fill="none" stroke={C.rsiLine} strokeWidth={1.4} />
            </>
          )}
        </g>
        <line x1={MARGIN.left} y1={rsiY(70)} x2={MARGIN.left+chartW} y2={rsiY(70)} stroke={C.red}       strokeWidth={0.8} strokeDasharray="3 3" opacity={0.6} />
        <text x={MARGIN.left-4} y={rsiY(70)+3} textAnchor="end" fontSize={8} fill={C.red}   fontFamily={MONO} opacity={0.8}>70</text>
        <line x1={MARGIN.left} y1={rsiY(35)} x2={MARGIN.left+chartW} y2={rsiY(35)} stroke={C.green}     strokeWidth={0.8} strokeDasharray="3 3" opacity={0.6} />
        <text x={MARGIN.left-4} y={rsiY(35)+3} textAnchor="end" fontSize={8} fill={C.green} fontFamily={MONO} opacity={0.8}>35</text>
        <line x1={MARGIN.left} y1={rsiY(50)} x2={MARGIN.left+chartW} y2={rsiY(50)} stroke={C.borderDim} strokeWidth={0.5} />
        <text x={MARGIN.left+4} y={rsiTop+10} fontSize={9} fill={C.textMuted}>RSI</text>
        <rect x={MARGIN.left} y={rsiTop} width={chartW} height={RSI_H} fill="none" stroke={C.border} strokeWidth={0.5} />

        {/* Time axis */}
        {timeTicks.map(({ i, ts, future }) => {
          const tx = MARGIN.left + i * slotW + slotW / 2;
          return (
            <g key={i} opacity={future ? 0.35 : 1}>
              <line x1={tx} y1={rsiTop+RSI_H} x2={tx} y2={rsiTop+RSI_H+4} stroke={future ? C.borderDim : C.border} strokeWidth={0.8} strokeDasharray={future ? "2 2" : "none"} />
              <text x={tx} y={rsiTop+RSI_H+14} textAnchor="middle" fontSize={8.5} fill={C.textMuted} fontFamily={MONO}>{fmtTime(ts)}</text>
            </g>
          );
        })}

        {/* Legend */}
        {[
          { color: C.bbUpper,    label: "BB",  dash: true  },
          { color: C.stUp,       label: "ST▲", dash: false },
          { color: C.stDown,     label: "ST▼", dash: false },
          { color: C.r1,         label: "R1",  dash: true  },
          { color: C.s1,         label: "S1",  dash: true  },
          { color: C.sigEnterCE, label: "▲CE", dash: false },
          { color: C.sigEnterPE, label: "▼PE", dash: false },
          { color: C.sigExitCE,  label: "✕CE", dash: false },
          { color: C.sigExitPE,  label: "✕PE", dash: false },
        ].map((item, li) => (
          <g key={li} transform={`translate(${MARGIN.left + li * 46}, ${mainTop - 1})`}>
            <line x1={0} y1={6} x2={14} y2={6} stroke={item.color} strokeWidth={1.5} strokeDasharray={item.dash ? "4 2" : "none"} />
            <text x={17} y={10} fontSize={8.5} fill={C.textMuted}>{item.label}</text>
          </g>
        ))}

        {/* Scrollbar */}
        {totalSlots > safeCount && (() => {
          const barY   = TOTAL_H - 5;
          const barW   = chartW;
          const thumbW = Math.max(20, (safeCount / totalSlots) * barW);
          const thumbX = MARGIN.left + (safeOffset / totalSlots) * barW;
          return (
            <g opacity={0.45}>
              <rect x={MARGIN.left} y={barY} width={barW} height={3} fill={C.borderDim} rx={1.5} />
              <rect x={thumbX} y={barY} width={thumbW} height={3} fill={C.blue} rx={1.5} />
            </g>
          );
        })()}

        <text x={MARGIN.left + chartW - 2} y={mainTop - 2} textAnchor="end" fontSize={8} fill={C.textMuted} fontFamily={MONO} opacity={0.5}>
          {safeOffset+1}–{safeOffset+visible.length}/{totalCandles}
        </text>
      </svg>

      {/* Y-zoom rail (right edge) */}
      <div style={{
        position: "absolute", top: mainTop + 6, right: 2,
        display: "flex", flexDirection: "column", gap: 4, zIndex: 6,
      }}>
        <button onClick={yZoomIn} title="Zoom Y in (or Shift+scroll)"
          style={ctrlBtnStyle}>＋</button>
        <button onClick={yZoomOut} title="Zoom Y out (or Shift+scroll)"
          style={ctrlBtnStyle}>－</button>
        <button onClick={yAutoFit} title="Auto-fit Y (or double-click)"
          style={ctrlBtnStyle}>⤢</button>
      </div>

      {/* Tooltip */}
      {tooltip && !isDragging && (
        <div style={{
          position: "absolute", top: tooltip.y + 16,
          left: clamp(tooltip.x - 10, 0, width - 180),
          background: C.bgCard, border: `1px solid ${C.border}`,
          borderRadius: 6, padding: "7px 10px",
          fontSize: 11, fontFamily: MONO, color: C.textSec,
          pointerEvents: "none", zIndex: 10, minWidth: 160,
          boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
        }}>
          <div style={{ color: C.textMuted, fontSize: 9, marginBottom: 4 }}>
            {fmtDayLabel(tooltip.candle.ts)} {fmtTime(tooltip.candle.ts)}
          </div>
          {[["O", tooltip.candle.open], ["H", tooltip.candle.high], ["L", tooltip.candle.low], ["C", tooltip.candle.close]].map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
              <span style={{ color: C.textMuted }}>{k}</span>
              <span>{fmtPrice(v)}</span>
            </div>
          ))}
          {tooltip.candle.bb_upper != null && (
            <>
              <div style={{ borderTop: `1px solid ${C.borderDim}`, margin: "4px 0" }} />
              {[["BB↑", tooltip.candle.bb_upper], ["BB—", tooltip.candle.bb_middle], ["BB↓", tooltip.candle.bb_lower]].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                  <span style={{ color: C.blue, fontSize: 10 }}>{k}</span>
                  <span>{fmtPrice(v)}</span>
                </div>
              ))}
            </>
          )}
          {(tooltip.candle.rsi_raw != null || tooltip.candle.rsi_smooth != null) && (
            <>
              {tooltip.candle.rsi_raw != null && (
                <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 2 }}>
                  <span style={{ color: C.violet }}>RSI (raw)</span>
                  <span style={{ color: tooltip.candle.rsi_raw > 70 ? C.red : tooltip.candle.rsi_raw < 35 ? C.green : C.violet }}>
                    {tooltip.candle.rsi_raw.toFixed(1)}
                  </span>
                </div>
              )}
              {tooltip.candle.rsi_smooth != null && (
                <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 1 }}>
                  <span style={{ color: C.violet, opacity: 0.6 }}>RSI (smooth)</span>
                  <span style={{ color: C.violet, opacity: 0.7, fontSize: 10 }}>{tooltip.candle.rsi_smooth.toFixed(1)}</span>
                </div>
              )}
            </>
          )}
          {tooltip.candle[keys.supertrend] != null && (
            <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 2 }}>
              <span style={{ color: tooltip.candle[keys.st_direction] === "UP" ? C.stUp : C.stDown }}>
                ST {tooltip.candle[keys.st_direction]}
              </span>
              <span>{fmtPrice(tooltip.candle[keys.supertrend])}</span>
            </div>
          )}
          {tooltip.candle[keys.signal_action] && (
            <div style={{ marginTop: 4 }}>
              <SignalBadge action={tooltip.candle[keys.signal_action]} />
              {tooltip.candle[keys.signal_reason] && (
                <div style={{ fontSize: 9, color: C.textMuted, marginTop: 2, maxWidth: 160, wordBreak: "break-word" }}>
                  {tooltip.candle[keys.signal_reason]}
                </div>
              )}
            </div>
          )}
          {tooltip.candle[keys.rejection_reason] && (
            <div style={{ fontSize: 9, color: C.amber, marginTop: 2, maxWidth: 160, wordBreak: "break-word" }}>
              ⚠ {tooltip.candle[keys.rejection_reason]}
            </div>
          )}
        </div>
      )}

      {(yZoom !== 1 || yOffset !== 0) && (
        <div onClick={yAutoFit} style={{
          position: "absolute", top: 8, left: MARGIN.left + 4,
          background: "rgba(245,158,11,0.18)", border: "1px solid rgba(245,158,11,0.4)",
          borderRadius: 4, padding: "2px 7px", fontSize: 10, color: C.amber,
          cursor: "pointer", fontFamily: MONO, userSelect: "none", zIndex: 4,
        }} title="Double-click chart or click here to reset Y axis">
          Y {yZoom.toFixed(1)}× · reset
        </div>
      )}

      {!atTailRef.current && (
        <button onClick={jumpToLatest} style={{
          position: "absolute", bottom: 36, right: MARGIN.right + 8,
          background: C.bgCard, border: `1px solid ${C.blue}`,
          borderRadius: 4, color: C.blue, fontSize: 10, fontFamily: MONO,
          padding: "3px 8px", cursor: "pointer", zIndex: 5,
          display: "flex", alignItems: "center", gap: 4,
        }}>
          ▶▶ Latest
        </button>
      )}
    </div>
  );
}

const ctrlBtnStyle = {
  width: 22, height: 22, borderRadius: 4,
  background: C.bgCard, border: `1px solid ${C.border}`,
  color: C.textSec, cursor: "pointer", fontSize: 13, lineHeight: 1,
  display: "flex", alignItems: "center", justifyContent: "center",
  fontFamily: MONO, padding: 0,
};

/* ─── BB-width squeeze bar (compact mode) ───────────────────── */
function BBWidthBar({ candles }) {
  if (!candles.length) return null;
  const last = candles[candles.length - 1];
  if (last.bb_width == null) return null;
  const widths      = candles.map(c => c.bb_width).filter(v => v != null);
  const maxW        = Math.max(...widths, 1);
  const pct         = clamp((last.bb_width / maxW) * 100, 0, 100);
  const isSqueezing = pct < 25;
  return (
    <div style={{ padding: "6px 10px 8px", borderTop: `1px solid ${C.borderDim}` }}>
      <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3, display: "flex", justifyContent: "space-between" }}>
        <span>BB Width</span>
        <span style={{ color: isSqueezing ? C.amber : C.textMuted }}>
          {isSqueezing ? "⚡ SQUEEZE" : last.bb_width.toFixed(2)}
        </span>
      </div>
      <div style={{ height: 3, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: isSqueezing ? C.amber : C.blue, borderRadius: 2, transition: "width 0.4s ease" }} />
      </div>
    </div>
  );
}

/* ─── Fullscreen overlay ─────────────────────────────────────── */
function FullscreenChart({ candles, activeSymbol, config, positions, onBecomePrimary, onClose, strategyId, openPnl }) {
  const containerRef = useRef(null);
  const [dims, setDims] = useState({ width: window.innerWidth, height: window.innerHeight });

  useEffect(() => {
    const ro = new ResizeObserver(([entry]) => setDims({
      width:  entry.contentRect.width  || window.innerWidth,
      height: entry.contentRect.height || window.innerHeight,
    }));
    if (containerRef.current) {
      ro.observe(containerRef.current);
      setDims({ width: containerRef.current.offsetWidth || window.innerWidth, height: containerRef.current.offsetHeight || window.innerHeight });
    }
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 9999, background: C.bg, display: "flex", flexDirection: "column" }}>
      <PanelHeader
        candles={candles} isPrimary={true} onBecomePrimary={onBecomePrimary}
        activeSymbol={activeSymbol} config={config} strategyId={strategyId}
        isFullscreen={true} onToggleFullscreen={onClose} openPnl={openPnl}
      />
      <InfoStrip
        config={config} positions={positions}
        activeSymbol={activeSymbol} onOpenSettings={() => {}}
        strategyId={strategyId} openPnl={openPnl}
      />
      <div ref={containerRef} style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        <CandleChart candles={candles} width={dims.width} chartHeight={dims.height} instanceId="fullscreen" strategyId={strategyId} />
      </div>
    </div>
  );
}

/* ─── Error boundary ────────────────────────────────────────── */
import React from "react";
class ChartErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(err) { return { error: err }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ height: 460, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#ef4444", fontSize: 12, gap: 8, padding: 24 }}>
          <div style={{ fontWeight: 700 }}>Chart render error</div>
          <div style={{ color: "#64748b", textAlign: "center", maxWidth: 320 }}>{String(this.state.error.message)}</div>
          <button onClick={() => this.setState({ error: null })} style={{ marginTop: 8, padding: "4px 12px", background: "#1e293b", border: "1px solid #334155", borderRadius: 4, color: "#94a3b8", cursor: "pointer", fontSize: 11 }}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ─── Main component ────────────────────────────────────────── */
export default function BBPanel({ ltpMap: ltpMapProp, isPrimary, onBecomePrimary, strategyId = "BB_V1" }) {
  const [candles, setCandles]           = useState([]);
  const [activeSymbol, setActiveSymbol] = useState("…");
  const [config, setConfig]             = useState(null);
  const [tradeState, setTradeState]     = useState(null);
  const [positions, setPositions]       = useState(null);
  const [ltpMap, setLtpMap]             = useState({});
  const [error, setError]               = useState(null);
  const [loading, setLoading]           = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const containerRef = useRef(null);
  const chartAreaRef = useRef(null);
  const [chartWidth, setChartWidth]   = useState(800);
  const [chartHeight, setChartHeight] = useState(450);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([e]) => setChartWidth(e.contentRect.width || 800));
    ro.observe(containerRef.current);
    setChartWidth(containerRef.current.offsetWidth || 800);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!chartAreaRef.current) return;
    const ro = new ResizeObserver(([e]) => setChartHeight(e.contentRect.height || 450));
    ro.observe(chartAreaRef.current);
    setChartHeight(chartAreaRef.current.offsetHeight || 450);
    return () => ro.disconnect();
  }, [isPrimary]);

  const fetchCandles = useCallback(async () => {
    // Multi-day: request a larger window so several sessions are available.
    // We only vary `limit` (a param the endpoint already accepts) — no new
    // params that could trip server-side validation. If the larger limit is
    // rejected (e.g. it exceeds a server-side ceiling and returns 422), we
    // transparently fall back to the original known-good limit so the chart
    // never goes blank.
    const base = `${getApiBase()}/futures/candles?symbol=auto&timeframe=3m`;
    const attempts = [
      `${base}&limit=${CANDLE_LIMIT}`,  // preferred multi-day window
      `${base}&limit=200`,              // known-good fallback (original)
      base,                             // last resort: endpoint default
    ];

    let lastErr = null;
    for (const url of attempts) {
      try {
        const res = await fetch(url);
        if (!res.ok) { lastErr = new Error(`HTTP ${res.status}`); continue; }
        const data = await res.json();
        if (data.error) { lastErr = new Error(data.error); continue; }
        // SESSION FILTER: drop anything outside 09:15–15:30 (and weekends).
        // This is the single gate that removes off-session candles AND the
        // CE/PE markers that ride on them.
        const sessionCandles = filterToSession(data.candles || []);
        setCandles(sessionCandles);
        setActiveSymbol(data.symbol || "—");
        setError(null);
        setLoading(false);
        return;
      } catch (e) {
        lastErr = e;
      }
    }
    setError(lastErr ? lastErr.message : "Failed to load candles");
    setLoading(false);
  }, []);

  const fetchMeta = useCallback(async () => {
    try {
      const [cfg, ts, pos] = await Promise.all([
        getStrategyConfig(strategyId),
        getTradeState(strategyId),
        getTodayPositions(),
      ]);
      setConfig(cfg || null);
      setTradeState(ts || null);
      setPositions(pos || null);
    } catch { /* non-fatal */ }
  }, [strategyId]);

  // Live LTP snapshot for per-strategy open P&L. If a parent already passes
  // ltpMap via props, prefer that; otherwise poll /ltp_snapshot here.
  const fetchLtp = useCallback(async () => {
    if (ltpMapProp && Object.keys(ltpMapProp).length) return; // parent provides it
    try {
      const res = await fetch(`${getApiBase()}/ltp_snapshot`);
      if (!res.ok) return;
      const data = await res.json();
      if (data && typeof data === "object") {
        const normalized = {};
        Object.entries(data).forEach(([symbol, price]) => {
          normalized[symbol.replace(/\s+/g, "").toUpperCase()] = price;
        });
        setLtpMap(normalized);
      }
    } catch { /* non-fatal */ }
  }, [ltpMapProp]);

  useEffect(() => {
    fetchCandles();
    fetchMeta();
    fetchLtp();
    const candleInterval = isMarketHours() ? 15_000 : 60_000;
    const metaInterval   = isMarketHours() ? 10_000 : 60_000;
    const ltpInterval    = isMarketHours() ? 2_000  : 30_000;
    pollRef.current      = setInterval(fetchCandles, candleInterval);
    const metaPoll       = setInterval(fetchMeta, metaInterval);
    const ltpPoll        = setInterval(fetchLtp, ltpInterval);
    return () => { clearInterval(pollRef.current); clearInterval(metaPoll); clearInterval(ltpPoll); };
  }, [fetchCandles, fetchMeta, fetchLtp]);

  // Effective LTP map: parent prop wins if present, else our own poll.
  const effLtpMap = useMemo(() => {
    if (ltpMapProp && Object.keys(ltpMapProp).length) {
      const norm = {};
      Object.entries(ltpMapProp).forEach(([s, p]) => {
        norm[s.replace(/\s+/g, "").toUpperCase()] = p;
      });
      return norm;
    }
    return ltpMap;
  }, [ltpMapProp, ltpMap]);

  // Per-strategy open P&L (LONG), recomputed when positions or ticks change.
  const openPnl = useMemo(
    () => computeStrategyOpenPnl(positions, strategyId, effLtpMap),
    [positions, strategyId, effLtpMap]
  );

  const hasData = candles.length > 0;

  return (
    <>
      {isFullscreen && hasData && (
        <FullscreenChart
          candles={candles} activeSymbol={activeSymbol}
          config={config} positions={positions}
          onBecomePrimary={onBecomePrimary} onClose={() => setIsFullscreen(false)}
          strategyId={strategyId} openPnl={openPnl}
        />
      )}

      <div
        ref={containerRef}
        style={{
          background: C.bg, border: `1px solid ${C.border}`,
          borderRadius: 8, overflow: "hidden",
          display: "flex", flexDirection: "column",
          height: "100%", minWidth: 0,
        }}
      >
        {hasData ? (
          <PanelHeader
            candles={candles} isPrimary={isPrimary}
            onBecomePrimary={onBecomePrimary} activeSymbol={activeSymbol}
            config={config} strategyId={strategyId}
            isFullscreen={isFullscreen}
            onToggleFullscreen={() => setIsFullscreen(v => !v)}
            openPnl={openPnl}
          />
        ) : (
          <div style={{ padding: "10px 14px", display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: C.blue, letterSpacing: "0.8px" }}>
              {strategyId === "BB_V2" ? "BB V2" : "BB"}
            </div>
            <div style={{ fontSize: 11, color: C.textMuted }}>
              {loading ? "Loading…" : error ? `Error: ${error}` : "No data"}
            </div>
          </div>
        )}

        {isPrimary && hasData && (
          <InfoStrip
            config={config} positions={positions}
            activeSymbol={activeSymbol} onOpenSettings={() => {}}
            strategyId={strategyId} openPnl={openPnl}
          />
        )}

        {isPrimary && hasData && (
          <div ref={chartAreaRef} style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
            <ChartErrorBoundary>
              <CandleChart
                candles={candles} width={chartWidth}
                chartHeight={chartHeight} instanceId="panel"
                strategyId={strategyId}
              />
            </ChartErrorBoundary>
          </div>
        )}

        {!isPrimary && hasData && <BBWidthBar candles={candles} />}

        {isPrimary && error && !hasData && (
          <div style={{ padding: 16, color: C.red, fontSize: 12 }}>
            Failed to load candles: {error}
          </div>
        )}
      </div>
    </>
  );
}
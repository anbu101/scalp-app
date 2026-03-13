/**
 * BBPanel — src/strategies/bb_v1/BBPanel.jsx
 *
 * Primary mode : compact info strip + interactive chart (pan/zoom/fullscreen)
 * Compact mode : ARMED/PAPER badges + BB-width squeeze bar
 *
 * Interactions:
 *   Scroll wheel  → zoom in/out centred on cursor
 *   Click + drag  → pan left/right
 *   Auto-follow   → locks to latest unless user has panned away
 *   Fullscreen    → fixed overlay (Escape or ✕ to close)
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { getApiBase } from "../../api/base";
import { getStrategyConfig, getTradeState, getTodayPositions } from "../../api";

/* ─── Design tokens ─────────────────────────────────────────────
   Aligned with the canonical palette used across all pages:
   Connections.jsx / Dashboard.jsx / ScalpPanel.jsx
─────────────────────────────────────────────────────────────── */
const C = {
  /* Backgrounds — match colors.bg.* across the app */
  bg:        "#020817",   // colors.bg.primary
  bgCard:    "#0f172a",   // colors.bg.secondary
  bgSurface: "#1e293b",   // colors.bg.tertiary

  /* Borders — match colors.border.* */
  border:    "#334155",   // colors.border.light
  borderDim: "#1a2540",   // colors.border.dark

  /* Text — match colors.text.* */
  text:      "#f1f5f9",   // colors.text.primary
  textSec:   "#94a3b8",   // colors.text.secondary
  textMuted: "#4b6280",   // colors.text.muted

  /* Semantic — match success / warning / danger */
  green:     "#10b981",             // colors.success
  greenDim:  "rgba(16,185,129,0.12)",  // colors.successBg
  red:       "#ef4444",             // colors.danger
  redDim:    "rgba(239,68,68,0.12)",   // colors.dangerBg
  amber:     "#f59e0b",             // colors.warning
  amberDim:  "rgba(245,158,11,0.12)",  // colors.warningBg
  blue:      "#3b82f6",             // colors.primary
  blueDim:   "rgba(59,130,246,0.12)",

  /* Chart-specific — unchanged */
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

  /* Signal colours — distinct from candle green/red */
  sigEnterCE: "#06b6d4",   // cyan
  sigEnterPE: "#f97316",   // orange
  sigExitCE:  "#facc15",   // yellow
  sigExitPE:  "#e879f9",   // fuchsia
};

const FONT = "'Inter', -apple-system, sans-serif";
const MONO = "'JetBrains Mono','Fira Code',monospace";

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
function fmtTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return ""; }
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

/* ─── Chart geometry ────────────────────────────────────────── */
const MARGIN       = { top: 12, right: 10, bottom: 28, left: 68 };
const RSI_GAP      = 10;
const DEFAULT_VIEW = 80;
const MIN_VIEW     = 10;

function computePanes(totalH) {
  const inner = totalH - MARGIN.top - MARGIN.bottom - RSI_GAP;
  const mainH = Math.max(100, Math.round(inner * 0.77));
  const rsiH  = Math.max(40,  inner - mainH);
  return { mainH, rsiH, totalH };
}

/* ─── Small reusable atoms ──────────────────────────────────── */
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

/* ─── Info strip (primary mode only) ────────────────────────── */
function InfoStrip({ config, tradeState, positions, activeSymbol, onOpenSettings }) {
  if (!config) return null;

  const mode   = config.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";
  const isArmed = tradeState && Object.values(tradeState).some(s => s && s !== "IDLE");

  const ceOpen = positions?.open?.filter(p => p.symbol?.includes("CE"))?.length ?? 0;
  const peOpen = positions?.open?.filter(p => p.symbol?.includes("PE"))?.length ?? 0;

  // Count today's closed CE/PE trades
  const ceClosed = positions?.closed?.filter(p => p.symbol?.includes("CE"))?.length ?? 0;
  const peClosed = positions?.closed?.filter(p => p.symbol?.includes("PE"))?.length ?? 0;

  const statStyle = {
    display: "flex", flexDirection: "column", gap: 1, minWidth: 0,
  };
  const statLabel = {
    fontSize: 8, fontWeight: 600, color: C.textMuted,
    letterSpacing: "0.6px", textTransform: "uppercase", whiteSpace: "nowrap",
  };
  const statVal = {
    fontSize: 12, fontWeight: 700, color: C.text,
    fontFamily: MONO, whiteSpace: "nowrap",
  };

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
    <div style={{
      borderBottom: `1px solid ${C.borderDim}`,
      background: C.bgCard,
    }}>
      {/* Row 1: mode badges + symbol meta + fullscreen hint */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "7px 14px 5px",
      }}>
        {/* Armed status */}
        <Pill
          label={isArmed ? "ARMED" : "IDLE"}
          color={isArmed ? C.amber : C.textMuted}
          bg={isArmed ? C.amberDim : "transparent"}
          border={isArmed ? C.amber : C.border}
          icon={isArmed ? "●" : "○"}
        />
        {/* Execution mode */}
        <Pill
          label={mode}
          color={isLive ? C.red : C.green}
          bg={isLive ? C.redDim : C.greenDim}
          border={isLive ? C.red : C.green}
          icon={isLive ? "⚡" : "✎"}
        />

        <span style={{ fontSize: 10, color: C.textMuted, marginLeft: 4 }}>
          {activeSymbol} · BB OPTIONS · 3M
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

      {/* Row 2: key stats */}
      <div style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "4px 14px 8px",
        overflowX: "auto",
      }}>
        <Stat label="SL %" value={config.sl_pct != null ? `${config.sl_pct}%` : "—"} />
        {divider}
        <Stat label="TP %" value={config.tp_pct != null ? `${config.tp_pct}%` : "—"} />
        {divider}
        <Stat label="Max Premium" value={config.max_premium != null ? `₹${config.max_premium}` : "—"} />
        {divider}
        <Stat label="Max Trades/Side" value={config.max_trades_per_side} />
        {divider}
        <Stat label="CE Lots" value={config.ce_lots} />
        {divider}
        <Stat label="PE Lots" value={config.pe_lots} />
        {divider}
        <Stat
          label="Session"
          value={config.session_start && config.session_end
            ? `${config.session_start} – ${config.session_end}` : "—"} />
        {divider}
        <Stat label="Square-off" value={config.auto_square_off_time || "—"} />
        {divider}
        <Stat label="Today CE" value={ceClosed}
          color={ceClosed > 0 ? C.sigEnterCE : C.textMuted} />
        {divider}
        <Stat label="Today PE" value={peClosed}
          color={peClosed > 0 ? C.sigEnterPE : C.textMuted} />
        {divider}
        <Stat label="Open" value={`CE:${ceOpen}  PE:${peOpen}`}
          color={(ceOpen + peOpen) > 0 ? C.amber : C.textMuted} />
      </div>
    </div>
  );
}

/* ─── Panel header (price row) ──────────────────────────────── */
function PanelHeader({
  candles, isPrimary, onBecomePrimary, activeSymbol,
  isFullscreen, onToggleFullscreen,
  config,   // needed for compact mode badge
}) {
  const last = candles[candles.length - 1];
  if (!last) return null;

  const prev   = candles[candles.length - 2];
  const change = prev ? last.close - prev.close : 0;
  const isUp   = change >= 0;

  let bbPos = null;
  if (last.bb_upper != null && last.bb_lower != null) {
    if (last.close > last.bb_upper)      bbPos = { label: "ABOVE BB", color: C.amber };
    else if (last.close < last.bb_lower) bbPos = { label: "BELOW BB", color: C.amber };
    else                                  bbPos = { label: "IN BAND",  color: C.textMuted };
  }

  const stDir      = last.st_direction?.toUpperCase();
  const lastSignal = [...candles].reverse().find(c => c.signal_action);

  const mode   = config?.trade_execution_mode || "PAPER";
  const isLive = mode === "LIVE";

  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: isPrimary ? "9px 14px 7px" : "7px 10px",
        borderBottom: `1px solid ${C.borderDim}`,
        cursor: isPrimary ? "default" : "pointer",
        flexWrap: "wrap",
        flexShrink: 0,
      }}
      onClick={!isPrimary ? onBecomePrimary : undefined}
    >
      {/* Strategy label */}
      <div style={{ fontSize: 11, fontWeight: 700, color: C.blue, letterSpacing: "0.8px" }}>BB</div>

      {/* Compact-mode badges */}
      {!isPrimary && config && (
        <>
          <Pill
            label={mode}
            color={isLive ? C.red : C.green}
            bg={isLive ? C.redDim : C.greenDim}
            border={isLive ? C.red : C.green}
            icon={isLive ? "⚡" : "✎"}
          />
        </>
      )}

      {/* Symbol */}
      <div style={{ fontSize: 11, color: C.textMuted, letterSpacing: "0.5px" }}>
        {activeSymbol} 3m
      </div>

      {/* Last price */}
      <div style={{
        fontSize: isPrimary ? 17 : 13, fontWeight: 700,
        fontFamily: MONO, color: isUp ? C.green : C.red, marginLeft: 2,
      }}>
        {fmtPrice(last.close)}
      </div>

      {/* Change */}
      <div style={{ fontSize: 11, fontFamily: MONO, color: isUp ? C.green : C.red }}>
        {isUp ? "+" : ""}{change.toFixed(2)}
      </div>

      {/* ST direction */}
      {stDir && (
        <div style={{
          fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
          background: stDir === "UP" ? C.greenDim : C.redDim,
          color: stDir === "UP" ? C.green : C.red,
          border: `1px solid ${stDir === "UP" ? C.green : C.red}`,
        }}>
          ST {stDir}
        </div>
      )}

      {/* BB position */}
      {bbPos && <div style={{ fontSize: 10, color: bbPos.color }}>{bbPos.label}</div>}

      {/* RSI — colour threshold matches strategy: >70 overbought, <35 oversold */}
      {last.rsi_smooth != null && (
        <div style={{
          fontSize: 10, fontFamily: MONO,
          color: last.rsi_smooth > 70 ? C.red : last.rsi_smooth < 35 ? C.green : C.violet,
        }}>
          RSI {last.rsi_smooth.toFixed(1)}
        </div>
      )}

      {/* Last signal */}
      {isPrimary && lastSignal?.signal_action && (
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: C.textMuted }}>Last:</span>
          <SignalBadge action={lastSignal.signal_action} />
          <span style={{ fontSize: 10, color: C.textMuted, fontFamily: MONO }}>
            {fmtTime(lastSignal.ts)}
          </span>
        </div>
      )}

      {/* Fullscreen toggle */}
      {isPrimary && (
        <button
          onClick={(e) => { e.stopPropagation(); onToggleFullscreen(); }}
          title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          style={{
            marginLeft: "auto", background: "none",
            border: `1px solid ${C.border}`,
            borderRadius: 4, color: C.textMuted,
            cursor: "pointer", padding: "3px 7px",
            fontSize: 13, lineHeight: 1,
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
function CandleChart({ candles, width, chartHeight = 450, instanceId = "main" }) {
  const { mainH: MAIN_H, rsiH: RSI_H, totalH: TOTAL_H } = computePanes(chartHeight);

  const totalCandles = candles.length;
  const [viewCount, setViewCount]   = useState(() => Math.min(DEFAULT_VIEW, totalCandles));
  const [viewOffset, setViewOffset] = useState(() => Math.max(0, totalCandles - Math.min(DEFAULT_VIEW, totalCandles)));
  const atTailRef   = useRef(true);
  const dragRef     = useRef({ active: false, startX: 0, startOffset: 0, startY: 0, startYOffset: 0, isY: false });
  const [isDragging, setIsDragging] = useState(false);
  const [tooltip, setTooltip]       = useState(null);

  // Y-axis zoom/pan — independent of auto-fit
  const [yZoom,   setYZoom]   = useState(1);    // >1 = zoomed in, <1 = zoomed out
  const [yOffset, setYOffset] = useState(0);    // price units shift (+ = up)
  const yAutoFit = () => { setYZoom(1); setYOffset(0); };

  // ── rangePRef: keeps rangeP accessible in onMouseMove without stale closure ──
  const rangePRef = useRef(1);

  /* ── Future empty slots: extend X axis to 15:30 even without candle data ── */
  const futureSlots = (() => {
    if (!totalCandles) return 0;
    const lastTs  = candles[totalCandles - 1].ts;
    const lastDt  = new Date(lastTs * 1000);
    const closeMs = new Date(lastDt);
    closeMs.setHours(15, 30, 0, 0);
    const fromMs  = Math.max(Date.now(), lastTs * 1000 + 180_000);
    const remaining = Math.max(0, closeMs - fromMs);
    return Math.ceil(remaining / (3 * 60 * 1000));
  })();
  const totalSlots = totalCandles + futureSlots;

  /* ── Sync on new candles / futureSlots ── */
  useEffect(() => {
    if (totalSlots === 0) return;
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

  // Real candles visible in this window; future slots fill the rest
  const visible            = candles.slice(safeOffset, Math.min(safeOffset + safeCount, totalCandles));
  const visibleFutureSlots = Math.max(0, (safeOffset + safeCount) - totalCandles);
  const visibleSlots       = safeCount;

  useEffect(() => {
    atTailRef.current = (safeOffset + safeCount >= totalSlots);
  }, [safeOffset, safeCount, totalSlots]);

  /* ── Zoom (X: plain scroll, Y: Shift+scroll) ── */
  const handleWheel = useCallback((e) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.12 : 0.88;

    if (e.shiftKey) {
      setYZoom(prev => clamp(prev / factor, 0.2, 20));
    } else {
      const rect       = e.currentTarget.getBoundingClientRect();
      const mouseX     = e.clientX - rect.left - MARGIN.left;
      const chartW     = Math.max(1, width - MARGIN.left - MARGIN.right);
      const cursorFrac = clamp(mouseX / chartW, 0, 1);
      setViewCount(prevCount => {
        const newCount  = Math.round(clamp(prevCount * factor, MIN_VIEW, totalSlots));
        const cursorSlot = safeOffset + cursorFrac * prevCount;
        const newOffset = Math.round(clamp(cursorSlot - cursorFrac * newCount, 0, totalSlots - newCount));
        setViewOffset(newOffset);
        atTailRef.current = (newOffset + newCount >= totalSlots);
        return newCount;
      });
    }
  }, [width, safeOffset, totalSlots]);

  const svgRef = useRef(null);
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  /* ── Pan (X: plain drag, Y: Shift+drag) ── */
  const onMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    dragRef.current = {
      active: true,
      startX: e.clientX, startOffset: safeOffset,
      startY: e.clientY, startYOffset: yOffset,
      isY: e.shiftKey,
    };
    setIsDragging(true);
    setTooltip(null);
  }, [safeOffset, yOffset]);

  const onMouseMove = useCallback((e) => {
    if (!dragRef.current.active) return;
    if (dragRef.current.isY) {
      const dy = e.clientY - dragRef.current.startY;
      // Use rangePRef so we always have the latest computed rangeP
      const pxPerPrice = MAIN_H / (rangePRef.current / yZoom || 1);
      setYOffset(dragRef.current.startYOffset + dy / pxPerPrice);
    } else {
      const chartW    = Math.max(1, width - MARGIN.left - MARGIN.right);
      const slotW     = chartW / safeCount;
      const dx        = e.clientX - dragRef.current.startX;
      const delta     = Math.round(-dx / slotW);
      const newOffset = clamp(dragRef.current.startOffset + delta, 0, Math.max(0, totalSlots - safeCount));
      setViewOffset(newOffset);
      atTailRef.current = (newOffset + safeCount >= totalSlots);
    }
  }, [width, safeCount, totalSlots, yZoom, MAIN_H]);

  const onMouseUp = useCallback(() => {
    dragRef.current.active = false;
    setIsDragging(false);
  }, []);

  const jumpToLatest = () => {
    setViewOffset(Math.max(0, totalCandles - safeCount));
    atTailRef.current = true;
  };

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

  // Slot width over ALL logical slots (real + future) in view
  const slotW   = visibleSlots > 0 ? chartW / visibleSlots : chartW;
  const candleW = Math.max(1.5, slotW * 0.72);
  const candleX = (i) => MARGIN.left + i * slotW + slotW / 2;

  /* ── Price scale — always include OHLC, never produce Infinity ── */
  const priceSource = visible.length > 0 ? visible : candles.slice(-1);
  const prices = priceSource.flatMap(c =>
    [c.open, c.high, c.low, c.close,
     c.bb_upper, c.bb_lower, c.r1, c.s1, c.supertrend].filter(v => v != null && isFinite(v))
  );
  const rawMin   = prices.length ? Math.min(...prices) : 50000;
  const rawMax   = prices.length ? Math.max(...prices) : 51000;
  const pad      = (rawMax - rawMin) * 0.06 || 10;
  const autoMinP = rawMin - pad;
  const autoMaxP = rawMax + pad;
  const rangeP   = (autoMaxP - autoMinP) || 1;
  rangePRef.current = rangeP;  // keep ref fresh for Y-pan

  // Apply Y zoom + pan
  const centerP   = (autoMinP + autoMaxP) / 2 - yOffset;
  const halfR     = (rangeP / 2) / yZoom;
  const minP      = centerP - halfR;
  const maxP      = centerP + halfR;
  const viewRange = (maxP - minP) || 1;

  const py   = (price) => mainTop + MAIN_H * (1 - (price - minP) / viewRange);
  const rsiY = (v)     => rsiTop + RSI_H * (1 - clamp(v, 0, 100) / 100);

  const tickCount  = 6;
  const priceTicks = Array.from({ length: tickCount + 1 }, (_, i) => minP + (viewRange / tickCount) * i);

  /* ── Time ticks: real candles + future slots ── */
  const lastCandleTs = totalCandles ? candles[totalCandles - 1].ts : 0;
  const timeStep     = Math.max(1, Math.floor(visibleSlots / 8));
  const timeTicks    = (() => {
    const ticks = [];
    visible.forEach((c, i) => {
      if (i % timeStep === 0) ticks.push({ i, ts: c.ts, future: false });
    });
    for (let j = 0; j < visibleFutureSlots; j++) {
      const slotIdx = visible.length + j;
      if (slotIdx % timeStep === 0) {
        const futureSlotNum = safeOffset + slotIdx - totalCandles;
        ticks.push({ i: slotIdx, ts: lastCandleTs + (futureSlotNum + 1) * 180, future: true });
      }
    }
    return ticks;
  })();

  /* ── SuperTrend — bridged at direction flip ── */
  const stSegments = [];
  let seg = null;
  visible.forEach((c, i) => {
    if (c.supertrend == null) { seg = null; return; }
    const dir = c.st_direction?.toUpperCase();
    const pt  = { x: candleX(i), y: py(c.supertrend) };
    if (!seg || seg.dir !== dir) {
      if (seg) { seg.points.push(pt); stSegments.push(seg); }
      seg = { dir, points: [pt] };
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
    const lowerRev = [...bbLowerPts].reverse().map((p) => `L ${p.x} ${p.y}`).join(" ");
    bbAreaPath = `${upper} ${lowerRev} Z`;
  }

  const linePath = (pts) => pts.length < 2 ? "" :
    pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");

  const lastR1 = [...visible].reverse().find(c => c.r1 != null)?.r1;
  const lastS1 = [...visible].reverse().find(c => c.s1 != null)?.s1;

  const mainClipId = `mainClip_${instanceId}`;
  const rsiClipId  = `rsiClip_${instanceId}`;

  /* ── Signal marker sizing ── */
  const arrowSize = Math.max(6, Math.min(10, slotW * 0.8));

  return (
    <div style={{ position: "relative", userSelect: "none" }}>
      <svg
        ref={svgRef}
        width={width}
        onDoubleClick={() => yAutoFit()}
        height={TOTAL_H}
        style={{ display: "block", fontFamily: FONT, cursor: isDragging ? "grabbing" : "crosshair" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={() => { onMouseUp(); setTooltip(null); }}
      >
        <defs>
          <clipPath id={mainClipId}>
            <rect x={MARGIN.left} y={mainTop} width={Math.max(0, chartW)} height={MAIN_H} />
          </clipPath>
          <clipPath id={rsiClipId}>
            <rect x={MARGIN.left} y={rsiTop} width={Math.max(0, chartW)} height={RSI_H} />
          </clipPath>
          {/* Glow filters for signal markers */}
          <filter id={`glowCE_${instanceId}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          <filter id={`glowPE_${instanceId}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>

        {/* ── Grid ── */}
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

        {/* ── BB bands ── */}
        {bbAreaPath && <path d={bbAreaPath} fill={C.bbFill} clipPath={`url(#${mainClipId})`} />}
        {bbUpperPts.length > 1 && (
          <path d={linePath(bbUpperPts)} fill="none" stroke={C.bbUpper} strokeWidth={1}
            strokeDasharray="4 3" clipPath={`url(#${mainClipId})`} />
        )}
        {bbMidPts.length > 1 && (
          <path d={linePath(bbMidPts)} fill="none" stroke={C.bbMiddle} strokeWidth={0.8}
            strokeDasharray="3 4" clipPath={`url(#${mainClipId})`} />
        )}
        {bbLowerPts.length > 1 && (
          <path d={linePath(bbLowerPts)} fill="none" stroke={C.bbLower} strokeWidth={1}
            strokeDasharray="4 3" clipPath={`url(#${mainClipId})`} />
        )}

        {/* ── R1 / S1 ── */}
        {lastR1 != null && py(lastR1) >= mainTop && py(lastR1) <= mainTop + MAIN_H && (
          <>
            <line x1={MARGIN.left} y1={py(lastR1)} x2={MARGIN.left + chartW} y2={py(lastR1)}
              stroke={C.r1} strokeWidth={1} strokeDasharray="6 4" opacity={0.7} />
            <text x={MARGIN.left + chartW - 2} y={py(lastR1) - 3}
              textAnchor="end" fontSize={9} fill={C.r1} fontFamily={MONO}>
              R1 {Math.round(lastR1)}
            </text>
          </>
        )}
        {lastS1 != null && py(lastS1) >= mainTop && py(lastS1) <= mainTop + MAIN_H && (
          <>
            <line x1={MARGIN.left} y1={py(lastS1)} x2={MARGIN.left + chartW} y2={py(lastS1)}
              stroke={C.s1} strokeWidth={1} strokeDasharray="6 4" opacity={0.7} />
            <text x={MARGIN.left + chartW - 2} y={py(lastS1) + 10}
              textAnchor="end" fontSize={9} fill={C.s1} fontFamily={MONO}>
              S1 {Math.round(lastS1)}
            </text>
          </>
        )}

        {/* ── SuperTrend ── */}
        <g clipPath={`url(#${mainClipId})`}>
          {stSegments.map((seg, si) => (
            <path key={si} d={linePath(seg.points)} fill="none"
              stroke={seg.dir === "UP" ? C.stUp : C.stDown}
              strokeWidth={1.8} opacity={0.9} />
          ))}
        </g>

        {/* ── Candles ── */}
        <g clipPath={`url(#${mainClipId})`}>
          {visible.map((c, i) => {
            const x = candleX(i);
            const isGreen = c.close >= c.open;
            const fill    = isGreen ? C.green : C.red;
            const bodyTop = py(Math.max(c.open, c.close));
            const bodyBot = py(Math.min(c.open, c.close));
            const bodyH   = Math.max(1, bodyBot - bodyTop);
            return (
              <g key={i}
                onMouseEnter={() => {
                  if (!dragRef.current.active)
                    setTooltip({ x: Math.min(x, width - 160), y: mainTop, candle: c });
                }}
              >
                <line x1={x} y1={py(c.high)} x2={x} y2={py(c.low)} stroke={C.wick} strokeWidth={1} />
                <rect x={x - candleW / 2} y={bodyTop} width={candleW} height={bodyH}
                  fill={fill} opacity={0.85} />
              </g>
            );
          })}
        </g>

        {/* ── Signal markers ─────────────────────────────────────
            ENTER_CE : cyan    upward  triangle + stem below wick
            ENTER_PE : orange  downward triangle + stem above wick
            EXIT_CE  : yellow  circle-X at close
            EXIT_PE  : fuchsia circle-X at close
        ──────────────────────────────────────────────────────── */}
        <g clipPath={`url(#${mainClipId})`}>
          {visible.map((c, i) => {
            if (!c.signal_action) return null;
            const x      = candleX(i);
            const action = c.signal_action;
            const hw     = arrowSize;
            const ah     = arrowSize * 1.1;
            const gap    = 6;

            if (action === "ENTER_CE") {
              const wickBottom = py(c.low);
              const arrowTip   = wickBottom + gap;
              const arrowBase  = arrowTip + ah;
              const stemStart  = arrowBase + 2;
              const stemEnd    = stemStart + 8;
              return (
                <g key={i} filter={`url(#glowCE_${instanceId})`}>
                  <line x1={x} y1={stemStart} x2={x} y2={stemEnd}
                    stroke={C.sigEnterCE} strokeWidth={1.5} opacity={0.5} />
                  <polygon
                    points={`${x},${arrowTip} ${x - hw},${arrowBase} ${x + hw},${arrowBase}`}
                    fill={C.sigEnterCE} opacity={0.95}
                  />
                  <text x={x} y={stemEnd + 9} textAnchor="middle"
                    fontSize={7.5} fill={C.sigEnterCE} fontWeight="800" fontFamily={MONO}>
                    CE
                  </text>
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
                  <line x1={x} y1={stemStart} x2={x} y2={stemEnd}
                    stroke={C.sigEnterPE} strokeWidth={1.5} opacity={0.5} />
                  <polygon
                    points={`${x},${arrowTip} ${x - hw},${arrowBase} ${x + hw},${arrowBase}`}
                    fill={C.sigEnterPE} opacity={0.95}
                  />
                  <text x={x} y={stemEnd - 3} textAnchor="middle"
                    fontSize={7.5} fill={C.sigEnterPE} fontWeight="800" fontFamily={MONO}>
                    PE
                  </text>
                </g>
              );
            }

            if (action === "EXIT_CE") {
              const y = py(c.close);
              const r = arrowSize * 0.9;
              return (
                <g key={i}>
                  <circle cx={x} cy={y} r={r} fill="rgba(250,204,21,0.18)"
                    stroke={C.sigExitCE} strokeWidth={1.8} />
                  <line x1={x - r * 0.55} y1={y - r * 0.55}
                        x2={x + r * 0.55} y2={y + r * 0.55}
                        stroke={C.sigExitCE} strokeWidth={1.6} />
                  <line x1={x + r * 0.55} y1={y - r * 0.55}
                        x2={x - r * 0.55} y2={y + r * 0.55}
                        stroke={C.sigExitCE} strokeWidth={1.6} />
                </g>
              );
            }

            if (action === "EXIT_PE") {
              const y = py(c.close);
              const r = arrowSize * 0.9;
              return (
                <g key={i}>
                  <circle cx={x} cy={y} r={r} fill="rgba(232,121,249,0.18)"
                    stroke={C.sigExitPE} strokeWidth={1.8} />
                  <line x1={x - r * 0.55} y1={y - r * 0.55}
                        x2={x + r * 0.55} y2={y + r * 0.55}
                        stroke={C.sigExitPE} strokeWidth={1.6} />
                  <line x1={x + r * 0.55} y1={y - r * 0.55}
                        x2={x - r * 0.55} y2={y + r * 0.55}
                        stroke={C.sigExitPE} strokeWidth={1.6} />
                </g>
              );
            }

            return null;
          })}
        </g>

        {/* ── Future zone: subtle shading + 15:30 marker ── */}
        {visibleFutureSlots > 0 && (() => {
          const futureStartX = MARGIN.left + visible.length * slotW;
          const futureW      = visibleFutureSlots * slotW;
          const closeSlotRel = (totalCandles + futureSlots - 1) - safeOffset;
          const closeX       = MARGIN.left + closeSlotRel * slotW + slotW / 2;
          const showClose    = closeX > MARGIN.left && closeX < MARGIN.left + chartW;
          return (
            <>
              <rect x={futureStartX} y={mainTop} width={futureW} height={MAIN_H}
                fill="rgba(255,255,255,0.01)" stroke="none" />
              {showClose && (
                <>
                  <line x1={closeX} y1={mainTop} x2={closeX} y2={rsiTop + RSI_H}
                    stroke={C.amber} strokeWidth={0.8} strokeDasharray="4 3" opacity={0.35} />
                  <text x={closeX + 3} y={mainTop + 11}
                    fontSize={8} fill={C.amber} opacity={0.45}>15:30</text>
                </>
              )}
            </>
          );
        })()}

        {/* ── Main pane border ── */}
        <rect x={MARGIN.left} y={mainTop} width={chartW} height={MAIN_H}
          fill="none" stroke={C.border} strokeWidth={0.5} />

        {/* ════ RSI PANE ════ */}

        {/* RSI background */}
        <rect x={MARGIN.left} y={rsiTop} width={chartW} height={RSI_H}
          fill={C.bgSurface} opacity={0.3} />

        {/* Overbought zone fill (>70) — faint red band, drawn before RSI line */}
        <rect
          x={MARGIN.left} y={rsiY(100)}
          width={chartW} height={rsiY(70) - rsiY(100)}
          fill="rgba(239,68,68,0.08)" clipPath={`url(#${rsiClipId})`}
        />

        {/* Oversold zone fill (<35) — faint green band, drawn before RSI line */}
        <rect
          x={MARGIN.left} y={rsiY(35)}
          width={chartW} height={rsiY(0) - rsiY(35)}
          fill="rgba(16,185,129,0.08)" clipPath={`url(#${rsiClipId})`}
        />

        {/* RSI fill + line */}
        <g clipPath={`url(#${rsiClipId})`}>
          {rsiPts.length > 1 && (
            <>
              <path
                d={`${linePath(rsiPts)} L ${rsiPts[rsiPts.length-1].x} ${rsiTop+RSI_H} L ${rsiPts[0].x} ${rsiTop+RSI_H} Z`}
                fill={C.rsiFill} />
              <path d={linePath(rsiPts)} fill="none" stroke={C.rsiLine} strokeWidth={1.4} />
            </>
          )}
        </g>

        {/* RSI 70 line — CE entry threshold */}
        <line x1={MARGIN.left} y1={rsiY(70)} x2={MARGIN.left+chartW} y2={rsiY(70)}
          stroke={C.red} strokeWidth={0.8} strokeDasharray="3 3" opacity={0.6} />
        <text x={MARGIN.left-4} y={rsiY(70)+3} textAnchor="end" fontSize={8}
          fill={C.red} fontFamily={MONO} opacity={0.8}>70</text>

        {/* RSI 35 line — PE entry threshold */}
        <line x1={MARGIN.left} y1={rsiY(35)} x2={MARGIN.left+chartW} y2={rsiY(35)}
          stroke={C.green} strokeWidth={0.8} strokeDasharray="3 3" opacity={0.6} />
        <text x={MARGIN.left-4} y={rsiY(35)+3} textAnchor="end" fontSize={8}
          fill={C.green} fontFamily={MONO} opacity={0.8}>35</text>

        {/* RSI 50 midline */}
        <line x1={MARGIN.left} y1={rsiY(50)} x2={MARGIN.left+chartW} y2={rsiY(50)}
          stroke={C.borderDim} strokeWidth={0.5} />

        {/* RSI label */}
        <text x={MARGIN.left+4} y={rsiTop+10} fontSize={9} fill={C.textMuted}>RSI</text>

        {/* RSI pane border */}
        <rect x={MARGIN.left} y={rsiTop} width={chartW} height={RSI_H}
          fill="none" stroke={C.border} strokeWidth={0.5} />

        {/* ── Time axis (real + future) ── */}
        {timeTicks.map(({ i, ts, future }) => {
          const tx = MARGIN.left + i * slotW + slotW / 2;
          return (
            <g key={i} opacity={future ? 0.35 : 1}>
              <line x1={tx} y1={rsiTop+RSI_H} x2={tx} y2={rsiTop+RSI_H+4}
                stroke={future ? C.borderDim : C.border} strokeWidth={0.8}
                strokeDasharray={future ? "2 2" : "none"} />
              <text x={tx} y={rsiTop+RSI_H+14} textAnchor="middle"
                fontSize={8.5} fill={C.textMuted} fontFamily={MONO}>
                {fmtTime(ts)}
              </text>
            </g>
          );
        })}

        {/* ── Legend ── */}
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
            <line x1={0} y1={6} x2={14} y2={6} stroke={item.color} strokeWidth={1.5}
              strokeDasharray={item.dash ? "4 2" : "none"} />
            <text x={17} y={10} fontSize={8.5} fill={C.textMuted}>{item.label}</text>
          </g>
        ))}

        {/* ── Mini scrollbar ── */}
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

        {/* ── View counter ── */}
        <text x={MARGIN.left + chartW - 2} y={mainTop - 2}
          textAnchor="end" fontSize={8} fill={C.textMuted} fontFamily={MONO} opacity={0.5}>
          {safeOffset+1}–{safeOffset+visible.length}/{totalCandles}
        </text>
      </svg>

      {/* ── Tooltip ── */}
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
            {fmtTime(tooltip.candle.ts)}
          </div>
          {[["O", tooltip.candle.open], ["H", tooltip.candle.high],
            ["L", tooltip.candle.low],  ["C", tooltip.candle.close]].map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
              <span style={{ color: C.textMuted }}>{k}</span>
              <span>{fmtPrice(v)}</span>
            </div>
          ))}
          {tooltip.candle.bb_upper != null && (
            <>
              <div style={{ borderTop: `1px solid ${C.borderDim}`, margin: "4px 0" }} />
              {[["BB↑", tooltip.candle.bb_upper],
                ["BB—", tooltip.candle.bb_middle],
                ["BB↓", tooltip.candle.bb_lower]].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                  <span style={{ color: C.blue, fontSize: 10 }}>{k}</span>
                  <span>{fmtPrice(v)}</span>
                </div>
              ))}
            </>
          )}
          {tooltip.candle.rsi_smooth != null && (
            <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 2 }}>
              <span style={{ color: C.violet }}>RSI</span>
              {/* Colour threshold matches strategy: >70 CE entry zone, <35 PE entry zone */}
              <span style={{ color: tooltip.candle.rsi_smooth > 70 ? C.red : tooltip.candle.rsi_smooth < 35 ? C.green : C.violet }}>
                {tooltip.candle.rsi_smooth.toFixed(1)}
              </span>
            </div>
          )}
          {tooltip.candle.supertrend != null && (
            <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 2 }}>
              <span style={{ color: tooltip.candle.st_direction === "UP" ? C.stUp : C.stDown }}>
                ST {tooltip.candle.st_direction}
              </span>
              <span>{fmtPrice(tooltip.candle.supertrend)}</span>
            </div>
          )}
          {tooltip.candle.signal_action && (
            <div style={{ marginTop: 4 }}>
              <SignalBadge action={tooltip.candle.signal_action} />
              {tooltip.candle.signal_reason && (
                <div style={{ fontSize: 9, color: C.textMuted, marginTop: 2, maxWidth: 160, wordBreak: "break-word" }}>
                  {tooltip.candle.signal_reason}
                </div>
              )}
            </div>
          )}
          {tooltip.candle.rejection_reason && (
            <div style={{ fontSize: 9, color: C.amber, marginTop: 2, maxWidth: 160, wordBreak: "break-word" }}>
              ⚠ {tooltip.candle.rejection_reason}
            </div>
          )}
        </div>
      )}

      {/* ── Y-zoom reset hint ── */}
      {(yZoom !== 1 || yOffset !== 0) && (
        <div
          onClick={yAutoFit}
          style={{
            position: "absolute", top: 8, left: MARGIN.left + 4,
            background: "rgba(245,158,11,0.18)", border: "1px solid rgba(245,158,11,0.4)",
            borderRadius: 4, padding: "2px 7px",
            fontSize: 10, color: C.amber, cursor: "pointer",
            fontFamily: MONO, userSelect: "none", zIndex: 4,
          }}
          title="Double-click chart or click here to reset Y axis"
        >
          Y {yZoom.toFixed(1)}× · reset
        </div>
      )}

      {/* ── Jump-to-latest button ── */}
      {!atTailRef.current && (
        <button
          onClick={jumpToLatest}
          style={{
            position: "absolute", bottom: 36, right: MARGIN.right + 8,
            background: C.bgCard, border: `1px solid ${C.blue}`,
            borderRadius: 4, color: C.blue, fontSize: 10, fontFamily: MONO,
            padding: "3px 8px", cursor: "pointer", zIndex: 5,
            display: "flex", alignItems: "center", gap: 4,
          }}
        >
          ▶▶ Latest
        </button>
      )}
    </div>
  );
}

/* ─── Compact BB-width bar ───────────────────────────────────── */
function BBWidthBar({ candles }) {
  if (!candles.length) return null;
  const last = candles[candles.length - 1];
  if (last.bb_width == null) return null;

  const widths = candles.map(c => c.bb_width).filter(v => v != null);
  const maxW   = Math.max(...widths, 1);
  const pct    = clamp((last.bb_width / maxW) * 100, 0, 100);
  const isSqueezing = pct < 25;

  return (
    <div style={{ padding: "6px 10px 8px", borderTop: `1px solid ${C.borderDim}` }}>
      <div style={{
        fontSize: 9, color: C.textMuted, marginBottom: 3,
        display: "flex", justifyContent: "space-between",
      }}>
        <span>BB Width</span>
        <span style={{ color: isSqueezing ? C.amber : C.textMuted }}>
          {isSqueezing ? "⚡ SQUEEZE" : last.bb_width.toFixed(2)}
        </span>
      </div>
      <div style={{ height: 3, background: C.borderDim, borderRadius: 2, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${pct}%`,
          background: isSqueezing ? C.amber : C.blue,
          borderRadius: 2, transition: "width 0.4s ease",
        }} />
      </div>
    </div>
  );
}

/* ─── Fullscreen overlay ─────────────────────────────────────── */
function FullscreenChart({ candles, activeSymbol, config, positions, tradeState, onBecomePrimary, onClose }) {
  const containerRef = useRef(null);
  const [dims, setDims] = useState({ width: window.innerWidth, height: window.innerHeight });

  useEffect(() => {
    const ro = new ResizeObserver(([entry]) => {
      setDims({
        width:  entry.contentRect.width  || window.innerWidth,
        height: entry.contentRect.height || window.innerHeight,
      });
    });
    if (containerRef.current) {
      ro.observe(containerRef.current);
      setDims({
        width:  containerRef.current.offsetWidth  || window.innerWidth,
        height: containerRef.current.offsetHeight || window.innerHeight,
      });
    }
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 9999,
      background: C.bg, display: "flex", flexDirection: "column",
    }}>
      <PanelHeader
        candles={candles} isPrimary={true}
        onBecomePrimary={onBecomePrimary}
        activeSymbol={activeSymbol}
        config={config}
        isFullscreen={true}
        onToggleFullscreen={onClose}
      />
      <InfoStrip
        config={config} tradeState={tradeState}
        positions={positions} activeSymbol={activeSymbol}
        onOpenSettings={() => {}}
      />
      <div ref={containerRef} style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        <CandleChart
          candles={candles} width={dims.width}
          chartHeight={dims.height} instanceId="fullscreen"
        />
      </div>
    </div>
  );
}

/* ─── Error boundary — prevents chart crash from blanking the whole page ── */
import React from "react";
class ChartErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(err) { return { error: err }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{
          height: 460, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          color: "#ef4444", fontSize: 12, gap: 8, padding: 24,
        }}>
          <div style={{ fontWeight: 700 }}>Chart render error</div>
          <div style={{ color: "#64748b", textAlign: "center", maxWidth: 320 }}>
            {String(this.state.error.message)}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              marginTop: 8, padding: "4px 12px", background: "#1e293b",
              border: "1px solid #334155", borderRadius: 4, color: "#94a3b8",
              cursor: "pointer", fontSize: 11,
            }}
          >Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ─── Main component ────────────────────────────────────────── */
export default function BBPanel({ ltpMap, isPrimary, onBecomePrimary }) {
  const [candles, setCandles]           = useState([]);
  const [activeSymbol, setActiveSymbol] = useState("…");
  const [config, setConfig]             = useState(null);
  const [tradeState, setTradeState]     = useState(null);
  const [positions, setPositions]       = useState(null);
  const [error, setError]               = useState(null);
  const [loading, setLoading]           = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const containerRef = useRef(null);
  const chartAreaRef = useRef(null);
  const [chartWidth, setChartWidth]   = useState(800);
  const [chartHeight, setChartHeight] = useState(450);
  const pollRef = useRef(null);

  /* ── Responsive width ── */
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([entry]) => setChartWidth(entry.contentRect.width || 800));
    ro.observe(containerRef.current);
    setChartWidth(containerRef.current.offsetWidth || 800);
    return () => ro.disconnect();
  }, []);

  /* ── Responsive height of chart area ── */
  useEffect(() => {
    if (!chartAreaRef.current) return;
    const ro = new ResizeObserver(([entry]) => setChartHeight(entry.contentRect.height || 450));
    ro.observe(chartAreaRef.current);
    setChartHeight(chartAreaRef.current.offsetHeight || 450);
    return () => ro.disconnect();
  }, [isPrimary]);

  /* ── Fetch candles ── */
  const fetchCandles = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/futures/candles?symbol=auto&timeframe=3m&limit=200`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setCandles(data.candles || []);
      setActiveSymbol(data.symbol || "—");
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── Fetch strategy meta (config + trade state + positions) ── */
  const fetchMeta = useCallback(async () => {
    try {
      const [cfg, ts, pos] = await Promise.all([
        getStrategyConfig("BB_V1"),
        getTradeState("BB_V1"),
        getTodayPositions(),
      ]);
      setConfig(cfg || null);
      setTradeState(ts || null);
      setPositions(pos || null);
    } catch {
      // non-fatal — chart still works without meta
    }
  }, []);

  useEffect(() => {
    fetchCandles();
    fetchMeta();
    const candleInterval = isMarketHours() ? 15_000 : 60_000;
    const metaInterval   = isMarketHours() ? 10_000 : 60_000;
    pollRef.current = setInterval(fetchCandles, candleInterval);
    const metaPoll  = setInterval(fetchMeta, metaInterval);
    return () => { clearInterval(pollRef.current); clearInterval(metaPoll); };
  }, [fetchCandles, fetchMeta]);

  const hasData = candles.length > 0;

  return (
    <>
      {/* ── Fullscreen overlay ── */}
      {isFullscreen && hasData && (
        <FullscreenChart
          candles={candles}
          activeSymbol={activeSymbol}
          config={config}
          tradeState={tradeState}
          positions={positions}
          onBecomePrimary={onBecomePrimary}
          onClose={() => setIsFullscreen(false)}
        />
      )}

      {/* ── Normal panel ── */}
      <div
        ref={containerRef}
        style={{
          background: C.bg, border: `1px solid ${C.border}`,
          borderRadius: 8, overflow: "hidden",
          display: "flex", flexDirection: "column",
          height: "100%", minWidth: 0,
        }}
      >
        {/* Price header — always visible */}
        {hasData ? (
          <PanelHeader
            candles={candles} isPrimary={isPrimary}
            onBecomePrimary={onBecomePrimary}
            activeSymbol={activeSymbol}
            config={config}
            isFullscreen={isFullscreen}
            onToggleFullscreen={() => setIsFullscreen(v => !v)}
          />
        ) : (
          <div style={{ padding: "10px 14px", display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: C.blue, letterSpacing: "0.8px" }}>BB</div>
            <div style={{ fontSize: 11, color: C.textMuted }}>
              {loading ? "Loading…" : error ? `Error: ${error}` : "No data"}
            </div>
          </div>
        )}

        {/* Info strip — primary only */}
        {isPrimary && hasData && (
          <InfoStrip
            config={config} tradeState={tradeState}
            positions={positions} activeSymbol={activeSymbol}
            onOpenSettings={() => {/* TODO: navigate to settings */}}
          />
        )}

        {/* Chart — primary only */}
        {isPrimary && hasData && (
          <div ref={chartAreaRef} style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
            <ChartErrorBoundary>
              <CandleChart
                candles={candles} width={chartWidth}
                chartHeight={chartHeight} instanceId="panel"
              />
            </ChartErrorBoundary>
          </div>
        )}

        {/* Compact: BB-width squeeze bar */}
        {!isPrimary && hasData && <BBWidthBar candles={candles} />}

        {/* Error */}
        {isPrimary && error && !hasData && (
          <div style={{ padding: 16, color: C.red, fontSize: 12 }}>
            Failed to load candles: {error}
          </div>
        )}
      </div>
    </>
  );
}
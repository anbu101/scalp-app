/*
 * Analytics.jsx  —  Comprehensive Trading Analytics
 * Path: src/pages/Analytics.jsx
 *
 * Changes v2:
 *  1. Multi-strategy filter: All / SCALP_V1 / BB_V1 / BB_V2 / HA_V1 (multi-select chips)
 *  2. Entry Time + Exit Time columns added to trade table
 *  3. SHORT trade P&L handled via trade_direction field (SCALP sells options)
 *  4. Open LIVE trades tracked live with unrealised P&L via LTPStore snapshot
 *
 * Changes v3:
 *  5. Open Live Trades rendered as direction-aware position-track CARDS
 *     (≤6 open) with a table fallback above that. See OpenTradesPanel block.
 *  6. DIRECTION IS PER-STRATEGY and now has a SINGLE SOURCE OF TRUTH:
 *     `isShortTrade()` is the one predicate used by P&L math AND the cards.
 *     SCALP_V1 / SCALP_V2 sell (SHORT); BB_V1 / BB_V2 / HA_V1 buy (LONG).
 *     Both computePnl and computeUnrealisedPnl route through it, so a missing
 *     trade_direction on a SCALP trade can no longer invert the live P&L sign.
 */

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { getApiBase } from "../api/base";
import { EmptyState }  from "../components/LoadingStates";
import { useToast }    from "../components/ToastNotifications";
import { exportToCSV, generateFilename } from "../utils/export";

/* ─────────────────────────────────────────────────────────────
   Design Tokens
───────────────────────────────────────────────────────────── */
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
  greenBg:   "rgba(16,185,129,0.12)",
  red:       "#ef4444",
  redBg:     "rgba(239,68,68,0.12)",
  amber:     "#f59e0b",
  amberBg:   "rgba(245,158,11,0.12)",
  blue:      "#3b82f6",
  blueBg:    "rgba(59,130,246,0.12)",
  teal:      "#14b8a6",
  violet:    "#8b5cf6",
  cyan:      "#06b6d4",
};

const MONO = "'JetBrains Mono','Fira Code',monospace";
const FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";

const DAY_NAMES = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

/* ─────────────────────────────────────────────────────────────
   Strategy definitions
───────────────────────────────────────────────────────────── */
const STRATEGIES = [
  { id: "SCALP_V1", label: "Scalp V1",  color: C.cyan,   desc: "Option Selling · BANKNIFTY" },
  { id: "SCALP_V2", label: "Scalp V2",  color: C.teal,   desc: "Order-split SHORT · 3 classes" },
  { id: "SCALP_V3", label: "Scalp V3",  color: C.green,  desc: "Buy-hedge test · signal CE/PE → buy opposite" },  // ← NEW
  { id: "BB_V1",    label: "BB V1",     color: C.blue,   desc: "Bollinger Band · BANKNIFTY" },
  { id: "BB_V2",    label: "BB V2",     color: C.violet, desc: "BB Variant · Tighter ST" },
  { id: "HA_V1",    label: "HA V1",     color: C.amber,  desc: "Heikin Ashi · NIFTY Weekly" },
];

/* ─────────────────────────────────────────────────────────────
   Utility helpers
───────────────────────────────────────────────────────────── */
const safeNum = (v) => (typeof v === "number" && isFinite(v) ? v : 0);

function fmtInr(v) {
  if (v == null) return "—";
  const abs = Math.abs(Math.round(v));
  return `₹${abs.toLocaleString("en-IN")}`;
}

function fmtPct(v, dec = 2) {
  if (v == null) return "—";
  return `${v.toFixed(dec)}%`;
}

function fmtDateTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function fmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("en-IN", {
    day: "2-digit", month: "short", year: "2-digit",
  });
}

/**
 * Duration between two timestamps — human-readable.
 */
function fmtDuration(fromTs, toTs) {
  if (!fromTs) return "—";
  const endTs = toTs || Math.floor(Date.now() / 1000);
  const secs  = Math.max(0, endTs - fromTs);
  if (secs < 60)    return `${secs}s`;
  if (secs < 3600)  return `${Math.floor(secs / 60)}m`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

function extractInstrument(symbol) {
  if (!symbol) return "OTHER";
  if (symbol.includes("BANKNIFTY")) return "BANKNIFTY";
  if (symbol.includes("NIFTY"))     return "NIFTY";
  return "OTHER";
}

function extractSide(symbol, slot) {
  if (slot?.startsWith("CE") || symbol?.endsWith("CE")) return "CE";
  if (slot?.startsWith("PE") || symbol?.endsWith("PE")) return "PE";
  return "OTHER";
}

/**
 * SINGLE SOURCE OF TRUTH for trade direction.
 *
 * SHORT when the broker side is sell. SCALP_V1 / SCALP_V2 sell options;
 * BB_V1 / BB_V2 / HA_V1 buy options. trade_direction is authoritative when
 * present; otherwise fall back to SL/entry geometry (SHORT keeps SL above
 * entry), then to the strategy family.
 *
 * Both the P&L helpers and the open-trade cards use THIS function so the
 * sign, the track orientation, and the SELL/BUY pill can never disagree.
 */
function isShortTrade(t) {
  const dir = t.trade_direction?.toUpperCase();
  if (dir === "SHORT") return true;
  if (dir === "LONG")  return false;
  const sl = safeNum(t.sl_price), entry = safeNum(t.entry_price);
  if (sl && entry) return sl > entry;            // SHORT: SL above entry
  // SCALP_V3 buys (LONG); SCALP_V1/V2 sell (SHORT). Exclude V3 from the family fallback.
  if ((t.strategy_id || "") === "SCALP_V3") return false;   // ← NEW
  return /^SCALP/.test(t.strategy_id || "");     // family fallback
}

/**
 * Compute realised P&L from a closed trade, respecting direction.
 * LONG  (BB / HA): (exit - entry) * qty
 * SHORT (SCALP):   (entry - exit) * qty
 */
function computePnl(trade) {
  // If the backend already stored pnl_value, trust it
  if (trade.pnl_value != null && trade.pnl_value !== 0) return safeNum(trade.pnl_value);
  // Fallback: compute client-side
  const entry = safeNum(trade.entry_price);
  const exit  = safeNum(trade.exit_price);
  const qty   = safeNum(trade.qty);
  if (!entry || !exit || !qty) return 0;
  return isShortTrade(trade) ? (entry - exit) * qty : (exit - entry) * qty;
}

/**
 * Compute unrealised P&L for an OPEN trade using current LTP.
 * Routes through isShortTrade so a missing trade_direction on a SCALP
 * position cannot invert the sign.
 */
function computeUnrealisedPnl(trade, ltpMap) {
  const symbol  = trade.symbol || trade.tradingsymbol;
  const ltp     = ltpMap[symbol?.toUpperCase().replace(/\s+/g, "")] ?? 0;
  if (!ltp) return null;
  const entry    = safeNum(trade.entry_price);
  const qty      = safeNum(trade.qty);
  return isShortTrade(trade) ? (entry - ltp) * qty : (ltp - entry) * qty;
}

function getPresetRange(preset) {
  const now       = new Date();
  const todayMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  switch (preset) {
    case "today":     return { from: todayMidnight, to: null };
    case "yesterday": {
      const y = new Date(todayMidnight); y.setDate(y.getDate() - 1);
      return { from: y, to: todayMidnight };
    }
    case "week": {
      const dow = todayMidnight.getDay();
      const mon = new Date(todayMidnight);
      mon.setDate(todayMidnight.getDate() - (dow === 0 ? 6 : dow - 1));
      return { from: mon, to: null };
    }
    case "month":
      return { from: new Date(now.getFullYear(), now.getMonth(), 1), to: null };
    case "all":
      return { from: null, to: null };
    default:
      return { from: todayMidnight, to: null };
  }
}

/* ─────────────────────────────────────────────────────────────
   Analytics computation  (pure, memoised)
───────────────────────────────────────────────────────────── */
function computeMetrics(allTrades) {
  const closed = allTrades.filter(t => t.state === "CLOSED" && t.exit_price != null);
  const open   = allTrades.filter(t => t.state !== "CLOSED");
  if (!closed.length) return null;

  const pnls    = closed.map(t => computePnl(t));
  const winPnls = pnls.filter(p => p > 0);
  const lossPnls= pnls.filter(p => p < 0);

  const totalPnL     = pnls.reduce((a, b) => a + b, 0);
  const wins         = winPnls.length;
  const losses       = lossPnls.length;
  const winRate      = (wins / closed.length) * 100;
  const avgWin       = wins   ? winPnls.reduce((a,b)  => a + b, 0) / wins   : 0;
  const avgLoss      = losses ? lossPnls.reduce((a,b) => a + b, 0) / losses : 0;
  const grossProfit  = winPnls.reduce((a,b)  => a + b, 0);
  const grossLoss    = Math.abs(lossPnls.reduce((a,b) => a + b, 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;
  const maxProfit    = wins   ? Math.max(...winPnls)  : 0;
  const maxLoss      = losses ? Math.min(...lossPnls) : 0;
  const riskPerTrade = avgWin > 0 ? (Math.abs(avgLoss) / avgWin) * 100 : 0;

  // Streaks
  let curW = 0, curL = 0, bestW = 0, bestL = 0;
  pnls.forEach(p => {
    if (p > 0)      { curW++; curL = 0; bestW = Math.max(bestW, curW); }
    else if (p < 0) { curL++; curW = 0; bestL = Math.max(bestL, curL); }
    else            { curW = 0; curL = 0; }
  });
  const currentStreak = curW > 0 ? curW : -curL;

  // Equity curve + drawdown
  let equity = 0, peak = 0, maxDD = 0;
  let inDD = false, ddStartTs = null, maxDDDays = 0;

  const equityCurve = closed.map((t) => {
    const p = computePnl(t);
    equity += p;
    const ts = t.entry_time;
    if (equity > peak) {
      if (inDD && ddStartTs && ts) {
        const days = Math.round((ts - ddStartTs) / 86400);
        maxDDDays  = Math.max(maxDDDays, days);
      }
      peak = equity; inDD = false; ddStartTs = null;
    } else {
      const dd = peak - equity;
      if (dd > maxDD) maxDD = dd;
      if (!inDD && ts) { inDD = true; ddStartTs = ts; }
    }
    return { value: equity, ts, pnl: p, symbol: t.tradingsymbol || t.symbol || "" };
  });

  function makeBreakdowns(keyFn) {
    const map = {};
    closed.forEach(t => {
      const key = keyFn(t);
      if (!map[key]) map[key] = { name: key, trades: 0, hits: 0, misses: 0, profit: 0, loss: 0 };
      const pnl = computePnl(t);
      map[key].trades++;
      if (pnl > 0) { map[key].hits++;   map[key].profit += pnl; }
      else          { map[key].misses++; map[key].loss   += pnl; }
    });
    return Object.values(map).sort((a, b) => b.trades - a.trades);
  }

  const dayBreakdown   = makeBreakdowns(t => t.entry_time ? DAY_NAMES[new Date(t.entry_time * 1000).getDay()] : "Unknown");
  const instrBreakdown = makeBreakdowns(t => extractInstrument(t.symbol || t.tradingsymbol));
  const sideBreakdown  = makeBreakdowns(t => extractSide(t.symbol || t.tradingsymbol, t.slot));

  const monthMap = {};
  closed.forEach(t => {
    if (!t.entry_time) return;
    const d = new Date(t.entry_time * 1000);
    const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    if (!monthMap[k]) monthMap[k] = { month: k, pnl: 0, trades: 0, wins: 0 };
    const pnl = computePnl(t);
    monthMap[k].pnl    += pnl;
    monthMap[k].trades++;
    if (pnl > 0) monthMap[k].wins++;
  });
  const monthlyPnL = Object.values(monthMap).sort((a, b) => a.month.localeCompare(b.month));

  return {
    totalTrades: closed.length,
    openTrades:  open.length,
    wins, losses, winRate,
    totalPnL, avgWin, avgLoss, grossProfit, grossLoss,
    profitFactor, maxProfit, maxLoss, riskPerTrade,
    bestWinStreak: bestW, bestLossStreak: bestL, currentStreak,
    maxDrawdown: maxDD, maxDrawdownDays: maxDDDays,
    equityCurve, dayBreakdown, instrBreakdown, sideBreakdown, monthlyPnL,
    closedTrades: closed, openTradesList: open,
  };
}

/* ─────────────────────────────────────────────────────────────
   Sub-components
───────────────────────────────────────────────────────────── */

function KpiCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: C.bgCard, border: `1px solid ${C.border}`,
      borderRadius: 8, padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 5,
    }}>
      <div style={{ fontSize: 9, fontWeight: 700, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.8px" }}>
        {label}
      </div>
      <div style={{ fontSize: 21, fontWeight: 700, fontFamily: MONO, color: color || C.text, lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: C.textSec, fontFamily: MONO, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function SplitKpi({ label, left, right }) {
  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
      <div style={{ fontSize: 9, fontWeight: 700, color: C.textMuted, textTransform: "uppercase", letterSpacing: "0.8px", marginBottom: 10 }}>
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 0 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 3 }}>{left.label}</div>
          <div style={{ fontSize: 17, fontWeight: 700, fontFamily: MONO, color: left.color || C.green }}>{left.value}</div>
        </div>
        <div style={{ width: 1, height: 36, background: C.borderDim, margin: "0 14px" }} />
        <div style={{ flex: 1, textAlign: "right" }}>
          <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 3 }}>{right.label}</div>
          <div style={{ fontSize: 17, fontWeight: 700, fontFamily: MONO, color: right.color || C.red }}>{right.value}</div>
        </div>
      </div>
    </div>
  );
}

function EquityCurve({ data, width, height = 230 }) {
  if (!data || data.length < 2) return null;

  const P = { top: 20, right: 16, bottom: 32, left: 76 };
  const W = width  - P.left - P.right;
  const H = height - P.top  - P.bottom;

  const vals   = data.map(d => d.value);
  const minVal = Math.min(...vals, 0);
  const maxVal = Math.max(...vals, 0);
  const range  = (maxVal - minVal) || 1;

  const px  = i   => P.left + (i / (data.length - 1)) * W;
  const py  = val => P.top  + H - ((val - minVal) / range) * H;
  const y0  = py(0);

  const pathD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${px(i).toFixed(1)} ${py(d.value).toFixed(1)}`).join(" ");
  const areaD = `${pathD} L ${px(data.length-1).toFixed(1)} ${y0.toFixed(1)} L ${P.left} ${y0.toFixed(1)} Z`;

  const ddSegments = [];
  let inDD = false, ddPts = [];
  let runPeak = data[0]?.value || 0;
  data.forEach((d, i) => {
    if (d.value > runPeak) runPeak = d.value;
    const isDown = d.value < runPeak;
    if (isDown && !inDD)   { inDD = true;  ddPts = [i]; }
    else if (!isDown && inDD) { inDD = false; ddPts.push(i); ddSegments.push([...ddPts]); ddPts = []; }
    else if (inDD)            { ddPts.push(i); }
  });
  if (inDD && ddPts.length) ddSegments.push(ddPts);

  const finalPnL  = data[data.length - 1]?.value || 0;
  const lineColor = finalPnL >= 0 ? C.green : C.red;

  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => minVal + (range / (tickCount - 1)) * i);
  const step  = Math.max(1, Math.floor(data.length / 8));
  const timeLabels = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={lineColor} stopOpacity="0.3" />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={P.left} y1={py(t)} x2={P.left + W} y2={py(t)} stroke={C.borderDim} strokeWidth={0.5} />
          <text x={P.left - 6} y={py(t) + 4} textAnchor="end" fontSize={9} fill={C.textMuted} fontFamily={MONO}>
            {t < 0 ? "-" : ""}{fmtInr(Math.abs(t))}
          </text>
        </g>
      ))}
      {minVal < 0 && maxVal > 0 && (
        <line x1={P.left} y1={y0} x2={P.left + W} y2={y0}
          stroke={C.textMuted} strokeWidth={1} strokeDasharray="4 3" opacity={0.5} />
      )}
      {ddSegments.map((seg, si) => {
        if (seg.length < 2) return null;
        const pts = seg.map(i => ({ x: px(i), y: py(data[i].value) }));
        const areaSeg = pts.map((p, j) => `${j === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ")
          + ` L ${pts[pts.length-1].x.toFixed(1)} ${y0.toFixed(1)} L ${pts[0].x.toFixed(1)} ${y0.toFixed(1)} Z`;
        return <path key={si} d={areaSeg} fill={C.red} opacity={0.08} />;
      })}
      <path d={areaD} fill="url(#eqGrad)" />
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={px(0)}              cy={py(data[0].value)}              r={4} fill={data[0].value >= 0 ? C.green : C.red} />
      <circle cx={px(data.length-1)} cy={py(data[data.length-1].value)} r={4} fill={finalPnL >= 0 ? C.green : C.red} />
      {timeLabels.map((d, idx) => (
        <text key={idx} x={px(d.i ?? idx * step).toFixed(1)} y={P.top + H + 18}
          textAnchor="middle" fontSize={9} fill={C.textMuted} fontFamily={MONO}>
          {d.ts ? new Date(d.ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : ""}
        </text>
      ))}
    </svg>
  );
}

const LEGEND = [
  { color: C.teal,  label: "HIT"    },
  { color: C.amber, label: "MISS"   },
  { color: C.green, label: "PROFIT" },
  { color: C.red,   label: "LOSS"   },
];

function BreakdownRow({ item, maxTrades, maxPnL }) {
  if (item.trades === 0) return null;
  const hitPct  = maxTrades ? (item.hits             / maxTrades) * 100 : 0;
  const missPct = maxTrades ? (item.misses            / maxTrades) * 100 : 0;
  const profPct = maxPnL > 0 ? (item.profit           / maxPnL)   * 100 : 0;
  const lossPct = maxPnL > 0 ? (Math.abs(item.loss)   / maxPnL)   * 100 : 0;
  const wr      = ((item.hits / item.trades) * 100).toFixed(0);
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{item.name}</span>
          <span style={{ fontSize: 10, color: C.textMuted }}>{item.trades} trades</span>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
            background: Number(wr) >= 50 ? C.greenBg : C.redBg,
            color:      Number(wr) >= 50 ? C.green   : C.red,
          }}>{wr}% WR</span>
        </div>
        <div style={{ display: "flex", gap: 14, fontSize: 10, fontFamily: MONO }}>
          <span style={{ color: C.teal  }}>Hit {item.hits}</span>
          <span style={{ color: C.amber }}>Miss {item.misses}</span>
        </div>
      </div>
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: C.bgSurface, marginBottom: 4 }}>
        <div style={{ width: `${hitPct}%`,  background: C.teal,  transition: "width 0.5s" }} />
        <div style={{ width: `${missPct}%`, background: C.amber, transition: "width 0.5s" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ fontSize: 10, fontFamily: MONO, color: C.green }}>+{fmtInr(item.profit)}</span>
        <span style={{ fontSize: 10, fontFamily: MONO, color: C.red  }}>-{fmtInr(Math.abs(item.loss))}</span>
      </div>
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: C.bgSurface }}>
        <div style={{ width: `${profPct}%`, background: C.green, transition: "width 0.5s" }} />
        <div style={{ width: `${lossPct}%`, background: C.red,   transition: "width 0.5s" }} />
      </div>
    </div>
  );
}

function BreakdownPanel({ title, items, maxTrades, maxPnL }) {
  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 12 }}>{title}</div>
      <div style={{ display: "flex", gap: 12, marginBottom: 14 }}>
        {LEGEND.map(({ color, label }) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: C.textMuted }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
            {label}
          </div>
        ))}
      </div>
      {items.map(it => (
        <BreakdownRow key={it.name} item={it} maxTrades={maxTrades} maxPnL={maxPnL} />
      ))}
      {items.length === 0 && (
        <div style={{ fontSize: 12, color: C.textMuted, textAlign: "center", padding: "20px 0" }}>No data</div>
      )}
    </div>
  );
}

function MonthlyGrid({ data }) {
  if (!data?.length) return null;
  const maxAbs = Math.max(...data.map(d => Math.abs(d.pnl)), 1);
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      {data.map(m => {
        const isPos = m.pnl >= 0;
        const inten = Math.abs(m.pnl) / maxAbs;
        const [yr, mo] = m.month.split("-");
        const moName = new Date(Number(yr), Number(mo) - 1, 1)
          .toLocaleString("en-IN", { month: "short" });
        const wr = m.trades ? ((m.wins / m.trades) * 100).toFixed(0) : 0;
        return (
          <div key={m.month} style={{
            background: isPos
              ? `rgba(16,185,129,${0.12 + inten * 0.55})`
              : `rgba(239,68,68,${0.12 + inten * 0.55})`,
            border:       `1px solid ${isPos ? "rgba(16,185,129,0.35)" : "rgba(239,68,68,0.35)"}`,
            borderRadius: 8, padding: "10px 14px",
            minWidth: 100, textAlign: "center",
          }}>
            <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 4 }}>{moName} {yr}</div>
            <div style={{ fontSize: 14, fontWeight: 700, fontFamily: MONO, color: isPos ? C.green : C.red }}>
              {isPos ? "+" : ""}{fmtInr(m.pnl)}
            </div>
            <div style={{ fontSize: 10, color: C.textMuted, marginTop: 4 }}>
              {m.trades} trades · {wr}% WR
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Open Live Trades Panel  (v3 — position-track cards)

   Shows currently-open positions (PROTECTED / BUY_PLACED / etc.)
   with real-time unrealised P&L from the LTPStore snapshot.

     - Card grid (≤6 open trades) replaces the flat 13-col table.
       Each card makes the SL↔TP journey the hero: a single track
       with a live dot at the current LTP, plus big LTP, P&L with %
       and direction, time-in-trade, and GTT-protection status.
     - DIRECTION IS PER-STRATEGY (via the shared isShortTrade helper):
         SCALP_V1 / SCALP_V2 → SHORT (option selling): TP below entry,
           SL above. Track: TP on the LEFT, SL on the RIGHT — dot near
           left = winning.
         BB_V1 / BB_V2 / HA_V1 → LONG (option buying): TP above entry,
           SL below. Track: TP on the RIGHT, SL on the LEFT — dot near
           right = winning.
       LONG and SHORT cards show MIRRORED tracks; both ends are labelled
       with TP/SL + price, and a "% to TP" readout keeps the target
       unambiguous regardless of orientation.
     - Above 6 open trades, falls back to the compact table.
     - Missing tick → greyed dot, "awaiting tick", no misleading position.
───────────────────────────────────────────────────────────── */

const CARD_FALLBACK_LIMIT = 6;

/* Fraction (0..100) of the way from SL toward TP, direction-aware.
 * Returns null when any leg or the live tick is missing. */
function progressToTp(t, ltp) {
  const tp = safeNum(t.tp_price), sl = safeNum(t.sl_price);
  if (!tp || !sl || ltp == null) return null;
  const short = isShortTrade(t);
  const range = Math.abs(sl - tp);
  if (range <= 0) return null;
  const pct = short
    ? ((sl - ltp) / range) * 100      // SHORT: lower ltp → closer to TP
    : ((ltp - sl) / range) * 100;     // LONG:  higher ltp → closer to TP
  return Math.max(0, Math.min(100, pct));
}

/* ─── Single position-track card ─── */
function OpenTradeCard({ t, ltpMap }) {
  const symbol     = t.symbol || t.tradingsymbol || "—";
  const normSym    = symbol.toUpperCase().replace(/\s+/g, "");
  const ltp        = ltpMap[normSym] ?? null;
  const side       = extractSide(symbol, t.slot);
  const short      = isShortTrade(t);
  const unrealised = computeUnrealisedPnl(t, ltpMap);
  const stratDef   = STRATEGIES.find((s) => s.id === t.strategy_id);

  const entry      = safeNum(t.entry_price);
  const pctMove    = (entry && ltp != null)
    ? ((short ? (entry - ltp) : (ltp - entry)) / entry) * 100
    : null;

  const prog       = progressToTp(t, ltp);            // 0..100 toward TP, or null
  const uColor     = unrealised == null ? C.textMuted : unrealised >= 0 ? C.green : C.red;
  const hasGtt     = !!t.sl_order_id;
  const now        = Math.floor(Date.now() / 1000);

  /* Track geometry: TP end is "good", SL end is "bad".
   * SHORT → TP left / SL right.  LONG → TP right / SL left.
   * `prog` is always % toward TP, so dotLeft maps prog onto the
   * physical left-right axis according to orientation. */
  const tpOnLeft   = short;
  const dotLeft    = prog == null ? null : (tpOnLeft ? (100 - prog) : prog);
  const dotColor   = prog == null ? C.textMuted
                   : prog >= 66 ? C.green
                   : prog >= 33 ? C.amber
                   : C.red;

  const TPLabel = (
    <span style={{ fontSize: 11, fontWeight: 700, color: C.green }}>
      TP {t.tp_price ? t.tp_price.toFixed(2) : "—"}
    </span>
  );
  const SLLabel = (
    <span style={{ fontSize: 11, fontWeight: 700, color: C.red }}>
      SL {t.sl_price ? t.sl_price.toFixed(2) : "—"}
    </span>
  );

  return (
    <div style={{
      background: C.bgCard,
      border: `1px solid ${C.border}`,
      borderTop: `3px solid ${stratDef ? stratDef.color : C.amber}`,
      borderRadius: 8, padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 2,
    }}>
      {/* Row 1: symbol + direction */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={symbol}>
          {symbol}
        </span>
        <span style={{ flexShrink: 0, fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
          background: short ? C.redBg : C.greenBg, color: short ? C.red : C.green }}>
          {short ? "↓ SELL" : "↑ BUY"}
        </span>
      </div>

      {/* Row 2: strategy / side / time / GTT */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, fontSize: 11, color: C.textMuted, flexWrap: "wrap" }}>
        <span style={{ padding: "1px 7px", borderRadius: 3, fontWeight: 700,
          background: stratDef ? `${stratDef.color}20` : C.bgSurface,
          color: stratDef ? stratDef.color : C.textMuted }}>
          {t.strategy_id || "—"}
        </span>
        <span style={{ padding: "1px 7px", borderRadius: 3, fontWeight: 700,
          background: side === "CE" ? C.greenBg : side === "PE" ? C.redBg : C.bgSurface,
          color:      side === "CE" ? C.green   : side === "PE" ? C.red   : C.textMuted }}>
          {side}
        </span>
        <span style={{ fontFamily: MONO, color: C.cyan }}>{fmtDuration(t.entry_time, now)}</span>
        <span style={{ marginLeft: "auto", fontWeight: 700,
          color: hasGtt ? C.green : C.amber }}>
          {hasGtt ? `✓ GTT ${String(t.sl_order_id).slice(-4)}` : "⚠ No GTT"}
        </span>
      </div>

      {/* Row 3: live LTP + entry */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 3 }}>
        <span style={{ fontFamily: MONO, fontSize: 28, fontWeight: 700, lineHeight: 1,
          color: ltp != null ? C.text : C.textMuted }}>
          {ltp != null ? ltp.toFixed(2) : "—"}
        </span>
        <span style={{ fontSize: 12, color: C.textMuted }}>
          {ltp != null ? "live" : "no tick"} · entry {entry ? entry.toFixed(2) : "—"}
        </span>
      </div>

      {/* Row 4: unrealised P&L + % move + qty */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 16 }}>
        <span style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700, color: uColor }}>
          {unrealised == null ? "—" : `${unrealised >= 0 ? "+" : ""}${fmtInr(unrealised)}`}
        </span>
        {pctMove != null && (
          <span style={{ fontSize: 12, fontWeight: 600, color: uColor }}>
            {pctMove >= 0 ? "▲" : "▼"} {Math.abs(pctMove).toFixed(1)}%
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 11, color: C.textMuted, fontFamily: MONO }}>
          qty {t.qty ?? "—"}
        </span>
      </div>

      {/* Row 5: SL↔TP position track */}
      <div style={{ position: "relative", height: 40, margin: "0 2px" }}>
        {/* % to TP readout, anchored above the dot */}
        {dotLeft != null && (
          <div style={{ position: "absolute", top: 0, left: `${dotLeft}%`, transform: "translateX(-50%)",
            fontSize: 10, fontWeight: 700, color: dotColor, whiteSpace: "nowrap" }}>
            {Math.round(prog)}% to TP
          </div>
        )}
        {/* the track */}
        <div style={{ position: "absolute", top: 18, left: 0, right: 0, height: 6, borderRadius: 99,
          background: C.bgSurface, overflow: "hidden" }}>
          {/* subtle profit-side tint: fill from the TP end up to the dot */}
          {dotLeft != null && (
            <div style={{
              position: "absolute", top: 0, bottom: 0,
              ...(tpOnLeft ? { left: 0, width: `${100 - dotLeft}%` }
                           : { right: 0, width: `${dotLeft}%` }),
              background: prog >= 50 ? C.greenBg : C.redBg,
            }} />
          )}
        </div>
        {/* live dot */}
        {dotLeft != null ? (
          <div style={{ position: "absolute", top: 12, left: `${dotLeft}%`, transform: "translateX(-50%)",
            width: 14, height: 14, borderRadius: "50%", background: dotColor,
            border: `2px solid ${C.bgCard}`, boxShadow: `0 0 0 1px ${dotColor}` }} />
        ) : (
          <div style={{ position: "absolute", top: 14, left: "50%", transform: "translateX(-50%)",
            fontSize: 10, color: C.textMuted }}>awaiting tick</div>
        )}
        {/* end labels: TP/SL swap with orientation */}
        <div style={{ position: "absolute", top: 28, left: 0 }}>{tpOnLeft ? TPLabel : SLLabel}</div>
        <div style={{ position: "absolute", top: 28, right: 0 }}>{tpOnLeft ? SLLabel : TPLabel}</div>
      </div>
    </div>
  );
}

/* ─── Compact table fallback (the original v2 table) ─── */
function OpenTradesTable({ trades, ltpMap }) {
  const now = Math.floor(Date.now() / 1000);
  const TD = { padding: "10px 12px", fontSize: 12, fontFamily: MONO, verticalAlign: "middle" };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead style={{ background: C.bgSurface }}>
          <tr>
            {["Symbol","Strategy","Side","Dir","Entry","LTP","Unrealised","SL","TP","GTT","Entry Time","Duration","Status"].map((h) => (
              <th key={h} style={{ padding: "8px 12px", fontSize: 9, fontWeight: 700, color: C.textMuted,
                textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "left",
                borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const symbol     = t.symbol || t.tradingsymbol || "—";
            const side       = extractSide(symbol, t.slot);
            const short      = isShortTrade(t);
            const ltp        = ltpMap[symbol.toUpperCase().replace(/\s+/g, "")] ?? null;
            const unrealised = computeUnrealisedPnl(t, ltpMap);
            const uColor     = unrealised == null ? C.textMuted : unrealised >= 0 ? C.green : C.red;
            const statusColor= t.state === "PROTECTED" ? C.green : C.amber;
            const stratDef   = STRATEGIES.find((s) => s.id === t.strategy_id);

            return (
              <tr key={t.trade_id || i}
                style={{ background: i % 2 ? C.bgCard : C.bg, borderTop: `1px solid ${C.borderDim}` }}
                onMouseEnter={(e) => (e.currentTarget.style.background = C.bgSurface)}
                onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 ? C.bgCard : C.bg)}
              >
                <td style={{ ...TD, color: C.text, fontWeight: 700, whiteSpace: "nowrap" }}>{symbol}</td>
                <td style={TD}>
                  <span style={{ padding: "2px 8px", borderRadius: 3, fontSize: 11, fontWeight: 700,
                    background: stratDef ? `${stratDef.color}20` : C.bgSurface,
                    color: stratDef ? stratDef.color : C.textMuted }}>{t.strategy_id || "—"}</span>
                </td>
                <td style={TD}>
                  <span style={{ padding: "2px 7px", borderRadius: 3, fontSize: 11, fontWeight: 700,
                    background: side === "CE" ? C.greenBg : side === "PE" ? C.redBg : C.bgSurface,
                    color:      side === "CE" ? C.green   : side === "PE" ? C.red   : C.textMuted }}>{side}</span>
                </td>
                <td style={TD}>
                  <span style={{ padding: "2px 7px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: short ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.12)",
                    color:      short ? C.red : C.green }}>{short ? "↓ SELL" : "↑ BUY"}</span>
                </td>
                <td style={{ ...TD, color: C.textSec }}>{t.entry_price?.toFixed(2) ?? "—"}</td>
                <td style={{ ...TD, fontWeight: 700, color: ltp != null ? C.text : C.textMuted }}>
                  {ltp != null ? ltp.toFixed(2) : <span style={{ opacity: 0.4 }}>No tick</span>}
                </td>
                <td style={{ ...TD, fontWeight: 700, color: uColor, textAlign: "right" }}>
                  {unrealised == null ? <span style={{ color: C.textMuted, fontWeight: 400 }}>—</span>
                    : `${unrealised >= 0 ? "+" : ""}${fmtInr(unrealised)}`}
                </td>
                <td style={{ ...TD, color: C.red, fontSize: 11 }}>{t.sl_price ? t.sl_price.toFixed(2) : "—"}</td>
                <td style={{ ...TD, color: C.green, fontSize: 11 }}>{t.tp_price ? t.tp_price.toFixed(2) : "—"}</td>
                <td style={{ ...TD, fontSize: 10 }}>
                  {t.sl_order_id
                    ? <span style={{ padding: "1px 5px", borderRadius: 3, background: C.greenBg, color: C.green }}>✓ {String(t.sl_order_id).slice(-6)}</span>
                    : <span style={{ color: C.amber }}>⚠ No GTT</span>}
                </td>
                <td style={{ ...TD, color: C.textMuted, fontSize: 11, whiteSpace: "nowrap" }}>{fmtDateTime(t.entry_time)}</td>
                <td style={{ ...TD, color: C.cyan, fontSize: 11 }}>{fmtDuration(t.entry_time, now)}</td>
                <td style={TD}>
                  <span style={{ padding: "2px 8px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: `${statusColor}20`, color: statusColor }}>{t.state}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ─── Main panel ─── */
function OpenTradesPanel({ trades, ltpMap }) {
  if (!trades.length) {
    return (
      <div style={{
        background: C.bgCard, border: `1px solid ${C.border}`,
        borderRadius: 8, marginBottom: 16, padding: "20px 24px",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{ fontSize: 18 }}>🔭</span>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>No open live trades</div>
          <div style={{ fontSize: 11, color: C.textMuted, marginTop: 2 }}>
            Open trades will appear here in real-time as soon as a position is entered
          </div>
        </div>
      </div>
    );
  }

  const totalUnrealised = trades.reduce((acc, t) => acc + (computeUnrealisedPnl(t, ltpMap) ?? 0), 0);
  const useCards = trades.length <= CARD_FALLBACK_LIMIT;

  return (
    <div style={{
      background: C.bgCard,
      border: `1px solid ${C.amber}40`,
      borderLeft: `3px solid ${C.amber}`,
      borderRadius: 8, marginBottom: 16, overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.borderDim}`,
        display: "flex", justifyContent: "space-between", alignItems: "center",
        background: "rgba(245,158,11,0.05)", flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: C.amber,
            animation: "livePulse 1.5s ease-in-out infinite", flexShrink: 0 }} />
          <span style={{ fontSize: 14, fontWeight: 700, color: C.amber }}>
            Open Positions · {trades.length}
          </span>
          <span style={{ fontSize: 10, color: C.textMuted }}>Live tracking · updates every 2s</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700, color: totalUnrealised >= 0 ? C.green : C.red }}>
          Unrealised: {totalUnrealised >= 0 ? "+" : ""}{fmtInr(totalUnrealised)}
        </div>
      </div>

      {/* Body: cards ≤6, else table */}
      {useCards ? (
        <div style={{ padding: 16, display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
          {trades.map((t, i) => (
            <OpenTradeCard key={t.trade_id || i} t={t} ltpMap={ltpMap} />
          ))}
        </div>
      ) : (
        <OpenTradesTable trades={trades} ltpMap={ltpMap} />
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Closed Trade Table
───────────────────────────────────────────────────────────── */
function TradeTable({ trades }) {
  const [sortCol, setSortCol] = useState("entry_time");
  const [sortDir, setSortDir] = useState("desc");

  function toggleSort(col) {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("desc"); }
  }

  const sorted = useMemo(() => {
    return [...trades].sort((a, b) => {
      let va = a[sortCol] ?? 0;
      let vb = b[sortCol] ?? 0;
      // special case: sort by computed pnl
      if (sortCol === "_pnl") { va = computePnl(a); vb = computePnl(b); }
      if (typeof va === "string") { va = va.toLowerCase(); vb = (vb ?? "").toLowerCase(); }
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1  : -1;
      return 0;
    });
  }, [trades, sortCol, sortDir]);

  const TH_STYLE = (col) => ({
    padding: "9px 10px", fontSize: 9, fontWeight: 700,
    color: sortCol === col ? C.blue : C.textMuted,
    textTransform: "uppercase", letterSpacing: "0.4px",
    textAlign: "left", borderBottom: `1px solid ${C.border}`,
    whiteSpace: "nowrap", cursor: "pointer", userSelect: "none",
  });
  const TD = { padding: "8px 10px", fontSize: 12, fontFamily: MONO, verticalAlign: "middle" };

  const exitReasonColor = (r) => {
    if (!r) return C.textMuted;
    if (["GTT_TP","TP","EOD_SQUARE_OFF","SuperTrend"].includes(r)) return C.green;
    if (["GTT_SL","SL"].includes(r)) return C.red;
    return C.amber;
  };

  const COLS = [
    { col: "tradingsymbol", label: "Symbol"      },
    { col: "strategy_id",   label: "Strategy"    },
    { col: "slot",          label: "Side"        },
    { col: "trade_direction",label:"Dir"         },
    { col: "entry_price",   label: "Entry ₹"    },
    { col: "exit_price",    label: "Exit ₹"     },
    { col: "qty",           label: "Qty"         },
    { col: "_pnl",          label: "P&L"         },
    { col: "exit_reason",   label: "Reason"      },
    { col: "entry_time",    label: "Entry Time"  },
    { col: "exit_time",     label: "Exit Time"   },
    { col: "entry_time",    label: "Duration"    },
  ];

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead style={{ background: C.bgSurface }}>
          <tr>
            {COLS.map(({ col, label }, ci) => (
              <th key={ci} style={TH_STYLE(col)} onClick={() => toggleSort(col)}>
                {label} {sortCol === col ? (sortDir === "asc" ? "↑" : "↓") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((t, i) => {
            const pnl      = computePnl(t);
            const isWin    = pnl > 0;
            const side     = extractSide(t.symbol || t.tradingsymbol, t.slot);
            const short    = isShortTrade(t);
            const symbol   = t.tradingsymbol || t.symbol || "—";
            const stratDef = STRATEGIES.find(s => s.id === t.strategy_id);

            return (
              <tr key={t.trade_id || i}
                style={{ background: i % 2 ? C.bgCard : C.bg, borderTop: `1px solid ${C.borderDim}` }}
                onMouseEnter={e => (e.currentTarget.style.background = C.bgSurface)}
                onMouseLeave={e => (e.currentTarget.style.background = i % 2 ? C.bgCard : C.bg)}
              >
                {/* Symbol */}
                <td style={{ ...TD, color: C.text, fontWeight: 600, whiteSpace: "nowrap" }}>{symbol}</td>

                {/* Strategy */}
                <td style={TD}>
                  <span style={{
                    padding: "2px 7px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: stratDef ? `${stratDef.color}20` : C.bgSurface,
                    color: stratDef ? stratDef.color : C.textMuted,
                  }}>{t.strategy_id || "—"}</span>
                </td>

                {/* Side (CE/PE) */}
                <td style={TD}>
                  <span style={{
                    padding: "2px 7px", borderRadius: 3, fontSize: 11, fontWeight: 700,
                    background: side === "CE" ? C.greenBg : side === "PE" ? C.redBg : C.bgSurface,
                    color:      side === "CE" ? C.green   : side === "PE" ? C.red   : C.textMuted,
                  }}>{side}</span>
                </td>

                {/* Direction (LONG/SHORT) — via shared predicate */}
                <td style={TD}>
                  <span style={{
                    padding: "2px 6px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: short ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.12)",
                    color:      short ? C.red : C.green,
                  }}>{short ? "↓ SELL" : "↑ BUY"}</span>
                </td>

                {/* Entry / Exit prices */}
                <td style={{ ...TD, color: C.textSec }}>{t.entry_price?.toFixed(2) ?? "—"}</td>
                <td style={{ ...TD, color: C.textSec }}>{t.exit_price?.toFixed(2)  ?? "—"}</td>
                <td style={{ ...TD, color: C.textSec }}>{t.qty ?? "—"}</td>

                {/* P&L */}
                <td style={{
                  ...TD, textAlign: "right", fontWeight: 700,
                  color:      pnl !== 0 ? (isWin ? C.green : C.red) : C.textMuted,
                  background: pnl !== 0 ? (isWin ? "rgba(16,185,129,0.07)" : "rgba(239,68,68,0.07)") : "transparent",
                }}>
                  {pnl !== 0 ? `${isWin ? "+" : ""}${fmtInr(pnl)}` : "—"}
                </td>

                {/* Exit Reason */}
                <td style={TD}>
                  {t.exit_reason && (
                    <span style={{
                      padding: "2px 7px", borderRadius: 3, fontSize: 10, fontWeight: 600,
                      background: isWin ? C.greenBg : C.redBg,
                      color: exitReasonColor(t.exit_reason),
                    }}>{t.exit_reason}</span>
                  )}
                </td>

                {/* Entry Time */}
                <td style={{ ...TD, color: C.textMuted, fontSize: 11, whiteSpace: "nowrap" }}>
                  {fmtDateTime(t.entry_time)}
                </td>

                {/* Exit Time */}
                <td style={{ ...TD, color: C.textMuted, fontSize: 11, whiteSpace: "nowrap" }}>
                  {fmtDateTime(t.exit_time)}
                </td>

                {/* Duration */}
                <td style={{ ...TD, color: C.cyan, fontSize: 11 }}>
                  {t.entry_time && t.exit_time ? fmtDuration(t.entry_time, t.exit_time) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Strategy Multi-Select Chips
───────────────────────────────────────────────────────────── */
function StrategyFilter({ selected, onChange }) {
  const allSelected = selected.length === 0;

  function toggleAll() { onChange([]); }

  function toggleStrategy(id) {
    if (selected.includes(id)) {
      const next = selected.filter(s => s !== id);
      onChange(next);
    } else {
      onChange([...selected, id]);
    }
  }

  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
      {/* ALL chip */}
      <button
        onClick={toggleAll}
        style={{
          padding: "5px 13px", borderRadius: 20, border: `1px solid ${allSelected ? C.text : C.borderDim}`,
          cursor: "pointer", fontSize: 12, fontWeight: allSelected ? 700 : 400, fontFamily: FONT,
          background: allSelected ? C.bgSurface : "transparent",
          color: allSelected ? C.text : C.textMuted,
          transition: "all 0.15s",
        }}
      >
        All
      </button>

      {STRATEGIES.map(s => {
        const active = selected.includes(s.id);
        return (
          <button
            key={s.id}
            onClick={() => toggleStrategy(s.id)}
            title={s.desc}
            style={{
              padding: "5px 13px", borderRadius: 20,
              border:     `1px solid ${active ? s.color : C.borderDim}`,
              cursor:     "pointer", fontSize: 12, fontWeight: active ? 700 : 400, fontFamily: FONT,
              background: active ? `${s.color}20` : "transparent",
              color:      active ? s.color : C.textMuted,
              transition: "all 0.15s",
            }}
          >
            {active && <span style={{ marginRight: 4 }}>✓</span>}
            {s.label}
          </button>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Main Component
───────────────────────────────────────────────────────────── */
export default function Analytics() {
  const toast = useToast();

  const [trades,           setTrades]           = useState([]);
  const [loading,          setLoading]          = useState(true);
  const [error,            setError]            = useState(null);
  const [preset,           setPreset]           = useState("today");
  const [customFrom,       setCustomFrom]       = useState("");
  const [customTo,         setCustomTo]         = useState("");
  const [selectedStrategies, setSelectedStrategies] = useState([]); // empty = all
  const [activeTab,        setActiveTab]        = useState("trades");
  const [ltpMap,           setLtpMap]           = useState({});

  const containerRef = useRef(null);
  const [chartWidth, setChartWidth] = useState(800);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([e]) =>
      setChartWidth(Math.max(300, e.contentRect.width - 32))
    );
    ro.observe(containerRef.current);
    setChartWidth(Math.max(300, containerRef.current.offsetWidth - 32));
    return () => ro.disconnect();
  }, []);

  // LTP poll every 2s for open trade unrealised P&L
  useEffect(() => {
    let alive = true;
    async function poll() {
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
        await new Promise(r => setTimeout(r, 2000));
      }
    }
    poll();
    return () => { alive = false; };
  }, []);

  const fetchTrades = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let fromTs, toTs;

      if (preset === "custom") {
        fromTs = customFrom ? Math.floor(new Date(customFrom + "T00:00:00").getTime() / 1000) : null;
        toTs   = customTo   ? Math.floor(new Date(customTo   + "T23:59:59").getTime() / 1000) : null;
      } else {
        const r = getPresetRange(preset);
        fromTs  = r.from ? Math.floor(r.from.getTime() / 1000) : null;
        toTs    = r.to   ? Math.floor(r.to.getTime()   / 1000) : null;
      }

      // Fetch for each selected strategy, or a single "all" fetch
      let allTrades = [];

      const strategyIds = selectedStrategies.length > 0 ? selectedStrategies : [null];

      await Promise.all(strategyIds.map(async (sid) => {
        const p = new URLSearchParams();
        if (fromTs) p.set("from_ts",    String(fromTs));
        if (toTs)   p.set("to_ts",      String(toTs));
        if (sid)    p.set("strategy_id", sid);
        // Include open trades too
        p.set("include_open", "true");

        const res = await fetch(`${getApiBase()}/trades/history?${p}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (Array.isArray(data)) allTrades.push(...data);
      }));

      // Deduplicate by trade_id
      const seen = new Set();
      allTrades = allTrades.filter(t => {
        if (!t.trade_id) return true;
        if (seen.has(t.trade_id)) return false;
        seen.add(t.trade_id);
        return true;
      });

      setTrades(allTrades);
    } catch (e) {
      setError(e.message);
      setTrades([]);
    } finally {
      setLoading(false);
    }
  }, [preset, customFrom, customTo, selectedStrategies]);

  useEffect(() => {
    fetchTrades();
    const iv = setInterval(fetchTrades, preset === "today" ? 10_000 : 60_000);
    return () => clearInterval(iv);
  }, [fetchTrades, preset]);

  // Separate open (live only, not paper) and closed
  const openLiveTrades  = useMemo(
    () => trades.filter(t => t.state !== "CLOSED" && t.exit_time == null),
    [trades]
  );
  const closedTrades    = useMemo(
    () => trades.filter(t => t.state === "CLOSED" && t.exit_price != null),
    [trades]
  );

  const metrics = useMemo(() => computeMetrics(trades), [trades]);

  const { maxBdTrades, maxBdPnL } = useMemo(() => {
    if (!metrics) return { maxBdTrades: 1, maxBdPnL: 1 };
    const all = [...metrics.dayBreakdown, ...metrics.instrBreakdown, ...metrics.sideBreakdown];
    return {
      maxBdTrades: Math.max(...all.map(d => d.trades), 1),
      maxBdPnL:    Math.max(...all.map(d => Math.max(d.profit, Math.abs(d.loss))), 1),
    };
  }, [metrics]);

  /* ── Styles ─────────────────────────────────────────────── */
  const presetBtn = (k) => ({
    padding: "5px 13px", borderRadius: 5, border: "none", cursor: "pointer",
    fontSize: 12, fontWeight: preset === k ? 600 : 400, fontFamily: FONT,
    background:    preset === k ? C.bgSurface : "transparent",
    color:         preset === k ? C.text      : C.textMuted,
    borderBottom:  preset === k ? `2px solid ${C.amber}` : "2px solid transparent",
    transition:    "all 0.15s",
  });

  const tabBtn = (k) => ({
    padding: "7px 18px", borderRadius: 6, border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600, fontFamily: FONT,
    background: activeTab === k ? C.blue : "transparent",
    color:      activeTab === k ? "#fff" : C.textMuted,
    transition: "all 0.15s",
  });

  const dateInput = {
    padding: "5px 9px", borderRadius: 5,
    border: `1px solid ${C.border}`, background: C.bgCard,
    color: C.text, fontSize: 12, fontFamily: FONT,
  };

  const totalUnrealised = openLiveTrades.reduce((acc, t) => {
    const u = computeUnrealisedPnl(t, ltpMap);
    return acc + (u ?? 0);
  }, 0);

  return (
    <div style={{ padding: 24, background: C.bg, color: C.text, minHeight: "100vh", fontFamily: FONT }}>

      {/* ── Page Header ──────────────────────────────────────── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700 }}>Performance Analytics</h1>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: C.textMuted }}>
            {openLiveTrades.length > 0 && (
              <span style={{
                marginRight: 10,
                padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 700,
                background: C.amberBg, color: C.amber,
                border: `1px solid ${C.amber}40`,
              }}>
                ● {openLiveTrades.length} open · {totalUnrealised >= 0 ? "+" : ""}{fmtInr(totalUnrealised)} unrealised
              </span>
            )}
            {metrics
              ? `${metrics.totalTrades} closed · Net P&L: ${metrics.totalPnL >= 0 ? "+" : ""}${fmtInr(metrics.totalPnL)}`
              : "Select a date range to analyse your performance"}
          </p>
        </div>

        <button
          disabled={!metrics}
          onClick={() => {
            if (!metrics) return;
            const rows = metrics.closedTrades.map(t => ({
              Symbol:      t.tradingsymbol || t.symbol,
              Strategy:    t.strategy_id,
              Side:        extractSide(t.symbol || t.tradingsymbol, t.slot),
              Direction:   isShortTrade(t) ? "SHORT" : "LONG",
              Entry:       t.entry_price,
              Exit:        t.exit_price,
              Qty:         t.qty,
              PnL:         computePnl(t),
              Reason:      t.exit_reason,
              "Entry Time": t.entry_time ? fmtDateTime(t.entry_time) : "",
              "Exit Time":  t.exit_time  ? fmtDateTime(t.exit_time)  : "",
              Duration:    t.entry_time && t.exit_time ? fmtDuration(t.entry_time, t.exit_time) : "",
            }));
            exportToCSV(rows, generateFilename("analytics_export", "csv"));
            toast.success("Exported", `${rows.length} trades downloaded`);
          }}
          style={{
            padding: "8px 16px", borderRadius: 6, border: `1px solid ${C.border}`,
            background: C.bgCard, color: metrics ? C.text : C.textMuted,
            fontSize: 13, fontWeight: 600, cursor: metrics ? "pointer" : "not-allowed",
            opacity: metrics ? 1 : 0.4,
          }}
        >
          📄 Export CSV
        </button>
      </div>

      {/* ── Controls ────────────────────────────────────────── */}
      <div style={{
        background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8,
        padding: "12px 16px", marginBottom: 20,
        display: "flex", flexDirection: "column", gap: 12,
      }}>
        {/* Row 1: Date presets */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 2, background: C.bg, padding: 3, borderRadius: 7, border: `1px solid ${C.borderDim}` }}>
            {[
              ["today",     "Today"],
              ["yesterday", "Yesterday"],
              ["week",      "This Week"],
              ["month",     "This Month"],
              ["all",       "All Time"],
              ["custom",    "Custom"],
            ].map(([k, label]) => (
              <button key={k} onClick={() => setPreset(k)} style={presetBtn(k)}>{label}</button>
            ))}
          </div>

          {preset === "custom" && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="date" value={customFrom} onChange={e => setCustomFrom(e.target.value)} style={dateInput} />
              <span style={{ color: C.textMuted, fontSize: 12 }}>→</span>
              <input type="date" value={customTo}   onChange={e => setCustomTo(e.target.value)}   style={dateInput} />
            </div>
          )}

          {preset === "today" && (
            <div style={{
              display: "flex", alignItems: "center", gap: 5, fontSize: 10, fontWeight: 700,
              color: C.blue, padding: "3px 10px", borderRadius: 12,
              background: C.blueBg, border: `1px solid rgba(59,130,246,0.3)`,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.blue, animation: "livePulse 2s infinite" }} />
              LIVE · 10s
            </div>
          )}
        </div>

        {/* Row 2: Strategy multi-select */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 10, color: C.textMuted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.6px", flexShrink: 0 }}>
            Strategy:
          </span>
          <StrategyFilter selected={selectedStrategies} onChange={setSelectedStrategies} />
          {selectedStrategies.length > 0 && (
            <button
              onClick={() => setSelectedStrategies([])}
              style={{
                padding: "3px 8px", borderRadius: 5, border: `1px solid ${C.borderDim}`,
                background: "transparent", color: C.textMuted, fontSize: 11, cursor: "pointer",
              }}
            >
              Clear ✕
            </button>
          )}
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────── */}
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 300, color: C.textMuted, gap: 10 }}>
          <div style={{ width: 18, height: 18, border: `2px solid ${C.border}`, borderTopColor: C.blue, borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
          Loading trades…
        </div>
      ) : error ? (
        <div style={{ padding: 20, background: C.bgCard, borderRadius: 8, border: `1px solid ${C.border}` }}>
          <p style={{ color: C.red, margin: 0, fontSize: 13 }}>
            ⚠ Failed to load: {error}
          </p>
          <p style={{ color: C.textMuted, margin: "6px 0 0", fontSize: 12 }}>
            Make sure the <code>/trades/history</code> endpoint supports <code>include_open=true</code> and <code>trade_direction</code> is returned.
          </p>
        </div>
      ) : (
        <>
          {/* ── KPI Grid — only when there are closed trades ─── */}
          {metrics ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 20 }}>
              <KpiCard
                label="Winning Probability"
                value={fmtPct(metrics.winRate)}
                sub={`${metrics.wins}W  /  ${metrics.losses}L  /  ${metrics.totalTrades} total`}
                color={metrics.winRate >= 50 ? C.green : C.red}
              />
              <KpiCard
                label="Win / Loss Ratio"
                value={`${metrics.wins} : ${metrics.losses}`}
                sub={`Profit Factor: ${metrics.profitFactor === Infinity ? "∞" : metrics.profitFactor.toFixed(2)}×`}
                color={C.text}
              />
              <KpiCard
                label="Risk Per Trade"
                value={fmtPct(metrics.riskPerTrade)}
                sub="Avg Loss ÷ Avg Win"
                color={metrics.riskPerTrade <= 60 ? C.green : metrics.riskPerTrade <= 100 ? C.amber : C.red}
              />
              <KpiCard
                label="Max Drawdown"
                value={fmtInr(metrics.maxDrawdown)}
                sub={`${metrics.maxDrawdownDays} days below peak`}
                color={C.red}
              />
              <SplitKpi
                label="Avg Profit  /  Avg Loss"
                left={{ label: "Avg Profit", value: fmtInr(metrics.avgWin),           color: C.green }}
                right={{ label: "Avg Loss",  value: fmtInr(Math.abs(metrics.avgLoss)), color: C.red  }}
              />
              <SplitKpi
                label="Win Streak  /  Loss Streak"
                left={{ label: "Best Win",   value: `${metrics.bestWinStreak} trades`,  color: C.green }}
                right={{ label: "Worst Loss", value: `${metrics.bestLossStreak} trades`, color: C.red  }}
              />
              <SplitKpi
                label="MAX Profit  /  MAX Loss"
                left={{ label: "Best Trade",  value: fmtInr(metrics.maxProfit),          color: C.green }}
                right={{ label: "Worst Trade", value: fmtInr(Math.abs(metrics.maxLoss)), color: C.red  }}
              />
              <KpiCard
                label="Net P&L"
                value={`${metrics.totalPnL >= 0 ? "+" : ""}${fmtInr(metrics.totalPnL)}`}
                sub={`Gross +${fmtInr(metrics.grossProfit)}  /  -${fmtInr(metrics.grossLoss)}`}
                color={metrics.totalPnL >= 0 ? C.green : C.red}
              />
            </div>
          ) : (
            /* No closed trades but may have open ones */
            !openLiveTrades.length && (
              <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: "50px 24px", textAlign: "center", marginBottom: 20 }}>
                <div style={{ fontSize: 36, marginBottom: 12, opacity: 0.4 }}>📊</div>
                <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No closed trades in this period</div>
                <div style={{ fontSize: 13, color: C.textMuted }}>Try a wider date range, or check your strategy filter.</div>
              </div>
            )
          )}

          {/* ── Tabs ──────────────────────────────────────────── */}
          <div style={{
            display: "flex", gap: 4, marginBottom: 14,
            background: C.bgCard, padding: 4, borderRadius: 8,
            border: `1px solid ${C.border}`, width: "fit-content",
          }}>
            {[
              ["trades",    `📋 Trades (${closedTrades.length})${openLiveTrades.length ? ` · ${openLiveTrades.length} open` : ""}`],
              ["overview",  "📈 Equity Curve"],
              ["breakdown", "📊 Breakdown"],
              ["monthly",   "📅 Monthly"],
            ].map(([k, label]) => (
              <button key={k} onClick={() => setActiveTab(k)} style={tabBtn(k)}>{label}</button>
            ))}
          </div>

          {/* ── Equity Curve ──────────────────────────────────── */}
          {activeTab === "overview" && (
            metrics ? (
              <div ref={containerRef} style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>Equity Curve</span>
                  <div style={{ display: "flex", gap: 20, fontSize: 11, color: C.textMuted }}>
                    <span>Start: ₹0</span>
                    <span style={{ color: metrics.totalPnL >= 0 ? C.green : C.red, fontWeight: 700 }}>
                      End: {metrics.totalPnL >= 0 ? "+" : ""}{fmtInr(metrics.totalPnL)}
                    </span>
                    <span>
                      <span style={{ display: "inline-block", width: 10, height: 10, background: "rgba(239,68,68,0.2)", borderRadius: 2, marginRight: 4 }} />
                      Drawdown zones
                    </span>
                  </div>
                </div>
                <EquityCurve data={metrics.equityCurve} width={chartWidth} height={240} />
              </div>
            ) : (
              <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: "60px 0", textAlign: "center", color: C.textMuted, fontSize: 13 }}>
                No closed trades to chart
              </div>
            )
          )}

          {/* ── Breakdown ─────────────────────────────────────── */}
          {activeTab === "breakdown" && (
            metrics ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <BreakdownPanel title="Day of Week" items={metrics.dayBreakdown}   maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
                <BreakdownPanel title="Instruments" items={metrics.instrBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
                <BreakdownPanel title="CE  vs  PE"  items={metrics.sideBreakdown}  maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
              </div>
            ) : (
              <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: "60px 0", textAlign: "center", color: C.textMuted, fontSize: 13 }}>
                No closed trades to analyse
              </div>
            )
          )}

          {/* ── Monthly ───────────────────────────────────────── */}
          {activeTab === "monthly" && (
            <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>Monthly P&L</div>
              {metrics ? <MonthlyGrid data={metrics.monthlyPnL} /> : (
                <div style={{ color: C.textMuted, fontSize: 13, textAlign: "center", padding: "40px 0" }}>No data</div>
              )}
            </div>
          )}

          {/* ── Trades Tab ────────────────────────────────────── */}
          {activeTab === "trades" && (
            <div>
              {/* Open Live Trades section — always shown first */}
              <div style={{ marginBottom: 6 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: C.amber, marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: C.amber, animation: "livePulse 1.5s infinite", display: "inline-block" }} />
                  Open Live Trades
                </div>
                <OpenTradesPanel trades={openLiveTrades} ltpMap={ltpMap} />
              </div>

              {/* Closed Trades section */}
              <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
                <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>Closed Trades · {closedTrades.length}</span>
                  <span style={{ fontSize: 11, color: C.textMuted }}>Click column header to sort</span>
                </div>
                {closedTrades.length > 0 ? (
                  <TradeTable trades={closedTrades} />
                ) : (
                  <div style={{ padding: "40px 24px", textAlign: "center", color: C.textMuted, fontSize: 13 }}>
                    No closed trades in this period
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}

      <style>{`
        @keyframes livePulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes spin       { to{transform:rotate(360deg)} }
      `}</style>
    </div>
  );
}
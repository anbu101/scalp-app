/**
 * Analytics.jsx  —  Comprehensive Trading Analytics
 * Path: src/pages/Analytics.jsx
 *
 * Features:
 *  - Date range: Today / Yesterday / This Week / This Month / All Time / Custom
 *  - Strategy filter: All / BB_V1 / SCALP_V1
 *  - Quantman-style KPI grid (winning probability, streaks, drawdown, risk/reward…)
 *  - Equity curve with drawdown shading
 *  - Breakdown tabs: Day of Week, Instrument, CE vs PE  (HIT/MISS + P&L bars)
 *  - Monthly P&L heat tiles
 *  - Full trade table with CSV export
 *
 * Backend dependency:
 *  GET /trades/history?from_ts=&to_ts=&strategy_id=   → flat array
 *  (see trade_history_routes.py)
 */

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { getApiBase } from "../api/base";
import { EmptyState }  from "../components/LoadingStates";
import { useToast }    from "../components/ToastNotifications";
import { exportToCSV, generateFilename } from "../utils/export";

/* ─────────────────────────────────────────────────────────────
   Design Tokens  (aligned with the rest of the app)
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
};

const MONO = "'JetBrains Mono','Fira Code',monospace";
const FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";

const DAY_NAMES = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

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

function getPresetRange(preset) {
  const now       = new Date();
  const todayMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  switch (preset) {
    case "today":     return { from: todayMidnight, to: null };
    case "yesterday": {
      const y    = new Date(todayMidnight); y.setDate(y.getDate() - 1);
      return { from: y, to: todayMidnight };
    }
    case "week": {
      const dow  = todayMidnight.getDay();
      const mon  = new Date(todayMidnight);
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

  const pnls     = closed.map(t => safeNum(t.pnl_value));
  const winPnls  = pnls.filter(p => p > 0);
  const lossPnls = pnls.filter(p => p < 0);

  const totalPnL     = pnls.reduce((a, b) => a + b, 0);
  const wins         = winPnls.length;
  const losses       = lossPnls.length;
  const winRate      = (wins / closed.length) * 100;
  const avgWin       = wins   ? winPnls.reduce((a, b)  => a + b, 0) / wins   : 0;
  const avgLoss      = losses ? lossPnls.reduce((a, b) => a + b, 0) / losses : 0;
  const grossProfit  = winPnls.reduce((a, b)  => a + b, 0);
  const grossLoss    = Math.abs(lossPnls.reduce((a, b) => a + b, 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;
  const maxProfit    = wins   ? Math.max(...winPnls)  : 0;
  const maxLoss      = losses ? Math.min(...lossPnls) : 0;
  const riskPerTrade = avgWin > 0 ? (Math.abs(avgLoss) / avgWin) * 100 : 0;

  // Streaks
  let curW = 0, curL = 0, bestW = 0, bestL = 0;
  pnls.forEach(p => {
    if (p > 0) { curW++; curL = 0; bestW = Math.max(bestW, curW); }
    else if (p < 0) { curL++; curW = 0; bestL = Math.max(bestL, curL); }
    else            { curW = 0; curL = 0; }
  });
  const currentStreak = curW > 0 ? curW : -curL;

  // Equity curve + drawdown
  let equity = 0, peak = 0, maxDD = 0, ddDayCount = 0;
  let inDD = false, ddStartTs = null, maxDDDays = 0;

  const equityCurve = closed.map((t, i) => {
    equity += safeNum(t.pnl_value);
    const ts = t.entry_time;

    if (equity > peak) {
      if (inDD && ddStartTs && ts) {
        const days = Math.round((ts - ddStartTs) / 86400);
        maxDDDays  = Math.max(maxDDDays, days);
      }
      peak  = equity;
      inDD  = false;
      ddStartTs = null;
    } else {
      const dd = peak - equity;
      if (dd > maxDD) { maxDD = dd; }
      if (!inDD && ts) { inDD = true; ddStartTs = ts; }
    }

    return { i, value: equity, ts, pnl: safeNum(t.pnl_value), symbol: t.tradingsymbol || t.symbol || "" };
  });

  // Breakdown helpers
  function makeBreakdowns(keyFn) {
    const map = {};
    closed.forEach(t => {
      const key = keyFn(t);
      if (!map[key]) map[key] = { name: key, trades: 0, hits: 0, misses: 0, profit: 0, loss: 0 };
      const pnl = safeNum(t.pnl_value);
      map[key].trades++;
      if (pnl > 0) { map[key].hits++;   map[key].profit += pnl; }
      else          { map[key].misses++; map[key].loss   += pnl; }
    });
    return Object.values(map).sort((a, b) => b.trades - a.trades);
  }

  const dayBreakdown  = makeBreakdowns(t => t.entry_time ? DAY_NAMES[new Date(t.entry_time * 1000).getDay()] : "Unknown");
  const instrBreakdown= makeBreakdowns(t => extractInstrument(t.symbol || t.tradingsymbol));
  const sideBreakdown = makeBreakdowns(t => extractSide(t.symbol || t.tradingsymbol, t.slot));

  // Monthly P&L
  const monthMap = {};
  closed.forEach(t => {
    if (!t.entry_time) return;
    const d = new Date(t.entry_time * 1000);
    const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    if (!monthMap[k]) monthMap[k] = { month: k, pnl: 0, trades: 0, wins: 0 };
    monthMap[k].pnl    += safeNum(t.pnl_value);
    monthMap[k].trades++;
    if (safeNum(t.pnl_value) > 0) monthMap[k].wins++;
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

// ── KPI Card ────────────────────────────────────────────────
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

// ── Split KPI Card ───────────────────────────────────────────
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

// ── Equity Curve ─────────────────────────────────────────────
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

  // Drawdown shading — segments where value < running peak
  const ddSegments = [];
  let inDD = false, ddPts = [];
  let runPeak = data[0]?.value || 0;
  data.forEach((d, i) => {
    if (d.value > runPeak) runPeak = d.value;
    const isDown = d.value < runPeak;
    if (isDown && !inDD) { inDD = true; ddPts = [i]; }
    else if (!isDown && inDD) { inDD = false; ddPts.push(i); ddSegments.push([...ddPts]); ddPts = []; }
    else if (inDD) { ddPts.push(i); }
  });
  if (inDD && ddPts.length) ddSegments.push(ddPts);

  const finalPnL  = data[data.length - 1]?.value || 0;
  const lineColor = finalPnL >= 0 ? C.green : C.red;

  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => minVal + (range / (tickCount - 1)) * i);

  // Time labels — max 8
  const step = Math.max(1, Math.floor(data.length / 8));
  const timeLabels = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={lineColor} stopOpacity="0.3" />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* Grid lines + Y labels */}
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={P.left} y1={py(t)} x2={P.left + W} y2={py(t)}
            stroke={C.borderDim} strokeWidth={0.5} />
          <text x={P.left - 6} y={py(t) + 4}
            textAnchor="end" fontSize={9} fill={C.textMuted} fontFamily={MONO}>
            {t < 0 ? "-" : ""}{fmtInr(Math.abs(t))}
          </text>
        </g>
      ))}

      {/* Zero line */}
      {minVal < 0 && maxVal > 0 && (
        <line x1={P.left} y1={y0} x2={P.left + W} y2={y0}
          stroke={C.textMuted} strokeWidth={1} strokeDasharray="4 3" opacity={0.5} />
      )}

      {/* Drawdown shading */}
      {ddSegments.map((seg, si) => {
        if (seg.length < 2) return null;
        const pts = seg.map(i => ({ x: px(i), y: py(data[i].value) }));
        const areaSeg = pts.map((p, j) => `${j === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ")
          + ` L ${pts[pts.length-1].x.toFixed(1)} ${y0.toFixed(1)} L ${pts[0].x.toFixed(1)} ${y0.toFixed(1)} Z`;
        return <path key={si} d={areaSeg} fill={C.red} opacity={0.08} />;
      })}

      {/* Area fill */}
      <path d={areaD} fill="url(#eqGrad)" />

      {/* Line */}
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round" />

      {/* Start / end dots */}
      <circle cx={px(0)}              cy={py(data[0].value)}              r={4} fill={data[0].value >= 0 ? C.green : C.red} />
      <circle cx={px(data.length-1)} cy={py(data[data.length-1].value)} r={4} fill={finalPnL >= 0 ? C.green : C.red} />

      {/* X-axis labels */}
      {timeLabels.map((d) => (
        <text key={d.i} x={px(d.i).toFixed(1)} y={P.top + H + 18}
          textAnchor="middle" fontSize={9} fill={C.textMuted} fontFamily={MONO}>
          {d.ts ? new Date(d.ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : ""}
        </text>
      ))}
    </svg>
  );
}

// ── Breakdown Bar Row (Quantman-style) ───────────────────────
function BreakdownRow({ item, maxTrades, maxPnL }) {
  if (item.trades === 0) return null;

  const hitPct  = maxTrades  ? (item.hits              / maxTrades) * 100 : 0;
  const missPct = maxTrades  ? (item.misses             / maxTrades) * 100 : 0;
  const profPct = maxPnL > 0 ? (item.profit             / maxPnL)   * 100 : 0;
  const lossPct = maxPnL > 0 ? (Math.abs(item.loss)    / maxPnL)   * 100 : 0;
  const wr      = ((item.hits / item.trades) * 100).toFixed(0);

  return (
    <div style={{ marginBottom: 18 }}>
      {/* Row header */}
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

      {/* Hit / Miss bar */}
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: C.bgSurface, marginBottom: 4 }}>
        <div style={{ width: `${hitPct}%`,  background: C.teal,  transition: "width 0.5s" }} />
        <div style={{ width: `${missPct}%`, background: C.amber, transition: "width 0.5s" }} />
      </div>

      {/* P&L amounts */}
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ fontSize: 10, fontFamily: MONO, color: C.green }}>+{fmtInr(item.profit)}</span>
        <span style={{ fontSize: 10, fontFamily: MONO, color: C.red  }}>-{fmtInr(Math.abs(item.loss))}</span>
      </div>

      {/* Profit / Loss bar */}
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: C.bgSurface }}>
        <div style={{ width: `${profPct}%`, background: C.green, transition: "width 0.5s" }} />
        <div style={{ width: `${lossPct}%`, background: C.red,   transition: "width 0.5s" }} />
      </div>
    </div>
  );
}

const LEGEND = [
  { color: C.teal,  label: "HIT"    },
  { color: C.amber, label: "MISS"   },
  { color: C.green, label: "PROFIT" },
  { color: C.red,   label: "LOSS"   },
];

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

// ── Monthly P&L Heat Tiles ───────────────────────────────────
function MonthlyGrid({ data }) {
  if (!data?.length) return null;
  const maxAbs = Math.max(...data.map(d => Math.abs(d.pnl)), 1);

  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      {data.map(m => {
        const isPos  = m.pnl >= 0;
        const inten  = Math.abs(m.pnl) / maxAbs;
        const [yr, mo] = m.month.split("-");
        const moName = new Date(Number(yr), Number(mo) - 1, 1)
          .toLocaleString("en-IN", { month: "short" });
        const wr = m.trades ? ((m.wins / m.trades) * 100).toFixed(0) : 0;

        return (
          <div key={m.month} style={{
            background: isPos
              ? `rgba(16,185,129,${0.12 + inten * 0.55})`
              : `rgba(239,68,68,${0.12 + inten * 0.55})`,
            border:      `1px solid ${isPos ? "rgba(16,185,129,0.35)" : "rgba(239,68,68,0.35)"}`,
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

// ── Trade Table ──────────────────────────────────────────────
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
      if (typeof va === "string") { va = va.toLowerCase(); vb = vb?.toLowerCase() ?? ""; }
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [trades, sortCol, sortDir]);

  const TH_STYLE = (col) => ({
    padding: "9px 10px", fontSize: 10, fontWeight: 600,
    color: sortCol === col ? C.blue : C.textMuted,
    textTransform: "uppercase", letterSpacing: "0.4px",
    textAlign: "left", borderBottom: `1px solid ${C.border}`,
    whiteSpace: "nowrap", cursor: "pointer", userSelect: "none",
  });

  const TD = { padding: "8px 10px", fontSize: 12, fontFamily: MONO };

  const exitReasonColor = (r) => {
    if (!r) return C.textMuted;
    if (r.includes("TP") || r === "SuperTrend" || r === "EOD_SQUARE_OFF") return C.green;
    return C.red;
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead style={{ background: C.bgSurface }}>
          <tr>
            {[
              { col: "tradingsymbol", label: "Symbol"   },
              { col: "strategy_id",   label: "Strategy" },
              { col: "slot",          label: "Side"     },
              { col: "entry_price",   label: "Entry"    },
              { col: "exit_price",    label: "Exit"     },
              { col: "qty",           label: "Qty"      },
              { col: "pnl_value",     label: "P&L"      },
              { col: "exit_reason",   label: "Reason"   },
              { col: "entry_time",    label: "Date"     },
            ].map(({ col, label }) => (
              <th key={col} style={TH_STYLE(col)} onClick={() => toggleSort(col)}>
                {label} {sortCol === col ? (sortDir === "asc" ? "↑" : "↓") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((t, i) => {
            const pnl    = safeNum(t.pnl_value);
            const isWin  = pnl > 0;
            const side   = extractSide(t.symbol || t.tradingsymbol, t.slot);
            return (
              <tr key={t.trade_id || i}
                style={{
                  background:   i % 2 ? C.bgCard : C.bg,
                  borderTop:    `1px solid ${C.borderDim}`,
                  transition:   "background 0.1s",
                }}
                onMouseEnter={e => (e.currentTarget.style.background = C.bgSurface)}
                onMouseLeave={e => (e.currentTarget.style.background = i % 2 ? C.bgCard : C.bg)}
              >
                <td style={{ ...TD, color: C.text, fontWeight: 600 }}>{t.tradingsymbol || t.symbol || "—"}</td>
                <td style={{ ...TD, color: C.textSec }}>{t.strategy_id || "—"}</td>
                <td style={TD}>
                  <span style={{
                    padding: "2px 7px", borderRadius: 3, fontSize: 11, fontWeight: 700,
                    background: side === "CE" ? C.greenBg : side === "PE" ? C.redBg : C.bgSurface,
                    color:      side === "CE" ? C.green   : side === "PE" ? C.red   : C.textMuted,
                  }}>{side}</span>
                </td>
                <td style={{ ...TD, color: C.textSec }}>{t.entry_price?.toFixed(2) ?? "—"}</td>
                <td style={{ ...TD, color: C.textSec }}>{t.exit_price?.toFixed(2)  ?? "—"}</td>
                <td style={{ ...TD, color: C.textSec }}>{t.qty ?? "—"}</td>
                <td style={{
                  ...TD, textAlign: "right", fontWeight: 700,
                  color:      pnl !== 0 ? (isWin ? C.green : C.red) : C.textMuted,
                  background: pnl !== 0 ? (isWin ? "rgba(16,185,129,0.07)" : "rgba(239,68,68,0.07)") : "transparent",
                }}>
                  {pnl !== 0 ? `${isWin ? "+" : ""}${fmtInr(pnl)}` : "—"}
                </td>
                <td style={TD}>
                  {t.exit_reason && (
                    <span style={{
                      padding: "2px 7px", borderRadius: 3, fontSize: 10, fontWeight: 600,
                      background: isWin ? C.greenBg : C.redBg,
                      color: exitReasonColor(t.exit_reason),
                    }}>{t.exit_reason}</span>
                  )}
                </td>
                <td style={{ ...TD, color: C.textMuted }}>
                  {t.entry_time
                    ? new Date(t.entry_time * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" })
                    : "—"}
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
   Main Component
───────────────────────────────────────────────────────────── */
export default function Analytics() {
  const toast = useToast();

  const [trades,      setTrades]      = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
  const [preset,      setPreset]      = useState("today");
  const [customFrom,  setCustomFrom]  = useState("");
  const [customTo,    setCustomTo]    = useState("");
  const [strategy,    setStrategy]    = useState("all");
  const [activeTab,   setActiveTab]   = useState("overview");

  const containerRef = useRef(null);
  const [chartWidth, setChartWidth]  = useState(800);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([e]) =>
      setChartWidth(Math.max(300, e.contentRect.width - 32))
    );
    ro.observe(containerRef.current);
    setChartWidth(Math.max(300, containerRef.current.offsetWidth - 32));
    return () => ro.disconnect();
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

      const p = new URLSearchParams();
      if (fromTs)               p.set("from_ts",     String(fromTs));
      if (toTs)                 p.set("to_ts",        String(toTs));
      if (strategy !== "all")   p.set("strategy_id",  strategy);

      const res  = await fetch(`${getApiBase()}/trades/history?${p}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTrades(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
      setTrades([]);
    } finally {
      setLoading(false);
    }
  }, [preset, customFrom, customTo, strategy]);

  useEffect(() => {
    fetchTrades();
    const iv = setInterval(fetchTrades, preset === "today" ? 10_000 : 60_000);
    return () => clearInterval(iv);
  }, [fetchTrades, preset]);

  const metrics = useMemo(() => computeMetrics(trades), [trades]);

  // ── Breakdown maximums ──────────────────────────────────────
  const { maxBdTrades, maxBdPnL } = useMemo(() => {
    if (!metrics) return { maxBdTrades: 1, maxBdPnL: 1 };
    const allItems = [
      ...metrics.dayBreakdown,
      ...metrics.instrBreakdown,
      ...metrics.sideBreakdown,
    ];
    return {
      maxBdTrades: Math.max(...allItems.map(d => d.trades), 1),
      maxBdPnL:    Math.max(...allItems.map(d => Math.max(d.profit, Math.abs(d.loss))), 1),
    };
  }, [metrics]);

  // ── Styles ─────────────────────────────────────────────────
  const presetBtn = (k) => ({
    padding: "5px 13px", borderRadius: 5, border: "none", cursor: "pointer",
    fontSize: 12, fontWeight: preset === k ? 600 : 400, fontFamily: FONT,
    background:    preset === k ? C.bgSurface : "transparent",
    color:         preset === k ? C.text      : C.textMuted,
    borderBottom:  preset === k ? `2px solid ${C.amber}` : "2px solid transparent",
    transition:    "all 0.15s",
  });

  const stratBtn = (k) => ({
    padding: "5px 13px", borderRadius: 5, border: "none", cursor: "pointer",
    fontSize: 12, fontWeight: strategy === k ? 600 : 400, fontFamily: FONT,
    background:   strategy === k ? C.bgSurface : "transparent",
    color:        strategy === k ? C.text      : C.textMuted,
    borderBottom: strategy === k ? `2px solid ${C.blue}` : "2px solid transparent",
    transition:   "all 0.15s",
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

  return (
    <div style={{ padding: 24, background: C.bg, color: C.text, minHeight: "100vh", fontFamily: FONT }}>

      {/* ── Page Header ────────────────────────────────────────── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700 }}>Performance Analytics</h1>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: C.textMuted }}>
            {metrics
              ? `${metrics.totalTrades} closed · ${metrics.openTrades} open · Net P&L: ${metrics.totalPnL >= 0 ? "+" : ""}${fmtInr(metrics.totalPnL)}`
              : "Select a date range to analyse your performance"}
          </p>
        </div>

        <button
          disabled={!metrics}
          onClick={() => {
            if (!metrics) return;
            const rows = metrics.closedTrades.map(t => ({
              Symbol:   t.tradingsymbol || t.symbol,
              Strategy: t.strategy_id,
              Side:     extractSide(t.symbol || t.tradingsymbol, t.slot),
              Entry:    t.entry_price,
              Exit:     t.exit_price,
              Qty:      t.qty,
              PnL:      t.pnl_value,
              Reason:   t.exit_reason,
              Date:     t.entry_time ? new Date(t.entry_time * 1000).toLocaleDateString("en-IN") : "",
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

      {/* ── Controls ──────────────────────────────────────────── */}
      <div style={{
        background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8,
        padding: "10px 16px", marginBottom: 20,
        display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
      }}>
        {/* Date presets */}
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

        <div style={{ width: 1, height: 24, background: C.borderDim }} />

        {/* Strategy */}
        <div style={{ display: "flex", gap: 2, background: C.bg, padding: 3, borderRadius: 7, border: `1px solid ${C.borderDim}` }}>
          {[["all","All"], ["BB_V1","BB (Live)"], ["SCALP_V1","Scalp (Paper)"]].map(([k, label]) => (
            <button key={k} onClick={() => setStrategy(k)} style={stratBtn(k)}>{label}</button>
          ))}
        </div>

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

      {/* ── Content ───────────────────────────────────────────── */}
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
            Make sure you have deployed the updated <code>trade_history_routes.py</code> with the <code>/trades/history</code> endpoint.
          </p>
        </div>
      ) : !metrics ? (
        <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: "60px 24px", textAlign: "center" }}>
          <div style={{ fontSize: 40, marginBottom: 14, opacity: 0.4 }}>📊</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No closed trades in this period</div>
          <div style={{ fontSize: 13, color: C.textMuted }}>Try a wider date range, or check that your strategy filter is set correctly.</div>
        </div>
      ) : (
        <>
          {/* ── KPI Grid ───────────────────────────────────────── */}
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

          {/* ── Tabs ───────────────────────────────────────────── */}
          <div style={{
            display: "flex", gap: 4, marginBottom: 14,
            background: C.bgCard, padding: 4, borderRadius: 8,
            border: `1px solid ${C.border}`, width: "fit-content",
          }}>
            {[
              ["overview",   "📈 Equity Curve"],
              ["breakdown",  "📊 Breakdown"],
              ["monthly",    "📅 Monthly"],
              ["trades",     `📋 Trades (${metrics.totalTrades})`],
            ].map(([k, label]) => (
              <button key={k} onClick={() => setActiveTab(k)} style={tabBtn(k)}>{label}</button>
            ))}
          </div>

          {/* ── Equity Curve ───────────────────────────────────── */}
          {activeTab === "overview" && (
            <div ref={containerRef} style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Equity Curve</span>
                <div style={{ display: "flex", gap: 20, fontSize: 11, color: C.textMuted }}>
                  <span>Start: ₹0</span>
                  <span style={{ color: metrics.totalPnL >= 0 ? C.green : C.red, fontWeight: 700 }}>
                    End: {metrics.totalPnL >= 0 ? "+" : ""}{fmtInr(metrics.totalPnL)}
                  </span>
                  <span style={{ color: C.redBg, fontSize: 10 }}>
                    <span style={{ display: "inline-block", width: 10, height: 10, background: "rgba(239,68,68,0.15)", borderRadius: 2, marginRight: 4 }} />
                    Drawdown zones
                  </span>
                </div>
              </div>
              <EquityCurve data={metrics.equityCurve} width={chartWidth} height={240} />
            </div>
          )}

          {/* ── Breakdown ──────────────────────────────────────── */}
          {activeTab === "breakdown" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
              <BreakdownPanel
                title="Day of Week"
                items={metrics.dayBreakdown}
                maxTrades={maxBdTrades}
                maxPnL={maxBdPnL}
              />
              <BreakdownPanel
                title="Instruments"
                items={metrics.instrBreakdown}
                maxTrades={maxBdTrades}
                maxPnL={maxBdPnL}
              />
              <BreakdownPanel
                title="CE  vs  PE"
                items={metrics.sideBreakdown}
                maxTrades={maxBdTrades}
                maxPnL={maxBdPnL}
              />
            </div>
          )}

          {/* ── Monthly ────────────────────────────────────────── */}
          {activeTab === "monthly" && (
            <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, padding: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>Monthly P&L</div>
              <MonthlyGrid data={metrics.monthlyPnL} />
            </div>
          )}

          {/* ── Trades Table ───────────────────────────────────── */}
          {activeTab === "trades" && (
            <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Closed Trades · {metrics.closedTrades.length}</span>
                <span style={{ fontSize: 11, color: C.textMuted }}>Click column header to sort</span>
              </div>
              <TradeTable trades={metrics.closedTrades} />
            </div>
          )}
        </>
      )}

      <style>{`
        @keyframes livePulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes spin { to{transform:rotate(360deg)} }
      `}</style>
    </div>
  );
}
// frontend/src/pages/Backtest.jsx
//
// SCALP V1 (short) / V3 / V4 (hedge) backtest UI.
//
// (BB_V1 / BB_V2 + all BANKNIFTY futures/options backfill removed — SCALP only.)
// (Kite "Run backfill (60d)" removed — Dhan NIFTY expired-weeklies backfill kept.)
//
// STATE PERSISTENCE: backend is source of truth. On mount we REHYDRATE run +
// dhan status and the last persisted run. Form params persist to localStorage.
//
// RESULTS now include Analytics-style tabs computed from the backtest's own
// trades: Summary (cards + table) / Equity / Breakdown / Daily / Weekly / Monthly.
//
// CSV is built CLIENT-SIDE from the loaded trades: per-trade rows PLUS
// Daily / Weekly / Monthly P&L blocks, a meaningful filename, and a visible
// download acknowledgement.
//
// SECURITY: backend routes are admin-gated; keep OFF the public Funnel until
// the API auth audit is done.

import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { getApiBase } from "../api/base";
import { colors, spacing, typography, pnlStyle } from "../tokens";
import RunComparison from "./backtest/RunComparison";
import BacktestQueue from "./backtest/BacktestQueue";

const LS_KEY = "scalp_backtest_params_v1";
const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// ── HA_COND_FILTER BEGIN ── canonical HA entry-condition names. Must match the
// strings HAConditionEvaluator emits (HAEntrySignal.condition) exactly.
const HA_ALL_CONDS = ["COND1", "COND2", "COND3"];
// ── HA_COND_FILTER END ──

function loadParams() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function saveParams(p) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch { /* ignore */ }
}

function Card({ children, style, elevated, innerRef }) {
  return (
    <div ref={innerRef} style={{
      background: elevated ? colors.bg.tertiary : colors.bg.secondary,
      border: `1px solid ${colors.border.light}`,
      borderRadius: 8,
      boxShadow: elevated ? "0 4px 6px -1px rgba(0,0,0,0.3)" : "0 1px 3px rgba(0,0,0,0.2)",
      ...style,
    }}>{children}</div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle = {
  padding: "7px 10px", borderRadius: 6,
  border: `1px solid ${colors.border.light}`,
  background: colors.bg.secondary, color: colors.text.primary,
  fontSize: 13, outline: "none", fontFamily: "'Inter', sans-serif",
};

const btn = (variant) => ({
  padding: "9px 18px", borderRadius: 6, border: "none", cursor: "pointer",
  background: variant === "primary" ? colors.primary
    : variant === "danger" ? colors.loss : colors.bg.tertiary,
  color: variant === "primary" || variant === "danger" ? "#fff" : colors.text.primary,
  fontSize: 13, fontWeight: 600,
});

async function apiCall(path, options = {}) {
  const res = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error((await res.text()) || `API ${res.status}`);
  return res.json();
}

function fmtDur(s) {
  if (s == null) return "—";
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}m ${sec}s`;
}

function ProgressBar({ pct, label }) {
  return (
    <div style={{ marginTop: spacing.md }}>
      <div style={{ height: 8, background: colors.bg.secondary, borderRadius: 4, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${Math.min(100, pct || 0).toFixed(1)}%`,
          background: colors.primary, transition: "width 0.4s ease",
        }} />
      </div>
      {label && (
        <div style={{ marginTop: 6, fontSize: 11, color: colors.text.muted, ...typography.mono }}>
          {label}
        </div>
      )}
    </div>
  );
}

/* ─── Backtest trade helpers ───
   trade shape: entry_ts, exit_ts, tradingsymbol, entry_price, exit_price,
                sl, tp, exit_reason, pnl (gross), charges, net_pnl, ambiguous_fill */
const safeNum = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
const netOf = (t) => (t.net_pnl != null ? safeNum(t.net_pnl) : safeNum(t.pnl) - safeNum(t.charges));

export function fmtInr(v) {
  if (v == null) return "—";
  const abs = Math.abs(Math.round(v));
  return `₹${abs.toLocaleString("en-IN")}`;
}
function extractSide(symbol) {
  if (!symbol) return "OTHER";
  if (symbol.endsWith("CE")) return "CE";
  if (symbol.endsWith("PE")) return "PE";
  return "OTHER";
}
function extractInstrument(symbol) {
  if (!symbol) return "OTHER";
  if (symbol.includes("BANKNIFTY")) return "BANKNIFTY";
  if (symbol.includes("NIFTY")) return "NIFTY";
  return "OTHER";
}

// ISO week key: YYYY-Www (Monday-based)
function isoWeekKey(d) {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = (date.getUTCDay() + 6) % 7;            // Mon=0..Sun=6
  date.setUTCDate(date.getUTCDate() - dayNum + 3);      // nearest Thursday
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4));
  const week = 1 + Math.round(
    ((date - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7
  );
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/* Period aggregation: [{key,label,pnl,trades,wins}] ascending. */
function aggregateByPeriod(trades, period) {
  const map = {};
  for (const t of trades) {
    const ts = t.entry_ts;
    if (!ts) continue;
    const d = new Date(ts * 1000);
    let key, label;
    if (period === "daily") {
      key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      label = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
    } else if (period === "weekly") {
      key = isoWeekKey(d);
      label = key;
    } else if (period === "yearly") {
      // ── YEARLY BEGIN ── calendar-year bucket for multi-year runs
      key = String(d.getFullYear());
      label = key;
      // ── YEARLY END ──
    } else {
      key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      const [yr, mo] = key.split("-");
      label = `${new Date(Number(yr), Number(mo) - 1, 1).toLocaleString("en-IN", { month: "short" })} ${yr}`;
    }
    if (!map[key]) map[key] = { key, label, pnl: 0, trades: 0, wins: 0 };
    const n = netOf(t);
    map[key].pnl += n;
    map[key].trades++;
    if (n > 0) map[key].wins++;
  }
  return Object.values(map).sort((a, b) => a.key.localeCompare(b.key));
}

/* Backtest metrics (adapted from Analytics, using net_pnl). */
export function computeMetrics(trades) {
  const closed = trades.filter((t) => t.exit_price != null);
  if (!closed.length) return null;

  const pnls = closed.map(netOf);
  const winPnls = pnls.filter((p) => p > 0);
  const lossPnls = pnls.filter((p) => p < 0);

  const totalPnL = pnls.reduce((a, b) => a + b, 0);
  const wins = winPnls.length;
  const losses = lossPnls.length;
  const winRate = (wins / closed.length) * 100;

  let curW = 0, curL = 0, bestW = 0, bestL = 0;
  pnls.forEach((p) => {
    if (p > 0) { curW++; curL = 0; bestW = Math.max(bestW, curW); }
    else if (p < 0) { curL++; curW = 0; bestL = Math.max(bestL, curL); }
    else { curW = 0; curL = 0; }
  });

  const byTime = [...closed].sort((a, b) => (a.entry_ts || 0) - (b.entry_ts || 0));
  let equity = 0, peak = 0, maxDD = 0;
  const equityCurve = byTime.map((t) => {
    equity += netOf(t);
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDD) maxDD = dd;
    return { value: equity, ts: t.entry_ts, symbol: t.tradingsymbol || "" };
  });

  function makeBreakdowns(keyFn) {
    const map = {};
    closed.forEach((t) => {
      const key = keyFn(t);
      if (!map[key]) map[key] = { name: key, trades: 0, hits: 0, misses: 0, profit: 0, loss: 0 };
      const p = netOf(t);
      map[key].trades++;
      if (p > 0) { map[key].hits++; map[key].profit += p; }
      else { map[key].misses++; map[key].loss += p; }
    });
    return Object.values(map).sort((a, b) => b.trades - a.trades);
  }

  // ── Extended KPIs ──────────────────────────────────────────────
  const grossProfitX = winPnls.reduce((a, b) => a + b, 0);
  const grossLossX = Math.abs(lossPnls.reduce((a, b) => a + b, 0));
  const profitFactor = grossLossX > 0 ? grossProfitX / grossLossX : (grossProfitX > 0 ? Infinity : 0);
  const expectancy = totalPnL / closed.length;                 // avg net per trade
  const avgWinX = wins ? grossProfitX / wins : 0;
  const avgLossX = losses ? lossPnls.reduce((a, b) => a + b, 0) / losses : 0; // negative
  const winLossRatio = avgLossX !== 0 ? Math.abs(avgWinX / avgLossX) : (avgWinX > 0 ? Infinity : 0);
  const largestWin = wins ? Math.max(...winPnls) : 0;
  const largestLoss = losses ? Math.min(...lossPnls) : 0;
  const returnToDD = maxDD > 0 ? totalPnL / maxDD : (totalPnL > 0 ? Infinity : 0);

  // Holding-time stats (need entry_ts & exit_ts; seconds)
  const holds = byTime.filter((t) => t.entry_ts && t.exit_ts)
    .map((t) => ({ s: t.exit_ts - t.entry_ts, net: netOf(t) }));
  const _med = (arr) => {
    if (!arr.length) return 0;
    const ss = [...arr].sort((a, b) => a - b);
    const m = Math.floor(ss.length / 2);
    return ss.length % 2 ? ss[m] : (ss[m - 1] + ss[m]) / 2;
  };
  const avgHold = holds.length ? holds.reduce((a, b) => a + b.s, 0) / holds.length : 0;
  const medHold = _med(holds.map((h) => h.s));
  const avgHoldWin = (() => { const w = holds.filter((h) => h.net > 0); return w.length ? w.reduce((a, b) => a + b.s, 0) / w.length : 0; })();
  const avgHoldLoss = (() => { const l = holds.filter((h) => h.net < 0); return l.length ? l.reduce((a, b) => a + b.s, 0) / l.length : 0; })();

  // Exit-reason breakdown
  const reasonMap = {};
  closed.forEach((t) => {
    const k = t.exit_reason || "—";
    if (!reasonMap[k]) reasonMap[k] = { reason: k, trades: 0, wins: 0, pnl: 0 };
    const n = netOf(t);
    reasonMap[k].trades++; if (n > 0) reasonMap[k].wins++; reasonMap[k].pnl += n;
  });
  const exitReasons = Object.values(reasonMap).sort((a, b) => b.trades - a.trades);

  // ── HA_COND_FILTER BEGIN ── entry-condition breakdown (HA_V1 / HA_SELL).
  // Every HA trade carries `condition` (COND1/COND2/COND3) from the runner;
  // non-HA strategies have no `condition` field, so this map stays empty and
  // the tab hides itself. Same shape as exitReasons so the table renders alike.
  const condMap = {};
  closed.forEach((t) => {
    if (!t.condition) return;
    const k = t.condition;
    if (!condMap[k]) condMap[k] = { reason: k, trades: 0, wins: 0, pnl: 0 };
    const n = netOf(t);
    condMap[k].trades++; if (n > 0) condMap[k].wins++; condMap[k].pnl += n;
  });
  const entryConditions = Object.values(condMap).sort((a, b) => b.trades - a.trades);
  // ── HA_COND_FILTER END ──

  return {
    totalTrades: closed.length, wins, losses, winRate, totalPnL,
    bestWinStreak: bestW, bestLossStreak: bestL, maxDrawdown: maxDD,
    equityCurve,
    profitFactor, expectancy, winLossRatio, avgWinX, avgLossX,
    largestWin, largestLoss, returnToDD,
    avgHold, medHold, avgHoldWin, avgHoldLoss, exitReasons,
    entryConditions,
    dayBreakdown: makeBreakdowns((t) => t.entry_ts ? DAY_NAMES[new Date(t.entry_ts * 1000).getDay()] : "Unknown"),
    instrBreakdown: makeBreakdowns((t) => extractInstrument(t.tradingsymbol)),
    sideBreakdown: makeBreakdowns((t) => extractSide(t.tradingsymbol)),
    daily: aggregateByPeriod(closed, "daily"),
    weekly: aggregateByPeriod(closed, "weekly"),
    monthly: aggregateByPeriod(closed, "monthly"),
    yearly: aggregateByPeriod(closed, "yearly"),   // ── YEARLY ──
  };
}

/* ── RUN_PARAMS_DISPLAY BEGIN ── flatten a run's config into [label, value]
   pairs for the results header. Union of all strategy config shapes (V1/V3/
   V4/V5/HA_V1/HA_SELL/WICK_V1); only SET params render (0 = disabled = hidden,
   matching runner semantics), so each strategy shows exactly its own knobs. */
export function describeConfig(cfg) {
  if (!cfg) return [];
  const out = [];
  const add = (label, v) => { if (v !== undefined && v !== null && v !== "") out.push([label, String(v)]); };
  if (cfg.option_premium) add("Premium", `${cfg.option_premium.min}–${cfg.option_premium.max}`);
  if (cfg.timeframe_minutes) add("Timeframe", `${cfg.timeframe_minutes}m`);
  if (cfg.top_wick_min) add("Top wick min", cfg.top_wick_min);
  if (cfg.risk_reward_ratio != null) add("R:R", cfg.risk_reward_ratio);
  if (cfg.sl_points) add("SL pts", cfg.sl_points);
  if (cfg.tp_points) add("TP pts", cfg.tp_points);
  if (cfg.min_sl_points) add("Min SL", cfg.min_sl_points);
  if (cfg.max_sl_points) add("Max SL cap", cfg.max_sl_points);
  if (cfg.risk_max_sl_points) add("Risk Max SL", cfg.risk_max_sl_points);
  if (cfg.hedge_sl_points) add("Hedge SL", cfg.hedge_sl_points);
  if (cfg.target_override?.enabled) add("Fixed target", `${cfg.target_override.points} pts`);
  if (cfg.entry_conditions?.length) add("Conditions", cfg.entry_conditions.map((c) => String(c).replace("COND", "C")).join("+"));
  if (cfg.max_trades_per_side) add("Max trades/side", cfg.max_trades_per_side);
  if (cfg.tp_hold_extra_candles) add("TP hold", `${cfg.tp_hold_extra_candles} candles`);
  if (cfg.trade_side_mode) add("Side", cfg.trade_side_mode);
  if (cfg.dual_side_mode) add("Concurrency", "1 CE + 1 PE");
  if (cfg.max_loss) add("Max Loss", `₹${cfg.max_loss}`);
  if (cfg.max_profit) add("Max Profit", `₹${cfg.max_profit}`);
  if (cfg.session?.primary) add("Session", `${cfg.session.primary.start}–${cfg.session.primary.end}`);
  if (cfg.quantity?.lots != null) add("Lots", cfg.quantity.lots);
  return out;
}
/* ── RUN_PARAMS_DISPLAY END ── */

/* ── Equity curve SVG ── */
export function EquityCurve({ data, width, height = 240 }) {
  if (!data || data.length < 2) return null;
  const P = { top: 20, right: 16, bottom: 32, left: 76 };
  const W = width - P.left - P.right;
  const H = height - P.top - P.bottom;
  const vals = data.map((d) => d.value);
  const minVal = Math.min(...vals, 0);
  const maxVal = Math.max(...vals, 0);
  const range = (maxVal - minVal) || 1;
  const px = (i) => P.left + (i / (data.length - 1)) * W;
  const py = (val) => P.top + H - ((val - minVal) / range) * H;
  const y0 = py(0);
  const pathD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${px(i).toFixed(1)} ${py(d.value).toFixed(1)}`).join(" ");
  const areaD = `${pathD} L ${px(data.length - 1).toFixed(1)} ${y0.toFixed(1)} L ${P.left} ${y0.toFixed(1)} Z`;
  const finalPnL = data[data.length - 1]?.value || 0;
  // Zero-aware coloring: color reflects each point's sign vs ZERO, not the
  // endpoint. The gradient flips green→red exactly at the y-pixel of zero, so
  // anything above the zero line is green and below is red (fixes "green while
  // underwater").
  const zeroFrac = Math.max(0, Math.min(1, ((maxVal - minVal) || 1 ? (maxVal - 0) / ((maxVal - minVal) || 1) : 0)));
  const lineColor = finalPnL >= 0 ? colors.profit : colors.loss; // endpoint dot only
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => minVal + (range / (tickCount - 1)) * i);
  const step = Math.max(1, Math.floor(data.length / 8));

  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      <defs>
        {/* Fill tint: green above zero, red below — split at the zero line. */}
        <linearGradient id="bteqFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={colors.profit} stopOpacity="0.28" />
          <stop offset={`${(zeroFrac * 100).toFixed(2)}%`} stopColor={colors.profit} stopOpacity="0.05" />
          <stop offset={`${(zeroFrac * 100).toFixed(2)}%`} stopColor={colors.loss} stopOpacity="0.05" />
          <stop offset="100%" stopColor={colors.loss} stopOpacity="0.28" />
        </linearGradient>
        {/* Stroke: green above zero, red below — hard flip at zero. */}
        <linearGradient id="bteqStroke" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={colors.profit} stopOpacity="1" />
          <stop offset={`${(zeroFrac * 100).toFixed(2)}%`} stopColor={colors.profit} stopOpacity="1" />
          <stop offset={`${(zeroFrac * 100).toFixed(2)}%`} stopColor={colors.loss} stopOpacity="1" />
          <stop offset="100%" stopColor={colors.loss} stopOpacity="1" />
        </linearGradient>
      </defs>
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={P.left} y1={py(t)} x2={P.left + W} y2={py(t)} stroke={colors.border.dark} strokeWidth={0.5} />
          <text x={P.left - 6} y={py(t) + 4} textAnchor="end" fontSize={9} fill={colors.text.muted} fontFamily="monospace">
            {t < 0 ? "-" : ""}{fmtInr(Math.abs(t))}
          </text>
        </g>
      ))}
      {minVal < 0 && maxVal > 0 && (
        <line x1={P.left} y1={y0} x2={P.left + W} y2={y0} stroke={colors.text.muted} strokeWidth={1} strokeDasharray="4 3" opacity={0.5} />
      )}
      <path d={areaD} fill="url(#bteqFill)" />
      <path d={pathD} fill="none" stroke="url(#bteqStroke)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={px(0)} cy={py(data[0].value)} r={4} fill={data[0].value >= 0 ? colors.profit : colors.loss} />
      <circle cx={px(data.length - 1)} cy={py(finalPnL)} r={4} fill={finalPnL >= 0 ? colors.profit : colors.loss} />
      {data.filter((_, i) => i % step === 0 || i === data.length - 1).map((d, idx) => (
        <text key={idx} x={px(Math.min(idx * step, data.length - 1)).toFixed(1)} y={P.top + H + 18}
          textAnchor="middle" fontSize={9} fill={colors.text.muted} fontFamily="monospace">
          {d.ts ? new Date(d.ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : ""}
        </text>
      ))}
    </svg>
  );
}

/* ── Breakdown bars ── */
function BreakdownRow({ item, maxTrades, maxPnL }) {
  if (item.trades === 0) return null;
  const hitPct = maxTrades ? (item.hits / maxTrades) * 100 : 0;
  const missPct = maxTrades ? (item.misses / maxTrades) * 100 : 0;
  const profPct = maxPnL > 0 ? (item.profit / maxPnL) * 100 : 0;
  const lossPct = maxPnL > 0 ? (Math.abs(item.loss) / maxPnL) * 100 : 0;
  const wr = ((item.hits / item.trades) * 100).toFixed(0);
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: colors.text.primary }}>{item.name}</span>
          <span style={{ fontSize: 10, color: colors.text.muted }}>{item.trades} trades</span>
          <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
            background: Number(wr) >= 50 ? colors.successBg : colors.lossBg,
            color: Number(wr) >= 50 ? colors.success : colors.loss }}>{wr}% WR</span>
        </div>
      </div>
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: colors.bg.secondary, marginBottom: 4 }}>
        <div style={{ width: `${hitPct}%`, background: colors.success }} />
        <div style={{ width: `${missPct}%`, background: colors.warning }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ fontSize: 10, ...typography.mono, color: colors.profit }}>+{fmtInr(item.profit)}</span>
        <span style={{ fontSize: 10, ...typography.mono, color: colors.loss }}>-{fmtInr(Math.abs(item.loss))}</span>
      </div>
      <div style={{ display: "flex", height: 7, borderRadius: 4, overflow: "hidden", background: colors.bg.secondary }}>
        <div style={{ width: `${profPct}%`, background: colors.profit }} />
        <div style={{ width: `${lossPct}%`, background: colors.loss }} />
      </div>
    </div>
  );
}

function BreakdownPanel({ title, items, maxTrades, maxPnL }) {
  return (
    <Card elevated style={{ padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: colors.text.primary, marginBottom: 12 }}>{title}</div>
      {items.map((it) => <BreakdownRow key={it.name} item={it} maxTrades={maxTrades} maxPnL={maxPnL} />)}
      {items.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted, textAlign: "center", padding: "20px 0" }}>No data</div>}
    </Card>
  );
}

/* ── Period grid (Daily / Weekly / Monthly) ── */
function PeriodGrid({ data }) {
  if (!data?.length) return <div style={{ color: colors.text.muted, fontSize: 13, textAlign: "center", padding: "40px 0" }}>No data</div>;
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1);
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      {data.map((m) => {
        const isPos = m.pnl >= 0;
        const inten = Math.abs(m.pnl) / maxAbs;
        const wr = m.trades ? ((m.wins / m.trades) * 100).toFixed(0) : 0;
        return (
          <div key={m.key} style={{
            background: isPos ? `rgba(16,185,129,${0.12 + inten * 0.55})` : `rgba(239,68,68,${0.12 + inten * 0.55})`,
            border: `1px solid ${isPos ? "rgba(16,185,129,0.35)" : "rgba(239,68,68,0.35)"}`,
            borderRadius: 8, padding: "10px 14px", minWidth: 110, textAlign: "center",
          }}>
            <div style={{ fontSize: 10, color: colors.text.muted, marginBottom: 4 }}>{m.label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, ...typography.mono, color: isPos ? colors.profit : colors.loss }}>
              {isPos ? "+" : ""}{fmtInr(m.pnl)}
            </div>
            <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 4 }}>{m.trades} trades · {wr}% WR</div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Small KPI tile with good/bad coloring ── */
function KpiTile({ label, value, sub, good, bad }) {
  const color = good ? colors.profit : bad ? colors.loss : colors.text.primary;
  return (
    <Card elevated style={{ padding: spacing.lg }}>
      <div style={{ ...typography.label, color: colors.text.muted }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, color }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 3 }}>{sub}</div>}
    </Card>
  );
}

/* ── Inline mini stat (no card) ── */
function MiniStat({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: colors.text.muted, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, ...typography.mono, color: color || colors.text.primary }}>{value}</div>
    </div>
  );
}

/* ── Hourly net-P&L bars (red below zero, green above) ── */
function HourBars({ data }) {
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {data.map((d) => {
        const pos = d.pnl >= 0;
        const w = (Math.abs(d.pnl) / maxAbs) * 50;
        const wr = d.trades ? ((d.wins / d.trades) * 100).toFixed(0) : 0;
        return (
          <div key={d.hour} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ width: 48, fontSize: 11, ...typography.mono, color: colors.text.muted }}>{d.hour}</span>
            <div style={{ flex: 1, position: "relative", height: 22, background: colors.bg.secondary, borderRadius: 4 }}>
              <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: colors.border.light }} />
              <div style={{ position: "absolute", top: 3, bottom: 3, borderRadius: 3,
                ...(pos ? { left: "50%", width: `${w}%`, background: colors.profit } : { right: "50%", width: `${w}%`, background: colors.loss }) }} />
            </div>
            <span style={{ width: 90, textAlign: "right", fontSize: 12, ...typography.mono, ...pnlStyle(d.pnl) }}>
              {d.pnl >= 0 ? "+" : ""}{fmtInr(d.pnl)}
            </span>
            <span style={{ width: 80, textAlign: "right", fontSize: 10, color: colors.text.tertiary }}>{d.trades}t · {wr}%</span>
          </div>
        );
      })}
    </div>
  );
}

/* ── "What these mean" explainer ── */
function MetricsExplainer() {
  const rows = [
    ["Profit Factor", "Gross profit ÷ gross loss. >1 means winners outweigh losers; ≥1.5 is solid, <1 loses money."],
    ["Expectancy / trade", "Average net P&L per trade (net P&L ÷ trades). Positive = a real edge per trade after costs."],
    ["Return ÷ Max DD", "Net P&L ÷ max drawdown. How much you earned per rupee of worst-case pain; higher is safer. ≥2 is healthy."],
    ["Win / Loss size", "Avg win ÷ avg loss (absolute). <1 means a high win rate is needed to stay profitable."],
    ["Max win / loss streak", "Longest run of consecutive winners / losers. Long loss streaks dictate position sizing & psychology."],
    ["Largest win / loss", "Best and worst single trade by net P&L — your realized tail risk."],
    ["Holding time", "How long trades stay open. Winners vs losers shows if you let winners run and cut losers (healthy) or the reverse."],
    ["Max Drawdown", "Largest peak-to-trough drop of the running net-P&L equity curve."],
    ["Exit Reasons", "Net P&L grouped by how each trade closed (EMA_EXIT / SL / TP / EOD). Reveals which exit helps or hurts."],
    ["Entry Conditions", "HA only: net P&L grouped by the entry condition (COND1/COND2/COND3) that fired the trade. Pair with the run-parameter chips to isolate one condition."],
    ["Time of Day", "Filter all stats by entry time (IST). Use it to confirm the window where the edge actually lives."],
  ];
  return (
    <Card elevated style={{ padding: spacing.lg }}>
      <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>What these mean</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "flex", gap: 12, fontSize: 12 }}>
            <span style={{ minWidth: 150, fontWeight: 700, color: colors.text.secondary }}>{k}</span>
            <span style={{ color: colors.text.muted, lineHeight: 1.5 }}>{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ── CSV builder (client-side) ── */
function csvEscape(v) {
  if (v == null) return "";
  const sv = String(v);
  return /[",\n]/.test(sv) ? `"${sv.replace(/"/g, '""')}"` : sv;
}
function buildCsv(trades, summary, metrics, strategyId) {
  const lines = [];
  lines.push(`Scalp Terminal Backtest Export,${csvEscape(strategyId)}`);
  lines.push("");

  if (summary) {
    lines.push("SUMMARY");
    lines.push("Metric,Value");
    lines.push(`Total trades,${summary.total_trades ?? ""}`);
    lines.push(`Wins,${summary.wins ?? ""}`);
    lines.push(`Losses,${summary.losses ?? ""}`);
    lines.push(`Win rate %,${summary.win_rate != null ? summary.win_rate.toFixed(2) : ""}`);
    lines.push(`Gross P&L,${summary.gross_pnl != null ? Math.round(summary.gross_pnl) : ""}`);
    lines.push(`Total charges,${summary.total_charges != null ? Math.round(summary.total_charges) : ""}`);
    lines.push(`Net P&L,${summary.net_pnl != null ? Math.round(summary.net_pnl) : ""}`);
    lines.push(`Max drawdown,${summary.max_drawdown != null ? Math.round(summary.max_drawdown) : ""}`);
    lines.push("");
  }

  lines.push("TRADES");
  // ── HA_COND_FILTER: Condition column added (empty for non-HA strategies). ──
  lines.push(["Symbol", "Condition", "Entry Time", "Entry", "SL", "TP", "Exit Time", "Exit", "Reason", "Gross", "Charges", "Net", "Ambiguous"].join(","));
  const sorted = [...trades].sort((a, b) => (a.entry_ts || 0) - (b.entry_ts || 0));
  for (const t of sorted) {
    lines.push([
      csvEscape(t.tradingsymbol),
      csvEscape(t.condition || ""),
      csvEscape(fmtTs(t.entry_ts)),
      t.entry_price != null ? t.entry_price.toFixed(2) : "",
      t.sl != null ? t.sl.toFixed(2) : "",
      t.tp != null ? t.tp.toFixed(2) : "",
      csvEscape(fmtTs(t.exit_ts)),
      t.exit_price != null ? t.exit_price.toFixed(2) : "",
      csvEscape(t.exit_reason),
      t.pnl != null ? Math.round(t.pnl) : "",
      t.charges != null ? Math.round(t.charges) : "",
      Math.round(netOf(t)),
      t.ambiguous_fill ? "YES" : "",
    ].join(","));
  }
  lines.push("");

  // ── HA_COND_FILTER BEGIN ── per-condition P&L block (HA runs only).
  if (metrics?.entryConditions?.length) {
    lines.push("ENTRY CONDITION P&L");
    lines.push("Condition,Net P&L,Trades,Wins,Win rate %");
    for (const r of metrics.entryConditions) {
      const wr = r.trades ? ((r.wins / r.trades) * 100).toFixed(0) : "0";
      lines.push([csvEscape(r.reason), Math.round(r.pnl), r.trades, r.wins, wr].join(","));
    }
    lines.push("");
  }
  // ── HA_COND_FILTER END ──

  const blocks = [["DAILY P&L", metrics?.daily], ["WEEKLY P&L", metrics?.weekly], ["MONTHLY P&L", metrics?.monthly], ["YEARLY P&L", metrics?.yearly]];   // ── YEARLY ──
  for (const [title, rows] of blocks) {
    if (!rows || !rows.length) continue;
    lines.push(title);
    lines.push("Period,Net P&L,Trades,Wins,Win rate %");
    for (const r of rows) {
      const wr = r.trades ? ((r.wins / r.trades) * 100).toFixed(0) : "0";
      lines.push([csvEscape(r.label), Math.round(r.pnl), r.trades, r.wins, wr].join(","));
    }
    lines.push("");
  }
  return lines.join("\n");
}

export default function Backtest() {
  const saved = loadParams() || {};

  // ── Strategy (SCALP only) ──
  const [strategyId, setStrategyId] = useState(
     ["SCALP_V1", "SCALP_V3", "SCALP_V4", "SCALP_V5", "HA_V1", "HA_SELL", "WICK_V1"].includes(saved.strategyId) ? saved.strategyId : "SCALP_V1"
  );
  const isHedge = strategyId === "SCALP_V3" || strategyId === "SCALP_V4";
  const isV5 = strategyId === "SCALP_V5";
  const isHA = strategyId === "HA_V1" || strategyId === "HA_SELL";
  const isWick = strategyId === "WICK_V1";
  const [wickTf, setWickTf] = useState(saved.wickTf ?? 3);
  const [wickTopWick, setWickTopWick] = useState(saved.wickTopWick ?? 1.5);
  const [wickSlPoints, setWickSlPoints] = useState(saved.wickSlPoints ?? 10);
  const [wickTpPoints, setWickTpPoints] = useState(saved.wickTpPoints ?? 16);
  const [wickDualSide, setWickDualSide] = useState(saved.wickDualSide ?? false);
  // "run" = the existing run+config+results view; "compare" = the analytics tool
  const [pageView, setPageView] = useState("run");

  // ── Dhan backfill (NIFTY expired weeklies) ──
  const [dhanRunning, setDhanRunning] = useState(false);
  const [dhanStatus, setDhanStatus] = useState(null);
  const [dhanError, setDhanError] = useState(null);
  const [dhanCancelling, setDhanCancelling] = useState(false);
  const [dhanFrom, setDhanFrom] = useState(saved.dhanFrom || "");
  const [dhanTo, setDhanTo] = useState(saved.dhanTo || "");
  const dhanPoll = useRef(null);

  // ── Coverage ──
  const [coverage, setCoverage] = useState(null);

  // ── Form ──
  const [dateFrom, setDateFrom] = useState(saved.dateFrom || "");
  const [dateTo, setDateTo] = useState(saved.dateTo || "");
  const [premiumMin, setPremiumMin] = useState(saved.premiumMin ?? 150);
  const [premiumMax, setPremiumMax] = useState(saved.premiumMax ?? 200);
  const [rr, setRr] = useState(saved.rr ?? 1.0);
  const [minSl, setMinSl] = useState(saved.minSl ?? 5);
  const [maxSl, setMaxSl] = useState(saved.maxSl ?? 0);
  const [riskMaxSl, setRiskMaxSl] = useState(saved.riskMaxSl ?? 0);
  const [hedgeSl, setHedgeSl] = useState(saved.hedgeSl ?? 20);
  const [sessStart, setSessStart] = useState(saved.sessStart || "09:30");
  const [sessEnd, setSessEnd] = useState(saved.sessEnd || "15:20");
  const [lots, setLots] = useState(saved.lots ?? 10);

  // ── V5-specific (option-buying: absolute SL/TP points + session MTM caps + side) ──
  const [slPoints, setSlPoints] = useState(saved.slPoints ?? 0);
  const [tpPoints, setTpPoints] = useState(saved.tpPoints ?? 0);
  const [maxLoss, setMaxLoss] = useState(saved.maxLoss ?? 0);
  const [maxProfit, setMaxProfit] = useState(saved.maxProfit ?? 0);
  const [sideMode, setSideMode] = useState(saved.sideMode || "BOTH");

  // ── HA_V1-specific (Heikin Ashi option-buying: R:R + fixed-target override +
  //    per-side daily cap). HA's SL is the signal's red-candle low (not a
  //    user-set point value), so there is no SL field — only the target shape. ──
  const [haTargetOverride, setHaTargetOverride] = useState(saved.haTargetOverride ?? false);
  const [haTargetPoints, setHaTargetPoints] = useState(saved.haTargetPoints ?? 0);
  const [haMaxTradesPerSide, setHaMaxTradesPerSide] = useState(saved.haMaxTradesPerSide ?? 10);
  const [tpHoldExtra, setTpHoldExtra] = useState(saved.tpHoldExtra ?? 0);

  // ── HA_COND_FILTER BEGIN ── entry-condition multi-select (HA_V1 + HA_SELL).
  // Subset of COND1/COND2/COND3. The toggle NEVER lets the set go empty (an
  // empty selection is ambiguous — the backend treats it as ALL for
  // back-compat, so the UI never sends one). Invalid persisted values fall
  // back to the full set.
  const [haConds, setHaConds] = useState(() => {
    const s = Array.isArray(saved.haConds)
      ? saved.haConds.filter((c) => HA_ALL_CONDS.includes(c)) : [];
    return s.length ? s : [...HA_ALL_CONDS];
  });
  const toggleHaCond = useCallback((c) => {
    setHaConds((prev) => prev.includes(c)
      ? (prev.length > 1 ? prev.filter((x) => x !== c) : prev)   // never empty
      : [...HA_ALL_CONDS.filter((x) => prev.includes(x) || x === c)]); // keep canonical order
  }, []);
  // ── HA_COND_FILTER END ──

  // ── Run ──
  const [runRunning, setRunRunning] = useState(false);
  const [runStatus, setRunStatus] = useState(null);
  const [runError, setRunError] = useState(null);
  const [runCancelling, setRunCancelling] = useState(false);
  const [runId, setRunId] = useState(null);
  const [summary, setSummary] = useState(null);
  const [trades, setTrades] = useState([]);
  const [resultStrategy, setResultStrategy] = useState(strategyId);
  // ── RUN_PARAMS_DISPLAY ── config + period of the LOADED run, shown in the
  // results header so the numbers on screen are never divorced from the exact
  // parameters that produced them (the form above may have changed since).
  const [resultConfig, setResultConfig] = useState(null);
  const [resultMeta, setResultMeta] = useState(null);   // {date_from, date_to} when known
  const runPoll = useRef(null);

  // ── Results tab + CSV status ──
  const [resultTab, setResultTab] = useState("summary");
  // ── TABLE_CAP: cap RENDERED rows so a multi-year run (16k+ trades) doesn't
  //    freeze the UI. Analytics + CSV still use the FULL trade set — only the
  //    visible <table> is capped. "Show all" lets the user override on demand. ──
  const TABLE_CAP = 500;
  const [showAllRows, setShowAllRows] = useState(false);
  // ── Time-of-Day filter (interactive; filters by ENTRY ist-time) ──
  const [todStart, setTodStart] = useState("09:15");
  const [todEnd, setTodEnd] = useState("15:30");
  const [csvMsg, setCsvMsg] = useState(null);
  const containerRef = useRef(null);
  const [chartWidth, setChartWidth] = useState(800);

  useEffect(() => {
    if (resultTab !== "equity" || !containerRef.current) return;
    const ro = new ResizeObserver(([e]) => setChartWidth(Math.max(300, e.contentRect.width - 32)));
    ro.observe(containerRef.current);
    setChartWidth(Math.max(300, containerRef.current.offsetWidth - 32));
    return () => ro.disconnect();
  }, [resultTab]);

  useEffect(() => {
    saveParams({ strategyId, dateFrom, dateTo, premiumMin, premiumMax, rr,
      minSl, maxSl, riskMaxSl, hedgeSl, sessStart, sessEnd, lots, dhanFrom, dhanTo,
      slPoints, tpPoints, maxLoss, maxProfit, sideMode,
      haTargetOverride, haTargetPoints, haMaxTradesPerSide, tpHoldExtra,
      haConds,
      wickTf, wickTopWick, wickSlPoints, wickTpPoints, wickDualSide });
  }, [strategyId, dateFrom, dateTo, premiumMin, premiumMax, rr, minSl, maxSl,
      riskMaxSl, hedgeSl, sessStart, sessEnd, lots, dhanFrom, dhanTo,
      slPoints, tpPoints, maxLoss, maxProfit, sideMode,
      haTargetOverride, haTargetPoints, haMaxTradesPerSide, tpHoldExtra,
      haConds,
      wickTf, wickTopWick, wickSlPoints, wickTpPoints, wickDualSide]);

  const loadRunDetail = useCallback(async (rid) => {
    if (!rid) return;
    try {
      const d = await apiCall(`/api/backtest/runs/${rid}`);
      setRunId(rid);
      setSummary(d.summary || null);
      setTrades(d.trades || []);
      if (d.strategy_id) setResultStrategy(d.strategy_id);
      // ── RUN_PARAMS_DISPLAY ── tolerate either detail shape (top-level or meta)
      setResultConfig(d.config || d.meta?.config || null);
      const mf = d.date_from || d.meta?.date_from;
      const mt = d.date_to || d.meta?.date_to;
      setResultMeta(mf ? { date_from: mf, date_to: mt } : null);
    } catch { /* ignore */ }
  }, []);

  const buildConfig = useCallback((sid) => {
    const v5 = sid === "SCALP_V5";
    const ha = sid === "HA_V1" || sid === "HA_SELL";
    const hedge = sid === "SCALP_V3" || sid === "SCALP_V4";
    if (sid === "WICK_V1") {
      return {
        timeframe_minutes: Number(wickTf),
        top_wick_min: Number(wickTopWick),
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(wickSlPoints),
        tp_points: Number(wickTpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_trades_per_side: Number(haMaxTradesPerSide),
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
        dual_side_mode: !!wickDualSide,
      };
    }
    if (ha) {
      return {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        risk_reward_ratio: Number(rr),
        min_sl_points: Number(minSl),
        max_sl_points: Number(maxSl),
        target_override: { enabled: !!haTargetOverride, points: Number(haTargetPoints) },
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_trades_per_side: Number(haMaxTradesPerSide),
        tp_hold_extra_candles: Number(tpHoldExtra),
        // ── HA_COND_FILTER ── enabled entry-condition subset (HA runners only)
        entry_conditions: haConds,
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
      };
    }
    if (v5) {
      return {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(slPoints),
        tp_points: Number(tpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
      };
    }
    const cfg = {
      option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
      risk_reward_ratio: Number(rr),
      min_sl_points: Number(minSl),
      max_sl_points: Number(maxSl),
      risk_max_sl_points: Number(riskMaxSl),
      session: { primary: { start: sessStart, end: sessEnd } },
      quantity: { lots: Number(lots) },
    };
    if (hedge) cfg.hedge_sl_points = Number(hedgeSl);
    return cfg;
    // HA_COND_FILTER: haConds added to deps. tpHoldExtra ALSO added — it was
    // MISSING before even though the ha branch sends tp_hold_extra_candles
    // (classic stale-closure bug: the Queue path could enqueue a stale value).
  }, [premiumMin, premiumMax, slPoints, tpPoints, sessStart, sessEnd, lots, sideMode,
      maxLoss, maxProfit, rr, minSl, maxSl, riskMaxSl, hedgeSl,
      haTargetOverride, haTargetPoints, haMaxTradesPerSide, tpHoldExtra, haConds,
      wickTf, wickTopWick, wickSlPoints, wickTpPoints, wickDualSide]);

  const startRunPolling = useCallback(() => {
    clearInterval(runPoll.current);
    runPoll.current = setInterval(async () => {
      try {
        const st = await apiCall("/api/backtest/run/status");
        setRunStatus(st);
        setRunRunning(st.running);
        if (!st.running) {
          clearInterval(runPoll.current);
          setRunError(st.error);
          setRunCancelling(false);
          if (st.run_id) await loadRunDetail(st.run_id);
        }
      } catch { /* keep polling */ }
    }, 1200);
  }, [loadRunDetail]);

  const startDhanPolling = useCallback(() => {
    clearInterval(dhanPoll.current);
    dhanPoll.current = setInterval(async () => {
      try {
        const st = await apiCall("/api/backtest/dhan/status");
        setDhanStatus(st);
        setDhanRunning(st.running);
        if (!st.running) {
          clearInterval(dhanPoll.current);
          setDhanError(st.error);
          setDhanCancelling(false);
          apiCall("/api/backtest/coverage?underlying=NIFTY").then(setCoverage).catch(() => {});
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  // ── REHYDRATE ON MOUNT ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const c = await apiCall("/api/backtest/coverage?underlying=NIFTY");
        if (!cancelled) {
          setCoverage(c);
          if (c.available && !saved.dateFrom) { setDateFrom(c.date_from); setDateTo(c.date_to); }
        }
      } catch { /* ignore */ }

      try {
        const st = await apiCall("/api/backtest/run/status");
        if (cancelled) return;
        setRunStatus(st);
        if (st.running) { setRunRunning(true); startRunPolling(); }
        else if (st.run_id) { await loadRunDetail(st.run_id); }
        else {
          try {
            const list = await apiCall("/api/backtest/runs?limit=1");
            if (!cancelled && list.runs && list.runs.length) await loadRunDetail(list.runs[0].run_id);
          } catch { /* ignore */ }
        }
      } catch { /* ignore */ }

      try {
        const dh = await apiCall("/api/backtest/dhan/status");
        if (cancelled) return;
        setDhanStatus(dh);
        if (dh.running) { setDhanRunning(true); startDhanPolling(); }
      } catch { /* ignore */ }
    })();
    return () => {
      cancelled = true;
      clearInterval(runPoll.current);
      clearInterval(dhanPoll.current);
    };
  }, []);

  // ── Dhan backfill actions ──
  const startDhanBackfill = useCallback(async () => {
    setDhanError(null);
    if (!dhanFrom || !dhanTo) { setDhanError("Pick a Dhan date range"); return; }
    try {
      await apiCall("/api/backtest/dhan/backfill/start", {
        method: "POST",
        body: JSON.stringify({ underlying: "NIFTY", date_from: dhanFrom, date_to: dhanTo, atm_window: 10 }),
      });
      setDhanCancelling(false);
      setDhanRunning(true);
      startDhanPolling();
    } catch (e) { setDhanError(String(e.message || e)); }
  }, [dhanFrom, dhanTo, startDhanPolling]);

  const cancelDhanBackfill = useCallback(async () => {
    setDhanCancelling(true);
    try { await apiCall("/api/backtest/dhan/backfill/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  // ── Run actions ──
  const startRun = useCallback(async () => {
    setRunError(null);
    let config_override;
    if (isWick) {
      config_override = {
        timeframe_minutes: Number(wickTf),
        top_wick_min: Number(wickTopWick),
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(wickSlPoints),
        tp_points: Number(wickTpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_trades_per_side: Number(haMaxTradesPerSide),
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
        dual_side_mode: !!wickDualSide,
      };
    } else if (isHA) {
      // HA_V1: LONG Heikin-Ashi option-buying. SL = signal red-candle low
      // (not user-set); TP = R:R or fixed target override. Per-side daily cap.
      config_override = {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        risk_reward_ratio: Number(rr),
        min_sl_points: Number(minSl),
        max_sl_points: Number(maxSl),
        target_override: { enabled: !!haTargetOverride, points: Number(haTargetPoints) },
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_trades_per_side: Number(haMaxTradesPerSide),
        tp_hold_extra_candles: Number(tpHoldExtra),
        // ── HA_COND_FILTER ── enabled entry-condition subset (HA runners only)
        entry_conditions: haConds,
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
      };
    } else if (isV5) {
      // SCALP_V5: LONG option-buying, absolute SL/TP points, session MTM caps.
      config_override = {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        sl_points: Number(slPoints),
        tp_points: Number(tpPoints),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
        trade_side_mode: sideMode,
        max_loss: Number(maxLoss),
        max_profit: Number(maxProfit),
      };
    } else {
      config_override = {
        option_premium: { min: Number(premiumMin), max: Number(premiumMax) },
        risk_reward_ratio: Number(rr),
        min_sl_points: Number(minSl),
        max_sl_points: Number(maxSl),
        risk_max_sl_points: Number(riskMaxSl),
        session: { primary: { start: sessStart, end: sessEnd } },
        quantity: { lots: Number(lots) },
      };
      if (isHedge) config_override.hedge_sl_points = Number(hedgeSl);
    }
    try {
      await apiCall("/api/backtest/run/start", {
        method: "POST",
        body: JSON.stringify({ strategy_id: strategyId, underlying: "NIFTY", date_from: dateFrom, date_to: dateTo, config_override }),
      });
      setResultStrategy(strategyId);
      // ── RUN_PARAMS_DISPLAY ── show the fresh run's params immediately
      setResultConfig(config_override);
      setResultMeta({ date_from: dateFrom, date_to: dateTo });
      setSummary(null); setTrades([]); setRunId(null);
      setRunCancelling(false);
      setRunRunning(true);
      startRunPolling();
    } catch (e) { setRunError(String(e.message || e)); }
    // HA_COND_FILTER: haConds added to deps. tpHoldExtra ALSO added — it was
    // MISSING before even though the isHA branch sends tp_hold_extra_candles
    // (classic stale-closure bug: the Run path could send a stale value).
  }, [strategyId, isHedge, isV5, isHA, dateFrom, dateTo, premiumMin, premiumMax, rr, minSl,
      maxSl, riskMaxSl, hedgeSl, sessStart, sessEnd, lots,
      slPoints, tpPoints, maxLoss, maxProfit, sideMode,
      haTargetOverride, haTargetPoints, haMaxTradesPerSide, tpHoldExtra, haConds,
      isWick, wickTf, wickTopWick, wickSlPoints, wickTpPoints, wickDualSide, startRunPolling]);

  const cancelRun = useCallback(async () => {
    setRunCancelling(true);
    try { await apiCall("/api/backtest/run/cancel", { method: "POST" }); } catch { /* ignore */ }
  }, []);

  // ── Time-of-Day-filtered trades (by ENTRY ist-time) ──
  const todTrades = useMemo(() => {
    if (todStart === "09:15" && todEnd === "15:30") return trades; // full window → no filter
    return trades.filter((t) => {
      const hm = istHM(t.entry_ts);
      return hm >= todStart && hm <= todEnd;
    });
  }, [trades, todStart, todEnd]);

  // ── Metrics (computed over the TOD-filtered set) ──
  const metrics = useMemo(() => computeMetrics(todTrades), [todTrades]);

  // Hourly P&L buckets (by ENTRY hour, IST) — for the Time-of-Day tab.
  const hourly = useMemo(() => {
    const map = {};
    for (const t of todTrades) {
      if (t.exit_price == null) continue;
      const hr = istHM(t.entry_ts).slice(0, 2) + ":00";
      if (!map[hr]) map[hr] = { hour: hr, pnl: 0, trades: 0, wins: 0 };
      const n = netOf(t);
      map[hr].pnl += n; map[hr].trades++; if (n > 0) map[hr].wins++;
    }
    return Object.values(map).sort((a, b) => a.hour.localeCompare(b.hour));
  }, [todTrades]);

  const { maxBdTrades, maxBdPnL } = useMemo(() => {
    if (!metrics) return { maxBdTrades: 1, maxBdPnL: 1 };
    const all = [...metrics.dayBreakdown, ...metrics.instrBreakdown, ...metrics.sideBreakdown];
    return {
      maxBdTrades: Math.max(...all.map((d) => d.trades), 1),
      maxBdPnL: Math.max(...all.map((d) => Math.max(d.profit, Math.abs(d.loss))), 1),
    };
  }, [metrics]);

  // ── Download CSV (client-side) ──
  const downloadCsv = useCallback(async () => {
    if (!trades.length) { setCsvMsg({ kind: "err", text: "Nothing to export yet — run a backtest first." }); return; }
    setCsvMsg({ kind: "info", text: "Preparing CSV…" });
    try {
      const csv = buildCsv(trades, summary, metrics, resultStrategy);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const safe = (x) => String(x || "").replace(/[^0-9A-Za-z_-]/g, "");
      const fname = `backtest_${safe(resultStrategy)}_${safe(dateFrom)}_to_${safe(dateTo)}` +
        `${runId ? "_" + safe(runId).slice(0, 8) : ""}.csv`;
      const a = document.createElement("a");
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setCsvMsg({ kind: "ok", text: `Downloaded ${trades.length} trades → ${fname}` });
      setTimeout(() => setCsvMsg(null), 6000);
    } catch (e) {
      setCsvMsg({ kind: "err", text: `Export failed: ${String(e.message || e)}` });
    }
  }, [trades, summary, metrics, resultStrategy, dateFrom, dateTo, runId]);

  const resultIsHedge = resultStrategy === "SCALP_V3" || resultStrategy === "SCALP_V4";
  const s = summary;

  const sortedTrades = React.useMemo(
    () => [...trades].sort((a, b) => (b.entry_ts || 0) - (a.entry_ts || 0)),
    [trades]
  );
  // Only these rows are rendered in the Summary table. The full `trades`,
  // `todTrades`, `metrics`, and CSV export are unaffected by this cap.
  const cappedTrades = React.useMemo(
    () => (showAllRows ? sortedTrades : sortedTrades.slice(0, TABLE_CAP)),
    [sortedTrades, showAllRows]
  );

  const runProg = runStatus?.progress;
  const runLabel = runCancelling
    ? "cancelling… (stops at the next checkpoint)"
    : runProg
    ? `day ${runProg.day}/${runProg.total_days}` +
      `${runProg.minutes_total ? ` · min ${runProg.minute}/${runProg.minutes_total}` : ""}` +
      ` · ${runProg.date}` +
      `${runStatus.eta_s != null ? ` · ETA ~${fmtDur(runStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(runStatus.elapsed_s)}`
    : "starting…";
  const dhanProg = dhanStatus?.progress;
  const dhanLabel = dhanCancelling
    ? "cancelling… (stops at the next request)"
    : dhanProg
    ? `${dhanProg.done}/${dhanProg.planned} requests · ${dhanProg.chunk || ""} ${dhanProg.offset || ""} ${dhanProg.side || ""} · rows ${dhanProg.rows?.toLocaleString("en-IN") || 0}` +
      `${dhanStatus.eta_s != null ? ` · ETA ~${fmtDur(dhanStatus.eta_s)}` : ""}` +
      ` · elapsed ${fmtDur(dhanStatus.elapsed_s)}`
    : "starting…";

  const RESULT_TABS = [
    ["summary", "Summary"],
    ["advanced", "Advanced KPIs"],
    ["timeofday", "Time of Day"],
    ["exits", "Exit Reasons"],
    ["conditions", "Entry Conditions"],
    ["equity", "Equity Curve"],
    ["breakdown", "Breakdown"],
    ["daily", "Daily"],
    ["weekly", "Weekly"],
    ["monthly", "Monthly"],
  ];
  // ── YEARLY ── tab appears only when the loaded run spans >1 calendar year.
  // (PeriodGrid still renders fine if the tab was selected and a 1-year run
  // is then loaded — the tab just disappears from the strip.)
  if (metrics?.yearly?.length > 1) RESULT_TABS.push(["yearly", "Yearly"]);
  const tabBtn = (k) => ({
    padding: "7px 16px", borderRadius: 6, border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600,
    background: resultTab === k ? colors.primary : "transparent",
    color: resultTab === k ? "#fff" : colors.text.muted,
  });

  return (
    <div style={{
      padding: spacing.xxl, background: colors.bg.primary, color: colors.text.primary,
      minHeight: "100vh", fontFamily: "'Inter', sans-serif", paddingBottom: 56,
    }}>
      <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700 }}>Backtest</h1>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <p style={{ margin: "4px 0 16px", fontSize: 12, color: colors.text.muted }}>
            { isWick
            ? `WICK_V1 · NIFTY · option-BUYING (LONG) · rejection-wick + midpoint pivot reclaim · ${wickTf}m signal / 1m fills · SL ${wickSlPoints} / TP ${wickTpPoints}`
            : isHA
            ? `${strategyId === "HA_SELL" ? "HA SELL · NIFTY · option-SELLING (SHORT)" : "HA_V1 · NIFTY · option-BUYING (LONG)"} · Heikin Ashi · 1-minute candles · conds ${haConds.map((c) => c.replace("COND", "C")).join("+")}`
            : isV5
            ? "SCALP V5 · NIFTY · option-BUYING (LONG) · 3-minute candles · EMA8 crosses above EMA20-High · EMA exit / SL / TP"
            : isHedge
            ? `${strategyId === "SCALP_V4" ? "SCALP V4" : "SCALP V3"} · NIFTY · option-BUYING hedge · signal tracked, opposite-side hedge bought (LONG)`
            : "SCALP V1 · NIFTY · short-selling · 1-minute OHLC · pessimistic fills"}
        </p>
        <div style={{ display: "flex", gap: 4, background: colors.bg.secondary, padding: 4, borderRadius: 8, border: `1px solid ${colors.border.light}` }}>
            {[["run", "Run"], ["queue", "Queue"], ["compare", "Compare Runs"]].map(([k, label]) => (
            <button key={k} onClick={() => setPageView(k)}
                style={{ padding: "6px 14px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600,
                background: pageView === k ? colors.primary : "transparent",
                color: pageView === k ? "#fff" : colors.text.muted }}>
                {label}
            </button>
            ))}
        </div>
        </div>

      {pageView === "queue" ? (
        <BacktestQueue
          colors={colors} spacing={spacing} typography={typography} Card={Card}
          apiCall={apiCall}
          strategyId={strategyId} dateFrom={dateFrom} dateTo={dateTo}
          buildConfig={buildConfig}
          onOpenRun={async (rid) => {
            setPageView("run");
            await loadRunDetail(rid);
            setResultTab("summary");
          }}
        />
      ) : pageView === "compare" ? (
        <RunComparison
          colors={colors} spacing={spacing} typography={typography} pnlStyle={pnlStyle}
          Card={Card} KpiTile={KpiTile}
          apiCall={apiCall} fmtInr={fmtInr} fmtTs={fmtTs}
          computeMetrics={computeMetrics} EquityCurve={EquityCurve}
          onOpenRun={async (rid) => {
            setPageView("run");
            await loadRunDetail(rid);
            setResultTab("summary");
          }}
        />
      ) : (
      <>

      {/* ── Strategy selector (SCALP only) ── */}
      <div style={{ display: "flex", gap: spacing.sm, marginBottom: spacing.lg }}>
        {[
          { id: "SCALP_V1", label: "SCALP V1", sub: "short" },
          { id: "SCALP_V3", label: "SCALP V3", sub: "hedge" },
          { id: "SCALP_V4", label: "SCALP V4", sub: "hedge + veto" },
          { id: "SCALP_V5", label: "SCALP V5", sub: "buy" },
          { id: "HA_V1", label: "HA V1", sub: "heikin ashi" },
          { id: "HA_SELL", label: "HA Sell", sub: "short" },
          { id: "WICK_V1", label: "WICK V1", sub: "wick pivot" },
        ].map((o) => {
          const active = strategyId === o.id;
          return (
            <button key={o.id} onClick={() => setStrategyId(o.id)}
              style={{
                padding: "8px 16px", borderRadius: 7, cursor: "pointer",
                border: `1px solid ${active ? colors.primary : colors.border.light}`,
                background: active ? colors.primaryBg : colors.bg.secondary,
                color: active ? colors.primary : colors.text.secondary,
                fontSize: 13, fontWeight: 600,
                display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 1,
              }}>
              {o.label}
              <span style={{ fontSize: 9, opacity: 0.7, fontWeight: 400 }}>{o.sub}</span>
            </button>
          );
        })}
      </div>

      {/* ── DATA / BACKFILL PANEL (Dhan expired weeklies only) ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.xl }}>
        <div>
          <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>Historical data</div>
          <div style={{ fontSize: 13, color: colors.text.secondary }}>
            {coverage?.available
              ? <>Corpus: <b>{coverage.date_from}</b> → <b>{coverage.date_to}</b> · {coverage.candles?.toLocaleString("en-IN")} candles</>
              : "No data yet — use the Dhan backfill below to fill the NIFTY expired-weeklies corpus."}
          </div>
        </div>

        <div style={{ marginTop: spacing.md }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.md }}>
            <div>
              <div style={{ ...typography.label, color: colors.text.muted, marginBottom: 4 }}>Dhan backfill (expired weeklies)</div>
              <div style={{ fontSize: 12, color: colors.text.secondary }}>
                {dhanStatus?.creds_set
                  ? <>Fills the exact per-week NIFTY contracts Kite can't return. ATM±10. Client <b>{dhanStatus.client_id}</b>.</>
                  : "Add Dhan credentials in Connections to enable expired-options backfill."}
              </div>
            </div>
            <div style={{ display: "flex", gap: spacing.sm, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="Dhan from"><input type="date" style={inputStyle} value={dhanFrom} onChange={(e) => setDhanFrom(e.target.value)} /></Field>
              <Field label="Dhan to"><input type="date" style={inputStyle} value={dhanTo} onChange={(e) => setDhanTo(e.target.value)} /></Field>
              <button style={btn("default")} disabled={dhanRunning || !dhanStatus?.creds_set} onClick={startDhanBackfill}>
                {dhanRunning ? "Backfilling…" : "Backfill (Dhan)"}
              </button>
              {dhanRunning && (
                <button style={btn("danger")} onClick={cancelDhanBackfill} disabled={dhanCancelling}>
                  {dhanCancelling ? "Cancelling…" : "Cancel"}
                </button>
              )}
            </div>
          </div>
          {dhanRunning && <ProgressBar pct={dhanStatus?.pct} label={dhanLabel} />}
          {!dhanRunning && dhanStatus?.result && !dhanError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: colors.profit }}>
              Done · {dhanStatus.result.rows_upserted?.toLocaleString("en-IN")} rows · {dhanStatus.result.days_covered} days · {dhanStatus.result.expiries?.length || 0} expiries · {dhanStatus.result.requests} requests
              {dhanStatus.result.errors?.length ? ` · ${dhanStatus.result.errors.length} call errors` : ""}
            </div>
          )}
          {dhanError && (
            <div style={{ marginTop: spacing.md, fontSize: 12, color: dhanError === "cancelled" ? colors.warning : colors.loss }}>
              {dhanError === "cancelled" ? "Dhan backfill cancelled." : dhanError}
            </div>
          )}
        </div>
      </Card>

      {/* ── BACKTEST PANEL ── */}
      <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.xl }}>
        <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>Run parameters</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: spacing.md }}>
          <Field label="Date from"><input type="date" style={inputStyle} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></Field>
          <Field label="Date to"><input type="date" style={inputStyle} value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></Field>
          <Field label="Premium min"><input type="number" style={inputStyle} value={premiumMin} onChange={(e) => setPremiumMin(e.target.value)} /></Field>
          <Field label="Premium max"><input type="number" style={inputStyle} value={premiumMax} onChange={(e) => setPremiumMax(e.target.value)} /></Field>
          {!isV5 && !isHA && !isWick && (
            <>
              <Field label="Risk:Reward"><input type="number" step="0.1" style={inputStyle} value={rr} onChange={(e) => setRr(e.target.value)} /></Field>
              <Field label="Min SL pts"><input type="number" style={inputStyle} value={minSl} onChange={(e) => setMinSl(e.target.value)} /></Field>
              <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>
              <Field label="Risk Max SL"><input type="number" style={inputStyle} value={riskMaxSl} onChange={(e) => setRiskMaxSl(e.target.value)} /></Field>
            </>
          )}
          {isHedge && (
            <Field label="Hedge SL pts"><input type="number" style={inputStyle} value={hedgeSl} onChange={(e) => setHedgeSl(e.target.value)} /></Field>
          )}
          {isV5 && (
            <>
              <Field label="SL pts"><input type="number" style={inputStyle} value={slPoints} onChange={(e) => setSlPoints(e.target.value)} /></Field>
              <Field label="TP pts"><input type="number" style={inputStyle} value={tpPoints} onChange={(e) => setTpPoints(e.target.value)} /></Field>
              <Field label="Max Loss ₹"><input type="number" style={inputStyle} value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)} /></Field>
              <Field label="Max Profit ₹"><input type="number" style={inputStyle} value={maxProfit} onChange={(e) => setMaxProfit(e.target.value)} /></Field>
              <Field label="Side">
                <select style={inputStyle} value={sideMode} onChange={(e) => setSideMode(e.target.value)}>
                  <option value="BOTH">BOTH</option>
                  <option value="CE">CE only</option>
                  <option value="PE">PE only</option>
                </select>
              </Field>
            </>
          )}
          {isHA && (
            <>
              {/* HA SL = signal's red-candle low (no SL field). TP = R:R, OR a
                  fixed-point target when override is enabled. Min SL gates out
                  entries whose SL distance (entry − red-low) is below this. */}
              <Field label="Risk:Reward"><input type="number" step="0.1" style={inputStyle} value={rr} onChange={(e) => setRr(e.target.value)} /></Field>
              <Field label="Min SL pts"><input type="number" style={inputStyle} value={minSl} onChange={(e) => setMinSl(e.target.value)} /></Field>
              <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>
              <Field label="Fixed target">
                <select style={inputStyle} value={haTargetOverride ? "1" : "0"} onChange={(e) => setHaTargetOverride(e.target.value === "1")}>
                  <option value="0">Off (use R:R)</option>
                  <option value="1">On (fixed pts)</option>
                </select>
              </Field>
              <Field label="Target pts"><input type="number" style={inputStyle} value={haTargetPoints} disabled={!haTargetOverride} onChange={(e) => setHaTargetPoints(e.target.value)} /></Field>
              <Field label="Max trades/side"><input type="number" style={inputStyle} value={haMaxTradesPerSide} onChange={(e) => setHaMaxTradesPerSide(e.target.value)} /></Field>
              <Field label="TP hold candles"><input type="number" style={inputStyle} value={tpHoldExtra} onChange={(e) => setTpHoldExtra(e.target.value)} /></Field>
              <Field label="Max Loss ₹"><input type="number" style={inputStyle} value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)} /></Field>
              <Field label="Max Profit ₹"><input type="number" style={inputStyle} value={maxProfit} onChange={(e) => setMaxProfit(e.target.value)} /></Field>
              <Field label="Side">
                <select style={inputStyle} value={sideMode} onChange={(e) => setSideMode(e.target.value)}>
                  <option value="BOTH">BOTH</option>
                  <option value="CE">CE only</option>
                  <option value="PE">PE only</option>
                </select>
              </Field>
              {/* ── HA_COND_FILTER BEGIN ── entry-condition multi-select chips.
                  Any/all combinations of COND1/COND2/COND3. The last enabled
                  chip cannot be turned off (empty = ambiguous; backend would
                  treat it as ALL, so we never send one). */}
              <Field label="Entry conditions">
                <div style={{ display: "flex", gap: 6 }}>
                  {HA_ALL_CONDS.map((c) => {
                    const on = haConds.includes(c);
                    const lastOn = on && haConds.length === 1;
                    return (
                      <button key={c} type="button" onClick={() => toggleHaCond(c)}
                        title={lastOn ? "At least one condition must stay enabled" : c}
                        style={{
                          padding: "7px 10px", borderRadius: 6, fontSize: 12, fontWeight: 700,
                          cursor: lastOn ? "not-allowed" : "pointer",
                          border: `1px solid ${on ? colors.primary : colors.border.light}`,
                          background: on ? colors.primaryBg : colors.bg.secondary,
                          color: on ? colors.primary : colors.text.muted,
                          opacity: lastOn ? 0.8 : 1,
                        }}>
                        {c.replace("COND", "C")}
                      </button>
                    );
                  })}
                </div>
              </Field>
              {/* ── HA_COND_FILTER END ── */}
            </>
          )}
          {isWick && (
            <>
              <Field label="Timeframe">
                <select style={inputStyle} value={wickTf} onChange={(e) => setWickTf(e.target.value)}>
                  <option value={1}>1m</option>
                  <option value={3}>3m</option>
                  <option value={5}>5m</option>
                  <option value={10}>10m</option>
                  <option value={15}>15m</option>
                </select>
              </Field>
              <Field label="Top wick min"><input type="number" step="0.1" style={inputStyle} value={wickTopWick} onChange={(e) => setWickTopWick(e.target.value)} /></Field>
              <Field label="SL points"><input type="number" style={inputStyle} value={wickSlPoints} onChange={(e) => setWickSlPoints(e.target.value)} /></Field>
              <Field label="TP points"><input type="number" style={inputStyle} value={wickTpPoints} onChange={(e) => setWickTpPoints(e.target.value)} /></Field>
              <Field label="Max trades/side"><input type="number" style={inputStyle} value={haMaxTradesPerSide} onChange={(e) => setHaMaxTradesPerSide(e.target.value)} /></Field>
              <Field label="Max Loss ₹"><input type="number" style={inputStyle} value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)} /></Field>
              <Field label="Max Profit ₹"><input type="number" style={inputStyle} value={maxProfit} onChange={(e) => setMaxProfit(e.target.value)} /></Field>
              <Field label="Side">
                <select style={inputStyle} value={sideMode} onChange={(e) => setSideMode(e.target.value)}>
                  <option value="BOTH">BOTH</option>
                  <option value="CE">CE only</option>
                  <option value="PE">PE only</option>
                </select>
              </Field>
            </>
          )}
          <Field label="Max 1 CE + 1 PE">
                <select style={inputStyle} value={wickDualSide ? "1" : "0"} onChange={(e) => setWickDualSide(e.target.value === "1")}>
                  <option value="0">Off (1 trade global)</option>
                  <option value="1">On (1 CE + 1 PE)</option>
                </select>
              </Field>
          <Field label="Session start"><input type="text" style={inputStyle} value={sessStart} onChange={(e) => setSessStart(e.target.value)} /></Field>
          <Field label="Session end"><input type="text" style={inputStyle} value={sessEnd} onChange={(e) => setSessEnd(e.target.value)} /></Field>
          <Field label="Lots"><input type="number" style={inputStyle} value={lots} onChange={(e) => setLots(e.target.value)} /></Field>
        </div>
        <div style={{ marginTop: spacing.lg, display: "flex", gap: spacing.md, alignItems: "center" }}>
          <button style={btn("primary")} disabled={runRunning || !dateFrom || !dateTo} onClick={startRun}>
            {runRunning ? "Running…" : "Run backtest"}
          </button>
          {runRunning && (
            <button style={btn("danger")} onClick={cancelRun} disabled={runCancelling}>
              {runCancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
        </div>
        {runRunning && <ProgressBar pct={runStatus?.pct} label={runLabel} />}
        {runError && (
          <div style={{ marginTop: spacing.md, fontSize: 12, color: runError === "cancelled" ? colors.warning : colors.loss }}>
            {runError === "cancelled" ? "Backtest cancelled." : runError}
          </div>
        )}
      </Card>

      {/* ── RESULTS ── */}
      {s && (
        <>
          {/* ── RUN_PARAMS_DISPLAY BEGIN ── exactly which knobs produced these
              numbers. Sourced from the PERSISTED run config (or the just-sent
              override for a fresh run), NOT the form above — so it stays true
              even after the form is changed or another strategy is selected. */}
          {resultConfig && describeConfig(resultConfig).length > 0 && (
            <Card elevated style={{ padding: spacing.md, marginBottom: spacing.lg }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ ...typography.label, color: colors.text.muted, marginRight: 4 }}>
                  Run parameters · <b style={{ color: colors.text.secondary }}>{resultStrategy}</b>
                  {runId ? <span style={{ ...typography.mono, fontSize: 10 }}> · {runId.slice(0, 8)}</span> : null}
                  {resultMeta ? <span> · {resultMeta.date_from} → {resultMeta.date_to}</span> : null}
                </span>
                {describeConfig(resultConfig).map(([k, v]) => (
                  <span key={k} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 5,
                    background: colors.bg.secondary, border: `1px solid ${colors.border.light}`,
                    color: colors.text.secondary, whiteSpace: "nowrap" }}>
                    <b style={{ color: colors.text.primary }}>{k}:</b> {v}
                  </span>
                ))}
              </div>
            </Card>
          )}
          {/* ── RUN_PARAMS_DISPLAY END ── */}
          <div style={{ display: "flex", gap: 4, marginBottom: spacing.lg, background: colors.bg.secondary,
            padding: 4, borderRadius: 8, border: `1px solid ${colors.border.light}`, width: "fit-content", flexWrap: "wrap" }}>
            {RESULT_TABS.map(([k, label]) => (
              <button key={k} style={tabBtn(k)} onClick={() => setResultTab(k)}>{label}</button>
            ))}
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: spacing.md, marginBottom: spacing.sm }}>
            {csvMsg && (
              <span style={{ fontSize: 12, fontWeight: 600,
                color: csvMsg.kind === "ok" ? colors.profit : csvMsg.kind === "err" ? colors.loss : colors.text.muted }}>
                {csvMsg.text}
              </span>
            )}
            <button style={btn("default")} onClick={downloadCsv}>📄 Download CSV</button>
          </div>

          {/* SUMMARY */}
          {resultTab === "summary" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Gross P&L</div>
                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.gross_pnl) }}>
                    {s.gross_pnl >= 0 ? "+" : ""}₹{Math.round(s.gross_pnl).toLocaleString("en-IN")}
                  </div>
                </Card>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Charges</div>
                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, color: colors.loss }}>
                    −₹{Math.round(s.total_charges).toLocaleString("en-IN")}
                  </div>
                </Card>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Net P&L</div>
                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.net_pnl) }}>
                    {s.net_pnl >= 0 ? "+" : ""}₹{Math.round(s.net_pnl).toLocaleString("en-IN")}
                  </div>
                </Card>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Win rate</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color: s.win_rate >= 50 ? colors.profit : colors.loss }}>
                    {s.win_rate.toFixed(1)}%
                  </div>
                  <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 3 }}>{s.wins}W / {s.losses}L</div>
                </Card>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Trades</div>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>{s.total_trades}</div>
                  <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 3 }}>{s.ambiguous_fills} ambiguous</div>
                </Card>
                <Card elevated style={{ padding: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted }}>Max DD (net)</div>
                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, color: colors.loss }}>
                    ₹{Math.round(s.max_drawdown).toLocaleString("en-IN")}
                  </div>
                </Card>
              </div>

              <Card>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                    <thead style={{ background: colors.bg.tertiary }}>
                      <tr>
                        {(resultIsHedge
                          ? ["Signal", "Hedge", "Entry", "Hedge ₹", "Hedge SL", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]
                          : ["Symbol", "Cond", "Entry", "Entry ₹", "SL", "TP", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]
                        ).map((h) => (
                          <th key={h} style={{ padding: "9px 8px", textAlign: "left", ...typography.label, color: colors.text.muted, borderBottom: `2px solid ${colors.border.light}`, whiteSpace: "nowrap" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {cappedTrades.map((t, i) => (
                        <tr key={i} style={{ background: i % 2 ? colors.bg.secondary : colors.bg.primary, borderTop: `1px solid ${colors.border.dark}` }}>
                          {resultIsHedge && (
                            <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.secondary, whiteSpace: "nowrap" }}>
                              {t.signal_symbol}
                              <span style={{ fontSize: 9, color: colors.text.muted, marginLeft: 4 }}>{t.signal_side}</span>
                            </td>
                          )}
                          <td style={{ padding: "8px", ...typography.mono, fontWeight: 600, whiteSpace: "nowrap" }}>{t.tradingsymbol}</td>
                          {!resultIsHedge && (
                            /* HA_COND_FILTER: condition badge (C1/C2/C3); blank for non-HA */
                            <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.secondary, whiteSpace: "nowrap" }}>
                              {t.condition ? t.condition.replace("COND", "C") : ""}
                            </td>
                          )}
                          <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>{fmtTs(t.entry_ts)}</td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.entry_price?.toFixed(2)}</td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.loss }}>{t.sl?.toFixed(2)}</td>
                          {!resultIsHedge && (
                            <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.profit }}>{t.tp?.toFixed(2)}</td>
                          )}
                          <td style={{ padding: "8px", ...typography.mono, fontSize: 11, color: colors.text.tertiary, whiteSpace: "nowrap" }}>{fmtTs(t.exit_ts)}</td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.exit_price?.toFixed(2)}</td>
                          <td style={{ padding: "8px" }}>
                            <span style={{ padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                              background: (t.exit_reason === "TP" || t.exit_reason === "SIG_TP") ? colors.successBg : t.exit_reason === "EOD" ? colors.warningBg : colors.lossBg,
                              color: (t.exit_reason === "TP" || t.exit_reason === "SIG_TP") ? colors.success : t.exit_reason === "EOD" ? colors.warning : colors.loss }}>
                              {t.exit_reason}
                            </span>
                          </td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right", ...pnlStyle(t.pnl) }}>{Math.round(t.pnl).toLocaleString("en-IN")}</td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.loss }}>−{Math.round(t.charges).toLocaleString("en-IN")}</td>
                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right", fontWeight: 700, ...pnlStyle(t.net_pnl) }}>{Math.round(netOf(t)).toLocaleString("en-IN")}</td>
                          <td style={{ padding: "8px", textAlign: "center" }}>{t.ambiguous_fill ? "⚠️" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {sortedTrades.length > TABLE_CAP && (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    gap: spacing.md, padding: "10px 12px", borderTop: `1px solid ${colors.border.dark}`,
                    fontSize: 12, color: colors.text.muted }}>
                    <span>
                      Showing <b style={{ color: colors.text.secondary }}>{cappedTrades.length.toLocaleString("en-IN")}</b>
                      {" "}of {sortedTrades.length.toLocaleString("en-IN")} trades
                      {!showAllRows && " (most recent first)"}
                      {" · analytics & CSV use all trades"}
                    </span>
                    <button style={btn("default")} onClick={() => setShowAllRows((v) => !v)}>
                      {showAllRows ? `Show first ${TABLE_CAP}` : "Show all (may be slow)"}
                    </button>
                  </div>
                )}
              </Card>
            </>
          )}

          {/* EQUITY */}
          {resultTab === "equity" && (
            <Card elevated innerRef={containerRef} style={{ padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Equity Curve</span>
                {metrics && (
                  <span style={{ fontSize: 12, ...pnlStyle(metrics.totalPnL), fontWeight: 700 }}>
                    End: {metrics.totalPnL >= 0 ? "+" : ""}{fmtInr(metrics.totalPnL)}
                  </span>
                )}
              </div>
              {metrics ? <EquityCurve data={metrics.equityCurve} width={chartWidth} height={260} />
                : <div style={{ color: colors.text.muted, fontSize: 13, textAlign: "center", padding: "60px 0" }}>No closed trades to chart</div>}
            </Card>
          )}

          {/* BREAKDOWN */}
          {resultTab === "breakdown" && (
            metrics ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <BreakdownPanel title="Day of Week" items={metrics.dayBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
                <BreakdownPanel title="Instruments" items={metrics.instrBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
                <BreakdownPanel title="CE vs PE" items={metrics.sideBreakdown} maxTrades={maxBdTrades} maxPnL={maxBdPnL} />
              </div>
            ) : <Card elevated style={{ padding: "60px 0", textAlign: "center", color: colors.text.muted, fontSize: 13 }}>No closed trades to analyse</Card>
          )}

          {/* DAILY / WEEKLY / MONTHLY */}
          {(resultTab === "daily" || resultTab === "weekly" || resultTab === "monthly" || resultTab === "yearly") && (
            <Card elevated style={{ padding: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, textTransform: "capitalize" }}>{resultTab} P&L</div>
              <PeriodGrid data={metrics ? metrics[resultTab] : []} />
            </Card>
          )}

          {/* ── ADVANCED KPIs ── */}
          {resultTab === "advanced" && (
            metrics ? (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: spacing.md, marginBottom: spacing.lg }}>
                  <KpiTile label="Profit Factor"
                    value={metrics.profitFactor === Infinity ? "∞" : metrics.profitFactor.toFixed(2)}
                    good={metrics.profitFactor >= 1.5} bad={metrics.profitFactor < 1}
                    sub="gross profit ÷ gross loss" />
                  <KpiTile label="Expectancy / trade"
                    value={`${metrics.expectancy >= 0 ? "+" : ""}${fmtInr(metrics.expectancy)}`}
                    good={metrics.expectancy > 0} bad={metrics.expectancy < 0}
                    sub="avg net P&L per trade" />
                  <KpiTile label="Return ÷ Max DD"
                    value={metrics.returnToDD === Infinity ? "∞" : metrics.returnToDD.toFixed(2)}
                    good={metrics.returnToDD >= 2} bad={metrics.returnToDD < 1}
                    sub="net P&L ÷ max drawdown" />
                  <KpiTile label="Win / Loss size"
                    value={metrics.winLossRatio === Infinity ? "∞" : metrics.winLossRatio.toFixed(2)}
                    good={metrics.winLossRatio >= 1} bad={metrics.winLossRatio < 1}
                    sub={`avg win ${fmtInr(metrics.avgWinX)} / avg loss ${fmtInr(Math.abs(metrics.avgLossX))}`} />
                  <KpiTile label="Max win streak" value={`${metrics.bestWinStreak}`} good={metrics.bestWinStreak > 0}
                    sub="longest consecutive wins" />
                  <KpiTile label="Max loss streak" value={`${metrics.bestLossStreak}`} bad={metrics.bestLossStreak >= 4}
                    sub="longest consecutive losses" />
                  <KpiTile label="Largest win" value={`+${fmtInr(metrics.largestWin)}`} good
                    sub="best single trade (net)" />
                  <KpiTile label="Largest loss" value={`-${fmtInr(Math.abs(metrics.largestLoss))}`} bad
                    sub="worst single trade (net)" />
                </div>
                <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
                  <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>Holding time</div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: spacing.md }}>
                    <MiniStat label="Avg hold" value={fmtHold(metrics.avgHold)} />
                    <MiniStat label="Median hold" value={fmtHold(metrics.medHold)} />
                    <MiniStat label="Avg hold (wins)" value={fmtHold(metrics.avgHoldWin)} color={colors.profit} />
                    <MiniStat label="Avg hold (losses)" value={fmtHold(metrics.avgHoldLoss)} color={colors.loss} />
                  </div>
                </Card>
                <MetricsExplainer />
              </>
            ) : <Card elevated style={{ padding: "60px 0", textAlign: "center", color: colors.text.muted, fontSize: 13 }}>No closed trades to analyse</Card>
          )}

          {/* ── TIME OF DAY ── */}
          {resultTab === "timeofday" && (
            <>
              <Card elevated style={{ padding: spacing.lg, marginBottom: spacing.lg }}>
                <div style={{ display: "flex", alignItems: "flex-end", gap: spacing.md, flexWrap: "wrap" }}>
                  <Field label="Entry from (IST)"><input type="time" style={inputStyle} value={todStart} onChange={(e) => setTodStart(e.target.value)} /></Field>
                  <Field label="Entry to (IST)"><input type="time" style={inputStyle} value={todEnd} onChange={(e) => setTodEnd(e.target.value)} /></Field>
                  <button style={btn("default")} onClick={() => { setTodStart("09:15"); setTodEnd("15:30"); }}>Reset</button>
                  <div style={{ marginLeft: "auto", fontSize: 12, color: colors.text.muted }}>
                    Showing <b>{todTrades.filter((t) => t.exit_price != null).length}</b> of {trades.filter((t) => t.exit_price != null).length} trades · filters EVERY tab by entry time
                  </div>
                </div>
                {metrics && (
                  <div style={{ marginTop: spacing.md, display: "flex", gap: spacing.lg, flexWrap: "wrap", fontSize: 13 }}>
                    <span>Net P&L in window: <b style={{ ...pnlStyle(metrics.totalPnL) }}>{metrics.totalPnL >= 0 ? "+" : ""}{fmtInr(metrics.totalPnL)}</b></span>
                    <span>Win rate: <b>{metrics.winRate.toFixed(1)}%</b></span>
                    <span>Expectancy: <b>{metrics.expectancy >= 0 ? "+" : ""}{fmtInr(metrics.expectancy)}</b>/trade</span>
                  </div>
                )}
              </Card>
              <Card elevated style={{ padding: spacing.lg }}>
                <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>Net P&L by entry hour (IST)</div>
                {hourly.length ? <HourBars data={hourly} /> :
                  <div style={{ color: colors.text.muted, fontSize: 13, textAlign: "center", padding: "30px 0" }}>No trades in this window</div>}
              </Card>
            </>
          )}

          {/* ── EXIT REASONS ── */}
          {resultTab === "exits" && (
            metrics ? (
              <Card elevated style={{ padding: spacing.lg }}>
                <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>P&L by exit reason</div>
                <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                  <thead style={{ background: colors.bg.tertiary }}>
                    <tr>
                      {["Reason", "Trades", "Win rate", "Net P&L", "Avg / trade"].map((h) => (
                        <th key={h} style={{ padding: "9px 10px", textAlign: h === "Reason" ? "left" : "right", ...typography.label, color: colors.text.muted, borderBottom: `2px solid ${colors.border.light}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.exitReasons.map((r, i) => {
                      const wr = r.trades ? (r.wins / r.trades) * 100 : 0;
                      return (
                        <tr key={i} style={{ borderTop: `1px solid ${colors.border.dark}` }}>
                          <td style={{ padding: "9px 10px", fontWeight: 600 }}>
                            <span style={{ padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 700,
                              background: r.reason === "TP" ? colors.successBg : r.reason === "EOD" ? colors.warningBg : colors.lossBg,
                              color: r.reason === "TP" ? colors.success : r.reason === "EOD" ? colors.warning : colors.loss }}>
                              {r.reason}
                            </span>
                          </td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono }}>{r.trades}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, color: wr >= 50 ? colors.profit : colors.loss }}>{wr.toFixed(0)}%</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(r.pnl) }}>{r.pnl >= 0 ? "+" : ""}{fmtInr(r.pnl)}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(r.pnl / (r.trades || 1)) }}>{fmtInr(r.pnl / (r.trades || 1))}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div style={{ marginTop: spacing.md, fontSize: 11, color: colors.text.tertiary }}>
                  For SCALP V5: compare EMA_EXIT vs TP vs SL net — if EMA_EXIT is net-negative while TP carries the strategy, the exit may be cutting winners early.
                </div>
              </Card>
            ) : <Card elevated style={{ padding: "60px 0", textAlign: "center", color: colors.text.muted, fontSize: 13 }}>No closed trades to analyse</Card>
          )}

          {/* ── HA_COND_FILTER BEGIN ── ENTRY CONDITIONS tab (HA_V1 / HA_SELL) ── */}
          {resultTab === "conditions" && (
            metrics && metrics.entryConditions?.length ? (
              <Card elevated style={{ padding: spacing.lg }}>
                <div style={{ ...typography.label, color: colors.text.muted, marginBottom: spacing.md }}>P&L by entry condition</div>
                <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>
                  <thead style={{ background: colors.bg.tertiary }}>
                    <tr>
                      {["Condition", "Trades", "Win rate", "Net P&L", "Avg / trade"].map((h) => (
                        <th key={h} style={{ padding: "9px 10px", textAlign: h === "Condition" ? "left" : "right", ...typography.label, color: colors.text.muted, borderBottom: `2px solid ${colors.border.light}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.entryConditions.map((r, i) => {
                      const wr = r.trades ? (r.wins / r.trades) * 100 : 0;
                      return (
                        <tr key={i} style={{ borderTop: `1px solid ${colors.border.dark}` }}>
                          <td style={{ padding: "9px 10px", fontWeight: 600 }}>
                            <span style={{ padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 700,
                              background: r.pnl >= 0 ? colors.successBg : colors.lossBg,
                              color: r.pnl >= 0 ? colors.success : colors.loss }}>
                              {r.reason}
                            </span>
                          </td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono }}>{r.trades}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, color: wr >= 50 ? colors.profit : colors.loss }}>{wr.toFixed(0)}%</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(r.pnl) }}>{r.pnl >= 0 ? "+" : ""}{fmtInr(r.pnl)}</td>
                          <td style={{ padding: "9px 10px", textAlign: "right", ...typography.mono, ...pnlStyle(r.pnl / (r.trades || 1)) }}>{fmtInr(r.pnl / (r.trades || 1))}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div style={{ marginTop: spacing.md, fontSize: 11, color: colors.text.tertiary }}>
                  HA only. This breaks down the CURRENT run's trades by the condition that fired them — useful for a first read, but note the interaction effect: with all conditions enabled, one condition's trade can occupy the single global slot and block another's. For a clean per-condition comparison, run each condition in isolation via the chips (the Queue makes this a one-click batch) and compare in Compare Runs.
                </div>
              </Card>
            ) : <Card elevated style={{ padding: "60px 0", textAlign: "center", color: colors.text.muted, fontSize: 13 }}>No entry-condition data — this tab applies to HA_V1 / HA_SELL runs</Card>
          )}
          {/* ── HA_COND_FILTER END ── */}
</>
      )}

      </>
      )}
    </div>
  );
}

export function fmtTs(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

// IST HH:MM of an epoch (fixed +5:30), for the time-of-day filter + hourly buckets.
function istHM(epoch) {
  if (!epoch) return "00:00";
  const d = new Date((epoch + 5.5 * 3600) * 1000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
function fmtHold(secs) {
  if (!secs) return "—";
  const s = Math.round(secs);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), rs = s % 60;
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60), rm = m % 60;
  return `${h}h ${rm}m`;
}
/**
 * StatusBar
 * Path: src/components/StatusBar.jsx
 *
 * VS Code-style bottom strip, fixed to viewport bottom.
 * Receives `health` from App.jsx (polled every 5s — no duplicate fetch).
 * Self-manages: clock, market countdown, market open/close state.
 *
 * ADDED (redesign): a Today's P&L breakdown (Realised · Unrealised · Total)
 * read from MarketDataContext, so P&L is visible from ANY route. Everything
 * else below is unchanged from the original StatusBar.
 */

import { useEffect, useState } from "react";
import { useMarketData } from "../context/MarketDataContext";

const MARKET_START = { h: 9,  m: 15 };
const MARKET_END   = { h: 15, m: 30 };

function toMins(h, m) { return h * 60 + m; }
const MARKET_START_MINS = toMins(MARKET_START.h, MARKET_START.m);
const MARKET_END_MINS   = toMins(MARKET_END.h,   MARKET_END.m);

function getNowMins() {
  const n = new Date();
  return n.getHours() * 60 + n.getMinutes() + n.getSeconds() / 60;
}

function isWeekend() {
  const d = new Date().getDay();
  return d === 0 || d === 6;
}

function getMarketState() {
  if (isWeekend()) return "WEEKEND";
  const now = getNowMins();
  if (now < MARKET_START_MINS) return "PRE";
  if (now >= MARKET_END_MINS)  return "CLOSED";
  return "OPEN";
}

function formatCountdown(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = Math.floor(totalSeconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function getCountdownSeconds() {
  if (isWeekend()) return null;
  const now = new Date();
  const nowSecs = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
  const startSecs = MARKET_START.h * 3600 + MARKET_START.m * 60;
  const endSecs   = MARKET_END.h   * 3600 + MARKET_END.m   * 60;
  if (nowSecs < startSecs) return { to: "open",  secs: startSecs - nowSecs };
  if (nowSecs < endSecs)   return { to: "close", secs: endSecs   - nowSecs };
  return null;
}

function formatTime(date) {
  return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/* ─────────────────────────────────────────────
   Colours
───────────────────────────────────────────── */

const BG      = "#060e1f";
const SUCCESS = "#10b981";
const WARNING = "#f59e0b";
const DANGER  = "#ef4444";
const PRIMARY = "#3b82f6";
const MUTED   = "#475569";
const TEXT    = "#94a3b8";
const BORDER  = "#1e293b";
const MONO    = "'JetBrains Mono','Fira Code','Courier New',monospace";

/* ─────────────────────────────────────────────
   Segment — a single status item in the bar
───────────────────────────────────────────── */

function Seg({ dot, color, label, value, dimmed, border }) {
  return (
    <div style={{
      display:      "flex",
      alignItems:   "center",
      gap:          5,
      padding:      "0 12px",
      borderRight:  border !== false ? `1px solid ${BORDER}` : "none",
      height:       "100%",
      opacity:      dimmed ? 0.45 : 1,
      transition:   "opacity 0.3s ease",
      userSelect:   "none",
    }}>
      {dot && (
        <span style={{
          width:        5,
          height:       5,
          borderRadius: "50%",
          background:   color || MUTED,
          boxShadow:    color ? `0 0 6px ${color}80` : "none",
          flexShrink:   0,
        }} />
      )}
      {label && (
        <span style={{ fontSize: 10, color: MUTED, textTransform: "uppercase", letterSpacing: "0.4px", fontWeight: 500 }}>
          {label}
        </span>
      )}
      {value && (
        <span style={{ fontSize: 11, color: color || TEXT, fontWeight: 600, letterSpacing: "0.2px" }}>
          {value}
        </span>
      )}
    </div>
  );
}

function Divider() {
  return <div style={{ width: 1, height: 14, background: BORDER, flexShrink: 0 }} />;
}

/* ── Money formatter for P&L segments ── */
function money(v) {
  const n = Math.round(Math.abs(v || 0)).toLocaleString("en-IN");
  return `${(v || 0) >= 0 ? "+" : "−"}₹${n}`;
}

/* ── P&L segment — label + signed value in mono ── */
function PnLSeg({ label, value, strong }) {
  const color = value === 0 ? TEXT : value > 0 ? SUCCESS : DANGER;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "0 12px", height: "100%", userSelect: "none" }}>
      <span style={{ fontSize: 10, color: MUTED, textTransform: "uppercase", letterSpacing: "0.4px", fontWeight: 500 }}>
        {label}
      </span>
      <span style={{ fontSize: strong ? 12 : 11, color, fontWeight: strong ? 800 : 600, fontFamily: MONO, fontVariantNumeric: "tabular-nums" }}>
        {money(value)}
      </span>
    </div>
  );
}

/* ─────────────────────────────────────────────
   StatusBar
───────────────────────────────────────────── */

export default function StatusBar({ health = {} }) {
  const [now, setNow]           = useState(new Date());
  const [countdown, setCountdown] = useState(getCountdownSeconds);
  const [marketState, setMarketState] = useState(getMarketState);

  // Today's P&L from app-level context (visible on every route)
  const { positions } = useMarketData();
  const totals = positions?.totals ?? { realised: 0, unrealised: 0, total: 0 };

  useEffect(() => {
    const t = setInterval(() => {
      setNow(new Date());
      setCountdown(getCountdownSeconds());
      setMarketState(getMarketState());
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const { backendUp, engineRunning, trading, zerodhaConnected } = health;

  // Backend
  const backendColor = backendUp ? SUCCESS : DANGER;
  const backendLabel = backendUp ? "Connected" : "Offline";

  // Engine — three distinct states
  const duringMarket = marketState === "OPEN";
  const engineColor = !backendUp
    ? MUTED
    : !engineRunning
    ? DANGER
    : duringMarket
    ? SUCCESS
    : PRIMARY;
  const engineLabel = !backendUp
    ? "—"
    : !engineRunning
    ? "Engine Off"
    : duringMarket
    ? "Engine On"
    : "Engine Idle";

  // Zerodha
  const brokerColor = !backendUp ? MUTED : zerodhaConnected ? SUCCESS : DANGER;
  const brokerLabel = !backendUp ? "—" : zerodhaConnected ? "Zerodha" : "No Broker";

  // Trading
  const tradingColor = trading ? SUCCESS : MUTED;
  const tradingLabel = trading ? "Trading" : "Standby";

  // Market state
  const mktColor = marketState === "OPEN"
    ? SUCCESS
    : marketState === "PRE"
    ? WARNING
    : MUTED;

  const mktLabel = marketState === "OPEN"
    ? "Market Open"
    : marketState === "PRE"
    ? "Pre-Market"
    : marketState === "WEEKEND"
    ? "Weekend"
    : "Market Closed";

  // Countdown urgency
  const countdownColor = !countdown
    ? MUTED
    : countdown.to === "close" && countdown.secs < 900
    ? countdown.secs < 300 ? DANGER : WARNING
    : countdown.to === "close"
    ? SUCCESS
    : PRIMARY;

  return (
    <div style={{
      position:    "fixed",
      bottom:      0,
      left:        0,
      right:       0,
      height:      28,
      background:  BG,
      borderTop:   `1px solid ${BORDER}`,
      display:     "flex",
      alignItems:  "center",
      zIndex:      9000,
      fontFamily:  "'Inter', -apple-system, sans-serif",
      overflow:    "hidden",
    }}>

      {/* Left group — system health */}
      <Seg dot color={backendColor} value={backendLabel} />
      <Seg dot color={engineColor}  value={engineLabel}  dimmed={!backendUp} />
      <Seg dot color={brokerColor}  value={brokerLabel}  dimmed={!backendUp} />
      <Seg dot color={tradingColor} value={tradingLabel} dimmed={!engineRunning} />

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Centre — market state */}
      <Seg dot color={mktColor} value={mktLabel} border={false} />

      {/* Countdown */}
      {countdown && (
        <Seg
          label={countdown.to === "close" ? "closes in" : "opens in"}
          value={formatCountdown(countdown.secs)}
          color={countdownColor}
        />
      )}

      <Divider />

      {/* Today's P&L — visible from every route */}
      <PnLSeg label="Realised"   value={totals.realised} />
      <PnLSeg label="Unrealised" value={totals.unrealised} />
      <PnLSeg label="Total"      value={totals.total} strong />

      <Divider />

      {/* Right — IST clock */}
      <div style={{
        padding:   "0 14px",
        fontSize:  11,
        color:     TEXT,
        fontWeight: 500,
        fontVariantNumeric: "tabular-nums",
        letterSpacing: "0.3px",
      }}>
        {formatTime(now)} IST
      </div>

    </div>
  );
}
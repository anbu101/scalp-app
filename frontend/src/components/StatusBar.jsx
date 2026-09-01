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
import { useEntitlements } from "../hooks/useEntitlements";
import { getVersion } from "@tauri-apps/api/app";
// ── CAS_2026 ── single source of truth for session boundaries
import { MARKET_START_MIN, FNO_END_MIN } from "../marketSession";

// ── CAS_2026 ── These were hardcoded as { h: 15, m: 30 }. That object-literal
// shape is why the original close-time sweep missed this file: it matches
// neither "15:30" nor 15*60+30 nor 930. Confirmed live on 2026-08-03 — the
// footer read "closes in 3m" at 15:27 and "Market Closed" from 15:31 while
// NFO options were still trading to 15:40. Now derived from the one source
// of truth. Do not re-inline.
const MARKET_START_MINS = MARKET_START_MIN;
const MARKET_END_MINS   = FNO_END_MIN;
const MARKET_START = { h: Math.floor(MARKET_START_MINS / 60), m: MARKET_START_MINS % 60 };
const MARKET_END   = { h: Math.floor(MARKET_END_MINS / 60),   m: MARKET_END_MINS % 60 };

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

// ── THEME_PHASE2A_20260831 ── theme-aware. Was a fixed dark palette, so the bar stayed
// dark under the light theme. Names kept; values now follow <html data-theme>.
const BG      = "var(--c-bg-secondary)";
const SUCCESS = "var(--c-success)";
const WARNING = "var(--c-warning)";
const DANGER  = "var(--c-danger)";
const PRIMARY = "var(--c-primary)";
const MUTED   = "var(--c-text-muted)";
const TEXT    = "var(--c-text-tertiary)";
const BORDER  = "var(--c-border-dark)";
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

  // License expiry (from /system/license via the shared entitlements hook)
  const { license } = useEntitlements();
  const expiresAt = license?.license_expires_at || null;
  const expDays = expiresAt
    ? Math.ceil((new Date(expiresAt + "T23:59:59") - Date.now()) / 86400000)
    : null;
  const expColor =
    expDays == null ? MUTED : expDays < 0 ? DANGER : expDays <= 7 ? WARNING : MUTED;


  // App version from tauri.conf.json (baked in at build time by deploy-scalp.command)
  const [appVersion, setAppVersion] = useState(null);
  useEffect(() => {
    getVersion().then(setAppVersion).catch(() => setAppVersion(null));
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      setNow(new Date());
      setCountdown(getCountdownSeconds());
      setMarketState(getMarketState());
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const { backendUp, engineRunning, trading, zerodhaConnected,
          angelConfigured, angelConnected } = health;   // ACC2_D9

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

  // ── ACC2_D9 ── Account 2 (Angel One) — segment exists ONLY when the
  // account is configured, so single-account users see an unchanged bar.
  const angelColor = !backendUp ? MUTED : angelConnected ? SUCCESS : DANGER;
  const angelLabel = !backendUp ? "—" : angelConnected ? "Angel" : "Angel Off";

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
      {angelConfigured && ( /* ACC2_D9 */
        <Seg dot color={angelColor} value={angelLabel} dimmed={!backendUp} />
      )}
      <Seg dot color={tradingColor} value={tradingLabel} dimmed={!engineRunning} />

      {/* App version — read from tauri.conf.json at build time */}
      {appVersion && (
        <div style={{
          display:      "flex",
          alignItems:   "center",
          padding:      "0 12px",
          borderRight:  `1px solid ${BORDER}`,
          height:       "100%",
          userSelect:   "none",
        }}>
          <span style={{
            fontSize:       10,
            color:          MUTED,
            fontFamily:     MONO,
            fontWeight:     500,
            letterSpacing:  "0.3px",
            fontVariantNumeric: "tabular-nums",
          }}>
            v{appVersion}
          </span>
        </div>
      )}

      {/* License expiry — amber within 7 days, red past due */}
      {expiresAt && (
        <Seg
          label="license"
          value={`${expiresAt} · ${expDays >= 0 ? expDays + "d" : "expired"}`}
          color={expColor}
        />
      )}

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
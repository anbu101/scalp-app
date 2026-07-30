/* frontend/src/marketSession.js
 *
 * ── CAS_2026 ──────────────────────────────────────────────────────────────
 * SINGLE SOURCE OF TRUTH for NSE session boundaries in the frontend.
 *
 * Created 2026-07-30 for the NSE Closing Auction Session (CAS) rollout
 * effective 2026-08-03. Before this file, the close bound (930) was duplicated
 * across App.jsx, BBPanel.jsx, HAPanel.jsx, Dashboard.jsx, Backtest.jsx and
 * Settings.jsx — six sites, five files, each needing a manual edit. Same class
 * of footgun as the diverging paramFormat.js copies. Import from here instead.
 *
 * WHAT CHANGED AT THE EXCHANGE ON 2026-08-03
 *   Equity DERIVATIVES (NFO — all this app trades) close 15:40, was 15:30.
 *   Market OPEN is unchanged at 09:15. Pre-open unchanged.
 *   Equity CASH for F&O-underlying stocks stops continuous trading at 15:15;
 *   the Closing Auction Session runs 15:15 → 15:35 and fixes the official
 *   closing price. Non-F&O cash stocks still close 15:30.
 *
 * TWO CLOCKS, NEVER ONE
 *   Option/futures-LTP-driven UI (open/closed badges, poll cadence, candle
 *   session filters, P&L windows)      → FNO_END_MIN (15:40).
 *   Anything keyed to the INDEX being continuously traded → CAS_START_MIN
 *   (15:15). Between 15:15 and 15:35 the index is an indicative auction value,
 *   and after ~15:35 it is expected to stop updating while options still
 *   trade. Never use FNO_END_MIN for a "the spot feed looks dead" check.
 *   Backend mirror of this doctrine: app/utils/market_hours.py.
 *
 * All values are MINUTE-OF-DAY in IST. The trading machine runs IST, so
 * Date#getHours() is IST; helpers that receive epoch timestamps from the
 * backend (which emits UTC) must use the ist* helpers below instead.
 * ─────────────────────────────────────────────────────────────────────────── */

/* ── Session boundaries (minute-of-day, IST) ── */
export const MARKET_START_MIN = 9 * 60 + 15;   // 555 — 09:15, unchanged
export const FNO_END_MIN      = 15 * 60 + 40;  // 940 — 15:40 NFO close
export const CASH_END_MIN     = 15 * 60 + 30;  // 930 — 15:30 non-CAS cash close
export const CAS_START_MIN    = 15 * 60 + 15;  // 915 — 15:15 auction begins
export const CAS_END_MIN      = 15 * 60 + 35;  // 935 — 15:35 close price fixed

/* Length of the tradable derivatives session, for progress bars. */
export const MARKET_DURATION_MIN = FNO_END_MIN - MARKET_START_MIN; // 385

/* String forms for <input type="time"> min/max and config defaults. */
export const MARKET_START_HM = "09:15";
export const FNO_END_HM      = "15:40";
export const CASH_END_HM     = "15:30";

/* IST is UTC+5:30, fixed — no DST. */
export const IST_OFFSET_MIN = 5 * 60 + 30;

/* ── Epoch-based helpers (backend emits UTC timestamps) ── */

/** { dow, min } in IST for an epoch-SECONDS timestamp. */
export function istParts(ts) {
  const istMs = ts * 1000 + IST_OFFSET_MIN * 60 * 1000;
  const d = new Date(istMs);
  return { dow: d.getUTCDay(), min: d.getUTCHours() * 60 + d.getUTCMinutes() };
}

/** Minute-of-day (IST) for an epoch-SECONDS timestamp. */
export function minuteOfDay(ts) {
  return istParts(ts).min;
}

/** Stable per-trading-day key (IST) for grouping candles. */
export function dayKey(ts) {
  const istMs = ts * 1000 + IST_OFFSET_MIN * 60 * 1000;
  const d = new Date(istMs);
  return `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
}

/* ── Wall-clock helpers (local time on the trading machine is IST) ── */

function nowParts() {
  const d = new Date();
  return { dow: d.getDay(), min: d.getHours() * 60 + d.getMinutes() };
}

function isWeekday(dow) {
  return dow !== 0 && dow !== 6;
}

/**
 * True while the equity DERIVATIVES segment is open (09:15–15:40 IST,
 * weekdays). Correct gate for option-LTP-driven UI: open/closed badges,
 * poll cadence, live P&L.
 */
export function isMarketOpen() {
  const { dow, min } = nowParts();
  return isWeekday(dow) && min >= MARKET_START_MIN && min < FNO_END_MIN;
}

/**
 * True while the index is computed from CONTINUOUSLY TRADED constituents
 * (09:15–15:15 IST, weekdays). Use for anything that assumes live spot.
 */
export function isSpotContinuousSession() {
  const { dow, min } = nowParts();
  return isWeekday(dow) && min >= MARKET_START_MIN && min < CAS_START_MIN;
}

/** True while the closing auction is running (15:15–15:35 IST, weekdays). */
export function isInCasWindow() {
  const { dow, min } = nowParts();
  return isWeekday(dow) && min >= CAS_START_MIN && min < CAS_END_MIN;
}

/** 0–100 progress through the derivatives session, for the nav progress bar. */
export function getMarketProgress() {
  const now  = new Date();
  const mins = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  if (mins < MARKET_START_MIN) return 0;
  if (mins >= FNO_END_MIN)     return 100;
  return ((mins - MARKET_START_MIN) / MARKET_DURATION_MIN) * 100;
}

/**
 * Keep only candles inside the tradable session, weekdays only.
 * Bound is FNO_END_MIN so the 15:30–15:40 tail that exists from 2026-08-03
 * is RETAINED — it was previously dropped silently along with any signal
 * marker riding on those candles.
 */
export function filterToSession(candles) {
  if (!Array.isArray(candles)) return [];
  return candles.filter((c) => {
    if (c == null || c.ts == null) return false;
    const { dow, min } = istParts(c.ts);
    if (!isWeekday(dow)) return false;
    return min >= MARKET_START_MIN && min < FNO_END_MIN;
  });
}

/** "HH:MM" → minute-of-day. Returns fallback on malformed input. */
export function hmToMin(hm, fallback = null) {
  if (typeof hm !== "string") return fallback;
  const m = hm.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return fallback;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  if (h < 0 || h > 23 || mi < 0 || mi > 59) return fallback;
  return h * 60 + mi;
}

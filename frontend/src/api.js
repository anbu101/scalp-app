//frontend/src/api.js
// =====================================================
// API BASE RESOLUTION (SAFE & LAZY)
// SINGLE SOURCE OF TRUTH
// =====================================================

function resolveApiBase() {
  console.log("[API] === resolveApiBase called ===");
  console.log("[API] window.__SCALP_API_BASE__:", window.__SCALP_API_BASE__);
  console.log("[API] window.__TAURI__:", !!window.__TAURI__);

  // Desktop (Tauri injects this AFTER page load)
  if (
    typeof window !== "undefined" &&
    typeof window.__SCALP_API_BASE__ === "string"
  ) {
    console.log("[API] ✅ Using injected base:", window.__SCALP_API_BASE__);
    return window.__SCALP_API_BASE__;
  }

  // Tauri fallback
  if (typeof window !== "undefined" && window.__TAURI__) {
    console.log("[API] ✅ Tauri detected, using 47321");
    return "http://127.0.0.1:47321";
  }

  // Browser dev fallback - USE CURRENT HOSTNAME!
    if (typeof window !== "undefined") {
      const hostname = window.location.hostname;
      const base = `http://${hostname}:47321`;
      console.log("[API] ⚠️ Browser fallback:", base);
      return base;
    }

  console.error("[API] ❌ No valid API base resolved");
  return null;
}

// =====================================================
// CORE API HELPER
// =====================================================

async function api(path, options = {}) {
  const API_BASE = resolveApiBase();
  const url = `${API_BASE}${path}`;

  console.log("[API] →", options.method || "GET", url);

  if (!API_BASE) {
    throw new Error("API_BASE unresolved");
  }

  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  console.log("[API] ←", res.status, path);

  if (!res.ok) {
    const txt = await res.text();
    console.error("[API] ❌ Error:", txt);
    throw new Error(txt || `API error ${res.status}`);
  }

  const json = await res.json();
  console.log("[API] ✓ Response:", json);
  return json;
}

// =====================================================
// SYSTEM STATUS
// =====================================================

export const getStatus = async () => {
  const s = await api("/status");
  return {
    backend: s.backend,
    engine:  s.engine,
    market:  s.market,
    mode:    s.mode,
    version: s.version,
  };
};

// =====================================================
// ZERODHA — SINGLE SOURCE OF TRUTH
// =====================================================

// 🔒 Authoritative backend status
export const getZerodhaStatus = () =>
  api("/zerodha/status");

// 🔐 Login URL
export const getZerodhaLoginUrl = () =>
  api("/zerodha/login-url");

// 🔑 Save credentials
export const saveZerodhaCredentials = (api_key, api_secret) =>
  api("/zerodha/configure", {
    method: "POST",
    body: JSON.stringify({ api_key, api_secret }),
  });

// ▶ Enable / Disable trading
export const enableZerodhaTrading = () =>
  api("/zerodha/enable-trading", { method: "POST" });

export const disableZerodhaTrading = () =>
  api("/zerodha/disable-trading", { method: "POST" });

// 💰 Account balance (Zerodha funds). Degrades gracefully if no session.
export const getAccountBalance = async () => {
  try {
    return await api("/zerodha/funds");
  } catch {
    return { net: null, connected: false };
  }
};
// =====================================================
// STRATEGY CONFIG
//
// Both functions now accept strategyId as first argument.
// Callers: getStrategyConfig("SCALP_V1"), getStrategyConfig("BB_V1"), etc.
//
// MIGRATION NOTE for existing callers:
//   Old: getStrategyConfig()          → update to getStrategyConfig("SCALP_V1")
//   Old: saveStrategyConfig(config)   → update to saveStrategyConfig("SCALP_V1", config)
// =====================================================

export const getStrategyConfig = async (strategyId) => {
  const res = await api(`/api/config?strategy_id=${strategyId}`);
  return res?.config;
};

export const saveStrategyConfig = (strategyId, config) =>
  api("/api/save_config", {
    method: "POST",
    body: JSON.stringify({
      strategy_id: strategyId,
      config,
    }),
  });

// =====================================================
// TRADING / LOGS
// =====================================================

export const getActiveTrade = async () => {
  try {
    return await api("/trade/active");
  } catch {
    return null;
  }
};

export const getLogs = async () => {
  try {
    return await api("/logs");
  } catch {
    return [];
  }
};

// =====================================================
// SELECTION / POSITIONS
// =====================================================

export const getCurrentSelection = async (strategyId = "SCALP_V1") => {
  try {
    return await api(`/selection/current?strategy_id=${strategyId}`);
  } catch {
    return null;
  }
};

// getTradeState fetches all strategies in one call (backend returns everything).
// Pass strategyId to get back only that strategy's slots, with the
// "STRATEGY_ID:" prefix stripped so callers get clean slot keys.
//
// Raw backend key:  "SCALP_V1:CE_1"  →  returned key: "CE_1"
// Raw backend key:  "BB_V1:slot_1"   →  returned key: "slot_1"
//
// NOTE: /trade/state reflects the LIVE trade registry only. It does NOT see
// paper trades. SCALP_V1's panel uses getScalpV1State() below instead, which
// is paper-aware. BB still uses this (its panel reads the live registry).
export const getTradeState = async (strategyId) => {
  const all = await api("/trade/state");
  const prefix = `${strategyId}:`;
  const filtered = {};
  Object.entries(all).forEach(([key, value]) => {
    if (key.startsWith(prefix)) {
      filtered[key.slice(prefix.length)] = value;
    }
  });
  return filtered;
};

// getScalpV1State — paper-aware SCALP_V1 slot/trade state for its panel.
//
// Hits the dedicated /api/scalp_v1/state endpoint (live-first, paper-fallback)
// instead of the shared /trade/state, which is blind to paper trades. Returns
// the same prefix-stripped shape getTradeState produces, so ScalpPanel's
// existing symbol-keyed lookup works unchanged.
//
//   Raw backend key:  "SCALP_V1:CE_1"               → returned key: "CE_1"
//   Raw backend key:  "SCALP_V1:NIFTY2660923450CE"  → returned key: "NIFTY2660923450CE"
//
// (The panel matches trades to cards by the `symbol` field inside each entry,
//  so the stripped key string itself is not significant.)
export const getScalpV1State = async () => {
  const all = await api("/api/scalp_v1/state");
  const prefix = "SCALP_V1:";
  const filtered = {};
  Object.entries(all).forEach(([key, value]) => {
    if (key.startsWith(prefix)) {
      filtered[key.slice(prefix.length)] = value;
    }
  });
  return filtered;
};

export const getTodayTrades = () =>
  api("/trades/today");

export const getTodayPositions = () =>
  api("/positions/today");

export const getLastSignals = () =>
  api("/signals/last");

// =====================================================
// TRADE SIDE MODE
// =====================================================

export const getTradeSideMode = () =>
  api(`/api/trade_side_mode?strategy_id=SCALP_V1`);

export const setTradeSideMode = (mode) =>
  api("/api/trade_side_mode", {
    method: "POST",
    body: JSON.stringify({
      strategy_id: "SCALP_V1",
      mode,
    }),
  });

// =====================================================
// LICENSE
// =====================================================

export const getLicenseStatus = () =>
  api("/system/license");

export const getGlobalConfig = () =>
  api("/api/global_config");

export const setGlobalTradeSwitch = (trade_on) =>
  api("/api/global_config", {
    method: "POST",
    body: JSON.stringify({ trade_on }),
  });

export const activateLicense = (key) =>
  api("/system/license/activate", {
    method: "POST",
    body: JSON.stringify({ key }),
  });

// ═══════════════════════════════════════════════════════════
// TELEGRAM API HELPERS
//
// FIX: these three now use the local api() helper (single source of truth for
// base-URL resolution) instead of a separate `getApiBase` import. The previous
// `import { getApiBase } from "./api/base"` + direct fetch bypassed the Tauri
// __SCALP_API_BASE__ injection, so Telegram config silently failed on the
// packaged desktop app while working in browser dev. Now identical resolution
// to every other call.
//
// Multi-channel config shape (sent/received verbatim — api() passes through):
//   { bot_token, channels: [ { id,name,chat_id,enabled,strategy_filter[],
//     mode_filter, notifications{4}, schedule{enabled,start,end} }, ... ] }
// ═══════════════════════════════════════════════════════════

/**
 * Get saved Telegram configuration (normalized multi-channel shape).
 */
export const getTelegramConfig = () =>
  api("/api/telegram/config");

/**
 * Save Telegram configuration (multi-channel).
 */
export const saveTelegramConfig = (config) =>
  api("/api/telegram/config", {
    method: "POST",
    body: JSON.stringify(config),
  });

/**
 * Test Telegram connection by sending a test message to a specific chat.
 */
export const testTelegramConnection = (botToken, chatId) =>
  api("/api/telegram/test", {
    method: "POST",
    body: JSON.stringify({ bot_token: botToken, chat_id: chatId }),
  });

// getScalpV3State — SCALP_V3 panel state (selection + the single two-instrument
// open trade). V3 has no slot model; it returns the open trade with BOTH the
// signal contract (tracked) and the hedge contract (bought), plus the
// under-surveillance selection. See /api/scalp_v3/state.
export const getScalpV3State = async () => {
  try {
    return await api("/api/scalp_v3/state");
  } catch {
    return { mode: "PAPER", selection: { CE: [], PE: [] }, open_trade: null };
  }
};

export const getScalpV5State = async () => {
  try {
    return await api("/api/scalp_v5/state");
  } catch {
    return { mode: "PAPER", selection: { CE: [], PE: [] }, open_trade: null };
  }
};

// ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
// IC: time-entry NIFTY weekly iron condor. TWO instances share one panel
// and one route family: sid ∈ {IC_V1 (legacy EOD), IC_V2 (NEXT_OPEN+ADJ)}.
// Group + legs state for the dashboard panel. See /api/ic/{sid}/state.
export const getICState = async (sid) => {
  try {
    return await api(`/api/ic/${sid}/state`);
  } catch {
    return { mode: "OFF", engine_up: false, group: null,
             entry_time: "09:18", exit_time: "15:28", latched_today: false };
  }
};

// Manual square-off (reason=MANUAL) — same close path as EOD; safe no-op
// when nothing is open. NO window.confirm (blocked in Tauri webview): the
// panel uses a two-tap arm/confirm button + inline status banner instead.
export const squareOffIC = (sid) =>
  api(`/api/ic/${sid}/square_off`, { method: "POST" });
// ── IC END ──

// ── TSG_V1 BEGIN ── 09:16 time-entry weekly strangle (Phase 1, LD9).
export const getTSGV1State = async () => {
  try {
    return await api("/api/tsg_v1/state");
  } catch {
    return { mode: "OFF", engine_up: false, day: null,
             entry_time: "09:16", exit_time: "15:26", lots: 1,
             expiry_lots: 0, mtm_sl: 35000, latched_today: false };
  }
};
export const squareOffTSGV1 = () =>
  api("/api/tsg_v1/square_off", { method: "POST" });
// ── TSG_V1 END ──

// ── GC_V1 BEGIN ── Glacier: NIFTY 1m breakout-retest with SL-flip chain.
export const getGCV1State = async () => {
  try {
    return await api("/api/gc_v1/state");
  } catch {
    return { mode: "OFF", engine_up: false, day: null, gc_mode: "SELL",
             exit_time: "15:15", entry_cutoff_time: "13:00",
             premium_max: 200, hedge_premium_max: 5, lots: 1,
             max_trades_per_day: 4 };
  }
};
export const squareOffGCV1 = () =>
  api("/api/gc_v1/square_off", { method: "POST" });
// ── GC_V1 END ──

// ── KILL_SWITCH BEGIN ── per-strategy emergency stop. Eligibility is one
// call for ALL strategies (KillSwitch polls it); the POST returns the full
// report {ok, closed, remaining, mode_flipped, detail[]} — the backend only
// flips mode → PAPER after verifying flat.
export const getKillEligibility = async () => {
  try {
    return await api("/api/kill/eligibility");
  } catch {
    return {};
  }
};

export const killStrategy = (strategyId) =>
  api(`/api/kill/${strategyId}`, { method: "POST" });
// ── KILL_SWITCH END ──

export const getSystemVersion = () =>
  api("/system/version");
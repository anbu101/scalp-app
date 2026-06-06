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

// ═══════════════════════════════════════════════════════════
// TELEGRAM API HELPERS
// Add these functions to your existing src/api.js or src/api/index.js
// ═══════════════════════════════════════════════════════════

import { getApiBase } from "./api/base"; // Adjust import based on your structure

/**
 * Get saved Telegram configuration
 */
export async function getTelegramConfig() {
  const res = await fetch(`${getApiBase()}/api/telegram/config`);
  if (!res.ok) throw new Error("Failed to load Telegram config");
  return res.json();
}

/**
 * Save Telegram configuration
 */
export async function saveTelegramConfig(config) {
  const res = await fetch(`${getApiBase()}/api/telegram/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error("Failed to save Telegram config");
  return res.json();
}

/**
 * Test Telegram connection by sending a test message
 */
export async function testTelegramConnection(botToken, chatId) {
  const res = await fetch(`${getApiBase()}/api/telegram/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bot_token: botToken,
      chat_id: chatId,
    }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "Test failed");
  }
  return res.json();
}

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
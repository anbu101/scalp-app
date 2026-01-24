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

  // Browser dev fallback
  if (typeof window !== "undefined") {
    console.log("[API] ⚠️ Browser fallback to 8000");
    return "http://127.0.0.1:8000";
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
    engine: s.engine,
    market: s.market,
    mode: s.mode,
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

// =====================================================
// STRATEGY CONFIG
// =====================================================

export const getStrategyConfig = () =>
  api("/config/strategy");

export const saveStrategyConfig = (config) =>
  api("/config/strategy", {
    method: "POST",
    body: JSON.stringify(config),
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

export const getCurrentSelection = async () => {
  try {
    return await api("/selection/current");
  } catch {
    return null;
  }
};

export const getTradeState = () =>
  api("/trade/state");

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
  api("/api/trade_side_mode");

export const setTradeSideMode = (mode) =>
  api("/api/trade_side_mode", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });

// =====================================================
// LICENSE
// =====================================================

export const getLicenseStatus = () =>
  api("/system/license");

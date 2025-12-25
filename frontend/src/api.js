const API_BASE = "";

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt || `API error ${res.status}`);
  }
  return res.json();
}

/* ---------------------
   Status
--------------------- */

export const getStatus = () =>
  api("/status");

/* ---------------------
   Zerodha (TRADING)
--------------------- */

export const getZerodhaStatus = () =>
  api("/zerodha/status");

export const getZerodhaLoginUrl = () =>
  api("/zerodha/login-url");

export const enableTrading = () =>
  api("/zerodha/enable-trading", { method: "POST" });

export const disableTrading = () =>
  api("/zerodha/disable-trading", { method: "POST" });

/* ---------------------
   Zerodha (CREDENTIALS)
--------------------- */

export const getZerodhaConfig = () =>
  api("/api/zerodha");

export const saveZerodhaConfig = (api_key, api_secret) =>
  api("/api/zerodha", {
    method: "POST",
    body: JSON.stringify({ api_key, api_secret }),
  });

/* ---------------------
   Trading / Logs
--------------------- */

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

/* ---------------------
   Option Selection
--------------------- */

export const getCurrentSelection = async () => {
  try {
    return await api("/selection/current");
  } catch {
    return null;
  }
};

/* ---------------------
   Strategy Config
--------------------- */

export const getStrategyConfig = () =>
  api("/config/strategy");

export const saveStrategyConfig = (config) =>
  api("/config/strategy", {
    method: "POST",
    body: JSON.stringify(config),
  });

export const getTradeState = () =>
  api("/trade/state");

export const getTodayTrades = () =>
  api("/trades/today");

export const getTodayPositions = () =>
  api("/positions/today");

export const getLastSignals = () =>
  api("/signals/last");

/* ---------------------
   Trade Side Mode
--------------------- */

export async function getTradeSideMode() {
  return api("/api/trade_side_mode");
}

export async function setTradeSideMode(mode) {
  return api("/api/trade_side_mode", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

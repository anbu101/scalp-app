import { useEffect, useState } from "react";
import {
  getZerodhaStatus,
  getZerodhaLoginUrl,
  enableTrading,
  disableTrading,
  getZerodhaConfig,
  saveZerodhaConfig,
} from "../api";

export default function ZerodhaLogin() {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [editingCreds, setEditingCreds] = useState(false);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    setLoading(true);
    try {
      const [cfg, st] = await Promise.all([
        getZerodhaConfig(),
        getZerodhaStatus(),
      ]);

      setConfig(cfg);
      setStatus(st);

      if (cfg?.api_key) {
        setApiKey(cfg.api_key);
      }
    } catch (e) {
      console.error(e);
      setConfig(null);
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }

  async function saveCredentials() {
    if (!apiKey || !apiSecret) {
      alert("API Key and API Secret are required");
      return;
    }

    await saveZerodhaConfig(apiKey, apiSecret);
    alert("Credentials saved. Please login to Zerodha.");
    
    setApiSecret("");
    setEditingCreds(false);
    
    // reset stale state before refresh
    setStatus(null);
    
    await refresh();
    
  }

  async function login() {
    const { login_url } = await getZerodhaLoginUrl();
    const w = window.open(login_url, "_blank");
  
    // 🔁 Poll until window closes, then refresh
    const timer = setInterval(() => {
      if (w.closed) {
        clearInterval(timer);
        refresh();
      }
    }, 1000);
  }
  
  async function enable() {
    await enableTrading();
    await refresh();
  }

  async function disable() {
    await disableTrading();
    await refresh();
  }

  if (loading) {
    return <p style={{ padding: 24 }}>Checking Zerodha status…</p>;
  }

  // -----------------------------
  // Derived state (single truth)
  // -----------------------------
  const configured = config?.configured === true;
  const connected = status?.connected === true;
  const tradingEnabled = status?.trading_enabled === true;
  const sessionExpired = status?.session_expired === true;
  const loginAt = status?.login_at;

  return (
    <div style={{ padding: 24, maxWidth: 560 }}>
      <h2>Zerodha Trading Control</h2>

      {/* =============================
          STATE 1: NOT CONFIGURED
      ============================= */}
      {!configured && (
        <>
          <p>❌ Zerodha not configured</p>

          <input
            placeholder="API Key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            style={{ width: "100%", marginBottom: 8 }}
          />

          <input
            placeholder="API Secret"
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            style={{ width: "100%", marginBottom: 12 }}
          />

          <button onClick={saveCredentials}>
            Save Zerodha Credentials
          </button>
        </>
      )}

      {/* =============================
          STATE 2: CONFIGURED, NO SESSION
      ============================= */}
      {configured && !connected && !editingCreds && (
        <>
          <p>⚙️ Zerodha configured</p>

          {sessionExpired ? (
            <p style={{ color: "#ef4444" }}>
              ⏰ Session expired – login again
            </p>
          ) : (
            <p style={{ color: "#f59e0b" }}>
              🔑 Login required (no active session)
            </p>
          )}

          <button onClick={login}>Login to Zerodha</button>{" "}
          <button onClick={() => setEditingCreds(true)}>
            Edit Credentials
          </button>
        </>
      )}

      {/* =============================
          STATE 2a: EDITING CREDENTIALS
      ============================= */}
      {configured && editingCreds && (
        <>
          <p>✏️ Edit Zerodha Credentials</p>

          <input
            placeholder="API Key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            style={{ width: "100%", marginBottom: 8 }}
          />

          <input
            placeholder="API Secret"
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            style={{ width: "100%", marginBottom: 12 }}
          />

          <button onClick={saveCredentials}>Save</button>{" "}
          <button onClick={() => setEditingCreds(false)}>
            Cancel
          </button>
        </>
      )}

      {/* =============================
          STATE 3: CONNECTED
      ============================= */}
      {configured && connected && (
        <>
          <p>✅ Zerodha connected</p>

          {loginAt && (
            <p style={{ fontSize: 13, color: "#94a3b8" }}>
              Logged in at: {new Date(loginAt).toLocaleString()}
            </p>
          )}

          <p>
            Trading Status:{" "}
            <b style={{ color: tradingEnabled ? "green" : "orange" }}>
              {tradingEnabled ? "ENABLED" : "DISABLED"}
            </b>
          </p>

          {tradingEnabled ? (
            <button onClick={disable}>Disable Trading</button>
          ) : (
            <button onClick={enable} disabled={!connected}>
              Enable Trading
            </button>
          )}

          <div style={{ marginTop: 12 }}>
            <button onClick={() => setEditingCreds(true)}>
              Edit Credentials
            </button>
          </div>
        </>
      )}

      <div style={{ marginTop: 24 }}>
        <button onClick={refresh}>Refresh Status</button>
      </div>
    </div>
  );
}

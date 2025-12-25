import { useEffect, useState } from "react";
import {
  getZerodhaStatus,
  getZerodhaLoginUrl,
  disconnectZerodha
} from "../api";

export default function ZerodhaLogin() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    try {
      setStatus(await getZerodhaStatus());
    } catch {
      setStatus({ connected: false });
    }
  }

  async function login() {
    setLoading(true);
    const { login_url } = await getZerodhaLoginUrl();
    window.location.href = login_url;
  }

  async function disconnect() {
    await disconnectZerodha();
    await refresh();
  }

  return (
    <div style={{ padding: 20 }}>
      <h2>Zerodha Login</h2>

      {status ? (
        status.connected ? (
          <p style={{ color: "lightgreen" }}>✔ Connected to Zerodha</p>
        ) : (
          <p style={{ color: "red" }}>❌ Not connected to Zerodha</p>
        )
      ) : (
        <p>Checking status…</p>
      )}

      <br />

      {!status?.connected && (
        <button onClick={login} disabled={loading}>
          Login to Zerodha
        </button>
      )}

      {status?.connected && (
        <>
            <p style={{ color: "lightgreen" }}>✔ Connected to Zerodha</p>
            <p>User ID: {status.user_id}</p>
            <p>User Name: {status.user_name}</p>
        </>
    )}

    </div>
  );
}

/* frontend/src/components/LicenseGate.jsx
 *
 * PHASE 2 - NEW FILE.
 * Wrap the app's main content with this gate (one line in App.jsx):
 *
 *     <LicenseGate>
 *       ...existing app...
 *     </LicenseGate>
 *
 * Behavior:
 *   - VALID / GRACE          -> renders children (normal app)
 *   - UNACTIVATED            -> activation screen (key entry)
 *   - EXPIRED/REVOKED/
 *     INVALID/CLOCK_TAMPER   -> blocked screen + key re-entry, polls every
 *                               60s so a server-side extend/unrevoke
 *                               unblocks automatically (heartbeat revives
 *                               the backend state; this gate follows)
 *   - status fetch fails     -> renders children (backend still booting;
 *                               BackendBootGuard owns that phase)
 *   - successful activation  -> "Restart the app" screen (Option A:
 *                               strategies launch at startup only)
 */
import { useCallback, useEffect, useState } from "react";
import { activateLicense, getLicenseStatus } from "../api";

const BLOCKING = ["UNACTIVATED", "EXPIRED", "REVOKED", "INVALID", "CLOCK_TAMPER"];
const POLL_MS = 60 * 1000;

const S = {
  wrap: {
    minHeight: "100vh", display: "flex", alignItems: "center",
    justifyContent: "center", background: "#0d1117", color: "#e6edf3",
    fontFamily: "system-ui, -apple-system, sans-serif", padding: "24px",
  },
  card: {
    width: "100%", maxWidth: "420px", background: "#161b22",
    border: "1px solid #30363d", borderRadius: "12px", padding: "28px",
    textAlign: "center",
  },
  h: { margin: "0 0 8px", fontSize: "20px", fontWeight: 600 },
  p: { margin: "0 0 20px", fontSize: "14px", color: "#9da7b3", lineHeight: 1.5 },
  input: {
    width: "100%", boxSizing: "border-box", padding: "12px",
    fontSize: "16px", letterSpacing: "1px", textAlign: "center",
    background: "#0d1117", color: "#e6edf3", border: "1px solid #30363d",
    borderRadius: "8px", outline: "none", textTransform: "uppercase",
  },
  btn: {
    width: "100%", marginTop: "14px", padding: "12px", fontSize: "15px",
    fontWeight: 600, background: "#238636", color: "#fff", border: "none",
    borderRadius: "8px", cursor: "pointer",
  },
  err: { marginTop: "12px", fontSize: "13px", color: "#ffb4b4" },
  ok: { marginTop: "12px", fontSize: "13px", color: "#7ee787" },
};

export default function LicenseGate({ children }) {
  const [license, setLicense] = useState(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activated, setActivated] = useState(false);

  const refresh = useCallback(() => {
    getLicenseStatus()
      .then(setLicense)
      .catch(() => setLicense(null));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await activateLicense(key.trim().toUpperCase());
      if (res.status === "ok") {
        setActivated(true);
      } else {
        setError(res.message || `Activation failed (${res.status})`);
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  // Activation just succeeded -> restart instruction (Option A)
  if (activated) {
    return (
      <div style={S.wrap}>
        <div style={S.card}>
          <h2 style={S.h}>✅ License activated</h2>
          <p style={S.p}>
            Please <b>quit and reopen Scalp Terminal</b> to start your
            licensed strategies.
          </p>
        </div>
      </div>
    );
  }

  // Backend booting / unreachable -> let the app's own boot guard handle it
  if (!license) return children;

  // Usable -> normal app (LicenseBanner shows GRACE warnings)
  if (!BLOCKING.includes(license.status)) return children;

  const unactivated = license.status === "UNACTIVATED";

  return (
    <div style={S.wrap}>
      <div style={S.card}>
        <h2 style={S.h}>
          {unactivated ? "Activate Scalp Terminal" : "🔒 License issue"}
        </h2>
        <p style={S.p}>
          {unactivated
            ? "Enter the license key you received (format SCLP-XXXX-XXXX-XXXX)."
            : `${license.message || "License is not valid."} If this was just
               fixed by the admin, this screen clears automatically within a
               minute. You can also re-enter a key below.`}
        </p>
        <input
          style={S.input}
          placeholder="SCLP-XXXX-XXXX-XXXX"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && submit()}
          spellCheck={false}
        />
        <button style={{ ...S.btn, opacity: busy ? 0.6 : 1 }} onClick={submit} disabled={busy}>
          {busy ? "Activating…" : "Activate"}
        </button>
        {error && <div style={S.err}>{error}</div>}
      </div>
    </div>
  );
}
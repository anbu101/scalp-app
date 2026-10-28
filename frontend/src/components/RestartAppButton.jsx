// frontend/src/components/RestartAppButton.jsx
// ── APP_RESTART_20261028 ── "Restart App" next to the trading switch on the
// Connections page. It asks for confirmation, then:
//   * desktop window: Tauri relaunch() restarts the whole app (the new
//     instance clears port 47321 and starts a fresh backend);
//   * phone / browser: POST /api/system/restart. The backend starts its
//     replacement and exits; this page polls /api/system/boot-id and reloads
//     once a different process answers.
import { useEffect, useRef, useState } from "react";
import { colors, alpha } from "../tokens";
import { getApiBase } from "../api/base";

const POLL_MS = 2000;
const GIVE_UP_MS = 120000;

function nativeRelaunch() {
  try {
    return window.__TAURI__?.process?.relaunch || null;
  } catch (e) {
    return null;
  }
}

// Mon–Fri 09:15–15:30 IST (UTC+5:30 fixed, no DST).
function marketOpenIST(now = new Date()) {
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  const d = ist.getDay();
  const m = ist.getHours() * 60 + ist.getMinutes();
  return d >= 1 && d <= 5 && m >= 9 * 60 + 15 && m < 15 * 60 + 30;
}

async function fetchJson(path, options = {}) {
  const res = await fetch(`${getApiBase()}${path}`, {
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

const btnBase = {
  padding: "8px 16px", borderRadius: 6, border: "none", fontSize: 13,
  fontWeight: 600, cursor: "pointer", minHeight: 36,
};

export default function RestartAppButton({ block = false }) {
  const [stage, setStage] = useState("idle"); // idle | confirm | restarting | slow | error
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const timers = useRef([]);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  function later(fn, ms) {
    timers.current.push(setTimeout(fn, ms));
  }

  function waitForNewBackend(oldId, started) {
    const tick = async () => {
      const ms = Date.now() - started;
      setElapsed(Math.round(ms / 1000));
      try {
        const b = await fetchJson("/api/system/boot-id");
        if (b.boot_id && b.boot_id !== oldId) {
          window.location.reload();
          return;
        }
      } catch (e) {
        /* backend down mid-restart — expected */
      }
      if (ms > GIVE_UP_MS) {
        setStage("slow");
        return;
      }
      later(tick, POLL_MS);
    };
    later(tick, POLL_MS);
  }

  async function restartViaBackend() {
    let oldId = null;
    try {
      oldId = (await fetchJson("/api/system/boot-id")).boot_id;
    } catch (e) {
      /* older backend without boot-id: the restart call below reports it */
    }
    const r = await fetchJson("/api/system/restart", {
      method: "POST",
      body: JSON.stringify({ confirm: "RESTART" }),
    });
    waitForNewBackend(r.boot_id || oldId, Date.now());
  }

  async function doRestart() {
    setError("");
    setElapsed(0);
    setStage("restarting");
    const relaunch = nativeRelaunch();
    try {
      if (relaunch) {
        try {
          await relaunch();       // the process is replaced; nothing below runs
          return;
        } catch (e) {
          console.warn("[RESTART] relaunch failed, restarting the backend instead", e);
        }
      }
      await restartViaBackend();
    } catch (e) {
      setError(e.message || String(e));
      setStage("error");
    }
  }

  const open = marketOpenIST();
  const isDesktop = !!nativeRelaunch();

  return (
    <>
      <button
        type="button"
        onClick={() => setStage("confirm")}
        style={{
          ...btnBase,
          background: colors.bg.tertiary,
          color: colors.text.secondary,
          border: `1px solid ${colors.border.medium}`,
          ...(block ? { width: "100%" } : {}),
        }}
        title="Close and reopen Scalp Terminal"
      >
        ↻ Restart App
      </button>

      {stage !== "idle" && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="restart-app-title"
          onClick={(e) => e.target === e.currentTarget && stage === "confirm" && setStage("idle")}
          style={{
            position: "fixed", inset: 0, zIndex: 10000,
            background: alpha("#000000", 60),
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 16,
          }}
        >
          <div style={{
            width: "100%", maxWidth: 420, background: colors.bg.secondary || colors.bg.page,
            border: `1px solid ${colors.border.medium}`, borderRadius: 10, padding: 20,
            color: colors.text.primary, boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
          }}>
            <h3 id="restart-app-title" style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
              {stage === "confirm" && "Restart Scalp Terminal?"}
              {stage === "restarting" && "Restarting…"}
              {stage === "slow" && "Still restarting"}
              {stage === "error" && "Restart failed"}
            </h3>

            {stage === "confirm" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  The app closes and starts again, which takes about 20–40 seconds.
                  Settings, strategies and open positions load back from disk. Nothing
                  is squared off.
                </p>
                {open && (
                  <p style={{
                    fontSize: 13, lineHeight: 1.5, margin: "12px 0 0", padding: "8px 10px",
                    borderRadius: 6, color: colors.warning,
                    background: alpha(colors.warning, 12),
                    border: `1px solid ${alpha(colors.warning, 40)}`,
                  }}>
                    The market is open. Live positions stay open at the broker, but
                    app-managed exits and stop checks pause until the app is back.
                  </p>
                )}
                {!isDesktop && (
                  <p style={{ fontSize: 12, color: colors.text.muted, margin: "10px 0 0" }}>
                    This page reloads by itself when the app is back.
                  </p>
                )}
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18, flexWrap: "wrap" }}>
                  <button type="button" onClick={() => setStage("idle")}
                    style={{ ...btnBase, background: colors.bg.tertiary, color: colors.text.secondary }}>
                    Cancel
                  </button>
                  <button type="button" onClick={doRestart} autoFocus
                    style={{ ...btnBase, background: colors.danger, color: colors.text.primary }}>
                    Restart app
                  </button>
                </div>
              </>
            )}

            {stage === "restarting" && (
              <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                Waiting for the app to come back{elapsed ? ` (${elapsed}s)` : ""}. Keep this
                page open; it reloads by itself.
              </p>
            )}

            {stage === "slow" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  The app has not answered for 2 minutes. Check Scalp Terminal on the
                  computer: if it is closed, open it there.
                </p>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
                  <button type="button" onClick={() => window.location.reload()}
                    style={{ ...btnBase, background: colors.primary, color: colors.text.primary }}>
                    Reload page
                  </button>
                </div>
              </>
            )}

            {stage === "error" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  {error}. The app was not restarted and is still running.
                </p>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
                  <button type="button" onClick={() => setStage("idle")}
                    style={{ ...btnBase, background: colors.bg.tertiary, color: colors.text.secondary }}>
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

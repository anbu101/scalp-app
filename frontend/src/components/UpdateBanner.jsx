// >>> AUTO_UPDATER_20260821 BEGIN (full-file rewrite of UpdateBanner) <<<
//
// Two sources of truth for "update available":
//   1. NATIVE (desktop app): tauri-plugin-updater checks
//      latest-{{target}}-{{arch}}.json on the scalp-releases repo, and can
//      download + verify + install + relaunch in one click. Used whenever
//      window.__TAURI__.updater exists.
//   2. ADVISORY (browser / mobile via Tailscale, or updater unavailable):
//      the old license-server min_version nag via /system/version. Button
//      just opens the releases page, exactly as before.
//
// Safety rules baked in:
//   - NEVER window.confirm/alert (silently blocked in Tauri webview) —
//     two-tap arm/confirm instead, auto-disarm after 6s.
//   - Market-hours guard (Mon–Fri 09:00–15:30 IST, fixed +5:30 offset, no
//     DST): update is soft-blocked — the confirm tap shows a loud warning
//     but the user can still proceed deliberately. Relaunch never happens
//     without two explicit taps.
//   - Any updater error falls back to the manual releases link. Fail open,
//     never disturb the app.

import { useEffect, useState, useRef } from "react";
import { getSystemVersion } from "../api";
import { colors } from "../tokens";

const FAST_RETRY_MS = 3000;
const SLOW_POLL_MS = 30 * 60 * 1000;
const ARM_TIMEOUT_MS = 6000;

const RELEASES_URL = "https://github.com/anbu101/scalp-releases/releases/latest";

function nativeUpdater() {
  try {
    return window.__TAURI__ && window.__TAURI__.updater ? window.__TAURI__.updater : null;
  } catch (e) {
    return null;
  }
}

function nativeRelaunch() {
  try {
    return window.__TAURI__ && window.__TAURI__.process
      ? window.__TAURI__.process.relaunch
      : null;
  } catch (e) {
    return null;
  }
}

// Mon–Fri 09:00–15:30 IST (UTC+5:30 fixed, no DST).
function isMarketHoursIST() {
  const now = new Date();
  const istMs = now.getTime() + (330 + now.getTimezoneOffset()) * 60000;
  const ist = new Date(istMs);
  const day = ist.getDay(); // 0 Sun .. 6 Sat
  if (day === 0 || day === 6) return false;
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return mins >= 9 * 60 && mins <= 15 * 60 + 30;
}

export default function UpdateBanner() {
  const [advisory, setAdvisory] = useState(null); // /system/version snapshot
  const [pendingUpdate, setPendingUpdate] = useState(null); // { version }
  const [phase, setPhase] = useState("idle"); // idle | armed | downloading | installing | error
  const [progressPct, setProgressPct] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [dismissed, setDismissed] = useState(false);

  const updateObjRef = useRef(null); // tauri Update instance
  const timerRef = useRef(null);
  const armTimerRef = useRef(null);
  const gotFirstRef = useRef(false);

  // phase mirror for the polling closure (stale-closure guard)
  const phaseRef = useRef(phase);
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    let cancelled = false;

    const schedule = (ms) => {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(load, ms);
    };

    const load = async () => {
      // 1) Advisory (always attempted; drives the browser fallback and the
      //    "you have vX" text).
      let advisoryOk = false;
      try {
        const data = await getSystemVersion();
        if (cancelled) return;
        setAdvisory(data);
        advisoryOk = true;
        gotFirstRef.current = true;
      } catch (e) {
        if (cancelled) return;
      }

      // 2) Native check (desktop only). Never during an in-flight install.
      const upd = nativeUpdater();
      if (upd && phaseRef.current !== "downloading" && phaseRef.current !== "installing") {
        try {
          const result = await upd.check();
          if (cancelled) return;
          if (result && result.version) {
            updateObjRef.current = result;
            setPendingUpdate({ version: result.version });
          } else {
            updateObjRef.current = null;
            setPendingUpdate(null);
          }
        } catch (e) {
          // Manifest missing / offline / repo issue — fail open, keep the
          // advisory-driven banner as the fallback path.
          if (cancelled) return;
        }
      }

      schedule(advisoryOk || gotFirstRef.current ? SLOW_POLL_MS : FAST_RETRY_MS);
    };

    load();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
      clearTimeout(armTimerRef.current);
    };
  }, []);

  const disarm = () => {
    clearTimeout(armTimerRef.current);
    setPhase("idle");
  };

  const onArm = () => {
    setErrorMsg("");
    setPhase("armed");
    clearTimeout(armTimerRef.current);
    armTimerRef.current = setTimeout(() => {
      setPhase((p) => (p === "armed" ? "idle" : p));
    }, ARM_TIMEOUT_MS);
  };

  const onConfirm = async () => {
    clearTimeout(armTimerRef.current);
    const updateObj = updateObjRef.current;
    if (!updateObj) {
      setPhase("idle");
      return;
    }
    try {
      setPhase("downloading");
      setProgressPct(0);
      let total = 0;
      let received = 0;
      await updateObj.downloadAndInstall((ev) => {
        if (!ev) return;
        if (ev.event === "Started") {
          total = (ev.data && ev.data.contentLength) || 0;
          received = 0;
          setProgressPct(0);
        } else if (ev.event === "Progress") {
          received += (ev.data && ev.data.chunkLength) || 0;
          if (total > 0) {
            setProgressPct(Math.min(99, Math.round((received / total) * 100)));
          }
        } else if (ev.event === "Finished") {
          setProgressPct(100);
        }
      });
      setPhase("installing");
      const relaunch = nativeRelaunch();
      if (relaunch) {
        await relaunch();
      } else {
        setErrorMsg("Installed. Please quit and reopen Scalp to finish the update.");
        setPhase("error");
      }
    } catch (e) {
      setPhase("error");
      setErrorMsg(
        "Auto-update failed (" +
          String((e && e.message) || e) +
          "). You can install manually from the releases page."
      );
    }
  };

  if (dismissed) return null;

  // >>> MIN_VERSION_GATE_20260826 BEGIN <<<
  // ADMIN GATE: banner visibility (native OR fallback) is controlled solely
  // by the license server's update_available flag (installed < Min App Ver
  // set in admin/ui). Deploying a GitHub release alone shows nothing; the
  // admin bumps Min App Ver to make the fleet see the update. If the license
  // server is unreachable, no banner shows (same admin-controlled fail-closed
  // behavior as the pre-updater banner). Exception: once a download/install
  // is in flight, keep the UI visible regardless, so progress is never
  // yanked away mid-install by an advisory refresh.
  const installing = phase === "downloading" || phase === "installing";
  const hasAdvisory = !!(advisory && advisory.update_available === true);
  if (!hasAdvisory && !installing) return null;
  const hasNative = !!pendingUpdate;
  // >>> MIN_VERSION_GATE_20260826 END <<<

  const marketHours = isMarketHoursIST();

  const currentV = advisory && advisory.current_version ? "v" + advisory.current_version : "";
  const message = hasNative
    ? "Scalp v" +
      pendingUpdate.version +
      " is available" +
      (currentV ? " (you have " + currentV + ")." : ".")
    : (advisory && advisory.message) || "A newer version of Scalp Terminal is available.";

  const wrapStyle = {
    background: phase === "armed" && marketHours ? colors.warningBg : "rgba(59,130,246,0.12)",
    color: colors.text.primary,
    borderBottom:
      "1px solid " + (phase === "armed" && marketHours ? colors.warning : colors.primary) + "55",
    padding: "8px 14px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    fontSize: 13,
    fontWeight: 500,
    lineHeight: 1.3,
    flexWrap: "wrap",
  };

  const btnStyle = {
    background: phase === "armed" ? (marketHours ? colors.warning : colors.danger) : colors.primary,
    color: colors.text.primary,
    border: "none",
    borderRadius: 5,
    padding: "4px 12px",
    fontSize: 12,
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "inherit",
    whiteSpace: "nowrap",
    textDecoration: "none",
    display: "inline-block",
  };

  const dismissStyle = {
    background: "none",
    border: "none",
    color: colors.text.muted,
    cursor: "pointer",
    fontSize: 16,
    lineHeight: 1,
    padding: "0 4px",
  };

  // ---- Native (desktop) rendering ----
  if (hasNative) {
    return (
      <div style={wrapStyle}>
        {phase === "idle" && (
          <>
            <span style={{ color: colors.text.secondary }}>{message}</span>
            <button onClick={onArm} style={btnStyle}>
              Update &amp; Restart
            </button>
          </>
        )}

        {phase === "armed" && (
          <>
            <span style={{ color: marketHours ? colors.warning : colors.text.secondary }}>
              {marketHours
                ? "\u26A0 MARKET HOURS — updating restarts the app and its strategies. Only proceed if you are flat / accept the restart."
                : "Restart Scalp now to install v" + pendingUpdate.version + "?"}
            </span>
            <button onClick={onConfirm} style={btnStyle}>
              {marketHours ? "Yes, update during market hours" : "Confirm update"}
            </button>
            <button onClick={disarm} style={{ ...btnStyle, background: "transparent", border: "1px solid " + colors.text.muted, fontWeight: 500 }}>
              Cancel
            </button>
          </>
        )}

        {phase === "downloading" && (
          <span style={{ color: colors.text.secondary }}>
            Downloading v{pendingUpdate.version}
            {progressPct !== null ? " — " + progressPct + "%" : "…"}
          </span>
        )}

        {phase === "installing" && (
          <span style={{ color: colors.text.secondary }}>
            Installing v{pendingUpdate.version}… the app will restart itself.
          </span>
        )}

        {phase === "error" && (
          <>
            <span style={{ color: colors.warning }}>{errorMsg}</span>
            <a href={RELEASES_URL} target="_blank" rel="noopener noreferrer" style={btnStyle}>
              Open releases page
            </a>
            <button onClick={() => setDismissed(true)} title="Dismiss until next launch" style={dismissStyle}>
              X
            </button>
          </>
        )}

        {(phase === "idle" || phase === "armed") && (
          <button onClick={() => setDismissed(true)} title="Dismiss until next launch" style={dismissStyle}>
            X
          </button>
        )}
      </div>
    );
  }

  // ---- Advisory fallback (browser / mobile / updater unavailable) ----
  return (
    <div style={wrapStyle}>
      <span style={{ color: colors.text.secondary }}>{message}</span>
      <a href={RELEASES_URL} target="_blank" rel="noopener noreferrer" style={btnStyle}>
        Get the update
      </a>
      <button onClick={() => setDismissed(true)} title="Dismiss until next launch" style={dismissStyle}>
        X
      </button>
    </div>
  );
}
// >>> AUTO_UPDATER_20260821 END <<<
/* frontend/src/components/UpdateBanner.jsx
 *
 * Soft "update available" banner. Reads GET /system/version (served by the
 * backend's version_check.snapshot()) and, when update_available is true,
 * shows a dismissible blue info bar with a link to the releases page.
 *
 * This is ADVISORY ONLY — it never blocks anything. It is deliberately a
 * separate banner from LicenseBanner:
 *   - LicenseBanner = amber/red, about whether the app may run.
 *   - UpdateBanner  = blue/info, just a nudge to update. Different color,
 *     different meaning, must never look like a warning.
 *
 * Dismiss is per-session (in-memory state only — no browser storage, which
 * isn't available in this environment). It reappears next launch if still
 * applicable, which is the intended gentle-but-persistent behavior.
 */
import { useEffect, useState } from "react";
import { getSystemVersion } from "../api";
import { colors } from "../tokens";

const POLL_MS = 30 * 60 * 1000; // 30 min — version rarely changes; light touch

// Where users go to update. Single permanent link to the latest release.
const RELEASES_URL = "https://github.com/anbu101/scalp-releases/releases/latest";

export default function UpdateBanner() {
  const [info, setInfo] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const load = () =>
      getSystemVersion()
        .then(setInfo)
        .catch(() => setInfo(null)); // backend booting / older backend -> stay silent
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  // Fail-safe: show nothing unless the backend explicitly says an update is
  // available. Any missing/odd response -> no banner.
  if (dismissed) return null;
  if (!info || info.update_available !== true) return null;

  const message =
    info.message ||
    (info.min_version && info.current_version
      ? `A newer version is available (you have v${info.current_version}, latest is v${info.min_version}).`
      : "A newer version of Scalp Terminal is available.");

  const openReleases = () => {
    try {
      window.open(RELEASES_URL, "_blank", "noopener,noreferrer");
    } catch {
      /* no-op */
    }
  };

  return (
    <div
      style={{
        background: "rgba(59,130,246,0.12)",
        color: colors.text.primary,
        borderBottom: `1px solid ${colors.primary}55`,
        padding: "8px 14px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        fontSize: 13,
        fontWeight: 500,
        lineHeight: 1.3,
      }}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>⬆️</span>
      <span style={{ color: colors.text.secondary }}>{message}</span>

      <button
        onClick={openReleases}
        style={{
          background: colors.primary,
          color: colors.text.primary,
          border: "none",
          borderRadius: 5,
          padding: "4px 12px",
          fontSize: 12,
          fontWeight: 700,
          cursor: "pointer",
          fontFamily: "inherit",
          whiteSpace: "nowrap",
        }}
      >
        Get the update
      </button>

      <button
        onClick={() => setDismissed(true)}
        title="Dismiss until next launch"
        style={{
          background: "none",
          border: "none",
          color: colors.text.muted,
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          padding: "0 4px",
        }}
      >
        ✕
      </button>
    </div>
  );
}
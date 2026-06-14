import { useEffect, useState, useRef } from "react";
import { getSystemVersion } from "../api";
import { colors } from "../tokens";

const FAST_RETRY_MS = 3000;
const SLOW_POLL_MS = 30 * 60 * 1000;

const RELEASES_URL = "https://github.com/anbu101/scalp-releases/releases/latest";

export default function UpdateBanner() {
  const [info, setInfo] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const timerRef = useRef(null);
  const gotFirstRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const schedule = (ms) => {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(load, ms);
    };

    const load = async () => {
      try {
        const data = await getSystemVersion();
        if (cancelled) return;
        setInfo(data);
        gotFirstRef.current = true;
        schedule(SLOW_POLL_MS);
      } catch (e) {
        if (cancelled) return;
        schedule(gotFirstRef.current ? SLOW_POLL_MS : FAST_RETRY_MS);
      }
    };

    load();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, []);

  if (dismissed) return null;
  if (!info || info.update_available !== true) return null;

  const message =
    info.message ||
    (info.min_version && info.current_version
      ? "A newer version is available (you have v" +
        info.current_version +
        ", latest is v" +
        info.min_version +
        ")."
      : "A newer version of Scalp Terminal is available.");

  const wrapStyle = {
    background: "rgba(59,130,246,0.12)",
    color: colors.text.primary,
    borderBottom: "1px solid " + colors.primary + "55",
    padding: "8px 14px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    fontSize: 13,
    fontWeight: 500,
    lineHeight: 1.3,
  };

  const linkStyle = {
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

  return (
    <div style={wrapStyle}>
      <span style={{ color: colors.text.secondary }}>{message}</span>
      <a href={RELEASES_URL} target="_blank" rel="noopener noreferrer" style={linkStyle}>
        Get the update
      </a>
      <button onClick={() => setDismissed(true)} title="Dismiss until next launch" style={dismissStyle}>
        X
      </button>
    </div>
  );
}

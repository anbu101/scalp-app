/* frontend/src/components/LicenseBanner.jsx
 *
 * PHASE 2 REPLACEMENT (updated Jun 12 - human countdown).
 * Non-blocking license warnings. Blocking states (UNACTIVATED, EXPIRED,
 * REVOKED, INVALID, CLOCK_TAMPER) are handled by LicenseGate, which
 * replaces the whole UI - this banner only covers:
 *   - GRACE      amber countdown (near license expiry, or server
 *                unreachable with the token running down - the backend
 *                message distinguishes the two)
 *   - any blocking status that appears MID-SESSION (gate already passed)
 *
 * UPDATE: grace_days_left arrives as a float number of days; under one
 * day it now renders as "Xh Ym left" instead of "0.38 days left".
 * Ticks at the 5-minute poll granularity, which is intentional - a
 * live seconds clock on a warning banner is anxiety theater.
 */
import { useEffect, useState } from "react";
import { getLicenseStatus } from "../api";

const POLL_MS = 5 * 60 * 1000; // 5 min

function fmtTimeLeft(days) {
  if (days == null) return "";
  if (days >= 1) return ` (${days.toFixed(1)} days left)`;
  const totalMin = Math.round(days * 24 * 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? ` (${h}h ${m}m left)` : ` (${m}m left)`;
}

export default function LicenseBanner() {
  const [license, setLicense] = useState(null);

  useEffect(() => {
    const load = () =>
      getLicenseStatus()
        .then(setLicense)
        .catch(() => setLicense(null)); // backend booting - stay quiet
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  if (!license || license.status === "VALID") return null;

  const isGrace = license.status === "GRACE";
  const style = {
    background: isGrace ? "#3b2f0a" : "#3b0a0a",
    color: isGrace ? "#ffe28a" : "#ffb4b4",
    padding: "10px",
    textAlign: "center",
    fontWeight: 500,
    fontSize: "14px",
  };

  const text = isGrace
    ? `⚠️ ${license.message || "License in grace period"}${fmtTimeLeft(
        license.grace_days_left
      )}`
    : `🔒 ${license.message || "License not valid"} — trading stops at next app launch`;

  return <div style={style}>{text}</div>;
}
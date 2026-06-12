/* frontend/src/components/LicenseBanner.jsx
 *
 * PHASE 2 REPLACEMENT.
 * Non-blocking license warnings. Blocking states (UNACTIVATED, EXPIRED,
 * REVOKED, INVALID, CLOCK_TAMPER) are handled by LicenseGate, which
 * replaces the whole UI - this banner only covers:
 *   - GRACE      amber countdown (server unreachable, token running down)
 *   - any blocking status that appears MID-SESSION (gate already passed)
 * v1 hid the banner on UNKNOWN (fail-open, dev-only). Production keeps a
 * fail-quiet approach for fetch errors (backend still booting) but never
 * treats a real non-VALID status as hidden.
 */
import { useEffect, useState } from "react";
import { getLicenseStatus } from "../api";

const POLL_MS = 5 * 60 * 1000; // 5 min

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
    ? `⚠️ ${license.message || "License in grace period"}${
        license.grace_days_left != null
          ? ` (${license.grace_days_left} days left)`
          : ""
      }`
    : `🔒 ${license.message || "License not valid"} — trading stops at next app launch`;

  return <div style={style}>{text}</div>;
}
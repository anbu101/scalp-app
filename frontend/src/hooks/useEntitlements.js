/* frontend/src/hooks/useEntitlements.js
 *
 * PHASE 3 — NEW FILE.
 * Single shared source of license entitlements for UI rendering decisions
 * (which strategy panels exist, whether admin-only tools show).
 *
 * IMPORTANT: this is COSMETIC only. Real enforcement is in the backend
 * (unlicensed strategies never launch; masked routes never serve internals).
 * Therefore the failure direction is deliberately fail-OPEN here: if the
 * license status can't be read (backend booting, legacy backend without
 * entitlements), the UI shows everything rather than hiding the admin's
 * own panels. The backend stays the wall; this is just the curtain.
 */
import { useCallback, useEffect, useState } from "react";
import { getLicenseStatus } from "../api";

const POLL_MS = 5 * 60 * 1000;

export function useEntitlements() {
  const [license, setLicense] = useState(null); // null = first fetch pending

  useEffect(() => {
    let alive = true;
    const load = () =>
      getLicenseStatus()
        .then((d) => alive && setLicense(d || {}))
        .catch(() => alive && setLicense({}));   // unreadable -> {} (fail-open)
    load();
    const t = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const loaded = license !== null;

  /* True if this strategy's panel should render.
     - entitlements.strategies missing/not-an-array (legacy backend,
       fetch error) -> show all (fail-open, cosmetic)
     - ["*"] -> all (ADMIN)
     - otherwise explicit membership */
  const allowsStrategy = useCallback((id) => {
    const s = license?.entitlements?.strategies;
    if (!Array.isArray(s)) return true;
    return s.includes("*") || s.includes(id);
  }, [license]);

  const isAdminUi = license?.ui_level === "admin";

  return { license, loaded, allowsStrategy, isAdminUi };
}
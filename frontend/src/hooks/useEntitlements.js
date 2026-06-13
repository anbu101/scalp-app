/* frontend/src/hooks/useEntitlements.js
 *
 * PHASE 3 (updated Jun 12 - fast first read).
 * One shared hook for license-driven RENDERING decisions. This is the
 * curtain, not the wall: the backend already refuses to launch
 * unlicensed strategies and masks route responses; this hook only
 * decides what the UI shows. It therefore FAILS OPEN - if the license
 * fetch errors or the backend predates entitlements, the UI renders
 * everything (an empty panel is harmless; a hidden licensed panel is a
 * support call).
 *
 * Exposes (interface unchanged from the previous version):
 *   license        the raw /system/license response ({} until loaded)
 *   loaded         true after the FIRST SUCCESSFUL read (StrategyHost
 *                  waits on this to avoid a flash of panels)
 *   allowsStrategy(id)   true if entitled (or unknown -> fail open)
 *   isAdminUi      true when ui_level === "admin"
 *
 * UPDATE - the StatusBar-delay fix: previously the first fetch raced
 * the backend boot; if it lost, the hook sat on {} for a full 5-minute
 * poll cycle, so the expiry segment appeared "a few minutes late".
 * Now: retry every 10s until the first successful read, then settle
 * into the 5-minute cadence. Implemented as a setTimeout chain (not
 * setInterval) so the cadence can change between ticks.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getLicenseStatus } from "../api";

const POLL_MS = 5 * 60 * 1000; // steady-state: 5 min
const RETRY_MS = 10 * 1000;    // until first successful read: 10 s

export function useEntitlements() {
  const [license, setLicense] = useState({});
  const [loaded, setLoaded] = useState(false);
  const loadedRef = useRef(false);
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;

    const tick = async () => {
      let ok = false;
      try {
        const data = await getLicenseStatus();
        if (data && data.status) {
          ok = true;
          if (aliveRef.current) {
            setLicense(data);
            if (!loadedRef.current) {
              loadedRef.current = true;
              setLoaded(true);
            }
          }
        }
      } catch {
        // backend still booting / transient - keep current state, retry
      }
      if (!aliveRef.current) return;
      // Fast retry only until the first successful read; 5-min after.
      // (ok is tracked for clarity/debugging; once loaded, a transient
      // failure just keeps the last good state until the next poll.)
      void ok;
      timerRef.current = setTimeout(tick, loadedRef.current ? POLL_MS : RETRY_MS);
    };

    tick();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const allowsStrategy = useCallback(
    (strategyId) => {
      const strategies = license?.entitlements?.strategies;
      // Fail OPEN: no entitlements info -> show everything (backend is
      // the wall; empty panels are harmless).
      if (!Array.isArray(strategies)) return true;
      return strategies.includes("*") || strategies.includes(strategyId);
    },
    [license]
  );

  const isAdminUi = license?.ui_level === "admin";

  return { license, loaded, allowsStrategy, isAdminUi };
}
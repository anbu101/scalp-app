// frontend/src/components/calm/useMeters.js — ── DASH_CALM_20261117 ──
// Every card's Entry/Exit meter from ONE call, GET /api/replay/meters (admin wall).
// Every 10 s in session, 4 s while a card is open, 60 s outside the session; paused
// while the app is in the background. The last good reading per strategy stays up
// through a failed poll (marked stale). Non-admin: never called, returns {}.
import { useEffect, useRef, useState } from "react";
import { getApiBase } from "../../api/base";

export function useMeters(ids, { enabled = true, fast = false, session = true } = {}) {
  const [state, setState] = useState({ meters: {}, stale: false, ts: 0 });
  const key = enabled ? [...ids].sort().join(",") : "";
  const kick = useRef(null);
  const period = fast ? 4000 : session ? 10000 : 60000;
  useEffect(() => {
    if (!key) { setState({ meters: {}, stale: false, ts: 0 }); return undefined; }
    let dead = false, t = null;
    const tick = async () => {
      if (dead) return;
      clearTimeout(t);
      if (!(typeof document !== "undefined" && document.hidden)) {
        try {
          const r = await fetch(`${getApiBase()}/api/replay/meters?strategies=${encodeURIComponent(key)}`);
          const j = await r.json().catch(() => ({}));
          if (!dead) {
            if (r.ok && j.ok) setState((s) => ({ meters: { ...s.meters, ...(j.meters || {}) }, stale: false, ts: Date.now() }));
            else setState((s) => ({ ...s, stale: true }));
          }
        } catch { if (!dead) setState((s) => ({ ...s, stale: true })); }
      }
      if (!dead) t = setTimeout(tick, period);
    };
    kick.current = tick;
    tick();
    return () => { dead = true; clearTimeout(t); kick.current = null; };
  }, [key, period]);
  return { ...state, refresh: () => kick.current && kick.current() };
}

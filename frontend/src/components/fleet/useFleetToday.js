// frontend/src/components/fleet/useFleetToday.js — ── DASH_MODERN_20260925 ──
// GET /api/fleet/today on a 5 s poll; the last good payload is kept across a
// failed poll so the page never blanks on a transient error.

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";

export function useFleetToday(pollMs = 5000) {
  const [state, setState] = useState({ data: null, loaded: false, error: null, ts: 0 });
  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const res = await fetch(`${getApiBase()}/api/fleet/today`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const j = await res.json();
        if (!alive) return;
        if (j && j.ok !== false) setState({ data: j, loaded: true, error: null, ts: Date.now() });
        else setState((s) => ({ ...s, loaded: true, error: (j && j.error) || "feed error" }));
      } catch (e) {
        if (alive) setState((s) => ({ ...s, loaded: true, error: String(e && e.message ? e.message : e) }));
      }
    }
    tick();
    const t = setInterval(tick, pollMs);
    return () => { alive = false; clearInterval(t); };
  }, [pollMs]);
  return state;
}

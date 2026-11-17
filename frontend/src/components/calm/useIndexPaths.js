// frontend/src/components/calm/useIndexPaths.js — ── INDEX_PATH_20261117 ──
// Today's NIFTY / BANKNIFTY line from GET /api/fleet/indices (Kite 1-minute history +
// the app's own samples), every 30 s in session, 5 min outside; the live price from the
// app-wide index poll (useMarketData, 500 ms) replaces the forming minute so the line's
// tip moves with the badge. Last good payload kept through a failed poll.
import { useEffect, useMemo, useState } from "react";
import { getApiBase } from "../../api/base";
import { useMarketData } from "../../context/MarketDataContext";

export const INDEX_KEYS = ["NIFTY", "BANKNIFTY"];

export function useIndexPaths(nowMin, session) {
  const { indices } = useMarketData();
  const [data, setData] = useState(null);
  const period = session ? 30000 : 300000;
  useEffect(() => {
    let dead = false, t = null;
    const tick = async () => {
      if (!(typeof document !== "undefined" && document.hidden)) {
        try {
          const r = await fetch(`${getApiBase()}/api/fleet/indices`);
          const j = await r.json().catch(() => ({}));
          if (!dead && r.ok && j.ok) setData(j);
        } catch {}
      }
      if (!dead) t = setTimeout(tick, period);
    };
    tick();
    return () => { dead = true; clearTimeout(t); };
  }, [period]);
  return useMemo(() => {
    const out = {};
    INDEX_KEYS.forEach((k) => {
      const api = data?.indices?.[k] || {};
      const live = indices?.[k] || {};
      const ltp = typeof live.ltp === "number" ? live.ltp : api.ltp;
      const prev = typeof live.prev_close === "number" ? live.prev_close : api.prev_close;
      let path = (api.path || []).slice();
      if (session && typeof ltp === "number" && nowMin >= 555 && nowMin <= 930) {
        const last = path[path.length - 1];
        if (last && last[0] === nowMin) path[path.length - 1] = [nowMin, ltp];
        else if (!last || last[0] < nowMin) path.push([nowMin, ltp]);
      }
      out[k] = { ltp: typeof ltp === "number" ? ltp : null, prev: typeof prev === "number" && prev > 0 ? prev : null, path, source: api.source, note: api.note };
    });
    return out;
  }, [data, indices, nowMin, session]);
}

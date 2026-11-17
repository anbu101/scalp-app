/**
 * useStrategyVisibility — src/hooks/useStrategyVisibility.js
 *
 * ── OFF_HIDDEN_20261117 ── (TD-009) which strategies are hidden because they
 * are switched OFF on the Settings page. Source of truth is the backend,
 * GET /api/strategies/visibility:
 *     hidden = mode OFF AND no open position AND no trade today
 * so an OFF strategy that still holds a position, or traded today, stays
 * visible (never hide a live position). History is never hidden: pages only
 * group the filter chips of hidden strategies under "Others".
 *
 * One shared poll (15 s) for every subscriber. Fail-OPEN: until the first
 * answer, on an error, or on an older backend without the route, nothing is
 * hidden (`loaded` still turns true so pages do not wait forever).
 */
import { useEffect, useState } from "react";
import { getApiBase } from "../api/base";

const POLL_MS = 15000;
const EMPTY = Object.freeze({ loaded: false, hidden: new Set() });

let state = EMPTY;
let timer = null;
let inflight = false;
const subs = new Set();

function publish(next) {
  const same = state.loaded === next.loaded && state.hidden.size === next.hidden.size
    && [...next.hidden].every((id) => state.hidden.has(id));
  if (same) return;
  state = next;
  subs.forEach((fn) => fn(state));
}

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    const res = await fetch(`${getApiBase()}/api/strategies/visibility`);
    const data = res.ok ? await res.json() : null;
    const ids = data && data.ok !== false && Array.isArray(data.hidden) ? data.hidden : [];
    publish({ loaded: true, hidden: new Set(ids.map(String)) });
  } catch {
    publish({ loaded: true, hidden: new Set() });   // fail-open
  } finally {
    inflight = false;
  }
}

function subscribe(fn) {
  subs.add(fn);
  if (!timer) {
    poll();
    timer = setInterval(poll, POLL_MS);
  }
  return () => {
    subs.delete(fn);
    if (subs.size === 0 && timer) { clearInterval(timer); timer = null; }
  };
}

/** Re-read now (e.g. right after a mode change in Settings) instead of waiting for the poll. */
export function refreshStrategyVisibility() {   // ── LVB_SETTINGS_20261117 ──
  return poll();
}

export function useStrategyVisibility() {
  const [s, setS] = useState(state);
  useEffect(() => subscribe(setS), []);
  return {
    loaded: s.loaded,
    hidden: s.hidden,
    isHidden: (id) => !!id && s.hidden.has(id),
  };
}

/** Split filter options into [shown, others]. Others are grouped only from
 *  TWO hidden strategies up — a single OFF strategy keeps its own chip.
 *  `idOf(opt)` maps an option to its strategy id. */
export function splitOthers(options, isHidden, idOf) {
  const others = options.filter((o) => isHidden(idOf(o)));
  if (others.length < 2) return [options, []];
  return [options.filter((o) => !isHidden(idOf(o))), others];
}

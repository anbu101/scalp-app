/**
 * STRATEGY HOST
 *
 * Intended path: src/components/StrategyHost.jsx
 *
 * Responsibilities:
 *   1. Owns the list of currently active strategies (hardcoded for now).
 *   2. Manages which panel is "primary" (expanded).
 *   3. Computes layout based on active strategy count.
 *   4. Renders the correct panel component per strategy.
 *   5. Passes ltpMap and layout role (isPrimary / onBecomePrimary) into each panel.
 *
 * What this does NOT do:
 *   - Does not fetch any data.
 *   - Does not know anything about scalp-specific state.
 *   - Does not hardcode CE/PE, slots, or selection — those live in ScalpPanel.
 *
 * To add a new strategy later:
 *   1. Add its id to ACTIVE_STRATEGY_IDS.
 *   2. Add a case for it in renderStrategyPanel().
 *   3. Replace ACTIVE_STRATEGY_IDS with a backend query (single-line change).
 */

import { useState } from "react";
import { getStrategyById } from "../strategies/registry";
import ScalpPanel from "../strategies/scalp/ScalpPanel";
import BBPanel    from "../strategies/bb_v1/BBPanel";

/* ----------------------------------
   Active Strategy List
   TODO: Replace with backend query when /strategies/active endpoint exists.
       e.g. const activeIds = await getActiveStrategies();
----------------------------------- */
const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "BB_V1"];

/* ----------------------------------
   Layout Constants
----------------------------------- */
const LAYOUT = {
  ONE: "ONE",
  TWO: "TWO",
  THREE: "THREE",
};

function getLayout(count) {
  if (count === 2) return LAYOUT.TWO;
  if (count === 3) return LAYOUT.THREE;
  return LAYOUT.ONE;
}

/**
 * Returns a style object for a panel slot given:
 *   layout    — ONE | TWO | THREE
 *   isPrimary — whether this panel is the expanded one
 */
function getPanelStyle(layout, isPrimary) {
  const base = {
    overflow: "hidden",
    transition: "flex 0.25s ease, opacity 0.2s ease",
    minWidth: 0, // prevent flex blowout
  };

  if (layout === LAYOUT.ONE) {
    return { ...base, flex: "1 1 100%" };
  }

  if (layout === LAYOUT.TWO) {
    // Primary: ~70%, Secondary: ~30%
    return isPrimary
      ? { ...base, flex: "7 1 0%"  }
      : { ...base, flex: "3 1 0%", cursor: "pointer" };
  }

  if (layout === LAYOUT.THREE) {
    // Primary: ~60%, each compact: ~20%
    return isPrimary
      ? { ...base, flex: "6 1 0%"  }
      : { ...base, flex: "2 1 0%", cursor: "pointer" };
  }

  return base;
}

/* ----------------------------------
   Panel renderer
   Add new strategy cases here as they are introduced.
----------------------------------- */
function renderStrategyPanel({ strategyId, ltpMap, isPrimary, onBecomePrimary }) {
  switch (strategyId) {
    case "SCALP_V1":
      return (
        <ScalpPanel
          key={strategyId}
          ltpMap={ltpMap}
          isPrimary={isPrimary}
          onBecomePrimary={onBecomePrimary}
        />
      );

    case "BB_V1":
      return (
        <BBPanel
          key={strategyId}
          ltpMap={ltpMap}
          isPrimary={isPrimary}
          onBecomePrimary={onBecomePrimary}
        />
      );

    default:
      // Unknown strategy — render a safe placeholder, never crash the host.
      return (
        <div
          key={strategyId}
          style={{
            padding: 16,
            border: "1px dashed #374151",
            borderRadius: 8,
            color: "#6b7280",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          Unknown strategy: {strategyId}
        </div>
      );
  }
}

/* ----------------------------------
   StrategyHost
----------------------------------- */
export default function StrategyHost({ ltpMap }) {
  // Validate active ids against registry — silently drops unknown ones.
  const activeStrategies = ACTIVE_STRATEGY_IDS.filter((id) => {
    const found = getStrategyById(id);
    if (!found) {
      console.warn(`[StrategyHost] Unknown strategy id "${id}" — skipping.`);
    }
    return !!found;
  });

  // Cap at 3 (backend constraint).
  const capped = activeStrategies.slice(0, 3);

  // The primary (expanded) panel. Defaults to first in list.
  const [primaryId, setPrimaryId] = useState(capped[0] ?? null);

  if (capped.length === 0) {
    return null;
  }

  const layout = getLayout(capped.length);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        gap: 16,
        alignItems: "stretch",
        width: "100%",
      }}
    >
      {capped.map((strategyId) => {
        const isPrimary = strategyId === primaryId;

        return (
          <div
            key={strategyId}
            style={getPanelStyle(layout, isPrimary)}
            // Clicking anywhere on a compact panel promotes it to primary.
            // ScalpPanel's own onClick handlers still fire normally when primary.
            onClick={!isPrimary ? () => setPrimaryId(strategyId) : undefined}
          >
            {renderStrategyPanel({
              strategyId,
              ltpMap,
              isPrimary,
              onBecomePrimary: () => setPrimaryId(strategyId),
            })}
          </div>
        );
      })}
    </div>
  );
}
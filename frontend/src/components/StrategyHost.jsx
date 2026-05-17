/**
 * STRATEGY HOST
 *
 * Intended path: src/components/StrategyHost.jsx
 *
 * Active strategies: SCALP_V1 · BB_V1 · BB_V2 · HA_V1
 *
 * Layout rules (up to 4 panels):
 *   1 panel  → full width
 *   2 panels → primary 70% / compact 30%
 *   3 panels → primary 60% / compact 20% each
 *   4 panels → primary 55% / compact 15% each
 *
 * Clicking any compact panel makes it primary (expands it).
 */

import { useState } from "react";
import { getStrategyById }  from "../strategies/registry";
import ScalpPanel           from "../strategies/scalp/ScalpPanel";
import BBPanel              from "../strategies/bb_v1/BBPanel";
import BBV2Panel            from "../strategies/bb_v2/BBV2Panel";
import HAPanel              from "../strategies/ha_v1/HAPanel";

/* ----------------------------------
   Active Strategy List
   Order determines default left→right
   rendering; first entry is primary
   on first load.
----------------------------------- */
const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "BB_V1", "BB_V2", "HA_V1"];

/* ----------------------------------
   Maximum panels to render simultaneously.
   4 is supported by the layout system.
   Raise this if you add more strategies.
----------------------------------- */
const MAX_PANELS = 4;

/* ----------------------------------
   Layout Constants
----------------------------------- */
const LAYOUT = {
  ONE:   "ONE",
  TWO:   "TWO",
  THREE: "THREE",
  FOUR:  "FOUR",
};

function getLayout(count) {
  if (count === 2) return LAYOUT.TWO;
  if (count === 3) return LAYOUT.THREE;
  if (count >= 4)  return LAYOUT.FOUR;
  return LAYOUT.ONE;
}

/**
 * Returns a flex style for a panel slot.
 *
 * Primary panel gets the majority of the width.
 * Compact panels collapse to a narrow sidebar.
 */
function getPanelStyle(layout, isPrimary) {
  const base = {
    overflow:   "hidden",
    transition: "flex 0.28s ease, opacity 0.22s ease",
    minWidth:   0,
  };

  switch (layout) {
    case LAYOUT.ONE:
      return { ...base, flex: "1 1 100%" };

    case LAYOUT.TWO:
      return isPrimary
        ? { ...base, flex: "7 1 0%" }
        : { ...base, flex: "3 1 0%", cursor: "pointer", opacity: 0.88 };

    case LAYOUT.THREE:
      return isPrimary
        ? { ...base, flex: "6 1 0%" }
        : { ...base, flex: "2 1 0%", cursor: "pointer", opacity: 0.85 };

    case LAYOUT.FOUR:
      return isPrimary
        ? { ...base, flex: "55 1 0%" }
        : { ...base, flex: "15 1 0%", cursor: "pointer", opacity: 0.82 };

    default:
      return base;
  }
}

/* ----------------------------------
   Panel renderer
   Add new strategy cases here.
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
          strategyId="BB_V1"
          ltpMap={ltpMap}
          isPrimary={isPrimary}
          onBecomePrimary={onBecomePrimary}
        />
      );

    case "BB_V2":
      return (
        <BBV2Panel
          key={strategyId}
          ltpMap={ltpMap}
          isPrimary={isPrimary}
          onBecomePrimary={onBecomePrimary}
        />
      );

    case "HA_V1":
      return (
        <HAPanel
          key={strategyId}
          ltpMap={ltpMap}
          isPrimary={isPrimary}
          onBecomePrimary={onBecomePrimary}
        />
      );

    default:
      // Unknown strategy — safe placeholder, never crash the host
      return (
        <div
          key={strategyId}
          style={{
            padding:      16,
            border:       "1px dashed #374151",
            borderRadius: 8,
            color:        "#6b7280",
            fontSize:     13,
            display:      "flex",
            alignItems:   "center",
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

  // Validate active IDs against registry — silently drops unknown ones
  const activeStrategies = ACTIVE_STRATEGY_IDS.filter((id) => {
    const found = getStrategyById(id);
    if (!found) {
      console.warn(`[StrategyHost] Unknown strategy id "${id}" — skipping.`);
    }
    return !!found;
  });

  // Apply cap
  const capped = activeStrategies.slice(0, MAX_PANELS);

  // Primary panel — defaults to first in list
  const [primaryId, setPrimaryId] = useState(capped[0] ?? null);

  if (capped.length === 0) {
    return null;
  }

  const layout = getLayout(capped.length);

  return (
    <div
      style={{
        display:       "flex",
        flexDirection: "row",
        gap:           12,
        alignItems:    "stretch",
        width:         "100%",
      }}
    >
      {capped.map((strategyId) => {
        const isPrimary = strategyId === primaryId;

        return (
          <div
            key={strategyId}
            style={getPanelStyle(layout, isPrimary)}
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
/**
 * STRATEGY HOST
 *
 * Intended path: src/components/StrategyHost.jsx
 */

import { useState } from "react";
import { getStrategyById } from "../strategies/registry";
import ScalpPanel from "../strategies/scalp/ScalpPanel";
import BBPanel    from "../strategies/bb_v1/BBPanel";
import HAPanel    from "../strategies/ha_v1/HAPanel";

/* ----------------------------------
   Active Strategy List
----------------------------------- */
const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "BB_V1", "HA_V1"];

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

function getPanelStyle(layout, isPrimary) {
  const base = {
    overflow: "hidden",
    transition: "flex 0.25s ease, opacity 0.2s ease",
    minWidth: 0,
  };

  if (layout === LAYOUT.ONE) {
    return { ...base, flex: "1 1 100%" };
  }

  if (layout === LAYOUT.TWO) {
    return isPrimary
      ? { ...base, flex: "7 1 0%" }
      : { ...base, flex: "3 1 0%", cursor: "pointer" };
  }

  if (layout === LAYOUT.THREE) {
    return isPrimary
      ? { ...base, flex: "6 1 0%" }
      : { ...base, flex: "2 1 0%", cursor: "pointer" };
  }

  return base;
}

/* ----------------------------------
   Panel renderer
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
  const activeStrategies = ACTIVE_STRATEGY_IDS.filter((id) => {
    const found = getStrategyById(id);
    if (!found) {
      console.warn(`[StrategyHost] Unknown strategy id "${id}" — skipping.`);
    }
    return !!found;
  });

  const capped = activeStrategies.slice(0, 3);

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
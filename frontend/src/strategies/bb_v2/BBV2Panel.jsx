// frontend/src/strategies/bb_v2/BBV2Panel.jsx
/**
 * BBV2Panel — thin wrapper around BBPanel for BB_V2 strategy.
 *
 * BB_V2 differences displayed:
 *  - Strategy label shows "BB V2"
 *  - Config fetched from BB_V2 strategy_id
 *  - ST multiplier is 1.5 (shown in info strip)
 *  - Pivot crossover signals (R2/PP/S2/S3) shown in tooltip
 */

import BBPanel from "../bb_v1/BBPanel";

export default function BBV2Panel({ ltpMap, isPrimary, onBecomePrimary }) {
  return (
    <BBPanel
      strategyId="BB_V2"
      ltpMap={ltpMap}
      isPrimary={isPrimary}
      onBecomePrimary={onBecomePrimary}
    />
  );
}
/**
 * STRATEGY REGISTRY
 *
 * Intended path: src/strategies/registry.js
 *
 * Single source of truth for known strategies.
 * Hardcoded for now — no dynamic loading, no backend contract.
 */

export const STRATEGY_REGISTRY = [
  {
    id: "SCALP_V1",
    label: "Scalp",
    capabilities: {
      hasSelection: true,
      hasSlots: true,
      hasCEPE: true,
    },
  },

  {
    id: "BB_V1",
    label: "NIFTY BB Options",
    broker: "ZERODHA",
    timeframe: "3m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,
      hasSlots: true,
      hasCEPE: false,
    },
  },

  {
    id: "BB_V2",
    label: "BB Options V2",
    broker: "ZERODHA",
    timeframe: "3m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,
      hasSlots:     true,
      hasCEPE:      false,
    },
  },

  {
    id: "HA_V1",
    label: "Heikin Ashi",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // uses NIFTY weekly options (1 CE + 1 PE)
      hasSlots: false,      // state managed internally by HATradeManager
      hasCEPE: true,        // has CE/PE side mode toggle
    },
  },
];

/**
 * Lookup a strategy definition by id.
 * Returns undefined if not found — callers should handle that.
 */
export function getStrategyById(id) {
  return STRATEGY_REGISTRY.find((s) => s.id === id);
}
/**
 * STRATEGY REGISTRY
 *
 * Intended path: src/strategies/registry.js
 *
 * Single source of truth for known strategies.
 * Hardcoded for now — no dynamic loading, no backend contract.
 *
 * Rules:
 *   - Add a new entry here when a new strategy is introduced.
 *   - `id` must match exactly what the backend uses (e.g. in getCurrentSelection calls).
 *   - `label` is display-only.
 *   - Capabilities are declared per-strategy so StrategyHost and ScalpPanel
 *     never need to guess what a strategy supports.
 *
 * Max 3 strategies can be active at once (enforced in StrategyHost).
 */

export const STRATEGY_REGISTRY = [
    {
      id: "SCALP_V1",
      label: "Scalp",
      capabilities: {
        hasSelection: true,   // uses getCurrentSelection
        hasSlots: true,       // uses slot-based tradeState
        hasCEPE: true,        // has CE/PE side mode toggle
      },
    },
  
    {
      id: "BB_V1",
      label: "NIFTY BB Options",
      broker: "ZERODHA",
      timeframe: "3m",
      modeSupported: ["PAPER", "LIVE"],
      capabilities: {
        hasSelection: false,  // no CE/PE selection; trades derived from BB signal
        hasSlots: true,       // uses slot-based tradeState (same shape as SCALP_V1)
        hasCEPE: false,       // no manual CE/PE side switcher
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
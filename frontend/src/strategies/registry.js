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

  {
    id: "SCALP_V2",
    label: "Scalp V2",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // surveillance per class (A/B/C) → armed picks
      hasSlots:     false,  // group manager owns all leg state (Model B)
      hasCEPE:      false,  // single-direction SHORT group, no CE/PE toggle
    },
  },
  {
    id: "SCALP_V3",
    label: "Scalp V3",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // 2 CE + 2 PE surveillance (signal + hedge candidates)
      hasSlots:     false,  // single DB-backed trade, no slot model (scalp_v3_trades)
      hasCEPE:      true,   // trade_side_mode gates which side may SIGNAL
    },
  },
  {
    id: "SCALP_V4",
    label: "Scalp V4",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // 2 CE + 2 PE surveillance (signal + hedge candidates)
      hasSlots:     false,  // single DB-backed trade, no slot model (scalp_v4_trades)
      hasCEPE:      true,   // trade_side_mode gates which side may SIGNAL
    },
  },
  {
    id: "SCALP_V5",
    label: "Scalp V5",
    broker: "ZERODHA",
    timeframe: "3m",                    // V5 is 3-minute candles (V1–V4 are 1m)
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // 2 CE + 2 PE premium-band surveillance
      hasSlots:     false,  // single DB-backed trade (scalpv5_trades), no slot model
      hasCEPE:      true,   // option-buying LONG; CE/PE surveillance both sides
    },
  },
  // ── IC_V1 BEGIN ──
  {
    id: "IC_V1",
    label: "Iron Condor V1",
    broker: "ZERODHA",
    timeframe: "1m",                    // nominal — time-entry, no candle pipeline
    modeSupported: ["OFF", "PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,  // strikes picked once at entry_time (premium ≤ cap)
      hasSlots:     false,  // ICGroupManager owns all 4-leg state (L1..L4)
      hasCEPE:      false,  // fixed 2-short + 2-wing template, no side toggle
    },
  },
  // ── IC_V1 END ──
];

/**
 * Lookup a strategy definition by id.
 * Returns undefined if not found — callers should handle that.
 */
export function getStrategyById(id) {
  return STRATEGY_REGISTRY.find((s) => s.id === id);
}
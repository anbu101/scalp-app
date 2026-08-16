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
    id: "PST_SELL",
    label: "PST Sell",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // premium<cap scan at ts-60 (shared PST loop)
      hasSlots:     false,  // own table pst_sell_trades
      hasCEPE:      true,   // side_mode gates the SIGNAL side
    },
  },
  {
    id: "PST_HEDGE",
    label: "PST Hedge",
    broker: "ZERODHA",
    timeframe: "1m",
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // BOTH sides selected; signal tracked, opposite BOUGHT
      hasSlots:     false,  // own table pst_hedge_trades
      hasCEPE:      true,   // side_mode gates the SIGNAL side
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
  // ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
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
  {
    id: "IC_V2",
    label: "Iron Condor V2",
    broker: "ZERODHA",
    timeframe: "1m",                    // nominal — time-entry, no candle pipeline
    modeSupported: ["OFF", "PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,  // strikes picked once at entry_time (premium ≤ cap)
      hasSlots:     false,  // ICGroupManager owns all 4-leg state (L1..L4 + ·ADJ)
      hasCEPE:      false,  // fixed 2-short + 2-wing template, no side toggle
    },
  },
  // ── IC END ──
  // ── TMA_V1 BEGIN ──
  {
    id: "TMA_V1",
    label: "TMA V1",
    broker: "ZERODHA",
    timeframe: "1m",                    // signals on 5m spot bars; fills on 1m
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // premium≤cap scan at ts-60 (SELL + hedge ladder)
      hasSlots:     false,  // TMATradeManager owns all state (tma_trades)
      hasCEPE:      false,  // fixed template: SELL opposite the trend + hedge
    },
  },
  // ── TMA_V1 END ──
  // ── TSG_V1 BEGIN ──
  {
    id: "TSG_V1",
    label: "TSG V1",
    broker: "ZERODHA",
    timeframe: "1m",                    // basket evaluated at 1m closes
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,  // one scheduled 09:16 entry, no scan loop
      hasSlots:     false,  // TsgManager owns all state (session JSON)
      hasCEPE:      false,  // fixed 2-short + 2-wing template
    },
  },
  // ── TSG_V1 END ──

  // ── GC_V1 BEGIN ──
  {
    id: "GC_V1",
    label: "GC V1",
    broker: "ZERODHA",
    timeframe: "1m",                    // decisions ONLY at 1m closes (LD6)
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: false,  // spot-signal strategy, no scan loop
      hasSlots:     false,  // GcManager owns all state (session JSON)
      hasCEPE:      false,  // side comes from the breakout signal
    },
  },
  // ── GC_V1 END ──
];

/**
 * Lookup a strategy definition by id.
 * Returns undefined if not found — callers should handle that.
 */
export function getStrategyById(id) {
  return STRATEGY_REGISTRY.find((s) => s.id === id);
}
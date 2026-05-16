-- =========================================================
-- 011_create_ha_candles.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   Dedicated Heikin Ashi candle store for HA_V1.
--
--   Kept separate from market_timeline (SCALP_V1) and
--   futures_candles (BB_V1) so each strategy owns its own
--   candle schema.  Changing one strategy's timeframe in
--   future will not affect the others.
--
-- Columns:
--   symbol        — option tradingsymbol (e.g. NIFTY26MAY25000CE)
--   timeframe     — "1m" today; extensible to "3m", "5m" etc.
--   ts            — bucket start epoch (seconds)
--   ha_open/high/low/close — Heikin Ashi OHLC values
--   ema20_low     — EMA(20, source=HA_Low, smoothing=None)
--                   matches TradingView settings: Length=20,
--                   Source=Low, Smoothing=None
--   is_green      — 1 when ha_close >= ha_open, else 0
--   signal_action — ENTER_CE / ENTER_PE / NULL
--   signal_reason — COND1 / COND2 / COND3 / NULL
-- =========================================================

CREATE TABLE IF NOT EXISTS ha_candles (
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL  DEFAULT '1m',
    ts            INTEGER NOT NULL,

    ha_open       REAL    NOT NULL,
    ha_high       REAL    NOT NULL,
    ha_low        REAL    NOT NULL,
    ha_close      REAL    NOT NULL,

    -- EMA(20) of HA Low  — TradingView: Length=20, Source=Low, Smoothing=None
    ema20_low     REAL,

    -- Convenience flag: 1 = green candle, 0 = red
    is_green      INTEGER,

    -- Signal written at entry candle
    signal_action TEXT,   -- ENTER_CE / ENTER_PE
    signal_reason TEXT,   -- COND1 / COND2 / COND3

    PRIMARY KEY (symbol, timeframe, ts)
);

-- Fast descending lookup for warmup + UI
CREATE INDEX IF NOT EXISTS idx_ha_candles_symbol_tf_ts
ON ha_candles (symbol, timeframe, ts DESC);
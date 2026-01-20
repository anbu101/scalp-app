-- =========================================================
-- Historical candles for INDEX (Backtest only)
-- =========================================================

CREATE TABLE IF NOT EXISTS historical_candles_index (
    symbol TEXT NOT NULL,              -- NIFTY
    timeframe TEXT NOT NULL,            -- 5m / 15m
    ts INTEGER NOT NULL,                -- candle timestamp (epoch seconds)

    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER,

    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_hist_idx_symbol_tf_ts
ON historical_candles_index (symbol, timeframe, ts);


-- =========================================================
-- Historical candles for OPTIONS (Backtest only)
-- =========================================================

CREATE TABLE IF NOT EXISTS historical_candles_options (
    symbol TEXT NOT NULL,               -- NIFTY2611326000PE
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,          -- CE / PE
    expiry TEXT NOT NULL,               -- 2026-01-30
    timeframe TEXT NOT NULL,            -- 5m / 15m
    ts INTEGER NOT NULL,                -- candle timestamp

    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER,
    oi INTEGER,

    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_hist_opt_symbol_tf_ts
ON historical_candles_options (symbol, timeframe, ts);

CREATE INDEX IF NOT EXISTS idx_hist_opt_expiry_strike
ON historical_candles_options (expiry, strike);

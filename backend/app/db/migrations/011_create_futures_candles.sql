-- =========================================================
-- 011_create_futures_candles.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================

CREATE TABLE IF NOT EXISTS futures_candles (
    symbol TEXT,
    timeframe TEXT,
    ts INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    PRIMARY KEY (symbol, timeframe, ts)
);

-- Indicator columns added by init_table() are optional at migration time;
-- futures_candles_repo.init_table() adds them safely via ALTER TABLE IF NOT EXISTS.
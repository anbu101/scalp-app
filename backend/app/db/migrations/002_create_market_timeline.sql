-- 002_create_market_timeline.sql
-- SAFE migration: does NOT touch existing data

CREATE TABLE IF NOT EXISTS market_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,

    open REAL,
    high REAL,
    low REAL,
    close REAL,

    ema8 REAL,
    ema20_low REAL,
    ema20_high REAL,

    rsi_raw REAL,

    conditions_json TEXT,
    signal TEXT,

    strategy_version TEXT,
    mode TEXT,

    created_at INTEGER
);

-- Index used heavily by engine + UI
CREATE INDEX IF NOT EXISTS idx_market_timeline_symbol_ts
ON market_timeline(symbol, ts);

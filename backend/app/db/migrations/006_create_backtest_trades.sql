-- =========================================================
-- 006_create_backtest_trades.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================

CREATE TABLE IF NOT EXISTS backtest_trades (
    backtest_trade_id TEXT PRIMARY KEY,

    backtest_run_id TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,

    symbol TEXT NOT NULL,
    token  INTEGER NOT NULL,
    side   TEXT NOT NULL CHECK (
        side IN ('CE', 'PE')
    ),

    atm_slot INTEGER NOT NULL,          -- -200 .. +200 (price-based or ATM offset)

    entry_time  INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    candle_ts   INTEGER NOT NULL,

    sl_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    rr REAL NOT NULL,

    lots INTEGER NOT NULL,
    lot_size INTEGER NOT NULL,
    qty INTEGER NOT NULL,

    exit_time INTEGER,
    exit_price REAL,
    exit_reason TEXT,

    pnl_points REAL,
    pnl_value REAL,

    brokerage REAL DEFAULT 0,
    stt REAL DEFAULT 0,
    exchange_charges REAL DEFAULT 0,
    sebi_charges REAL DEFAULT 0,
    stamp_duty REAL DEFAULT 0,
    gst REAL DEFAULT 0,
    total_charges REAL DEFAULT 0,

    net_pnl REAL,

    sl_tp_same_candle INTEGER DEFAULT 0 CHECK (
        sl_tp_same_candle IN (0, 1)
    ),

    state TEXT NOT NULL CHECK (
        state IN ('OPEN', 'CLOSED', 'SKIPPED')
    ),

    created_at INTEGER NOT NULL
);


-- =========================================================
-- INDEXES (PERF CRITICAL)
-- =========================================================

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run
ON backtest_trades(backtest_run_id);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_strategy
ON backtest_trades(strategy_name);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_symbol_slot_state
ON backtest_trades(symbol, atm_slot, state);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_slot_pnl
ON backtest_trades(strategy_name, atm_slot, net_pnl);

CREATE TABLE IF NOT EXISTS paper_trades (
    paper_trade_id TEXT PRIMARY KEY,

    strategy_name TEXT NOT NULL,
    trade_mode TEXT NOT NULL,            -- PAPER / LIVE

    symbol TEXT NOT NULL,
    token INTEGER NOT NULL,
    side TEXT NOT NULL,                  -- CE / PE / BOTH

    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    candle_ts INTEGER NOT NULL,

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

    -- Zerodha OPTION charges (locked v2)
    brokerage REAL DEFAULT 0,
    stt REAL DEFAULT 0,
    exchange_charges REAL DEFAULT 0,
    sebi_charges REAL DEFAULT 0,
    stamp_duty REAL DEFAULT 0,
    gst REAL DEFAULT 0,
    total_charges REAL DEFAULT 0,

    net_pnl REAL,

    state TEXT NOT NULL,                 -- OPEN / CLOSED / SKIPPED
    created_at INTEGER NOT NULL
);

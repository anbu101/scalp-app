-- =========================================================
-- 021_create_tma_trades.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   TMA_V1 is a 2-leg trend-following CREDIT SPREAD on NIFTY weekly
--   options (Triple-EMA 5/13/89 @5m spot signals): one SELL leg (the
--   monitored leg — carries SL/TP and drives every exit) + one BUY
--   hedge leg (same option type, deeper OTM). One logical trade is the
--   PAIR, linked by group_id; each leg is one row (direction SELL|BUY).
--
--   mode PAPER|LIVE per row (dynamic-mode stamp, PST convention).
--   exit_reason is UNCONSTRAINED TEXT (migration-020 convention —
--   vocabulary is owned by the engine: SL, TP, XOVER, EOD, MTM_CUT,
--   UNWIND, BROKER_EXIT, STALE_RESTART, MANUAL).
--
--   No other strategy reads or writes tma_trades — creation is fully
--   isolated. CREATE TABLE IF NOT EXISTS makes re-runs safe.
-- =========================================================

CREATE TABLE IF NOT EXISTS tma_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    group_id        TEXT NOT NULL,                  -- links SELL + BUY legs of one spread
    mode            TEXT NOT NULL DEFAULT 'PAPER',  -- PAPER | LIVE (stamped at entry)
    direction       TEXT NOT NULL,                  -- SELL (monitored) | BUY (hedge)

    tradingsymbol   TEXT NOT NULL,
    token           INTEGER,
    instrument_type TEXT,                           -- CE | PE (the traded option type)
    trend_side      TEXT,                           -- signal trend: CE=bullish | PE=bearish
    strike          REAL,
    expiry          TEXT,                           -- contract expiry ISO (era-aware at entry)

    qty             INTEGER NOT NULL,
    entry_ts        INTEGER NOT NULL,               -- fill-candle completion stamp (parity)
    entry_price     REAL NOT NULL,
    sl              REAL,                           -- SELL leg premium SL level (NULL = off)
    tp              REAL,                           -- SELL leg premium TP level (NULL = off)

    entry_order_id  TEXT,                           -- live entry order id
    sell_gtt_id     TEXT,                           -- SELL leg SL GTT id (live) — load-bearing for exit

    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,                           -- unconstrained (migration-020 convention)
    status          TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | CLOSED | STALE

    condition       TEXT DEFAULT 'C1',
    ambiguous       INTEGER NOT NULL DEFAULT 0,
    pnl             REAL,
    charges         REAL,
    net_pnl         REAL,

    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Manager scan: find the OPEN group fast (restart adoption + EOD hygiene).
CREATE INDEX IF NOT EXISTS idx_tma_trades_status
    ON tma_trades (status, entry_ts);

-- Group lookup: both legs of one spread (frontend grouping, exit fan-out).
CREATE INDEX IF NOT EXISTS idx_tma_trades_group
    ON tma_trades (group_id);

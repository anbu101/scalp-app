-- =========================================================
-- 024_create_tma2_trades.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   TMA_V2 is a 2-leg trend-following CREDIT SPREAD on NIFTY weekly
--   options (four-EMA STACK 13/55/89/144 @5m spot signals): one SELL
--   leg (the monitored leg — carries SL/TP and drives every exit) + one
--   BUY hedge leg (same option type, deeper OTM). One logical trade is
--   the PAIR, linked by group_id; each leg is one row (direction
--   SELL|BUY).
--
--   Differences from tma_trades (migration 021), which is why this is a
--   SEPARATE table rather than a shared one:
--     * condition vocabulary is E1|E2 (bearish / bullish stack) rather
--       than TMA_V1's single C1 — and ONE position at a time spans BOTH
--       directions (backtest D2, a single shared slot).
--     * TMA_V1 may be retired independently; neither strategy's rows,
--       migrations or engine code touch the other's.
--
--   mode PAPER|LIVE per row (dynamic-mode stamp, PST convention).
--   exit_reason is UNCONSTRAINED TEXT (migration-020 convention —
--   vocabulary is owned by the engine: SL, TP, XOVER, EOD, MTM_CUT,
--   UNWIND, BROKER_EXIT, STALE_RESTART, MANUAL).
--
--   No other strategy reads or writes tma2_trades — creation is fully
--   isolated. CREATE TABLE IF NOT EXISTS makes re-runs safe. The engine's
--   TMA2Repo.ensure_schema() runs the SAME DDL for fresh installs / dev
--   servers that start before the migration runner (PSTRepo doctrine).
-- =========================================================

CREATE TABLE IF NOT EXISTS tma2_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'PAPER',
    direction       TEXT NOT NULL,
    tradingsymbol   TEXT NOT NULL,
    token           INTEGER,
    instrument_type TEXT,
    trend_side      TEXT,
    strike          REAL,
    expiry          TEXT,
    qty             INTEGER NOT NULL,
    entry_ts        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    sl              REAL,
    tp              REAL,
    entry_order_id  TEXT,
    sell_gtt_id     TEXT,
    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    condition       TEXT DEFAULT 'E1',
    ambiguous       INTEGER NOT NULL DEFAULT 0,
    pnl             REAL,
    charges         REAL,
    net_pnl         REAL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_tma2_trades_status ON tma2_trades (status, entry_ts);
CREATE INDEX IF NOT EXISTS idx_tma2_trades_group  ON tma2_trades (group_id);
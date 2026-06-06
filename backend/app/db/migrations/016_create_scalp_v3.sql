-- =========================================================
-- 016_create_scalp_v3.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   SCALP_V3 is a TEST option-BUYING strategy derived from SCALP_V1.
--   It reuses SCALP_V1's selection + signal logic, but DIVERGES at
--   execution: the contract that fires the signal (the "signal"
--   instrument, e.g. 24500CE) is NEVER traded — it is only tracked
--   for its SL/TP. Instead, V3 BUYS the highest-premium opposite-side
--   option (the "hedge" instrument, e.g. 24450PE) and protects it with
--   an SL-only GTT at (hedge_fill - MAX_SL). The hedge is exited when
--   EITHER the signal contract hits its own SL/TP, OR the hedge's own
--   SL-only GTT fires.
--
--   Because ONE logical trade spans TWO different instruments in two
--   different premium spaces, V3 CANNOT reuse trades / paper_trades
--   (single-symbol schema). This migration creates ONE own table,
--   scalp_v3_trades, with a paper flag (0=live, 1=paper) — mirroring
--   the scalp_v2_groups single-table+paper-flag convention.
--
--   No other strategy (BB_V1, BB_V2, HA_V1, SCALP_V1, SCALP_V2) reads
--   or writes this table, so creation is fully isolated. Removing V3
--   later = DROP this one table + delete the V3 engine package.
--
-- CREATE TABLE IF NOT EXISTS makes this migration safe to re-run.
-- =========================================================

CREATE TABLE IF NOT EXISTS scalp_v3_trades (
    v3_trade_id        TEXT PRIMARY KEY,
    strategy_name      TEXT NOT NULL DEFAULT 'SCALP_V3',
    session_date       TEXT,
    paper              INTEGER NOT NULL DEFAULT 0,    -- 0 = live, 1 = paper

    -- ── SIGNAL instrument (tracked, NEVER traded) ──
    -- e.g. 24500CE. Drives WHEN to exit via its own SL/TP.
    signal_symbol      TEXT NOT NULL,
    signal_token       INTEGER NOT NULL,
    signal_side        TEXT,                          -- 'CE' | 'PE'
    signal_entry_price REAL,                          -- CE close at signal candle
    signal_sl          REAL,                          -- ABOVE signal_entry (CE premium rising = CE-short loss)
    signal_tp          REAL,                          -- BELOW signal_entry (prev red low)
    signal_candle_ts   INTEGER,

    -- ── HEDGE instrument (BOUGHT, protected, exited) ──
    -- e.g. 24450PE. LONG. Highest-premium opposite-side selection.
    hedge_symbol       TEXT NOT NULL,
    hedge_token        INTEGER NOT NULL,
    hedge_side         TEXT,                          -- 'CE' | 'PE' (opposite of signal_side)
    hedge_direction    TEXT NOT NULL DEFAULT 'LONG',
    hedge_qty          INTEGER NOT NULL,
    hedge_entry_price  REAL,                          -- true buy fill (updated after fill confirm)
    hedge_sl           REAL,                          -- hedge_entry - MAX_SL (absolute pts), SL-only
    hedge_order_id     TEXT,                          -- entry BUY order id (live only)
    hedge_gtt_id       TEXT,                          -- SL-only GTT id (live only) — load-bearing for exit

    -- ── lifecycle ──
    state              TEXT NOT NULL DEFAULT 'OPEN',  -- 'OPEN' | 'CLOSED'
    exit_price         REAL,                          -- hedge exit fill
    exit_order_id      TEXT,
    exit_reason        TEXT,                          -- SIG_SL|SIG_TP|HEDGE_SL|EOD|MANUAL|BROKER_EXIT
    realized_pnl       REAL,                          -- (exit - hedge_entry) * hedge_qty   [LONG]

    entry_time         INTEGER DEFAULT (strftime('%s','now')),
    exit_time          INTEGER,
    created_at         INTEGER DEFAULT (strftime('%s','now')),
    updated_at         INTEGER DEFAULT (strftime('%s','now'))
);

-- Global single-trade gate + manager scan: find the OPEN V3 trade fast.
CREATE INDEX IF NOT EXISTS ix_scalp_v3_trades_state
    ON scalp_v3_trades (strategy_name, paper, state);

-- Signal-token watcher map: tick dispatch resolves signal_token -> open trade.
CREATE INDEX IF NOT EXISTS ix_scalp_v3_trades_signal_token
    ON scalp_v3_trades (signal_token, state);

-- Hedge-token lookup: tick exit for the hedge's own SL (paper) + reconcile.
CREATE INDEX IF NOT EXISTS ix_scalp_v3_trades_hedge_token
    ON scalp_v3_trades (hedge_token, state);
-- =========================================================
-- 018_create_scalpv5.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   SCALP_V5 is a TEST option-BUYING strategy on 3-minute candles.
--   It reuses SCALP_V1's indicator engine (EMA8 / EMA20_low / EMA20_high
--   / RSI) but with DIFFERENT entry/exit conditions and LONG geometry.
--   Unlike SCALP_V3/V4 (which track a signal contract and buy a separate
--   hedge), V5 BUYS THE SIGNALLING CONTRACT ITSELF — ONE logical trade =
--   ONE instrument. The schema is therefore single-instrument.
--
--   Entry (all on the completed 3m candle):
--     1. green candle (close > open)
--     2. EMA8 CROSSES ABOVE EMA20_HIGH (transition candle only:
--        prev ema8 <= prev ema20_high AND now ema8 > now ema20_high)
--     3. close > ema20_high
--   Exit: first of
--     - candle closes below EMA20_HIGH  (close < ema20_high)  → EMA_EXIT
--     - SL (ltp <= sl_price) / TP (ltp >= tp_price)
--     - MTM (max_loss / max_profit) / EOD
--   (No time-based exit. SL/TP are absolute config points; 0 = disabled.)
--
--   No other strategy (BB_V1, BB_V2, HA_V1, SCALP_V1..V4) reads or writes
--   this table, so creation is fully isolated. Removing V5 later = DROP this
--   one table + delete the app/engine/scalpv5/ package.
--
-- CREATE TABLE IF NOT EXISTS makes this migration safe to re-run.
-- =========================================================

CREATE TABLE IF NOT EXISTS scalpv5_trades (
    v5_trade_id        TEXT PRIMARY KEY,
    strategy_name      TEXT NOT NULL DEFAULT 'SCALP_V5',
    session_date       TEXT,
    paper              INTEGER NOT NULL DEFAULT 0,     -- 0 = live, 1 = paper

    -- ── traded instrument (the signalling contract IS the position) ──
    symbol             TEXT NOT NULL,
    token              INTEGER NOT NULL,
    side               TEXT,                           -- 'CE' | 'PE'
    direction          TEXT NOT NULL DEFAULT 'LONG',   -- always LONG (buy)
    qty                INTEGER NOT NULL,

    -- ── entry / risk levels ──
    entry_price        REAL,                           -- provisional limit → true fill
    sl_price           REAL,                           -- entry - sl_points (NULL if disabled)
    tp_price           REAL,                           -- entry + tp_points (NULL if disabled)
    entry_candle_ts    INTEGER,                        -- candle.end_ts at entry (audit only)

    order_id           TEXT,                           -- entry BUY order id (live only)
    gtt_id             TEXT,                           -- OCO / SL-only GTT id (live; NULL when no GTT)

    -- ── lifecycle ──
    state              TEXT NOT NULL DEFAULT 'OPEN',   -- 'OPEN' | 'CLOSED'
    exit_price         REAL,
    exit_order_id      TEXT,
    exit_reason        TEXT,                           -- EMA_EXIT|SL|TP|MAX_LOSS|MAX_PROFIT|EOD|MANUAL|BROKER_EXIT|ENTRY_TIMEOUT|STALE_RECONCILE
    realized_pnl       REAL,                           -- (exit - entry) * qty   [LONG]

    entry_time         INTEGER DEFAULT (strftime('%s','now')),
    exit_time          INTEGER,
    created_at         INTEGER DEFAULT (strftime('%s','now')),
    updated_at         INTEGER DEFAULT (strftime('%s','now'))
);

-- Global single-trade gate + manager scan: find the OPEN V5 trade fast.
CREATE INDEX IF NOT EXISTS ix_scalpv5_trades_state
    ON scalpv5_trades (strategy_name, paper, state);

-- Token watcher map: tick dispatch resolves token -> open trade (SL/TP/exit).
CREATE INDEX IF NOT EXISTS ix_scalpv5_trades_token
    ON scalpv5_trades (token, state);
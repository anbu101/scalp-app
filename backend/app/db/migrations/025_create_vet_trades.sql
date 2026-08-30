-- =========================================================
-- 025_create_vet_trades.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   VET_V1 is a trend-following options strategy on 5m NIFTY SPOT
--   (dual-EMA 10/20 + SMA(trend)±ATR×0.618 regime channel, transition-only
--   signals, RANGE-HOLD). It runs FOUR sealed configurations from ONE
--   runtime, selected entirely from Settings:
--       option BUYING   — long CE on an up-trend, long PE on a down-trend
--       option SELLING  — short PE on an up-trend, short CE on a down-trend,
--                         optionally with a protective long WING
--       intraday        — eod_square ON, flat by 15:15
--       positional      — eod_square OFF, carries overnight
--
--   One logical position is therefore ONE leg (buying, or naked selling) or
--   TWO legs (selling + wing), linked by group_id, each leg one row.
--   leg_role is MAIN | WING; direction is LONG | SHORT.
--
-- Why a private table rather than generic paper_trades:
--   A hedged position is two orders that open and close together and are one
--   economic position. paper_trades has no way to express the pairing, and
--   two independent generic rows would double the trade count in every
--   downstream view while halving the apparent win rate (a wing is almost
--   always a small loser). Same reasoning, and the same shape, as
--   tma2_trades (migration 024).
--
-- Differences from tma2_trades, which is why this is a separate table:
--   * no sl / tp / sell_gtt_id columns — every sealed VET config runs
--     sl_pct=0 and tp_pct=0, so there is NO GTT layer at all. Exits are
--     FLIP, SIGNAL_EXIT, EXPIRY_EXIT, EOD and KILL, decided by the engine
--     at 5m closes.
--   * signal_bar_ts + condition — the 5m bar and its regime state (-1|0|+1)
--     that produced the entry. These make live-vs-backtest parity diffable
--     per trade rather than only in aggregate.
--   * leg_action — the config in force at entry, so a row taken in BUY mode
--     stays interpretable after the setting is flipped to SELL.
--
-- NOTE ON WINGS: live wings are REAL contracts only. The backtest may PRICE
-- a wing that never printed (ic_synth_wing); live cannot buy one, so when no
-- real contract sits under the cap the ENTRY IS SKIPPED and no row is
-- written. There is deliberately no synthetic flag here — its presence would
-- imply live could produce one.
--
-- Rollback: DROP TABLE vet_trades;  (paper-only data; safe once VET_V1 is
-- removed from strategy_registry and the runtime is not launching)
-- =========================================================

CREATE TABLE IF NOT EXISTS vet_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT    NOT NULL,
    leg_role        TEXT    NOT NULL DEFAULT 'MAIN',   -- MAIN | WING
    mode            TEXT    NOT NULL DEFAULT 'PAPER',  -- PAPER | LIVE
    direction       TEXT    NOT NULL,                  -- LONG | SHORT
    tradingsymbol   TEXT    NOT NULL,
    token           INTEGER,
    instrument_type TEXT,                              -- CE | PE
    strike          REAL,
    expiry          TEXT,
    qty             INTEGER NOT NULL,
    lots            INTEGER,
    lot_size        INTEGER,
    entry_ts        INTEGER NOT NULL,
    entry_price     REAL    NOT NULL,
    entry_order_id  TEXT,
    signal_bar_ts   INTEGER,
    condition       INTEGER,
    leg_action      TEXT,
    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,
    exit_order_id   TEXT,
    status          TEXT    NOT NULL DEFAULT 'OPEN',   -- OPEN | CLOSED | STALE
    pnl             REAL,
    charges         REAL,
    net_pnl         REAL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_vet_trades_status ON vet_trades (status, entry_ts);
CREATE INDEX IF NOT EXISTS idx_vet_trades_group  ON vet_trades (group_id);
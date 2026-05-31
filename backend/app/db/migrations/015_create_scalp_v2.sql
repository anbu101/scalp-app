-- =========================================================
-- 015_create_scalp_v2.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   SCALP_V2 is a 3-class order-splitting short-selling strategy
--   managed under a Model B group manager. Each "group" represents
--   one logical short position split across 3 trade classes, with a
--   master class driving entry and a tick-driven 15s staggered exit.
--
--   This migration creates ONLY the scalp_v2_groups table. It does
--   NOT touch trades / paper_trades (those get group_id + trade_class
--   via guarded ADD COLUMN in runner.py, since SQLite lacks
--   ADD COLUMN IF NOT EXISTS and bare ALTERs here would hard-fail on
--   re-run and corrupt the migration-complete marker).
--
--   No other strategy (BB_V1, BB_V2, HA_V1, SCALP_V1) reads or writes
--   this table, so creation is fully isolated.
--
-- CREATE TABLE IF NOT EXISTS makes this migration safe to re-run.
-- =========================================================

CREATE TABLE IF NOT EXISTS scalp_v2_groups (
    group_id          TEXT PRIMARY KEY,
    session_date      TEXT,
    paper             INTEGER NOT NULL DEFAULT 0,   -- 0 = live, 1 = paper
    direction         TEXT NOT NULL DEFAULT 'SHORT',
    master_class      TEXT,
    master_instrument TEXT,
    status            TEXT NOT NULL DEFAULT 'PENDING',
    sl_pct            REAL,
    tp_pct            REAL,
    entry_signal_ts   INTEGER,
    exit_trigger_ts   INTEGER,
    exit_reason       TEXT,
    realized_pnl      REAL DEFAULT 0.0,
    created_at        INTEGER DEFAULT (strftime('%s','now')),
    updated_at        INTEGER DEFAULT (strftime('%s','now'))
);

-- Fast lookup of active groups per session (group manager polls these).
CREATE INDEX IF NOT EXISTS ix_scalp_v2_groups_session_status
    ON scalp_v2_groups (session_date, paper, status);
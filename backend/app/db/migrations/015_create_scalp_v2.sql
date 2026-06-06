-- =========================================================
-- 015_create_scalp_v2.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   SCALP_V2 is a 3-leg option-SELLING strategy (SHORT). One logical
--   trade is a GROUP of legs managed together: the signal-strike leg
--   (L1, the master trigger) plus staggered legs (L2/L3). Group-level
--   lifecycle (status, direction, SL/TP %, realised P&L) is tracked in
--   scalp_v2_groups; the individual legs are recorded as rows in the
--   shared trades / paper_trades tables, tagged with group_id +
--   trade_class (added by the runner's _ensure_scalp_v2_trade_columns).
--
--   No other strategy (BB_V1, BB_V2, HA_V1, SCALP_V1, SCALP_V3) reads
--   or writes scalp_v2_groups, so creation is fully isolated.
--
-- CREATE TABLE IF NOT EXISTS makes this migration safe to re-run.
-- =========================================================

CREATE TABLE IF NOT EXISTS scalp_v2_groups (
    group_id           TEXT PRIMARY KEY,
    session_date       TEXT,
    paper              INTEGER NOT NULL DEFAULT 0,    -- 0 = live, 1 = paper

    direction          TEXT NOT NULL DEFAULT 'SHORT', -- SCALP_V2 is option-selling

    -- ── Master (L1 / signal-strike) leg metadata ──
    master_class       TEXT,                          -- leg-class label for the master trigger
    master_instrument  TEXT,                          -- signal-strike tradingsymbol

    -- ── Group lifecycle ──
    status             TEXT NOT NULL DEFAULT 'PENDING', -- PENDING | OPEN | CLOSED ...
    sl_pct             REAL,
    tp_pct             REAL,
    entry_signal_ts    INTEGER,
    exit_trigger_ts    INTEGER,
    exit_reason        TEXT,
    realized_pnl       REAL DEFAULT 0.0,

    created_at         INTEGER DEFAULT (strftime('%s','now')),
    updated_at         INTEGER DEFAULT (strftime('%s','now'))
);

-- Session lookup: list today's groups by recency.
CREATE INDEX IF NOT EXISTS ix_scalp_v2_groups_session
    ON scalp_v2_groups (session_date, status);
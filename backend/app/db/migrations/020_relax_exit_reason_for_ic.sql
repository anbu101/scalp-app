-- =========================================================
-- 020_relax_exit_reason_for_ic.sql
-- REMOVE trades.exit_reason CHECK CONSTRAINT
-- =========================================================
-- Problem:
--   009's exit_reason CHECK allows only:
--     'TP','SL','MANUAL','BROKER_EXIT','GTT_TP','GTT_SL',
--     'SuperTrend','EOD_SQUARE_OFF'
--
--   IC_V1 live exits use MTC_COST / EOD_MTC / EOD /
--   MTC_MARKET_OUT (Move-To-Cost vocabulary, identical to the
--   backtest). Under the old constraint close_trade() raises,
--   the exception is caught+logged, and the row is STUCK OPEN —
--   which then trips uniq_open_trade_per_slot on the next day's
--   entry for the same slot (L1..L4).
--
--   (Same latent failure class exists for SCALP_V2's live
--   'EOD_SQUAREOFF' / 'GROUP_EXIT' reasons — silently logged as
--   [V2][LIVE_EXIT_FAIL]. This migration fixes those too.)
--
-- Fix:
--   Recreate trades with exit_reason as UNCONSTRAINED TEXT —
--   the same convention the newer per-strategy tables already
--   use (scalp_v3/v4/scalpv5 all have exit_reason TEXT with a
--   comment, no CHECK). Reason vocabulary is owned by the
--   engines, not the schema.
--
--   tp_mode and state CHECKs are DELIBERATELY UNCHANGED — any
--   widening there is a separate decision (see V2 live-entry
--   note in the IC_V1 wiring review).
--
-- Preserves (verified against the live effective schema, i.e.
-- 009 + migration 014 + runner hotfix columns):
--   * strategy_id, trade_direction, group_id, trade_class
--   * uniq_open_trade_per_slot partial unique index
--   * prevent_double_close and validate_exit_price triggers
--   * the 019 STATE-SCOPED version of lock_entry_fields
--     (NOT 009's original — HA's limit→fill entry correction
--      depends on the state-scoped version)
--
-- Safe:
--   Temporary table swap, single transaction, idempotent
--   (second run recreates an identical table; INSERT copies
--   the same rows).
-- =========================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Step 1: replacement table — identical except exit_reason ──
CREATE TABLE IF NOT EXISTS trades_v3 (
    trade_id        TEXT PRIMARY KEY,
    strategy_id     TEXT,

    -- SLOT INFO
    slot            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    token           INTEGER NOT NULL,

    -- ENTRY
    entry_time      INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    qty             INTEGER NOT NULL,
    buy_order_id    TEXT NOT NULL,

    -- RISK / TARGET
    sl_price        REAL NOT NULL,
    sl_order_id     TEXT,
    tp_price        REAL NOT NULL,
    tp_mode         TEXT NOT NULL CHECK (
                        tp_mode IN ('AUTO_RR', 'MANUAL', 'GTT')
                    ),

    -- LIFECYCLE STATE
    state           TEXT NOT NULL CHECK (
                        state IN ('BUY_PLACED', 'PROTECTED', 'CLOSED')
                    ),

    -- EXIT (exit_reason: engine-owned vocabulary, no CHECK —
    -- matches scalp_v3/v4/scalpv5 convention)
    exit_time       INTEGER,
    exit_price      REAL,
    exit_order_id   TEXT,
    exit_reason     TEXT,

    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),

    -- post-009 columns (migration 014 + runner hotfix)
    trade_direction TEXT NOT NULL DEFAULT 'LONG',
    group_id        TEXT,
    trade_class     TEXT
);

-- ── Step 2: copy all existing data (explicit column lists) ────
INSERT OR IGNORE INTO trades_v3 (
    trade_id, strategy_id, slot, symbol, token,
    entry_time, entry_price, qty, buy_order_id,
    sl_price, sl_order_id, tp_price, tp_mode,
    state, exit_time, exit_price, exit_order_id, exit_reason,
    created_at, trade_direction, group_id, trade_class
)
SELECT
    trade_id, strategy_id, slot, symbol, token,
    entry_time, entry_price, qty, buy_order_id,
    sl_price, sl_order_id, tp_price, tp_mode,
    state, exit_time, exit_price, exit_order_id, exit_reason,
    created_at, trade_direction, group_id, trade_class
FROM trades;

-- ── Step 3: swap ───────────────────────────────────────────────
DROP TABLE trades;
ALTER TABLE trades_v3 RENAME TO trades;

-- ── Step 4: recreate unique index ─────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uniq_open_trade_per_slot
ON trades(slot)
WHERE exit_time IS NULL;

-- ── Step 5: recreate triggers ─────────────────────────────────

CREATE TRIGGER IF NOT EXISTS prevent_double_close
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN OLD.exit_time IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Trade already closed');
END;

-- 019's STATE-SCOPED version (entry_price mutable only in BUY_PLACED)
DROP TRIGGER IF EXISTS lock_entry_fields;
CREATE TRIGGER lock_entry_fields
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN
    (OLD.state != 'BUY_PLACED' AND OLD.entry_price != NEW.entry_price)
    OR OLD.qty          != NEW.qty
    OR OLD.buy_order_id != NEW.buy_order_id
BEGIN
    SELECT RAISE(ABORT, 'Entry fields are immutable');
END;

CREATE TRIGGER IF NOT EXISTS validate_exit_price
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN NEW.exit_price IS NOT NULL
AND  NEW.exit_price <= 0
BEGIN
    SELECT RAISE(ABORT, 'Invalid exit price');
END;

COMMIT;

PRAGMA foreign_keys = ON;
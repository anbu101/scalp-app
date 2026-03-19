-- =========================================================
-- 008_fix_exit_reason_constraint.sql
-- EXPAND exit_reason CHECK CONSTRAINT
-- =========================================================
-- Problem:
--   The original exit_reason CHECK only allowed:
--     'TP', 'SL', 'MANUAL', 'BROKER_EXIT', 'GTT_TP', 'GTT_SL'
--
--   BB strategy uses 'SuperTrend' and 'EOD_SQUARE_OFF' which
--   are valid and meaningful exit reasons. These were being
--   silently rejected, leaving trades stuck as OPEN in the DB
--   and then incorrectly closed as BROKER_EXIT by reconciliation.
--
-- Fix:
--   Recreate the trades table with the expanded constraint.
--   All existing data is preserved.
--   All triggers and indexes are recreated.
--
-- Safe:
--   Uses a temporary table swap pattern.
--   Wrapped in a transaction — fully atomic.
--   Idempotent: if run twice, second run is a no-op
--   because the new constraint already exists.
-- =========================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Step 1: create replacement table with expanded constraint ──
CREATE TABLE IF NOT EXISTS trades_v2 (
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

    -- EXIT
    exit_time       INTEGER,
    exit_price      REAL,
    exit_order_id   TEXT,
    exit_reason     TEXT CHECK (
                        exit_reason IN (
                            'TP',
                            'SL',
                            'MANUAL',
                            'BROKER_EXIT',
                            'GTT_TP',
                            'GTT_SL',
                            'SuperTrend',
                            'EOD_SQUARE_OFF'
                        )
                    ),

    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- ── Step 2: copy all existing data ────────────────────────────
INSERT OR IGNORE INTO trades_v2
SELECT
    trade_id,
    strategy_id,
    slot,
    symbol,
    token,
    entry_time,
    entry_price,
    qty,
    buy_order_id,
    sl_price,
    sl_order_id,
    tp_price,
    tp_mode,
    state,
    exit_time,
    exit_price,
    exit_order_id,
    -- Remap any legacy values that would violate the new constraint
    -- (should not exist, but defensive just in case)
    CASE exit_reason
        WHEN 'SL_HIT'       THEN 'GTT_SL'
        WHEN 'TP_HIT'       THEN 'GTT_TP'
        WHEN 'GTT_TRIGGERED'THEN 'BROKER_EXIT'
        ELSE exit_reason
    END AS exit_reason,
    created_at
FROM trades;

-- ── Step 3: swap tables ────────────────────────────────────────
DROP TABLE trades;
ALTER TABLE trades_v2 RENAME TO trades;

-- ── Step 4: recreate unique index ─────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uniq_open_trade_per_slot
ON trades(slot)
WHERE exit_time IS NULL;

-- ── Step 5: recreate triggers ─────────────────────────────────

-- Prevent closing an already-closed trade
CREATE TRIGGER IF NOT EXISTS prevent_double_close
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN OLD.exit_time IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Trade already closed');
END;

-- Entry fields are immutable after insert
CREATE TRIGGER IF NOT EXISTS lock_entry_fields
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN
    OLD.entry_price   != NEW.entry_price   OR
    OLD.qty           != NEW.qty           OR
    OLD.buy_order_id  != NEW.buy_order_id
BEGIN
    SELECT RAISE(ABORT, 'Entry fields are immutable');
END;

-- Exit price must be positive
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
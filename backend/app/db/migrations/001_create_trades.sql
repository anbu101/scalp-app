CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,

    -- SLOT INFO
    slot TEXT NOT NULL,                  -- CE_1 / CE_2 / PE_1 / PE_2
    symbol TEXT NOT NULL,
    token INTEGER NOT NULL,

    -- ENTRY
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    qty INTEGER NOT NULL,
    buy_order_id TEXT NOT NULL,

    -- RISK / TARGET
    sl_price REAL NOT NULL,
    sl_order_id TEXT,                    -- SL-M order OR GTT ID
    tp_price REAL NOT NULL,
    tp_mode TEXT NOT NULL CHECK (
        tp_mode IN ('AUTO_RR', 'MANUAL', 'GTT')
    ),

    -- 🔒 TRADE LIFECYCLE STATE (CRITICAL)
    state TEXT NOT NULL CHECK (
        state IN ('BUY_PLACED', 'PROTECTED', 'CLOSED')
    ),

    -- EXIT
    exit_time INTEGER,
    exit_price REAL,
    exit_order_id TEXT,
    exit_reason TEXT CHECK (
        exit_reason IN (
            'TP',
            'SL',
            'MANUAL',
            'BROKER_EXIT',
            'GTT_TP',
            'GTT_SL'
        )
    ),

    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- =====================================================
-- 🔒 ONLY ONE OPEN TRADE PER SLOT
-- =====================================================
CREATE UNIQUE INDEX IF NOT EXISTS uniq_open_trade_per_slot
ON trades(slot)
WHERE exit_time IS NULL;

-- =====================================================
-- 🔒 PREVENT DOUBLE CLOSE
-- =====================================================
CREATE TRIGGER IF NOT EXISTS prevent_double_close
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN OLD.exit_time IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Trade already closed');
END;

-- =====================================================
-- 🔒 ENTRY FIELDS IMMUTABLE
-- =====================================================
CREATE TRIGGER IF NOT EXISTS lock_entry_fields
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN
    OLD.entry_price != NEW.entry_price OR
    OLD.qty != NEW.qty OR
    OLD.buy_order_id != NEW.buy_order_id
BEGIN
    SELECT RAISE(ABORT, 'Entry fields are immutable');
END;

-- =====================================================
-- 🔒 EXIT PRICE MUST BE VALID
-- =====================================================
CREATE TRIGGER IF NOT EXISTS validate_exit_price
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN NEW.exit_price IS NOT NULL
AND NEW.exit_price <= 0
BEGIN
    SELECT RAISE(ABORT, 'Invalid exit price');
END;

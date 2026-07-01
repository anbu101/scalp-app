-- =========================================================
-- 009_state_scoped_entry_lock.sql
-- STATE-SCOPE the immutable-entry trigger
-- =========================================================
-- Problem:
--   lock_entry_fields aborts ANY update that changes entry_price, qty, or
--   buy_order_id — including the ONE legitimate correction HA_V1 (and any
--   future confirm-fill strategy) must make: patch the provisional LIMIT entry
--   to the TRUE fill price the moment the broker reports COMPLETE.
--
--   HA writes to this shared `trades` table, records the row at the protective
--   LIMIT price (entry ≈ ltp × 1.03), then confirms the fill in the background
--   and corrects entry_price → true fill. The old trigger blocked that UPDATE
--   ("Entry fields are immutable"), leaving the DB entry permanently at the
--   padded limit while in-memory state held the real fill — a P&L desync on
--   every live HA trade.
--
-- Fix:
--   Replace lock_entry_fields with a STATE-SCOPED version:
--     * entry_price may change ONLY while state = 'BUY_PLACED' (the pre-fill-
--       confirm window). Once the trade is PROTECTED or CLOSED, entry_price is
--       locked again — a filled/closed trade's entry can never be rewritten.
--     * qty and buy_order_id remain immutable in EVERY state (they never
--       legitimately change after insert).
--
--   This preserves the trigger's real purpose (no accidental rewrite of a
--   live/closed trade's entry — the guarantee BB_V1/BB_V2/SCALP_V1 rely on)
--   while permitting the single limit→fill correction.
--
-- Safe:
--   Wrapped in a transaction. Idempotent: DROP ... IF EXISTS then CREATE, so
--   re-running is a no-op that lands the same final trigger.
-- =========================================================

BEGIN TRANSACTION;

DROP TRIGGER IF EXISTS lock_entry_fields;

CREATE TRIGGER lock_entry_fields
BEFORE UPDATE ON trades
FOR EACH ROW
WHEN
    -- entry_price is locked ONCE the trade leaves BUY_PLACED …
    (OLD.state != 'BUY_PLACED' AND OLD.entry_price != NEW.entry_price)
    -- … qty and buy_order_id are locked in EVERY state.
    OR OLD.qty          != NEW.qty
    OR OLD.buy_order_id != NEW.buy_order_id
BEGIN
    SELECT RAISE(ABORT, 'Entry fields are immutable');
END;

COMMIT;
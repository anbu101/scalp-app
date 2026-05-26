-- =========================================================
-- 011_add_trade_direction.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
-- Purpose:
--   SCALP_V1 is switching from option BUYING to option SELLING
--   (short selling). A trade_direction column is needed on both
--   the live trades table and paper_trades table so that:
--     • P&L calculations can flip sign correctly for SHORT trades
--     • GTT exit-reason inference can be direction-aware
--     • Historical LONG trades from BB_V1 / HA_V1 are unaffected
--       (they default to 'LONG')
--
-- All existing rows receive DEFAULT 'LONG' so no existing
-- BB_V1, BB_V2, SCALP_V1 (historic), or HA_V1 records are
-- broken.
--
-- SQLite ADD COLUMN with NOT NULL + DEFAULT is safe because
-- SQLite fills existing rows with the default value.
-- =========================================================

-- ── paper_trades ──────────────────────────────────────────
ALTER TABLE paper_trades
    ADD COLUMN trade_direction TEXT NOT NULL DEFAULT 'LONG';

-- ── live trades ───────────────────────────────────────────
ALTER TABLE trades
    ADD COLUMN trade_direction TEXT NOT NULL DEFAULT 'LONG';

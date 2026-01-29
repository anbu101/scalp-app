-- =========================================================
-- 007_fix_trades_slot.sql
-- SAFE • NO-OP MIGRATION
-- =========================================================
-- Purpose:
--   Historical installs may be missing `trades.slot`.
--   SQLite cannot conditionally ADD COLUMN in pure SQL.
--   This fix is intentionally handled in Python (runner.py)
--   using PRAGMA table_info checks.
--
-- IMPORTANT:
--   • DO NOT add ALTER TABLE here
--   • DO NOT drop or recreate tables
--   • This file exists ONLY to lock migration order
--     and prevent reapplication.
--
-- Status: NO-OP (by design)
-- =========================================================

PRAGMA foreign_keys=off;
PRAGMA foreign_keys=on;

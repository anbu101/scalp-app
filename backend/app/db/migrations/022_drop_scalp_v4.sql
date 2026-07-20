-- 022_drop_scalp_v4.sql
-- SCALP_V4 removed from the app (engine, routes, jobs, UI all deleted).
-- Migration 017 stays in the chain (never rewrite applied history); on a
-- fresh install 017 creates the table and this migration drops it — both
-- idempotent, order-safe.
--
-- ⚠ DESTRUCTIVE: erases all historical SCALP_V4 paper/live rows. If you
-- want to preserve V4 history, delete THIS file before shipping — all code
-- reads were removed regardless, so an orphaned table is harmless.

DROP TABLE IF EXISTS scalp_v4_trades;

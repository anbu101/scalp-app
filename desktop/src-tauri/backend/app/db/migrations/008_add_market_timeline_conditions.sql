-- =====================================================
-- 003_add_market_timeline_conditions.sql
-- SAFE, IDEMPOTENT, BACKWARD-COMPATIBLE
-- =====================================================

-- SQLite does NOT support IF NOT EXISTS for columns,
-- so these are safe because ADD COLUMN only runs once
-- per DB lifecycle (guarded by schema_migrations).

ALTER TABLE market_timeline ADD COLUMN cond_close_gt_open INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_close_gt_ema8 INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_close_ge_ema20 INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_close_not_above_ema20 INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_not_touching_high INTEGER;

ALTER TABLE market_timeline ADD COLUMN cond_rsi_ge_40 INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_rsi_le_65 INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_rsi_range INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_rsi_rising INTEGER;

ALTER TABLE market_timeline ADD COLUMN cond_is_trading_time INTEGER;
ALTER TABLE market_timeline ADD COLUMN cond_no_open_trade INTEGER;

ALTER TABLE market_timeline ADD COLUMN cond_all INTEGER;

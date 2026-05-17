-- =========================================================
-- 013_bb_v2_futures_candles.sql
-- SAFE • IDEMPOTENT • NO DATA LOSS
-- =========================================================
--
-- BB_V1 and BB_V2 share the same BANKNIFTY futures candles
-- but have different SuperTrend parameters (2.0 vs 1.5) and
-- different signals.  Instead of a separate table or using
-- strategy_id as a PK discriminator, we add _v2 suffix columns
-- for every field that differs between the two strategies.
--
-- Shared columns (written by whichever strategy runs first,
-- values are identical):
--   bb_upper, bb_middle, bb_lower, bb_width
--   rsi_raw, rsi_smooth
--   r1, s1
--
-- NEW shared columns (same pivot math, available to both):
--   r2  — Resistance 2  = PP + (H - L)
--   pp  — Pivot Point   = (H + L + C) / 3
--   s2  — Support 2     = PP - (H - L)
--   s3  — Support 3     = S1 - (H - L)
--
-- NEW V2-specific columns:
--   supertrend_v2      — ST(10, 1.5) value
--   st_direction_v2    — "UP" / "DOWN" for ST(10, 1.5)
--   signal_action_v2   — BB_V2 signal (ENTER_CE, EXIT_PE …)
--   signal_reason_v2
--   rejection_reason_v2
--   ce_in_trade_v2
--   pe_in_trade_v2
--   ce_trades_today_v2
--   pe_trades_today_v2
-- =========================================================

-- ── Extended pivot levels (shared, same math for V1 and V2) ──
ALTER TABLE futures_candles ADD COLUMN r2   REAL;
ALTER TABLE futures_candles ADD COLUMN pp   REAL;
ALTER TABLE futures_candles ADD COLUMN s2   REAL;
ALTER TABLE futures_candles ADD COLUMN s3   REAL;

-- ── BB_V2-specific SuperTrend (10, 1.5) ──────────────────────
ALTER TABLE futures_candles ADD COLUMN supertrend_v2    REAL;
ALTER TABLE futures_candles ADD COLUMN st_direction_v2  TEXT;

-- ── BB_V2-specific signal fields ─────────────────────────────
ALTER TABLE futures_candles ADD COLUMN signal_action_v2    TEXT;
ALTER TABLE futures_candles ADD COLUMN signal_reason_v2    TEXT;
ALTER TABLE futures_candles ADD COLUMN rejection_reason_v2 TEXT;

-- ── BB_V2-specific trade-state snapshots ─────────────────────
ALTER TABLE futures_candles ADD COLUMN ce_in_trade_v2     INTEGER;
ALTER TABLE futures_candles ADD COLUMN pe_in_trade_v2     INTEGER;
ALTER TABLE futures_candles ADD COLUMN ce_trades_today_v2 INTEGER;
ALTER TABLE futures_candles ADD COLUMN pe_trades_today_v2 INTEGER;
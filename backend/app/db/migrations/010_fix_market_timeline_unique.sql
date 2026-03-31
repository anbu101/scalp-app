-- =========================================================
-- 010_fix_market_timeline_unique.sql
-- FIX: market_timeline row-erasure bug
-- SAFE • IDEMPOTENT • NO DATA LOSS (keeps newest row per candle)
-- =========================================================
--
-- ROOT CAUSE:
--   market_timeline had no UNIQUE constraint on (symbol, timeframe, ts).
--   If migration 005 created UNIQUE(symbol, timeframe) *without* ts,
--   every INSERT OR IGNORE would delete the previous candle row for
--   that (symbol, timeframe) pair — exactly the 15:10 data being
--   erased when 15:15 arrives.
--
--   Even without 005, the missing UNIQUE on (symbol, timeframe, ts)
--   made INSERT OR IGNORE a plain INSERT, accumulating duplicate rows.
--
-- FIX:
--   1. Drop any bad unique index on (symbol, timeframe) without ts
--   2. Deduplicate existing rows keeping the row with the most
--      indicator data (highest id = latest upsert)
--   3. Create correct UNIQUE INDEX on (symbol, timeframe, ts)
-- =========================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Step 1: Drop the bad index if it exists ────────────────────
-- The plain non-unique index from migration 002 is harmless and
-- kept. Only drop indexes that are UNIQUE and missing ts.
DROP INDEX IF EXISTS uq_market_timeline_symbol_tf;
DROP INDEX IF EXISTS uq_market_timeline;
DROP INDEX IF EXISTS uniq_market_timeline;

-- ── Step 2: Deduplicate — keep highest id per (symbol,timeframe,ts)
-- Highest id = the row that received the final UPDATE with indicators.
DELETE FROM market_timeline
WHERE id NOT IN (
    SELECT MAX(id)
    FROM market_timeline
    GROUP BY symbol, timeframe, ts
);

-- ── Step 3: Create the correct unique index ────────────────────
-- ts is INCLUDED so each candle timestamp is its own row.
-- INSERT OR IGNORE in timeline_repo.py will now correctly skip
-- duplicate inserts for the same candle instead of acting as plain INSERT.
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_timeline_symbol_tf_ts
ON market_timeline (symbol, timeframe, ts);

-- ── Step 4: Replace the old non-unique index (symbol, ts) ─────
-- The old index is now superseded by the unique one above.
DROP INDEX IF EXISTS idx_market_timeline_symbol_ts;

-- A fast covering index for the warmup read (fetch_recent_candles_for_warmup)
CREATE INDEX IF NOT EXISTS idx_market_timeline_symbol_tf_ts_desc
ON market_timeline (symbol, timeframe, ts DESC);

COMMIT;

PRAGMA foreign_keys = ON;
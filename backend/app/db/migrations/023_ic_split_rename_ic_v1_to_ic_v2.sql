-- 023_ic_split_rename_ic_v1_to_ic_v2.sql
--
-- ── IC_SPLIT (2026-08-04, DS3 locked) ──────────────────────────────────────
-- Every trades / paper_trades row tagged IC_V1 before this migration was
-- produced under IC_V2 semantics (NEXT_OPEN / ONE_NIGHT_MAX + ADJ_ON_MTC —
-- the live engine ran those semantics under the IC_V1 name from 2026-07-26).
-- Retagging them keeps per-strategy analytics truthful: the NEW IC_V1
-- (legacy EOD condor) starts with a clean history.
--
-- Transient artifacts (in-app alerts) are deliberately NOT retagged.
UPDATE trades       SET strategy_id   = 'IC_V2' WHERE strategy_id   = 'IC_V1';
UPDATE paper_trades SET strategy_name = 'IC_V2' WHERE strategy_name = 'IC_V1';

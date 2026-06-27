-- backend/app/backtest/repo/schema.sql
--
-- BACKTEST STORAGE SCHEMA  (fully isolated — no live table is referenced)
--
-- Tables:
--   backtest_candles_1m   historical 1-minute OHLC corpus (Kite backfill)
--   backtest_candles_1s   forward-recorded 1-second bars (recorder, later)
--   backtest_runs         one row per backtest execution + its frozen config
--   backtest_trades       one row per simulated trade  (THIS is the CSV export)
--
-- DESIGN NOTES
--   * Both candle tables share an identical column shape so candle_source can
--     read either with the same code. They are SEPARATE tables on purpose —
--     1m and 1s are different resolutions and must never be mixed as rows
--     (settled decision). 1s is used ONLY to adjudicate ambiguous 1m fills.
--   * PRIMARY KEY (instrument_token, ts) makes backfill IDEMPOTENT:
--       INSERT ... ON CONFLICT(instrument_token, ts) DO UPDATE
--     re-running a backfill overwrites the same rows with fresh values and
--     adds new ones — it NEVER deletes. The corpus only grows.
--   * No FOREIGN KEYs to live tables. Dropping the whole backtest feature is
--     "drop these four tables"; nothing else is touched.

-- ==================================================================
-- 1-MINUTE HISTORICAL CANDLES  (Kite backfill target)
-- ==================================================================
CREATE TABLE IF NOT EXISTS backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts                INTEGER NOT NULL,         -- epoch seconds, candle START, IST grid
    underlying        TEXT    NOT NULL,         -- 'NIFTY' | 'BANKNIFTY'
    tradingsymbol     TEXT    NOT NULL,         -- e.g. NIFTY2511323500CE
    instrument_type   TEXT    NOT NULL,         -- 'CE' | 'PE' | 'FUT'
    strike            REAL    NOT NULL,         -- 0.0 for futures
    expiry            TEXT    NOT NULL,         -- ISO date 'YYYY-MM-DD'
    open              REAL    NOT NULL,
    high              REAL    NOT NULL,
    low               REAL    NOT NULL,
    close             REAL    NOT NULL,
    volume            INTEGER NOT NULL DEFAULT 0,
    oi                INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts)
);

CREATE INDEX IF NOT EXISTS idx_bt1m_sym_ts
    ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX IF NOT EXISTS idx_bt1m_under_exp_ts
    ON backtest_candles_1m (underlying, expiry, ts);

-- ==================================================================
-- 1-SECOND FORWARD CANDLES  (recorder target — built later)
-- Identical shape; separate table. Used only to resolve ambiguous
-- 1-minute fills (both SL and TP inside one minute's range).
-- ==================================================================
CREATE TABLE IF NOT EXISTS backtest_candles_1s (
    instrument_token  INTEGER NOT NULL,
    ts                INTEGER NOT NULL,         -- epoch seconds, 1-second grid
    underlying        TEXT    NOT NULL,
    tradingsymbol     TEXT    NOT NULL,
    instrument_type   TEXT    NOT NULL,
    strike            REAL    NOT NULL,
    expiry            TEXT    NOT NULL,
    open              REAL    NOT NULL,
    high              REAL    NOT NULL,
    low               REAL    NOT NULL,
    close             REAL    NOT NULL,
    volume            INTEGER NOT NULL DEFAULT 0,
    oi                INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts)
);

CREATE INDEX IF NOT EXISTS idx_bt1s_sym_ts
    ON backtest_candles_1s (tradingsymbol, ts);

-- ==================================================================
-- BACKTEST RUNS  (one row per execution; freezes the config used)
-- ==================================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id        TEXT    PRIMARY KEY,          -- uuid4
    strategy_id   TEXT    NOT NULL,             -- 'SCALP_V1'
    underlying    TEXT    NOT NULL,             -- 'NIFTY'
    date_from     TEXT    NOT NULL,             -- ISO date
    date_to       TEXT    NOT NULL,
    config_json   TEXT    NOT NULL,             -- EXACT strategy config used (frozen)
    fill_model    TEXT    NOT NULL,             -- 'pessimistic' (default)
    status        TEXT    NOT NULL,             -- 'running' | 'done' | 'error'
    created_at    INTEGER NOT NULL,             -- epoch seconds
    finished_at   INTEGER,                      -- epoch seconds, NULL until done
    summary_json  TEXT,                         -- pnl, win_rate, trades, max_dd,
                                                 -- ambiguous_fill_count, etc.
    error_text    TEXT                          -- populated on status='error'
);

-- ==================================================================
-- BACKTEST TRADES  (one row per simulated trade — the CSV download)
-- ==================================================================
CREATE TABLE IF NOT EXISTS backtest_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    tradingsymbol   TEXT    NOT NULL,
    instrument_type TEXT    NOT NULL,           -- 'CE' | 'PE'
    strike          REAL    NOT NULL,
    expiry          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,           -- 'SHORT' for SCALP_V1
    entry_ts        INTEGER NOT NULL,
    entry_price     REAL    NOT NULL,
    sl              REAL    NOT NULL,
    tp              REAL,                        -- NULL for V3/V4 hedge (no TP leg)
    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,                        -- 'TP' | 'SL' | 'EOD' | ...
    pnl             REAL,                        -- (entry-exit)*qty for SHORT
    qty             INTEGER NOT NULL,
    ambiguous_fill  INTEGER NOT NULL DEFAULT 0,  -- 1 if SL & TP both inside exit
                                                 -- candle AND resolved by the
                                                 -- pessimistic rule (no 1s data)
    max_adverse     REAL,                        -- worst premium move vs entry
    max_favorable   REAL,                        -- best premium move vs entry
    charges         REAL    NOT NULL DEFAULT 0,  -- round-trip charges (zerodha_charges)
    net_pnl         REAL,                        -- pnl - charges
    -- HEDGE columns (SCALP_V3/V4 only; NULL for V1). The primary row IS the
    -- hedge (tradingsymbol=hedge, direction=LONG, entry/sl=hedge); these add
    -- the tracked signal contract's identity + levels for display/CSV.
    signal_symbol   TEXT,                        -- tracked signal contract (V3/V4)
    signal_side     TEXT,                        -- 'CE' | 'PE' of the signal
    signal_sl       REAL,                        -- signal SL level
    signal_tp       REAL,                        -- signal TP level
    hedge_side      TEXT,                        -- 'CE' | 'PE' of the hedge
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_bttrades_run
    ON backtest_trades (run_id);
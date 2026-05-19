# backend/app/db/futures_candles_repo.py

import sqlite3
from typing import Optional
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

# ==================================================
# COLUMN DEFINITIONS
# ==================================================

BASE_COLUMNS = {
    "symbol":    "TEXT",
    "timeframe": "TEXT",
    "ts":        "INTEGER",
    "open":      "REAL",
    "high":      "REAL",
    "low":       "REAL",
    "close":     "REAL",
}

INDICATOR_COLUMNS = {
    # --- Bollinger (shared V1 + V2) ---
    "bb_middle": "REAL",
    "bb_upper":  "REAL",
    "bb_lower":  "REAL",
    "bb_width":  "REAL",

    # --- RSI (shared V1 + V2) ---
    "rsi_raw":    "REAL",
    "rsi_smooth": "REAL",

    # --- SuperTrend V1 — ST(10, 2.0) ---
    "supertrend":   "REAL",
    "st_direction": "TEXT",

    # --- SuperTrend V2 — ST(10, 1.5) ---
    "supertrend_v2":   "REAL",
    "st_direction_v2": "TEXT",

    # --- Pivots shared — original (R1, S1) ---
    "r1": "REAL",
    "s1": "REAL",

    # --- Pivots shared — extended (R2, PP, S2, S3) ---
    "r2": "REAL",
    "pp": "REAL",
    "s2": "REAL",
    "s3": "REAL",

    # --- Signal V1 ---
    "signal_action":    "TEXT",
    "signal_reason":    "TEXT",
    "rejection_reason": "TEXT",

    # --- Signal V2 ---
    "signal_action_v2":    "TEXT",
    "signal_reason_v2":    "TEXT",
    "rejection_reason_v2": "TEXT",

    # --- Trade State V1 ---
    "ce_in_trade":     "INTEGER",
    "pe_in_trade":     "INTEGER",
    "ce_trades_today": "INTEGER",
    "pe_trades_today": "INTEGER",

    # --- Trade State V2 ---
    "ce_in_trade_v2":     "INTEGER",
    "pe_in_trade_v2":     "INTEGER",
    "ce_trades_today_v2": "INTEGER",
    "pe_trades_today_v2": "INTEGER",
}

# ==================================================
# INIT TABLE (AUTO MIGRATION SAFE)
# ==================================================

def init_table():
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_candles (
            symbol    TEXT,
            timeframe TEXT,
            ts        INTEGER,
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            PRIMARY KEY (symbol, timeframe, ts)
        )
        """
    )

    cur = conn.execute("PRAGMA table_info(futures_candles)")
    existing_cols = {row[1] for row in cur.fetchall()}

    for col, col_type in INDICATOR_COLUMNS.items():
        if col not in existing_cols:
            conn.execute(
                f"ALTER TABLE futures_candles ADD COLUMN {col} {col_type}"
            )
            write_audit_log(
                f"[DB][MIGRATION] Added futures_candles.{col}"
            )

    conn.commit()


# ==================================================
# GET LATEST CANDLE TS
# ==================================================

def get_latest_candle_ts(
    *,
    symbol:    str,
    timeframe: str,
) -> Optional[int]:
    conn = get_conn()

    row = conn.execute(
        """
        SELECT MAX(ts)
        FROM futures_candles
        WHERE symbol    = ?
          AND timeframe = ?
        """,
        (symbol, timeframe),
    ).fetchone()

    if not row or row[0] is None:
        return None

    return int(row[0])


# ==================================================
# INSERT / UPSERT CANDLE
#
# ISOLATION CONTRACT:
#   All state/signal params default to None.
#   None → stored as SQL NULL → COALESCE in the upsert
#   keeps whatever the other strategy already wrote.
#
#   This means:
#     BB_V1 passes real values for V1 columns, None for V2 columns.
#     BB_V2 passes real values for V2 columns, None for V1 columns.
#     Neither strategy ever clobbers the other's data.
#
#   Shared indicator columns (BB bands, RSI, extended pivots) are
#   identical regardless of which engine writes them — no conflict.
# ==================================================

def insert_candle(
    *,
    symbol:    str,
    timeframe: str,
    ts:        int,
    open_:     float,
    high:      float,
    low:       float,
    close:     float,
    indicators: dict = None,

    # V1 signal fields  (None → COALESCE keeps existing V1 value)
    signal_action:    Optional[str]  = None,
    signal_reason:    Optional[str]  = None,
    rejection_reason: Optional[str]  = None,

    # V1 state fields  (None → COALESCE keeps existing V1 value)
    ce_in_trade:     Optional[bool] = None,
    pe_in_trade:     Optional[bool] = None,
    ce_trades_today: Optional[int]  = None,
    pe_trades_today: Optional[int]  = None,

    # V2 signal fields  (None → COALESCE keeps existing V2 value)
    signal_action_v2:    Optional[str]  = None,
    signal_reason_v2:    Optional[str]  = None,
    rejection_reason_v2: Optional[str]  = None,

    # V2 state fields  (None → COALESCE keeps existing V2 value)
    ce_in_trade_v2:     Optional[bool] = None,
    pe_in_trade_v2:     Optional[bool] = None,
    ce_trades_today_v2: Optional[int]  = None,
    pe_trades_today_v2: Optional[int]  = None,
):
    conn = get_conn()
    ind  = indicators or {}

    # --------------------------------------------------
    # Unpack shared indicator fields
    # --------------------------------------------------
    bb_middle  = ind.get("bb_middle")
    bb_upper   = ind.get("bb_upper")
    bb_lower   = ind.get("bb_lower")
    bb_width   = ind.get("bb_width")

    rsi_raw    = ind.get("rsi_raw")
    rsi_smooth = ind.get("rsi_smooth")

    # SuperTrend — keyed differently in each strategy's indicator dict
    supertrend    = ind.get("supertrend")          # V1: ST(10, 2.0) or None
    st_direction  = ind.get("st_direction")        # V1 or None

    supertrend_v2   = ind.get("supertrend_v2")    # V2: ST(10, 1.5) or None
    st_direction_v2 = ind.get("st_direction_v2")  # V2 or None

    # Shared pivots
    r1 = ind.get("r1")
    s1 = ind.get("s1")
    r2 = ind.get("r2")
    pp = ind.get("pp")
    s2 = ind.get("s2")
    s3 = ind.get("s3")

    # --------------------------------------------------
    # Convert Optional[bool] → int or None
    # bool(True)=1, bool(False)=0, None stays None
    # --------------------------------------------------
    def _int_or_none(v):
        return int(v) if v is not None else None

    conn.execute(
        """
        INSERT INTO futures_candles (
            symbol, timeframe, ts,
            open, high, low, close,

            bb_middle, bb_upper, bb_lower, bb_width,
            rsi_raw, rsi_smooth,

            supertrend,    st_direction,
            supertrend_v2, st_direction_v2,

            r1, s1,
            r2, pp, s2, s3,

            signal_action,    signal_reason,    rejection_reason,
            ce_in_trade,      pe_in_trade,
            ce_trades_today,  pe_trades_today,

            signal_action_v2,    signal_reason_v2,    rejection_reason_v2,
            ce_in_trade_v2,      pe_in_trade_v2,
            ce_trades_today_v2,  pe_trades_today_v2
        )
        VALUES (
            ?, ?, ?,
            ?, ?, ?, ?,

            ?, ?, ?, ?,
            ?, ?,

            ?, ?,
            ?, ?,

            ?, ?,
            ?, ?, ?, ?,

            ?, ?, ?,
            ?, ?,
            ?, ?,

            ?, ?, ?,
            ?, ?,
            ?, ?
        )
        ON CONFLICT(symbol, timeframe, ts)
        DO UPDATE SET
            open  = excluded.open,
            high  = excluded.high,
            low   = excluded.low,
            close = excluded.close,

            -- Shared BB
            bb_middle = COALESCE(excluded.bb_middle, bb_middle),
            bb_upper  = COALESCE(excluded.bb_upper,  bb_upper),
            bb_lower  = COALESCE(excluded.bb_lower,  bb_lower),
            bb_width  = COALESCE(excluded.bb_width,  bb_width),

            -- Shared RSI
            rsi_raw    = COALESCE(excluded.rsi_raw,    rsi_raw),
            rsi_smooth = COALESCE(excluded.rsi_smooth, rsi_smooth),

            -- V1 SuperTrend — isolated, COALESCE never lets V2 overwrite
            supertrend   = COALESCE(excluded.supertrend,   supertrend),
            st_direction = COALESCE(excluded.st_direction, st_direction),

            -- V2 SuperTrend — isolated, COALESCE never lets V1 overwrite
            supertrend_v2   = COALESCE(excluded.supertrend_v2,   supertrend_v2),
            st_direction_v2 = COALESCE(excluded.st_direction_v2, st_direction_v2),

            -- Shared pivots (identical values from both strategies)
            r1 = COALESCE(excluded.r1, r1),
            s1 = COALESCE(excluded.s1, s1),
            r2 = COALESCE(excluded.r2, r2),
            pp = COALESCE(excluded.pp, pp),
            s2 = COALESCE(excluded.s2, s2),
            s3 = COALESCE(excluded.s3, s3),

            -- V1 signal fields — COALESCE: None from V2 keeps V1's value
            signal_action    = COALESCE(excluded.signal_action,    signal_action),
            signal_reason    = COALESCE(excluded.signal_reason,    signal_reason),
            rejection_reason = COALESCE(excluded.rejection_reason, rejection_reason),

            -- V1 state fields — COALESCE: None from V2 keeps V1's value
            ce_in_trade     = COALESCE(excluded.ce_in_trade,     ce_in_trade),
            pe_in_trade     = COALESCE(excluded.pe_in_trade,     pe_in_trade),
            ce_trades_today = COALESCE(excluded.ce_trades_today, ce_trades_today),
            pe_trades_today = COALESCE(excluded.pe_trades_today, pe_trades_today),

            -- V2 signal fields — COALESCE: None from V1 keeps V2's value
            signal_action_v2    = COALESCE(excluded.signal_action_v2,    signal_action_v2),
            signal_reason_v2    = COALESCE(excluded.signal_reason_v2,    signal_reason_v2),
            rejection_reason_v2 = COALESCE(excluded.rejection_reason_v2, rejection_reason_v2),

            -- V2 state fields — COALESCE: None from V1 keeps V2's value
            ce_in_trade_v2     = COALESCE(excluded.ce_in_trade_v2,     ce_in_trade_v2),
            pe_in_trade_v2     = COALESCE(excluded.pe_in_trade_v2,     pe_in_trade_v2),
            ce_trades_today_v2 = COALESCE(excluded.ce_trades_today_v2, ce_trades_today_v2),
            pe_trades_today_v2 = COALESCE(excluded.pe_trades_today_v2, pe_trades_today_v2)
        """,
        (
            symbol, timeframe, ts,
            open_, high, low, close,

            bb_middle, bb_upper, bb_lower, bb_width,
            rsi_raw, rsi_smooth,

            supertrend,    st_direction,
            supertrend_v2, st_direction_v2,

            r1, s1,
            r2, pp, s2, s3,

            signal_action,    signal_reason,    rejection_reason,
            _int_or_none(ce_in_trade), _int_or_none(pe_in_trade),
            ce_trades_today,  pe_trades_today,

            signal_action_v2,    signal_reason_v2,    rejection_reason_v2,
            _int_or_none(ce_in_trade_v2), _int_or_none(pe_in_trade_v2),
            ce_trades_today_v2,  pe_trades_today_v2,
        ),
    )

    conn.commit()


# ==================================================
# FETCH RECENT CANDLES (for indicator warmup)
# ==================================================

def fetch_recent_candles(
    *,
    symbol:    str,
    timeframe: str,
    limit:     int = 100,
):
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT *
        FROM futures_candles
        WHERE symbol    = ?
          AND timeframe = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (symbol, timeframe, limit),
    ).fetchall()

    columns = [
        col[1]
        for col in conn.execute(
            "PRAGMA table_info(futures_candles)"
        ).fetchall()
    ]

    result = [dict(zip(columns, row)) for row in rows]
    return list(reversed(result))


# ==================================================
# FETCH PREVIOUS DAY CANDLE (for pivot calculation)
# ==================================================

def fetch_previous_day_candle(*, symbol: str):
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT *
        FROM futures_candles
        WHERE symbol    = ?
          AND timeframe = '1d'
        ORDER BY ts DESC
        LIMIT 2
        """,
        (symbol,),
    ).fetchall()

    if not rows or len(rows) < 2:
        return None

    columns = [
        col[1]
        for col in conn.execute(
            "PRAGMA table_info(futures_candles)"
        ).fetchall()
    ]

    return dict(zip(columns, rows[1]))

# ==================================================
# COMPAT ALIAS (used by debug UI route)
# futures_candles_ui.py imports fetch_candles
# ==================================================

def fetch_candles(
    *,
    symbol:    str,
    timeframe: str,
    limit:     int = 100,
):
    return fetch_recent_candles(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )
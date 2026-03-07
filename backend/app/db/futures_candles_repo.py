import sqlite3
from typing import Optional
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

# ==================================================
# REQUIRED COLUMNS
# ==================================================

BASE_COLUMNS = {
    "symbol": "TEXT",
    "timeframe": "TEXT",
    "ts": "INTEGER",
    "open": "REAL",
    "high": "REAL",
    "low": "REAL",
    "close": "REAL",
}

INDICATOR_COLUMNS = {
    # --- Bollinger ---
    "bb_middle": "REAL",
    "bb_upper": "REAL",
    "bb_lower": "REAL",
    "bb_width": "REAL",

    # --- RSI ---
    "rsi_raw": "REAL",
    "rsi_smooth": "REAL",

    # --- SuperTrend ---
    "supertrend": "REAL",
    "st_direction": "TEXT",

    # --- Pivots ---
    "r1": "REAL",
    "s1": "REAL",

    # --- Signal ---
    "signal_action": "TEXT",
    "signal_reason": "TEXT",
    "rejection_reason": "TEXT",

    # --- Trade State ---
    "ce_in_trade": "INTEGER",
    "pe_in_trade": "INTEGER",
    "ce_trades_today": "INTEGER",
    "pe_trades_today": "INTEGER",
}

# ==================================================
# INIT TABLE (AUTO MIGRATION SAFE)
# ==================================================

def init_table():
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_candles (
            symbol TEXT,
            timeframe TEXT,
            ts INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
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
# 🔥 NEW: GET LATEST CANDLE TS (CRITICAL FOR INCREMENTAL LOAD)
# ==================================================

def get_latest_candle_ts(
    *,
    symbol: str,
    timeframe: str,
) -> Optional[int]:
    """
    Returns latest timestamp stored for given symbol/timeframe.
    Used for incremental historical loading.
    """

    conn = get_conn()

    row = conn.execute(
        """
        SELECT MAX(ts)
        FROM futures_candles
        WHERE symbol = ?
        AND timeframe = ?
        """,
        (symbol, timeframe),
    ).fetchone()

    if not row or row[0] is None:
        return None

    return int(row[0])


# ==================================================
# INSERT / UPSERT CANDLE
# ==================================================

def insert_candle(
    *,
    symbol: str,
    timeframe: str,
    ts: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    indicators: dict = None,
    signal_action: str = None,
    signal_reason: str = None,
    rejection_reason: str = None,
    ce_in_trade: bool = False,
    pe_in_trade: bool = False,
    ce_trades_today: int = 0,
    pe_trades_today: int = 0,
):

    conn = get_conn()

    values = {
        "bb_middle": None,
        "bb_upper": None,
        "bb_lower": None,
        "bb_width": None,
        "rsi_raw": None,
        "rsi_smooth": None,
        "supertrend": None,
        "st_direction": None,
        "r1": None,
        "s1": None,
    }

    if indicators:
        for k in values.keys():
            values[k] = indicators.get(k)

    conn.execute(
        """
        INSERT INTO futures_candles (
            symbol,
            timeframe,
            ts,
            open,
            high,
            low,
            close,
            bb_middle,
            bb_upper,
            bb_lower,
            bb_width,
            rsi_raw,
            rsi_smooth,
            supertrend,
            st_direction,
            r1,
            s1,
            signal_action,
            signal_reason,
            rejection_reason,
            ce_in_trade,
            pe_in_trade,
            ce_trades_today,
            pe_trades_today
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, ts)
        DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            bb_middle=COALESCE(excluded.bb_middle, bb_middle),
            bb_upper=COALESCE(excluded.bb_upper, bb_upper),
            bb_lower=COALESCE(excluded.bb_lower, bb_lower),
            bb_width=COALESCE(excluded.bb_width, bb_width),

            rsi_raw=COALESCE(excluded.rsi_raw, rsi_raw),
            rsi_smooth=COALESCE(excluded.rsi_smooth, rsi_smooth),

            supertrend=COALESCE(excluded.supertrend, supertrend),
            st_direction=COALESCE(excluded.st_direction, st_direction),

            r1=COALESCE(excluded.r1, r1),
            s1=COALESCE(excluded.s1, s1),

            signal_action=COALESCE(excluded.signal_action, signal_action),
            signal_reason=COALESCE(excluded.signal_reason, signal_reason),
            rejection_reason=COALESCE(excluded.rejection_reason, rejection_reason),

            ce_in_trade=excluded.ce_in_trade,
            pe_in_trade=excluded.pe_in_trade,
            ce_trades_today=excluded.ce_trades_today,
            pe_trades_today=excluded.pe_trades_today
        """,
        (
            symbol,
            timeframe,
            ts,
            open_,
            high,
            low,
            close,
            values["bb_middle"],
            values["bb_upper"],
            values["bb_lower"],
            values["bb_width"],
            values["rsi_raw"],
            values["rsi_smooth"],
            values["supertrend"],
            values["st_direction"],
            values["r1"],
            values["s1"],
            signal_action,
            signal_reason,
            rejection_reason,
            int(ce_in_trade),
            int(pe_in_trade),
            ce_trades_today,
            pe_trades_today,
        ),
    )

    conn.commit()


# ==================================================
# FETCH RECENT CANDLES (for indicator warmup)
# ==================================================

def fetch_recent_candles(
    *,
    symbol: str,
    timeframe: str,
    limit: int = 100,
):
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT *
        FROM futures_candles
        WHERE symbol = ?
        AND timeframe = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (symbol, timeframe, limit),
    ).fetchall()

    columns = [col[1] for col in conn.execute(
        "PRAGMA table_info(futures_candles)"
    ).fetchall()]

    result = []
    for row in rows:
        result.append(dict(zip(columns, row)))

    return list(reversed(result))


# ==================================================
# FETCH PREVIOUS DAY CANDLE (for pivots)
# ==================================================

def fetch_previous_day_candle(*, symbol: str):
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT *
        FROM futures_candles
        WHERE symbol = ?
        AND timeframe = '1d'
        ORDER BY ts DESC
        LIMIT 2
        """,
        (symbol,),
    ).fetchall()

    if not rows or len(rows) < 2:
        return None

    columns = [col[1] for col in conn.execute(
        "PRAGMA table_info(futures_candles)"
    ).fetchall()]

    return dict(zip(columns, rows[1]))

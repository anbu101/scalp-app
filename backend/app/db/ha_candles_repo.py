# backend/app/db/ha_candles_repo.py
"""
HA Candles Repository
=====================
Persists 1-minute Heikin Ashi candles for HA_V1 to a dedicated
`ha_candles` table — completely separate from `market_timeline`
(which belongs to SCALP_V1) and `futures_candles` (which belongs
to BB_V1).

Why a separate table?
  - market_timeline uses a different schema (regular OHLC + condition
    columns specific to the SCALP_V1 Pine script logic).
  - If SCALP_V1 later moves to 5m and HA_V1 stays on 1m (or vice
    versa), a shared table would require a compound (symbol, timeframe)
    key — and the indicator columns differ anyway.
  - Keeping tables strategy-scoped avoids accidental cross-contamination
    of indicator values and makes housekeeping simpler.

Schema:
  ha_candles
    symbol      TEXT   — option tradingsymbol (e.g. NIFTY26MAY25000CE)
    timeframe   TEXT   — "1m" (or future "3m", "5m")
    ts          INT    — bucket start epoch (seconds)
    ha_open     REAL
    ha_high     REAL
    ha_low      REAL
    ha_close    REAL
    ema20_low   REAL   — EMA(20, source=HA_Low, smoothing=None)
    is_green    INT    — 1 = green candle, 0 = red
    signal_action TEXT — ENTER_CE / ENTER_PE / NULL
    signal_reason TEXT — COND1 / COND2 / COND3 / NULL

PRIMARY KEY (symbol, timeframe, ts)  — one row per candle, upsert-safe.
"""

from typing import Optional
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# ──────────────────────────────────────────────────────────────────
# Table initialisation (called at startup by migration runner)
# ──────────────────────────────────────────────────────────────────

def init_table():
    """
    Create ha_candles table if it does not exist, and add any
    missing columns (safe to call multiple times on restart).
    """
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ha_candles (
            symbol        TEXT    NOT NULL,
            timeframe     TEXT    NOT NULL,
            ts            INTEGER NOT NULL,
            ha_open       REAL    NOT NULL,
            ha_high       REAL    NOT NULL,
            ha_low        REAL    NOT NULL,
            ha_close      REAL    NOT NULL,
            ema20_low     REAL,
            is_green      INTEGER,
            signal_action TEXT,
            signal_reason TEXT,
            PRIMARY KEY (symbol, timeframe, ts)
        )
    """)

    # Index for fast time-range queries (UI / warmup)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ha_candles_symbol_tf_ts
        ON ha_candles (symbol, timeframe, ts DESC)
    """)

    conn.commit()
    write_audit_log("[HA][DB] ha_candles table ready")


# ──────────────────────────────────────────────────────────────────
# INSERT / UPSERT
# ──────────────────────────────────────────────────────────────────

def insert_ha_candle(
    *,
    symbol:        str,
    timeframe:     str,
    ts:            int,
    ha_open:       float,
    ha_high:       float,
    ha_low:        float,
    ha_close:      float,
    ema20_low:     Optional[float] = None,
    is_green:      Optional[bool]  = None,
    signal_action: Optional[str]   = None,
    signal_reason: Optional[str]   = None,
):
    """
    Insert or update a HA candle row.

    ON CONFLICT the row is updated so that:
      - OHLC values are always refreshed (last tick wins)
      - ema20_low is updated when provided (COALESCE keeps existing if NULL)
      - signal_action / signal_reason are updated when provided
      - is_green is refreshed

    This means the engine can call insert_ha_candle() twice for the
    same candle — once on close (no signal) and once after signal
    evaluation — without losing data.
    """
    conn = get_conn()

    conn.execute(
        """
        INSERT INTO ha_candles (
            symbol, timeframe, ts,
            ha_open, ha_high, ha_low, ha_close,
            ema20_low, is_green,
            signal_action, signal_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
            ha_open       = excluded.ha_open,
            ha_high       = excluded.ha_high,
            ha_low        = excluded.ha_low,
            ha_close      = excluded.ha_close,
            ema20_low     = COALESCE(excluded.ema20_low,     ema20_low),
            is_green      = COALESCE(excluded.is_green,      is_green),
            signal_action = COALESCE(excluded.signal_action, signal_action),
            signal_reason = COALESCE(excluded.signal_reason, signal_reason)
        """,
        (
            symbol,
            timeframe,
            int(ts),
            ha_open,
            ha_high,
            ha_low,
            ha_close,
            ema20_low,
            int(is_green) if is_green is not None else None,
            signal_action,
            signal_reason,
        ),
    )

    conn.commit()


# ──────────────────────────────────────────────────────────────────
# READ — warmup (most recent N candles)
# ──────────────────────────────────────────────────────────────────

def fetch_recent_ha_candles(
    *,
    symbol:    str,
    timeframe: str,
    limit:     int = 100,
) -> list:
    """
    Returns the most recent `limit` HA candles in chronological order
    (oldest first), as a list of dicts.

    Used by ha_tick_engine to warm up the HeikinAshiConverter and
    EMA state after a restart without losing indicator continuity.
    """
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT symbol, timeframe, ts,
               ha_open, ha_high, ha_low, ha_close,
               ema20_low, is_green,
               signal_action, signal_reason
        FROM ha_candles
        WHERE symbol    = ?
          AND timeframe = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (symbol, timeframe, limit),
    ).fetchall()

    cols = [
        "symbol", "timeframe", "ts",
        "ha_open", "ha_high", "ha_low", "ha_close",
        "ema20_low", "is_green",
        "signal_action", "signal_reason",
    ]

    # Return in chronological order (oldest first)
    return [dict(zip(cols, row)) for row in reversed(rows)]


# ──────────────────────────────────────────────────────────────────
# READ — UI / debug (most recent N candles for a symbol)
# ──────────────────────────────────────────────────────────────────

def fetch_ha_candles_for_ui(
    *,
    symbol:    str,
    timeframe: str = "1m",
    limit:     int = 200,
) -> list:
    """Same as fetch_recent_ha_candles but for UI consumption."""
    return fetch_recent_ha_candles(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )


# ──────────────────────────────────────────────────────────────────
# Housekeeping — delete rows older than N days
# ──────────────────────────────────────────────────────────────────

def delete_old_ha_candles(*, keep_days: int = 10):
    """
    Delete HA candle rows older than `keep_days` calendar days.
    Called by the existing housekeeping_loop in db/housekeeping.py.
    """
    from datetime import datetime, timedelta

    conn = get_conn()

    cutoff = int(
        (datetime.now() - timedelta(days=keep_days)).timestamp()
    )

    cur = conn.execute(
        "DELETE FROM ha_candles WHERE ts < ?",
        (cutoff,),
    )
    conn.commit()

    if cur.rowcount:
        write_audit_log(
            f"[HOUSEKEEPING] ha_candles deleted {cur.rowcount} old rows "
            f"(keep_days={keep_days})"
        )
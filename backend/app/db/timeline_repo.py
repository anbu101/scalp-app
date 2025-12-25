from typing import List
from datetime import datetime, time as dtime

from app.db.sqlite import get_conn




# --------------------------------------------------
# INSERT: OHLC ONLY (first write)
# --------------------------------------------------

def insert_timeline_row(data: dict):
    """
    Insert ONE completed candle (OHLC only).
    Called exactly once per candle.
    """

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO market_timeline (
            symbol, timeframe, ts,
            open, high, low, close,
            strategy_version
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?,
            ?
        )
    """, (
        data["symbol"],
        data["timeframe"],
        data["ts"],
        data["open"],
        data["high"],
        data["low"],
        data["close"],
        data["strategy_version"],
    ))

    conn.commit()


# --------------------------------------------------
# UPDATE: indicators + conditions + signal
# --------------------------------------------------

def update_timeline_row(
    *,
    symbol: str,
    timeframe: str,
    ts: int,
    data: dict,
) -> int:
    """
    Update indicators / conditions / signal
    for an EXISTING candle row.

    RETURNS: number of rows updated (0 or 1)
    """

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE market_timeline SET
            ema8 = ?,
            ema20_low = ?,
            ema20_high = ?,
            rsi_raw = ?,

            cond_close_gt_open = ?,
            cond_close_gt_ema8 = ?,
            cond_close_ge_ema20 = ?,
            cond_close_not_above_ema20 = ?,
            cond_not_touching_high = ?,

            cond_rsi_ge_40 = ?,
            cond_rsi_le_65 = ?,
            cond_rsi_range = ?,
            cond_rsi_rising = ?,

            cond_is_trading_time = ?,
            cond_no_open_trade = ?,

            cond_all = ?,
            signal = ?

        WHERE symbol = ?
          AND timeframe = ?
          AND ts = ?
    """, (
        data.get("ema8"),
        data.get("ema20_low"),
        data.get("ema20_high"),
        data.get("rsi_raw"),

        _b(data.get("cond_close_gt_open")),
        _b(data.get("cond_close_gt_ema8")),
        _b(data.get("cond_close_ge_ema20")),
        _b(data.get("cond_close_not_above_ema20")),
        _b(data.get("cond_not_touching_high")),

        _b(data.get("cond_rsi_ge_40")),
        _b(data.get("cond_rsi_le_65")),
        _b(data.get("cond_rsi_range")),
        _b(data.get("cond_rsi_rising")),

        _b(data.get("cond_is_trading_time")),
        _b(data.get("cond_no_open_trade")),

        _b(data.get("cond_all")),
        data.get("signal"),

        symbol,
        timeframe,
        ts,
    ))

    updated = cur.rowcount
    conn.commit()
    return updated


# --------------------------------------------------
# READ: Warmup candles (SESSION SAFE)
# --------------------------------------------------

def fetch_recent_candles_for_warmup(
    *,
    symbol: str,
    timeframe: str,
    limit: int,
) -> List[dict]:
    """
    Fetch last N completed candles for indicator warmup.

    TradingView-parity behavior:
    - Uses continuous historical candles
    - No session/day filtering
    - Count-based warmup
    """

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            ts, open, high, low, close
        FROM market_timeline
        WHERE symbol = ?
          AND timeframe = ?
        ORDER BY ts DESC
        LIMIT ?
    """, (
        symbol,
        timeframe,
        limit,
    ))

    rows = cur.fetchall()

    # reverse → chronological order (oldest → newest)
    rows.reverse()

    return [
        {
            "ts": r[0],
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
        }
        for r in rows
    ]



# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _b(v):
    if v is None:
        return None
    return int(bool(v))

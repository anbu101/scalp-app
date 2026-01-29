from app.db.sqlite import get_conn

def load_option_candles(
    symbol: str,
    timeframe: str,
    ts_from: int,
    ts_to: int,
):
    """
    Loads option candles between ts_from and ts_to (5m only).
    """
    conn = get_conn()
    cur = conn.cursor()

    return cur.execute(
        """
        SELECT *
        FROM historical_candles_options
        WHERE symbol = ?
          AND timeframe = ?
          AND ts BETWEEN ? AND ?
        ORDER BY ts
        """,
        (symbol, timeframe, ts_from, ts_to),
    ).fetchall()

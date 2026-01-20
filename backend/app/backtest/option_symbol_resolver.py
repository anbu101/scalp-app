from app.db.sqlite import get_conn

def resolve_option_symbol(strike: int, option_type: str, ts: int):
    conn = get_conn()
    cur = conn.cursor()

    row = cur.execute(
        """
        SELECT symbol
        FROM historical_candles_options
        WHERE strike = ?
          AND option_type = ?
          AND ts <= ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (strike, option_type, ts),
    ).fetchone()

    return row["symbol"] if row else None

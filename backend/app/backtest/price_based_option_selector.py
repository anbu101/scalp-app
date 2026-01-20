class PriceBasedOptionSelector:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def select(self, ts, direction, min_price, max_price):
        opt_type = "CE" if direction == "BULLISH" else "PE"

        row = self.cur.execute(
            """
            SELECT symbol, option_type, close
            FROM historical_candles_options
            WHERE option_type = ?
              AND timeframe = '5m'
              AND ts <= ?
              AND close BETWEEN ? AND ?
            ORDER BY expiry ASC, ts DESC
            LIMIT 1
            """,
            (opt_type, ts, min_price, max_price)
        ).fetchone()

        if not row:
            return None

        return {
            "symbol": row[0],
            "option_type": row[1],
            "price": row[2],
        }

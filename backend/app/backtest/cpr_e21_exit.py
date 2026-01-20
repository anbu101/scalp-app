from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")
FORCE_EXIT_TIME = time(15, 25)


def simulate_exit(conn, trade_id, direction, entry_ts, sl, tp, ema21_by_ts):
    """
    direction: BULLISH / BEARISH
    sl / tp: INDEX based levels
    ema21_by_ts: dict[ts] -> ema21 value
    """

    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT ts, high, low, close
        FROM historical_candles_index
        WHERE symbol='NIFTY'
          AND timeframe='5m'
          AND ts > ?
        ORDER BY ts
        """,
        (entry_ts,)
    ).fetchall()

    reason = None
    exit_ts = None

    for ts, high, low, close in rows:
        dt = datetime.fromtimestamp(ts, IST)

        # ---- force exit
        if dt.time() >= FORCE_EXIT_TIME:
            reason = "EOD"
            exit_ts = ts
            break

        ema21 = ema21_by_ts.get(ts)
        if ema21 is None:
            continue  # EMA not ready yet

        # ---- SL / TP / EMA exit (INDEX based)
        if direction == "BULLISH":
            if low <= sl:
                reason = "SL"
                exit_ts = ts
                break
            if high >= tp:
                reason = "TP"
                exit_ts = ts
                break
            if close < ema21:
                reason = "EMA_EXIT"
                exit_ts = ts
                break
        else:
            if high >= sl:
                reason = "SL"
                exit_ts = ts
                break
            if low <= tp:
                reason = "TP"
                exit_ts = ts
                break
            if close > ema21:
                reason = "EMA_EXIT"
                exit_ts = ts
                break

    if not exit_ts:
        return  # no exit

    # ---- resolve option symbol
    row = cur.execute(
        """
        SELECT symbol
        FROM backtest_trades
        WHERE backtest_trade_id=?
        """,
        (trade_id,)
    ).fetchone()

    if not row:
        return

    symbol = row[0]

    # ---- option exit price (next available 5m candle)
    opt_row = cur.execute(
        """
        SELECT close
        FROM historical_candles_options
        WHERE symbol=?
          AND timeframe='5m'
          AND ts >= ?
        ORDER BY ts
        LIMIT 1
        """,
        (symbol, exit_ts)
    ).fetchone()

    if not opt_row:
        return

    exit_price = opt_row[0]

    cur.execute(
        """
        UPDATE backtest_trades
        SET
            exit_time=?,
            exit_price=?,
            exit_reason=?,
            state='CLOSED'
        WHERE backtest_trade_id=?
        """,
        (exit_ts, exit_price, reason, trade_id)
    )

    conn.commit()

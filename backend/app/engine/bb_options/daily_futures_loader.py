from datetime import datetime, timedelta

from kiteconnect import KiteConnect

from app.db.futures_candles_repo import insert_candle
from app.event_bus.audit_logger import write_audit_log


def load_recent_daily_futures(
    *,
    kite: KiteConnect,
    instrument_token: int,
    symbol: str,
):

    to_date = datetime.now()
    from_date = to_date - timedelta(days=10)

    try:
        data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval="day",
        )
    except Exception as e:
        write_audit_log(f"[BB] Failed to fetch daily futures: {e}")
        return

    if not data:
        write_audit_log("[BB] No daily futures data returned")
        return

    for row in data:
        ts = int(row["date"].timestamp())

        insert_candle(
            symbol=symbol,
            timeframe="1d",
            ts=ts,
            open_=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
        )

    write_audit_log("[BB] Daily futures data stored")

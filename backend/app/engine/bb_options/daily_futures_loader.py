from datetime import datetime, timedelta
from typing import Optional

from kiteconnect import KiteConnect

from app.db.futures_candles_repo import (
    insert_candle,
    get_latest_candle_ts,
)
from app.event_bus.audit_logger import write_audit_log


def load_recent_daily_futures(
    *,
    kite: KiteConnect,
    instrument_token: int,
    symbol: str,
):
    """
    PRODUCTION-SAFE DAILY FUTURES LOADER

    ✔ Incremental loading
    ✔ No table wipe
    ✔ No duplicate insertion
    ✔ Restart-safe
    """

    timeframe = "1d"

    # ------------------------------------------------------
    # 1️⃣ Determine fetch window
    # ------------------------------------------------------

    latest_ts: Optional[int] = get_latest_candle_ts(
        symbol=symbol,
        timeframe=timeframe,
    )

    to_date = datetime.now()

    if latest_ts:
        # Fetch only after last stored candle
        # fetch small lookback window to survive holidays/weekends
        from_date = datetime.fromtimestamp(latest_ts) - timedelta(days=5)

        write_audit_log(
            f"[BB] Daily futures incremental load from {from_date.date()}"
        )
    else:
        # First run → fetch last 30 days
        from_date = to_date - timedelta(days=30)

        write_audit_log(
            "[BB] Daily futures first load (last 30 days)"
        )

    if from_date >= to_date:
        write_audit_log(
            "[BB] Daily futures up-to-date — no fetch required"
        )
        return

    # ------------------------------------------------------
    # 2️⃣ Fetch from Zerodha
    # ------------------------------------------------------

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
        write_audit_log(
            f"[BB] No daily futures data returned "
            f"symbol={symbol} from={from_date.date()} to={to_date.date()}"
        )
        return

    inserted = 0

    # ------------------------------------------------------
    # 3️⃣ Insert only new candles
    # ------------------------------------------------------

    for row in data:
        ts = int(row["date"].timestamp())

        # Skip already stored timestamps (extra safety)
        if latest_ts and ts <= latest_ts:
            continue

        insert_candle(
            symbol=symbol,
            timeframe=timeframe,
            ts=ts,
            open_=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
        )

        inserted += 1

    write_audit_log(
        f"[BB] Daily futures load complete — inserted {inserted} candles"
    )

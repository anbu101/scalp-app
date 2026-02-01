from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")
UTC = pytz.utc

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

def is_market_open() -> bool:
    # Always start from UTC (platform-safe)
    now_utc = datetime.now(UTC)

    # Convert explicitly to IST
    now = now_utc.astimezone(IST)

    # Monday = 0, Sunday = 6
    if now.weekday() >= 5:
        return False

    return MARKET_OPEN <= now.time() <= MARKET_CLOSE

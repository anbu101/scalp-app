# backend/app/backtest/test_historical_candles.py

from datetime import datetime, timedelta
import pytz

from app.utils.app_paths import ensure_app_dirs, export_env
from app.backtest.historical_fetcher import HistoricalFetcher

# --------------------------------------------------
# ENV + PATH BOOTSTRAP (CANONICAL)
# --------------------------------------------------

ensure_app_dirs()
export_env()

IST = pytz.timezone("Asia/Kolkata")


# --------------------------------------------------
# RUN
# --------------------------------------------------

fetcher = HistoricalFetcher()

now_ist = datetime.now(IST)

fetcher.fetch(
    from_date=now_ist - timedelta(days=30),
    to_date=now_ist
)

import os
os.environ["DB_PATH"] = "/data/app.db"

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")

from app.backtest.historical_fetcher import HistoricalFetcher

fetcher = HistoricalFetcher()

now_ist = datetime.now(IST)

fetcher.fetch(
    from_date=now_ist - timedelta(days=30),
    to_date=now_ist
)

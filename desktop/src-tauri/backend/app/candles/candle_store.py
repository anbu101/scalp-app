from pathlib import Path
import csv
from typing import Optional
from datetime import datetime

from app.marketdata.candle import Candle

# Global last-close cache (token → close)
LAST_CLOSE = {}

class CandleStore:
    def __init__(
        self,
        base_dir: str,
        exchange: str,
        instrument_token: int,
        timeframe_sec: int,
        symbol: str,
    ):
        """
        Folder structure:
        app/state/candles/NFO/<TRADINGSYMBOL>/60s.csv
        """

        self.exchange = exchange
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.timeframe_sec = timeframe_sec

        # 🔑 SYMBOL-BASED DIRECTORY
        self.base_dir = Path(base_dir) / exchange / symbol
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.file = self.base_dir / f"{timeframe_sec}s.csv"

        # Create CSV with header once
        if not self.file.exists():
            with self.file.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "candle_time",
                        "symbol",
                        "instrument_token",
                        "start_ts",
                        "end_ts",
                        "open",
                        "high",
                        "low",
                        "close",
                    ]
                )

    # --------------------------------------------------

    # app/candles/candle_store.py

    def load_last_candle_end_ts(self):
        """
        Load last candle end timestamp from CSV/DB.
        MUST return int or None.
        """
        try:
            if not self.file.exists():
                return None

            last_line = self.file.read_text().strip().splitlines()[-1]
            parts = last_line.split(",")

            end_ts = parts[1]  # or correct index
            return int(end_ts)

        except Exception:
            return None



    # --------------------------------------------------

    def append(self, candle: Candle):
        candle_time = datetime.fromtimestamp(
            candle.end_ts
        ).strftime("%H:%M:%S")

        with self.file.open("a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    candle_time,
                    self.symbol,
                    self.instrument_token,
                    candle.start_ts,
                    candle.end_ts,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                ]   
            )
            LAST_CLOSE[self.instrument_token] = candle.close 
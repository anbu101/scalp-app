import json
import os
from datetime import datetime
from typing import Optional

import pandas as pd

try:
    from dhanhq import dhanhq
except ImportError as e:
    raise ImportError(
        "dhanhq python package not found. "
        "Install with: pip install dhanhq"
    ) from e


class DhanFetcher:
    """
    Dhan API Adapter
    ----------------
    - Loads instrument master from local file
    - Fetches historical candles from Dhan
    - Places / exits orders
    """

    def __init__(self, client_id: str, access_token: str, master_path: str):
        self.client = dhanhq(client_id, access_token)
        self.master_path = master_path
        self._instrument_cache: Optional[pd.DataFrame] = None

    # --------------------------------------------------
    # Instruments (LOCAL)
    # --------------------------------------------------

    def load_instruments(self, force: bool = False) -> pd.DataFrame:
        if self._instrument_cache is not None and not force:
            return self._instrument_cache

        if not os.path.exists(self.master_path):
            raise FileNotFoundError(
                f"Instrument master not found: {self.master_path}"
            )

        with open(self.master_path, "r") as f:
            data = json.load(f)

        df = pd.DataFrame(data)
        if df.empty:
            raise RuntimeError("Instrument master is empty")

        self._instrument_cache = df
        return df

    # --------------------------------------------------
    # Market Data
    # --------------------------------------------------

    def get_historical_candles(
        self,
        security_id: str,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        exchange: str = "NFO",
        instrument_type: str = "OPTIDX",
    ) -> pd.DataFrame:
        """
        Fetch historical candles using REAL dhanhq API.

        interval: "1m", "3m", "5m"
        """

        # Dhan expects YYYY-MM-DD
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")

        candles = self.client.historical_data(
            security_id=security_id,
            exchange_segment=exchange,
            instrument_type=instrument_type,
            from_date=from_str,
            to_date=to_str,
            interval=interval,
        )

        if not candles:
            return pd.DataFrame()

        # Dhan returns list of lists
        df = pd.DataFrame(
            candles,
            columns=["time", "open", "high", "low", "close", "volume"],
        )

        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        return df

    # --------------------------------------------------
    # Orders
    # --------------------------------------------------

    def place_market_order(
        self,
        security_id: str,
        transaction_type: str,
        quantity: int,
        product_type: str = "INTRADAY",
        exchange_segment: str = "NFO",
    ):
        return self.client.place_order(
            security_id=security_id,
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type="MARKET",
            product_type=product_type,
        )

    def square_off_position(
        self,
        security_id: str,
        quantity: int,
        product_type: str = "INTRADAY",
        exchange_segment: str = "NFO",
    ):
        return self.place_market_order(
            security_id=security_id,
            transaction_type="SELL",
            quantity=quantity,
            product_type=product_type,
            exchange_segment=exchange_segment,
        )

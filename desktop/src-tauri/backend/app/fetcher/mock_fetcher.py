# backend/app/fetcher/mock_fetcher.py
from . import InstrumentFetcher
from typing import List, Dict
import random
import datetime

class MockFetcher(InstrumentFetcher):
    def __init__(self, seed=None):
        self.seed = seed

    def fetch_instruments(self, underlying: str = "NIFTY") -> List[Dict]:
        # Produce a deterministic-ish list of mock options for testing
        base_strike = 21500
        expiries = [(datetime.date.today() + datetime.timedelta(days=i)).isoformat() for i in (7, 14, 21)]
        instruments = []
        for i in range(-3, 6):
            strike = base_strike + i*100
            for typ in ("CE", "PE"):
                last_price = max(5, abs(200 - (i*20) + (1 if typ=="CE" else -1)*10))
                instruments.append({
                    "symbol": f"{underlying}-{strike}{typ}",
                    "strike": float(strike),
                    "option_type": typ,
                    "last_price": float(last_price),
                    "expiry": expiries[0],
                    "volume": float(100 + abs(i)*10)
                })
        return instruments

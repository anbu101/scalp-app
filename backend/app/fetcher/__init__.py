# backend/app/fetcher/__init__.py
from abc import ABC, abstractmethod
from typing import List, Dict

class InstrumentFetcher(ABC):
    @abstractmethod
    def fetch_instruments(self, underlying: str = "NIFTY") -> List[Dict]:
        """
        Return a list of instrument dicts like:
        { "symbol":"NIFTY-21500CE", "strike":21500, "option_type":"CE", "last_price":180.0, "expiry":"2025-12-25", "volume": 1234 }
        """
        raise NotImplementedError()

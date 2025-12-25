# backend/app/engine/price_cache.py

import time
from typing import Dict, List
from app.engine.logger import log

class PriceCache:
    def __init__(self):
        self._cache: Dict[str, Dict] = {}

    # WS pushes price here
    def update_price(self, symbol: str, ltp: float):
        self._cache[symbol] = {
            "ltp": ltp,
            "ts": time.time()
        }

    # EXIT engine reads only (NO REST)
    def get_ltps(self, symbols: List[str]) -> Dict[str, float]:
        result = {}
        for s in symbols:
            if s in self._cache:
                result[s] = self._cache[s]["ltp"]
        return result

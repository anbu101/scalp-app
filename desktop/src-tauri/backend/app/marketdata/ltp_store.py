from threading import Lock
from typing import Optional, Dict, Tuple
import time


class LTPStore:
    """
    🔒 Single authoritative in-memory LTP store

    Written ONLY by:
      - ZerodhaTickEngine (WebSocket ticks)

    Read by:
      - TradeStateManager
      - Reconciliation engines
      - Executor (for GTT last_price)
      - OptionSelector (freshness-aware)

    NO REST calls.
    NO fallbacks.
    """

    _prices: Dict[str, float] = {}
    _timestamps: Dict[str, float] = {}
    _lock = Lock()

    # --------------------------------------------------
    # 🔹 WRITE (WebSocket only)
    # --------------------------------------------------

    @classmethod
    def update(cls, symbol: str, price: float):
        now = time.time()
        with cls._lock:
            cls._prices[symbol] = price
            cls._timestamps[symbol] = now

    # --------------------------------------------------
    # 🔹 STANDARD READ (Backward Compatible)
    # --------------------------------------------------

    @classmethod
    def get(cls, symbol: str) -> Optional[float]:
        """
        Returns ONLY price (backward compatible).
        """
        with cls._lock:
            return cls._prices.get(symbol)

    # --------------------------------------------------
    # 🔹 SAFE READ WITH TIMESTAMP (For LIVE Safety Logic)
    # --------------------------------------------------

    @classmethod
    def get_with_timestamp(cls, symbol: str) -> Optional[Tuple[float, float]]:
        """
        Returns (price, timestamp).

        Used by safety-aware components like OptionSelector.
        """
        with cls._lock:
            price = cls._prices.get(symbol)
            ts = cls._timestamps.get(symbol)

            if price is None or ts is None:
                return None

            return price, ts

    # --------------------------------------------------
    # 🔹 HEALTH CHECK
    # --------------------------------------------------

    @classmethod
    def has_any(cls) -> bool:
        with cls._lock:
            return bool(cls._prices)

    # --------------------------------------------------
    # 🔹 UI SAFE SNAPSHOT
    # --------------------------------------------------

    @classmethod
    def snapshot(cls) -> Dict[str, float]:
        """
        Returns COPY of prices only (UI safe).
        """
        with cls._lock:
            return dict(cls._prices)

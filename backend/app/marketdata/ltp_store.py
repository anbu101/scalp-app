from threading import Lock
from typing import Optional, Dict


class LTPStore:
    """
    🔒 Single authoritative in-memory LTP store

    Written ONLY by:
      - ZerodhaTickEngine (WebSocket ticks)

    Read by:
      - TradeStateManager
      - Reconciliation engines
      - Executor (for GTT last_price)

    NO REST calls.
    NO fallbacks.
    """

    _prices = {}
    _lock = Lock()

    @classmethod
    def update(cls, symbol: str, price: float):
        with cls._lock:
            cls._prices[symbol] = price

    @classmethod
    def get(cls, symbol: str) -> Optional[float]:
        with cls._lock:
            return cls._prices.get(symbol)

    @classmethod
    def has_any(cls) -> bool:
        """
        Returns True if at least one LTP has been received via WS.
        Used by reconciliation logic to avoid acting on empty state.
        """
        with cls._lock:
            return bool(cls._prices)

    # --------------------------------------------------
    # 🔹 UI SAFE READ-ONLY SNAPSHOT
    # --------------------------------------------------

    @classmethod
    def snapshot(cls) -> Dict[str, float]:
        """
        Returns a COPY of latest LTPs.

        - Read-only
        - UI-safe
        - No side effects
        - No ordering guarantees (not needed)

        Used ONLY by UI endpoints.
        """
        with cls._lock:
            return dict(cls._prices)

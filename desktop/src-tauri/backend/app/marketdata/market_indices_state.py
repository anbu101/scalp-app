from typing import Dict, Optional
from threading import Lock

# =================================================
# Market Indices State (READ-ONLY for UI)
# =================================================

class MarketIndicesState:
    """
    🔒 Single in-memory source for INDEX values

    Indices:
      - NIFTY
      - BANKNIFTY
      - SENSEX

    Written by:
      - ZerodhaTickEngine (live LTP)
      - One-time prev-close loader

    Read by:
      - UI API only
    """

    # Always-present indices (UI CONTRACT)
    _INDEX_KEYS = ["NIFTY", "BANKNIFTY", "SENSEX"]

    _lock = Lock()

    # Live LTP
    _ltp: Dict[str, float] = {}

    # Previous day close
    _prev_close: Dict[str, float] = {}

    # -------------------------
    # Write APIs
    # -------------------------

    @classmethod
    def update_ltp(cls, index: str, price: float):
        with cls._lock:
            cls._ltp[index] = price


    @classmethod
    def set_prev_close(cls, index: str, price: float):
        with cls._lock:
            cls._prev_close[index] = price

    # -------------------------
    # Read APIs (UI SAFE)
    # -------------------------

    @classmethod
    def snapshot(cls) -> Dict[str, dict]:
        out = {}

        with cls._lock:
            for idx in cls._INDEX_KEYS:
                ltp = cls._ltp.get(idx)
                prev = cls._prev_close.get(idx)

                if ltp is None or prev is None:
                    out[idx] = {
                        "ltp": ltp,
                        "prev_close": prev,
                        "change": None,
                        "change_pct": None,
                    }
                    continue

                change = ltp - prev
                change_pct = (change / prev) * 100 if prev else 0

                out[idx] = {
                    "ltp": round(ltp, 2),
                    "prev_close": round(prev, 2),
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                }

        return out



    @classmethod
    def is_ready(cls) -> bool:
        """
        True once prev-close is loaded (even if market closed).
        """
        with cls._lock:
            return bool(cls._prev_close)

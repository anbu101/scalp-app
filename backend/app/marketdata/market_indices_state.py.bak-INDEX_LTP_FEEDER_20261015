from typing import Dict, Optional
from threading import Lock
# ── INDEX_PREVCLOSE_ROLLOVER BEGIN (import) ──
from datetime import date
# ── INDEX_PREVCLOSE_ROLLOVER END (import) ──

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
      - One-time prev-close loader (+ daily rollover watchdog)

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

    # ── INDEX_PREVCLOSE_ROLLOVER BEGIN (state) ──
    # Trading date each prev_close is valid FOR (== date.today() at load
    # time). A prev_close stamped for a different date is stale: serving
    # it produces wrong change/% (observed 2026-08-13: BANKNIFTY shown
    # green vs a two-day-old reference). Stale entries are CLEARED, not
    # served — UI shows "—" until a fresh load succeeds (fail-closed).
    _prev_close_for: Dict[str, date] = {}
    # ── INDEX_PREVCLOSE_ROLLOVER END (state) ──

    # -------------------------
    # Write APIs
    # -------------------------

    @classmethod
    def update_ltp(cls, index: str, price: float):
        with cls._lock:
            cls._ltp[index] = price


    # ── INDEX_PREVCLOSE_ROLLOVER BEGIN (set_prev_close) ──
    @classmethod
    def set_prev_close(cls, index: str, price: float, valid_for: date):
        """
        valid_for: the trading date this prev_close serves (i.e. the
        loader's date.today() at load time). Required so rollover can
        detect staleness. Sole caller: load_index_prev_close_once().
        """
        with cls._lock:
            cls._prev_close[index] = price
            cls._prev_close_for[index] = valid_for

    @classmethod
    def prev_close_reload_needed(cls, today: date) -> bool:
        """
        Rollover check for the watchdog.

        FAIL-CLOSED side effect: any prev_close stamped for a date other
        than `today` (or missing its stamp) is removed immediately, so
        snapshot() reverts that index to change=None rather than serving
        a wrong number.

        Returns True when a (re)load should be attempted:
          - store is empty (startup loader failed / not yet run), or
          - stale entries were just cleared.
        """
        with cls._lock:
            if not cls._prev_close:
                return True

            stale = [
                idx for idx in list(cls._prev_close.keys())
                if cls._prev_close_for.get(idx) != today
            ]

            for idx in stale:
                cls._prev_close.pop(idx, None)
                cls._prev_close_for.pop(idx, None)

            if stale:
                return True

            return False
    # ── INDEX_PREVCLOSE_ROLLOVER END (set_prev_close) ──

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
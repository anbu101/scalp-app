from collections import deque
from statistics import mean, pstdev
from typing import Dict

from app.marketdata.candle import Candle
from app.db.futures_candles_repo import fetch_recent_candles
from app.event_bus.audit_logger import write_audit_log


class BollingerEngine:

    def __init__(self, symbol: str, period: int = 20, std_dev: float = 2):
        self.symbol = symbol
        self.period = period
        self.std_dev_multiplier = std_dev

        self.closes = deque(maxlen=period)
        self._ready = False

        self._warmup_from_db()

    # ==================================================
    # WARMUP
    # ==================================================

    def _warmup_from_db(self):

        rows = fetch_recent_candles(
            symbol=self.symbol,
            timeframe="3m",
            limit=self.period,
        )

        if not rows:
            write_audit_log("[BB] No 3m futures candles found for warmup")
            return

        for r in rows:
            self.closes.append(float(r["close"]))

        if len(self.closes) == self.period:
            self._ready = True
            write_audit_log(
                f"[BB] Warmed up with {self.period} candles"
            )

    # ==================================================
    # UPDATE
    # ==================================================

    def update(self, candle: Candle) -> Dict[str, float]:

        self.closes.append(candle.close)

        if len(self.closes) < self.period:
            self._ready = False
            return {}

        self._ready = True

        sma = mean(self.closes)
        std = pstdev(self.closes)

        upper = sma + (self.std_dev_multiplier * std)
        lower = sma - (self.std_dev_multiplier * std)

        return {
            "bb_middle": sma,
            "bb_upper": upper,
            "bb_lower": lower,
        }

    # ==================================================
    # STATUS
    # ==================================================

    def is_ready(self) -> bool:
        return self._ready

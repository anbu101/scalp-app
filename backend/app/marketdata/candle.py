# app/marketdata/candle.py

from dataclasses import dataclass
from enum import Enum


class CandleSource(str, Enum):
    WARMUP = "warmup"   # historical / replay
    LIVE = "live"       # built from LTP ticks


@dataclass
class Candle:
    start_ts: int
    end_ts: int
    open: float
    high: float
    low: float
    close: float
    source: CandleSource

    def __post_init__(self):
        """
        🔒 HARD GUARANTEE:
        - All timestamps are INT
        - Protects entire system from DB / CSV / logger pollution
        """

        try:
            self.start_ts = int(self.start_ts)
        except Exception:
            raise ValueError(f"Candle.start_ts must be int, got {self.start_ts!r}")

        try:
            self.end_ts = int(self.end_ts)
        except Exception:
            raise ValueError(f"Candle.end_ts must be int, got {self.end_ts!r}")

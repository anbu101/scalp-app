import logging
from typing import Dict, List

from candles.candle_builder import CandleBuilder
from backend.app.indicators.indicator_eng_OLD_DO_NOT_USE import IndicatorEngine
from backend.app.engine.strategy_engine import StrategyEngine

logger = logging.getLogger(__name__)


class CandleEngine:
    """
    Wires:
    LTP ticks -> CandleBuilder -> IndicatorEngine -> StrategyEngine
    """

    TF_MAP = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
    }

    def __init__(
        self,
        instrument_token: int,
        timeframe: str,
        last_candle_end_ts: int | None = None,
    ):
        if timeframe not in self.TF_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        self.token = instrument_token
        self.timeframe = timeframe
        self.tf_sec = self.TF_MAP[timeframe]

        # Core components
        self.builder = CandleBuilder(
            instrument_token=instrument_token,
            timeframe_sec=self.tf_sec,
            last_candle_end_ts=last_candle_end_ts,
        )

        self.indicators = IndicatorEngine(
            instrument_token=instrument_token,
            timeframe_label=timeframe,
        )

        self.strategy = StrategyEngine(
            instrument_token=instrument_token,
            timeframe_label=timeframe,
        )

        logger.info(
            f"[ENGINE] Initialized token={instrument_token} TF={timeframe}"
        )

    # =========================
    # Public API
    # =========================

    def on_tick(self, ltp: float, ts: int):
        """
        Called for every LTP tick.
        """

        candle = self.builder.on_tick(ltp, ts)
        if candle is None:
            return

        logger.debug(
            f"[CANDLE][{self.token}][{self.timeframe}] "
            f"O={candle.open} H={candle.high} "
            f"L={candle.low} C={candle.close}"
        )

        # Indicators
        snapshot = self.indicators.on_candle(candle)

        # Strategy (only if indicators ready)
        if snapshot is not None:
            self.strategy.on_candle(candle, snapshot)

from typing import Optional, Dict
from app.marketdata.candle import Candle


class BreakoutSignal:

    def __init__(self, direction: Optional[str] = None):
        self.direction = direction  # "LONG", "SHORT", or None

    @property
    def is_long(self) -> bool:
        return self.direction == "LONG"

    @property
    def is_short(self) -> bool:
        return self.direction == "SHORT"

    @property
    def is_signal(self) -> bool:
        return self.direction is not None


class BreakoutSignalEngine:

    def __init__(self):
        self.prev_close: Optional[float] = None
        self.prev_upper: Optional[float] = None
        self.prev_lower: Optional[float] = None

    def update(
        self,
        candle: Candle,
        bands: Dict[str, float],
    ) -> BreakoutSignal:

        if not bands:
            return BreakoutSignal()

        curr_close = candle.close
        curr_upper = bands["upper"]
        curr_lower = bands["lower"]

        signal = BreakoutSignal()

        if (
            self.prev_close is not None
            and self.prev_upper is not None
            and self.prev_lower is not None
        ):

            # LONG breakout
            if (
                self.prev_close <= self.prev_upper
                and curr_close > curr_upper
            ):
                signal = BreakoutSignal("LONG")

            # SHORT breakout
            elif (
                self.prev_close >= self.prev_lower
                and curr_close < curr_lower
            ):
                signal = BreakoutSignal("SHORT")

        # Store for next candle comparison
        self.prev_close = curr_close
        self.prev_upper = curr_upper
        self.prev_lower = curr_lower

        return signal

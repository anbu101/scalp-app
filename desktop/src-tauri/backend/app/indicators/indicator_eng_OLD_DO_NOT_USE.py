from dataclasses import dataclass
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class IndicatorSnapshot:
    ema8: float
    ema20_low: float
    ema20_high: float
    rsi_raw: float
    rsi_smoothed: float
    rsi_rising: bool


class IndicatorEnginePineV19:
    """
    Pine v1.9 faithful indicator engine
    """

    EMA8_LEN = 8
    EMA20_LEN = 20
    EMA20_SMOOTH = 9

    RSI_LEN = 5
    RSI_SMOOTH = 5

    MIN_CANDLES = 30

    def __init__(self, instrument_token: int, timeframe_label: str):
        self.token = instrument_token
        self.tf = timeframe_label

        self.candles = []

        self._ema8_raw = None
        self._ema20_low_raw = None
        self._ema20_high_raw = None

        self._ema8_sma_buf = []
        self._ema20_low_sma_buf = []
        self._ema20_high_sma_buf = []

        self._prev_close = None
        self._avg_gain = None
        self._avg_loss = None
        self._rsi_raw_prev = None
        self._rsi_sma_buf = []

    # ================= PUBLIC =================

    def on_candle(self, candle) -> Optional[IndicatorSnapshot]:
        self._append_candle(candle)

        if len(self.candles) < self.MIN_CANDLES:
            return None

        ema8 = self._calc_ema8(candle.close)
        ema20_low = self._calc_ema20_low(candle.low)
        ema20_high = self._calc_ema20_high(candle.high)

        rsi_raw, rsi_sm = self._calc_rsi(candle.close)
        rsi_rising = (
            self._rsi_raw_prev is not None and rsi_raw > self._rsi_raw_prev
        )

        self._rsi_raw_prev = rsi_raw

        return IndicatorSnapshot(
            ema8=ema8,
            ema20_low=ema20_low,
            ema20_high=ema20_high,
            rsi_raw=rsi_raw,
            rsi_smoothed=rsi_sm,
            rsi_rising=rsi_rising,
        )

    # ================= INTERNAL =================

    def _append_candle(self, candle):
        self.candles.append(
            {
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
            }
        )

    def _ema(self, prev, val, length):
        alpha = 2 / (length + 1)
        return val if prev is None else (val - prev) * alpha + prev

    def _sma(self, buf: List[float], val: float, length: int):
        buf.append(val)
        if len(buf) > length:
            buf.pop(0)
        return sum(buf) / len(buf)

    def _calc_ema8(self, close):
        self._ema8_raw = self._ema(self._ema8_raw, close, self.EMA8_LEN)
        return self._sma(self._ema8_sma_buf, self._ema8_raw, self.EMA8_LEN)

    def _calc_ema20_low(self, low):
        self._ema20_low_raw = self._ema(self._ema20_low_raw, low, self.EMA20_LEN)
        return self._sma(
            self._ema20_low_sma_buf, self._ema20_low_raw, self.EMA20_SMOOTH
        )

    def _calc_ema20_high(self, high):
        self._ema20_high_raw = self._ema(self._ema20_high_raw, high, self.EMA20_LEN)
        return self._sma(
            self._ema20_high_sma_buf, self._ema20_high_raw, self.EMA20_SMOOTH
        )

    def _calc_rsi(self, close):
        if self._prev_close is None:
            self._prev_close = close
            return 50.0, 50.0

        change = close - self._prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        if self._avg_gain is None:
            self._avg_gain = gain
            self._avg_loss = loss
        else:
            self._avg_gain = (
                (self._avg_gain * (self.RSI_LEN - 1)) + gain
            ) / self.RSI_LEN
            self._avg_loss = (
                (self._avg_loss * (self.RSI_LEN - 1)) + loss
            ) / self.RSI_LEN

        if self._avg_loss == 0:
            rsi_raw = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            rsi_raw = 100.0 - (100.0 / (1.0 + rs))

        rsi_sm = self._sma(self._rsi_sma_buf, rsi_raw, self.RSI_SMOOTH)

        self._prev_close = close
        return rsi_raw, rsi_sm

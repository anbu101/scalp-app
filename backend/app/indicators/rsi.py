from collections import deque
from typing import Optional


class RSIEnginePine:
    """
    Pine-equivalent RSI engine.

    Matches:
    - ta.rsi() using Wilder RMA
    - Optional SMA smoothing
    - rsiRaw > rsiRaw[1] logic
    """

    def __init__(
        self,
        rsi_length: int = 5,
        smooth_length: int = 5,
    ):
        self.rsi_length = rsi_length
        self.smooth_length = smooth_length

        # Price history
        self.prev_close: Optional[float] = None

        # Wilder RMA state
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None

        # RSI state
        self.rsi_raw: Optional[float] = None
        self.prev_rsi_raw: Optional[float] = None

        # Smoothing buffer (SMA of RSI)
        self.rsi_sma_buf = deque(maxlen=smooth_length)
        self.rsi_smoothed: Optional[float] = None

        # Warm-up counters
        self._init_gains = []
        self._init_losses = []

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def update(self, close: float) -> dict:
        """
        Call once per completed candle.
        Returns dict with RSI values & flags.
        """

        if self.prev_close is None:
            self.prev_close = close
            return self._snapshot(ready=False)

        # Price change
        delta = close - self.prev_close
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)

        self.prev_close = close

        # --------------------------------------------------
        # INITIALIZATION PHASE (first rsi_length candles)
        # --------------------------------------------------
        if self.avg_gain is None or self.avg_loss is None:
            self._init_gains.append(gain)
            self._init_losses.append(loss)

            if len(self._init_gains) < self.rsi_length:
                return self._snapshot(ready=False)

            # First Wilder seed = SMA of gains/losses
            self.avg_gain = sum(self._init_gains) / self.rsi_length
            self.avg_loss = sum(self._init_losses) / self.rsi_length

        else:
            # --------------------------------------------------
            # Wilder RMA update (THIS IS CRITICAL)
            # --------------------------------------------------
            self.avg_gain = (
                (self.avg_gain * (self.rsi_length - 1)) + gain
            ) / self.rsi_length

            self.avg_loss = (
                (self.avg_loss * (self.rsi_length - 1)) + loss
            ) / self.rsi_length

        # --------------------------------------------------
        # RSI RAW
        # --------------------------------------------------
        self.prev_rsi_raw = self.rsi_raw

        if self.avg_loss == 0:
            self.rsi_raw = 100.0
        else:
            rs = self.avg_gain / self.avg_loss
            self.rsi_raw = 100.0 - (100.0 / (1.0 + rs))

        # --------------------------------------------------
        # RSI SMOOTHED (SMA of RSI Raw)
        # --------------------------------------------------
        self.rsi_sma_buf.append(self.rsi_raw)

        if len(self.rsi_sma_buf) == self.smooth_length:
            self.rsi_smoothed = sum(self.rsi_sma_buf) / self.smooth_length

        return self._snapshot(ready=self.is_ready())

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def is_ready(self) -> bool:
        """
        Equivalent to Pine having enough bars.
        """
        return (
            self.rsi_raw is not None
            and self.prev_rsi_raw is not None
            and len(self.rsi_sma_buf) == self.smooth_length
        )

    def _snapshot(self, ready: bool) -> dict:
        rsi_rising = (
            ready
            and self.prev_rsi_raw is not None
            and self.rsi_raw > self.prev_rsi_raw
        )

        return {
            "rsi_raw": self.rsi_raw,
            "rsi_smoothed": self.rsi_smoothed,
            "rsi_rising": rsi_rising,
            "ready": ready,
        }

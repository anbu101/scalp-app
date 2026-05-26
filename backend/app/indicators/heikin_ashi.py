# backend/app/indicators/heikin_ashi.py

"""
Heikin Ashi Candle Converter

Heikin Ashi formulas:
  HA Close  = (O + H + L + C) / 4
  HA Open   = (prev_HA_Open + prev_HA_Close) / 2   [first bar: (O + C) / 2]
  HA High   = max(H, HA_Open, HA_Close)
  HA Low    = min(L, HA_Open, HA_Close)

IMPORTANT:
  - This converter MUST receive ONLY completed candles.
  - Candles MUST arrive in chronological order.
  - Duplicate timestamps are not allowed.

HA candles smooth price action and make trend/reversal signals cleaner.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class HACandle:
    ts: int
    open: float
    high: float
    low: float
    close: float

    # ─────────────────────────────────────────────────────────────
    # Candle colour
    # ─────────────────────────────────────────────────────────────

    @property
    def is_green(self) -> bool:
        return self.close >= self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    # ─────────────────────────────────────────────────────────────
    # EMA touch helpers
    # ─────────────────────────────────────────────────────────────

    def touches_or_crosses_ema_low(self, ema_low: float) -> bool:
        """
        True if ANY part of candle touches/crosses ema_low.
        """
        return self.low <= ema_low <= self.high

    def touches_or_crosses_ema_high(self, ema_high: float) -> bool:
        """
        True if ANY part of candle touches/crosses ema_high.
        """
        return self.low <= ema_high <= self.high


class HeikinAshiConverter:
    """
    Stateful converter.

    IMPORTANT:
      update() must be called ONLY with CLOSED candles.
      Candles must arrive oldest → newest.
    """

    def __init__(self):
        self._prev_ha_open: Optional[float] = None
        self._prev_ha_close: Optional[float] = None
        self._last_ts: Optional[int] = None

    def reset(self):
        """
        Reset converter state.
        Use after reconnects/gaps/restarts.
        """
        self._prev_ha_open = None
        self._prev_ha_close = None
        self._last_ts = None

    def update(
        self,
        ts: int,
        o: float,
        h: float,
        l: float,
        c: float,
    ) -> HACandle:

        # ─────────────────────────────────────────────────────────
        # Chronological protection
        # ─────────────────────────────────────────────────────────

        if self._last_ts is not None:

            if ts == self._last_ts:
                raise ValueError(
                    f"Duplicate candle timestamp received: {ts}"
                )

            if ts < self._last_ts:
                raise ValueError(
                    f"Out-of-order candle received: {ts} < {self._last_ts}"
                )

        # ─────────────────────────────────────────────────────────
        # HA calculations
        # ─────────────────────────────────────────────────────────

        ha_close = (o + h + l + c) / 4.0

        if self._prev_ha_open is None:
            ha_open = (o + c) / 2.0
        else:
            ha_open = (
                self._prev_ha_open +
                self._prev_ha_close
            ) / 2.0

        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)

        # ─────────────────────────────────────────────────────────
        # Persist state
        # ─────────────────────────────────────────────────────────

        self._prev_ha_open = ha_open
        self._prev_ha_close = ha_close
        self._last_ts = ts

        return HACandle(
            ts=ts,
            open=round(ha_open, 2),
            high=round(ha_high, 2),
            low=round(ha_low, 2),
            close=round(ha_close, 2),
        )
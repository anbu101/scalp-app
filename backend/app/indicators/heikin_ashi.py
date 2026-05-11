# backend/app/indicators/heikin_ashi.py
"""
Heikin Ashi Candle Converter

Heikin Ashi formulas:
  HA Close  = (O + H + L + C) / 4
  HA Open   = (prev_HA_Open + prev_HA_Close) / 2   [first bar: (O + C) / 2]
  HA High   = max(H, HA_Open, HA_Close)
  HA Low    = min(L, HA_Open, HA_Close)

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

    # ── Colour ──────────────────────────────────────────────────────
    @property
    def is_green(self) -> bool:
        return self.close >= self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    # ── EMA touch checks (body OR wick) ─────────────────────────────

    def touches_or_crosses_ema_low(self, ema_low: float) -> bool:
        """
        True if ANY part of the candle (body or wick) is at or below ema_low.
        HA Low is the lowest point (bottom wick), so this covers every case.
        """
        return self.low <= ema_low

    def touches_or_crosses_ema_high(self, ema_high: float) -> bool:
        """
        True if ANY part of the candle (body or wick) is at or above ema_high.
        """
        return self.high >= ema_high


class HeikinAshiConverter:
    """
    Stateful converter: maintains previous HA open/close to compute next bar.
    Call update() once per completed OHLC candle in chronological order.
    """

    def __init__(self):
        self._prev_ha_open: Optional[float] = None
        self._prev_ha_close: Optional[float] = None

    def reset(self):
        """Reset state (e.g., after a gap or restart)."""
        self._prev_ha_open = None
        self._prev_ha_close = None

    def update(self, ts: int, o: float, h: float, l: float, c: float) -> HACandle:
        ha_close = (o + h + l + c) / 4.0

        if self._prev_ha_open is None:
            # First candle — seed HA open from actual open/close
            ha_open = (o + c) / 2.0
        else:
            ha_open = (self._prev_ha_open + self._prev_ha_close) / 2.0

        ha_high = max(h, ha_open, ha_close)
        ha_low  = min(l, ha_open, ha_close)

        self._prev_ha_open  = ha_open
        self._prev_ha_close = ha_close

        return HACandle(
            ts=ts,
            open=round(ha_open,  2),
            high=round(ha_high,  2),
            low=round(ha_low,    2),
            close=round(ha_close, 2),
        )
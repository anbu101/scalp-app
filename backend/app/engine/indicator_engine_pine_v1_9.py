# backend/app/engine/indicator_engine_pine_v1_9.py

from typing import Optional, List

from app.indicators.ema import EMA, SMA
from app.indicators.rsi import RSIEnginePine
from app.marketdata.candle import Candle
from app.event_bus.audit_logger import write_audit_log
from datetime import datetime


class IndicatorEnginePineV19:
    """
    Sequential indicator engine.
    Feeds candles one-by-one exactly like TradingView.
    """

    def __init__(self):
        # EMA 8 (close)
        self.ema8 = EMA(8)

        # EMA 20 low / high (then SMA smoothed with len=9)
        self.ema20_low_raw = EMA(20)
        self.ema20_high_raw = EMA(20)

        self.ema20_low_smooth = SMA(9)
        self.ema20_high_smooth = SMA(9)

        # RSI (Wilder, len=5, smooth=5)
        self.rsi_engine = RSIEnginePine(rsi_length=5, smooth_length=5)
        self._prev_rsi_raw: Optional[float] = None

        # Last computed values
        self.values: dict = {}

        self.ready: bool = False
        self._ready_logged: bool = False

        # Track last red candle low (LIVE ONLY)
        self._last_red_low: Optional[float] = None

        # Warmup guard
        self._is_warmup: bool = False

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------

    def update(self, candle: Candle):
        """
        Feed ONE completed candle.
        """

        # 🔒 HARD NORMALIZATION
        try:
            o = float(candle.open)
            h = float(candle.high)
            l = float(candle.low)
            c = float(candle.close)
        except Exception:
            return None  # corrupted candle → ignore safely

        # Track previous RED candle low (LIVE ONLY)
        if not self._is_warmup and c < o:
            self._last_red_low = l

        # --- EMA 8 ---
        ema8_val = self.ema8.update(c)

        # --- EMA 20 low / high ---
        ema20_low_raw = self.ema20_low_raw.update(l)
        ema20_high_raw = self.ema20_high_raw.update(h)

        ema20_low = (
            self.ema20_low_smooth.update(ema20_low_raw)
            if ema20_low_raw is not None
            else None
        )
        ema20_high = (
            self.ema20_high_smooth.update(ema20_high_raw)
            if ema20_high_raw is not None
            else None
        )

        # --- RSI ---
        rsi_out = self.rsi_engine.update(c)
        rsi_raw = rsi_out["rsi_raw"]
        rsi_smoothed = rsi_out["rsi_smoothed"]

        rsi_rising = (
            self._prev_rsi_raw is not None
            and rsi_raw is not None
            and rsi_raw > self._prev_rsi_raw
        )
        self._prev_rsi_raw = rsi_raw

        # Store latest values
        self.values = {
            "ema8": ema8_val,
            "ema20_low": ema20_low,
            "ema20_high": ema20_high,
            "rsi_raw": rsi_raw,
            "rsi_smoothed": rsi_smoothed,
            "rsi_rising": rsi_rising,
        }

        # 🔒 READY LATCH (once true, always true)
        if not self.ready:
            self.ready = all(v is not None for v in self.values.values())

        # Indicator ready log (LIVE ONLY, once)
        if self.ready and not self._ready_logged and not self._is_warmup:
            #write_audit_log(
             #   "[INDICATOR] READY "
              #  f"EMA8={ema8_val} "
               # f"EMA20_L={ema20_low} "
                #f"EMA20_H={ema20_high} "
                #f"RSI={rsi_smoothed}"
            #)
            self._ready_logged = True

        return self.values if self.ready else None

    # -------------------------------------------------
    # Warmup
    # -------------------------------------------------

    def warmup(
        self,
        candles: List[Candle],
        *,
        use_history: bool = False,
        history_lookback: int = 200,
    ):
        """
        Warm up indicators.

        Default (use_history=False):
            - Uses ONLY today's candles (current behavior, unchanged)

        TradingView-style (use_history=True):
            - Uses last `history_lookback` candles across days
        """
        self._is_warmup = True

        if use_history:
            # 🔹 TradingView-style continuous EMA warmup
            for candle in candles[-history_lookback:]:
                self.update(candle)
        else:
            # 🔹 Current behavior (day-scoped EMA reset)
            today = datetime.now().date()
            for candle in candles:
                candle_day = datetime.fromtimestamp(candle.start_ts).date()
                if candle_day != today:
                    continue
                self.update(candle)

        self._is_warmup = False

    # -------------------------------------------------

    def is_ready(self) -> bool:
        return self.ready

    def snapshot(self) -> dict:
        return self.values.copy()

    # -------------------------------------------------
    # Strategy helpers
    # -------------------------------------------------

    def find_previous_red_low(self) -> Optional[float]:
        """
        Returns the low of the most recent LIVE red candle.
        """
        return self._last_red_low

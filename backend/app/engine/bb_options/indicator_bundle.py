from collections import deque
from statistics import mean, pstdev
from typing import Dict, Optional

from app.marketdata.candle import Candle
from app.db.futures_candles_repo import fetch_recent_candles
from app.event_bus.audit_logger import write_audit_log
from app.indicators.pivot_cache import PivotCache


class IndicatorBundle:

    def __init__(self, symbol: str):
        self.symbol = symbol

        # --- BB ---
        self.bb_period = 20
        self.bb_std = 2
        self.bb_closes = deque(maxlen=self.bb_period)

        # --- RSI ---
        self.rsi_length = 14
        self.rsi_smooth = 3
        self.rsi_closes = deque(maxlen=self.rsi_length + 1)
        self.rsi_values = deque(maxlen=self.rsi_smooth)

        # --- SuperTrend ---
        self.st_length = 10
        self.st_multiplier = 2

        self.atr: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.final_upper: Optional[float] = None
        self.final_lower: Optional[float] = None
        self.supertrend: Optional[float] = None
        self._atr_seed = []

        # --- Pivot (Session Frozen) ---
        self.pp = None
        self.r1 = None
        self.s1 = None

        self._warmup()

    # ==================================================
    # WARMUP
    # ==================================================

    def _warmup(self):
        rows = fetch_recent_candles(
            symbol=self.symbol,
            timeframe="3m",
            limit=100,
        )

        if not rows:
            write_audit_log("[BB] No warmup futures candles found")
            return

        for r in rows:
            c = Candle(
                start_ts=r["ts"],
                end_ts=r["ts"] + 180,
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                source="WARMUP",
            )
            self._update_internal(c, warmup=True)

        write_audit_log("[BB] IndicatorBundle warmup complete")

    # ==================================================
    # UPDATE
    # ==================================================

    def update(self, candle: Candle) -> Dict[str, float]:
        return self._update_internal(candle, warmup=False)

    # ==================================================
    # INTERNAL
    # ==================================================

    def _update_internal(self, candle: Candle, warmup=False):

        close = candle.close
        high = candle.high
        low = candle.low

        # ==========================
        # BOLLINGER BANDS
        # ==========================
        self.bb_closes.append(close)

        bb_middle = None
        bb_upper = None
        bb_lower = None
        bb_width = None

        if len(self.bb_closes) == self.bb_period:
            sma = mean(self.bb_closes)
            std = pstdev(self.bb_closes)

            bb_middle = sma
            bb_upper = sma + self.bb_std * std
            bb_lower = sma - self.bb_std * std
            bb_width = bb_upper - bb_lower

        # ==========================
        # RSI (RAW + SMOOTH)
        # ==========================
        self.rsi_closes.append(close)

        rsi_raw = None
        rsi_smooth = None

        if len(self.rsi_closes) > self.rsi_length:

            gains = []
            losses = []

            for i in range(1, len(self.rsi_closes)):
                diff = self.rsi_closes[i] - self.rsi_closes[i - 1]
                gains.append(max(diff, 0))
                losses.append(abs(min(diff, 0)))

            avg_gain = mean(gains[-self.rsi_length:])
            avg_loss = mean(losses[-self.rsi_length:])

            if avg_loss == 0:
                rsi_raw = 100
            else:
                rs = avg_gain / avg_loss
                rsi_raw = 100 - (100 / (1 + rs))

            self.rsi_values.append(rsi_raw)

            if len(self.rsi_values) == self.rsi_smooth:
                rsi_smooth = mean(self.rsi_values)

        # ==========================
        # SUPERTREND
        # ==========================
        st_value = None
        st_direction = None

        if self.prev_close is not None:

            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low - self.prev_close),
            )

            if self.atr is None:
                self._atr_seed.append(tr)
                if len(self._atr_seed) == self.st_length:
                    self.atr = sum(self._atr_seed) / self.st_length
            else:
                self.atr = (
                    (self.atr * (self.st_length - 1)) + tr
                ) / self.st_length

            if self.atr is not None:

                hl2 = (high + low) / 2

                basic_upper = hl2 + self.st_multiplier * self.atr
                basic_lower = hl2 - self.st_multiplier * self.atr

                if self.final_upper is None:
                    self.final_upper = basic_upper
                    self.final_lower = basic_lower
                    self.supertrend = basic_lower
                else:

                    if (
                        basic_upper < self.final_upper
                        or self.prev_close > self.final_upper
                    ):
                        self.final_upper = basic_upper

                    if (
                        basic_lower > self.final_lower
                        or self.prev_close < self.final_lower
                    ):
                        self.final_lower = basic_lower

                    if self.supertrend == self.final_upper:
                        if close <= self.final_upper:
                            self.supertrend = self.final_upper
                        else:
                            self.supertrend = self.final_lower
                    else:
                        if close >= self.final_lower:
                            self.supertrend = self.final_lower
                        else:
                            self.supertrend = self.final_upper

                st_value = self.supertrend
                st_direction = "UP" if close >= self.supertrend else "DOWN"

        self.prev_close = close

        # ==========================
        # PIVOTS (SESSION FROZEN)
        # ==========================
        if self.pp is None:
            pivots = PivotCache.get_pivots(self.symbol)
            if pivots:
                self.pp = pivots["pp"]
                self.r1 = pivots["r1"]
                self.s1 = pivots["s1"]

        # ==========================
        # RETURN VALUES
        # ==========================
        return {
            "bb_middle": bb_middle,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width": bb_width,

            "rsi_raw": rsi_raw,
            "rsi_smooth": rsi_smooth,
            "rsi": rsi_smooth,

            "supertrend": st_value,
            "st_direction": st_direction,

            "r1": self.r1,
            "s1": self.s1,
        }

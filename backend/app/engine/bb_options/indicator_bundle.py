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

        # --- RSI (Wilder's RMA) ---
        self.rsi_length = 14
        self.rsi_smooth = 3
        self.rsi_closes  = deque(maxlen=self.rsi_length + 1)
        self.rsi_values  = deque(maxlen=self.rsi_smooth)
        # BUG 3 FIX: store Wilder state instead of recomputing SMA every bar
        self._rsi_avg_gain: Optional[float] = None
        self._rsi_avg_loss: Optional[float] = None
        self._rsi_seed_gains: list = []
        self._rsi_seed_losses: list = []

        # --- SuperTrend ---
        self.st_length     = 10
        self.st_multiplier = 2

        self.atr: Optional[float]         = None
        self.prev_close: Optional[float]  = None
        self.final_upper: Optional[float] = None
        self.final_lower: Optional[float] = None
        self.supertrend: Optional[float]  = None
        # BUG 2 FIX: use a plain list that is discarded after ATR seeds
        self._atr_seed: list = []

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
            timeframe="3m",   # ← explicit: excludes 1d EOD candles stored in same table
            limit=300,
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
        high  = candle.high
        low   = candle.low

        # ==========================
        # BOLLINGER BANDS
        # (pstdev = population std, matches TradingView default)
        # ==========================
        self.bb_closes.append(close)

        bb_middle = bb_upper = bb_lower = bb_width = None

        if len(self.bb_closes) == self.bb_period:
            sma = mean(self.bb_closes)
            std = pstdev(self.bb_closes)

            bb_middle = sma
            bb_upper  = sma + self.bb_std * std
            bb_lower  = sma - self.bb_std * std
            bb_width  = bb_upper - bb_lower

        # ==========================
        # RSI — Wilder's RMA
        # First rsi_length bars: seed with SMA.
        # Subsequent bars: EMA with alpha = 1/rsi_length.
        # This matches Pine Script ta.rsi() exactly.
        # ==========================
        self.rsi_closes.append(close)

        rsi_raw = rsi_smooth = None

        if len(self.rsi_closes) >= 2:
            diff = self.rsi_closes[-1] - self.rsi_closes[-2]
            gain = max(diff, 0.0)
            loss = abs(min(diff, 0.0))

            if self._rsi_avg_gain is None:
                # Accumulate seed values
                self._rsi_seed_gains.append(gain)
                self._rsi_seed_losses.append(loss)

                if len(self._rsi_seed_gains) == self.rsi_length:
                    # BUG 3 FIX: initialise with SMA, then switch to Wilder RMA
                    self._rsi_avg_gain = mean(self._rsi_seed_gains)
                    self._rsi_avg_loss = mean(self._rsi_seed_losses)
                    # Free seed memory
                    self._rsi_seed_gains  = []
                    self._rsi_seed_losses = []
            else:
                # Wilder's smoothing: alpha = 1 / rsi_length
                alpha = 1.0 / self.rsi_length
                self._rsi_avg_gain = alpha * gain + (1 - alpha) * self._rsi_avg_gain
                self._rsi_avg_loss = alpha * loss + (1 - alpha) * self._rsi_avg_loss

            if self._rsi_avg_gain is not None:
                if self._rsi_avg_loss == 0:
                    rsi_raw = 100.0
                else:
                    rs      = self._rsi_avg_gain / self._rsi_avg_loss
                    rsi_raw = 100.0 - (100.0 / (1.0 + rs))

                self.rsi_values.append(rsi_raw)

                if len(self.rsi_values) == self.rsi_smooth:
                    rsi_smooth = mean(self.rsi_values)

        # ==========================
        # SUPERTREND (10, 2)
        # Canonical Pine Script algorithm:
        #   upperBand = hl2 + mult * atr
        #   lowerBand = hl2 - mult * atr
        #   finalUpper = min(upperBand, prevFinalUpper)  unless prev_close > prevFinalUpper
        #   finalLower = max(lowerBand, prevFinalLower)  unless prev_close < prevFinalLower
        #   direction  = based on PREVIOUS supertrend vs PREVIOUS bands  ← critical
        # ==========================
        st_value = st_direction = None

        if self.prev_close is not None:

            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low  - self.prev_close),
            )

            # ATR: SMA seed, then Wilder smoothing
            if self.atr is None:
                self._atr_seed.append(tr)
                if len(self._atr_seed) == self.st_length:   # BUG 2 FIX: == not >=
                    self.atr = sum(self._atr_seed) / self.st_length
                    self._atr_seed = []                       # free memory
            else:
                self.atr = (
                    (self.atr * (self.st_length - 1)) + tr
                ) / self.st_length

            if self.atr is not None:

                hl2         = (high + low) / 2
                basic_upper = hl2 + self.st_multiplier * self.atr
                basic_lower = hl2 - self.st_multiplier * self.atr

                if self.final_upper is None:
                    # First valid bar — initialise bands and direction
                    self.final_upper = basic_upper
                    self.final_lower = basic_lower
                    self.supertrend  = (
                        basic_upper if close <= basic_upper else basic_lower
                    )

                else:
                    # ── BUG 1 FIX ────────────────────────────────────────────
                    # Snapshot the CURRENT (previous bar's) band values BEFORE
                    # updating them.  The direction switch must compare the old
                    # supertrend against the old bands, not the just-updated ones.
                    # Without this, when final_upper ratchets down (basic_upper <
                    # final_upper), the identity check
                    #     self.supertrend == self.final_upper
                    # becomes False immediately after the update, causing the code
                    # to fall into the bullish branch and flip direction incorrectly.
                    # ─────────────────────────────────────────────────────────
                    prev_final_upper = self.final_upper
                    prev_final_lower = self.final_lower

                    # Update final bands (ratchet logic)
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

                    # Direction switch: compare against SNAPSHOT values
                    if self.supertrend == prev_final_upper:
                        # Was bearish — stays bearish unless close breaks above
                        self.supertrend = (
                            self.final_upper
                            if close <= self.final_upper
                            else self.final_lower
                        )
                    else:
                        # Was bullish — stays bullish unless close breaks below
                        self.supertrend = (
                            self.final_lower
                            if close >= self.final_lower
                            else self.final_upper
                        )

                st_value     = self.supertrend
                st_direction = "UP" if self.supertrend == self.final_lower else "DOWN"

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
            "bb_upper":  bb_upper,
            "bb_lower":  bb_lower,
            "bb_width":  bb_width,

            "rsi_raw":    rsi_raw,
            "rsi_smooth": rsi_smooth,
            "rsi":        rsi_smooth,

            "supertrend":   st_value,
            "st_direction": st_direction,

            "r1": self.r1,
            "s1": self.s1,
        }
# backend/app/engine/bb_v2/indicator_bundle_v2.py
"""
IndicatorBundleV2

Key differences from IndicatorBundle (V1):
  - SuperTrend multiplier: 1.5  (V1 uses 2.0)
  - Extended pivots: R2, PP, S1, S2, S3  (V1 only tracks R1, S1)
  - Return dict uses "supertrend_v2" / "st_direction_v2" keys so the
    V2 pipeline is unambiguous end-to-end and never collides with V1
    field names at the DB write layer.
  - Exposes "prev_close" in return dict for crossover detection in
    ConfluenceSignalEngineV2.
"""

from collections import deque
from statistics import mean, pstdev
from typing import Dict, Optional

from app.marketdata.candle import Candle
from app.db.futures_candles_repo import fetch_recent_candles
from app.event_bus.audit_logger import write_audit_log
from app.indicators.pivot_cache import PivotCache


class IndicatorBundleV2:

    def __init__(self, symbol: str):
        self.symbol = symbol

        # --- Bollinger Bands (same params as V1 — shared) ---
        self.bb_period = 20
        self.bb_std    = 2
        self.bb_closes = deque(maxlen=self.bb_period)

        # --- RSI — Wilder's RMA (same params as V1 — shared) ---
        self.rsi_length       = 14
        self.rsi_smooth       = 3
        self.rsi_closes       = deque(maxlen=self.rsi_length + 1)
        self.rsi_values       = deque(maxlen=self.rsi_smooth)
        self._rsi_avg_gain:   Optional[float] = None
        self._rsi_avg_loss:   Optional[float] = None
        self._rsi_seed_gains: list = []
        self._rsi_seed_losses: list = []

        # --- SuperTrend (10, 1.5) — KEY V2 DIFFERENCE ---
        self.st_length     = 10
        self.st_multiplier = 1.5          # V1 uses 2.0

        self.atr:          Optional[float] = None
        self.prev_close:   Optional[float] = None   # for TR calculation
        self.final_upper:  Optional[float] = None
        self.final_lower:  Optional[float] = None
        self.supertrend:   Optional[float] = None
        self._atr_seed:    list = []

        # --- Extended Pivots (session frozen) ---
        # V2 uses R2, R1, PP, S1, S2, S3
        # V1 only uses R1, S1
        self.pp: Optional[float] = None
        self.r1: Optional[float] = None
        self.r2: Optional[float] = None
        self.s1: Optional[float] = None
        self.s2: Optional[float] = None
        self.s3: Optional[float] = None

        self._warmup()

    # ==================================================
    # WARMUP
    # ==================================================

    def _warmup(self):
        rows = fetch_recent_candles(
            symbol=self.symbol,
            timeframe="3m",
            limit=300,
        )

        if not rows:
            write_audit_log("[BB_V2] No warmup futures candles found")
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

        write_audit_log("[BB_V2] IndicatorBundleV2 warmup complete")

    # ==================================================
    # PUBLIC API
    # ==================================================

    def update(self, candle: Candle) -> Dict:
        return self._update_internal(candle, warmup=False)

    # ==================================================
    # INTERNAL
    # ==================================================

    def _update_internal(self, candle: Candle, warmup: bool = False) -> Dict:

        close = candle.close
        high  = candle.high
        low   = candle.low

        # Snapshot prev_close BEFORE any state mutation.
        # ConfluenceSignalEngineV2 needs this for crossover detection.
        prev_close_snapshot = self.prev_close

        # ==========================
        # BOLLINGER BANDS
        # Identical to V1 — same underlying data, results will match.
        # ==========================
        self.bb_closes.append(close)

        bb_middle = bb_upper = bb_lower = bb_width = None

        if len(self.bb_closes) == self.bb_period:
            sma       = mean(self.bb_closes)
            std       = pstdev(self.bb_closes)
            bb_middle = sma
            bb_upper  = sma + self.bb_std * std
            bb_lower  = sma - self.bb_std * std
            bb_width  = bb_upper - bb_lower

        # ==========================
        # RSI — Wilder's RMA
        # Identical to V1 — same underlying data, results will match.
        # ==========================
        self.rsi_closes.append(close)

        rsi_raw = rsi_smooth = None

        if len(self.rsi_closes) >= 2:
            diff = self.rsi_closes[-1] - self.rsi_closes[-2]
            gain = max(diff, 0.0)
            loss = abs(min(diff, 0.0))

            if self._rsi_avg_gain is None:
                self._rsi_seed_gains.append(gain)
                self._rsi_seed_losses.append(loss)

                if len(self._rsi_seed_gains) == self.rsi_length:
                    self._rsi_avg_gain    = mean(self._rsi_seed_gains)
                    self._rsi_avg_loss    = mean(self._rsi_seed_losses)
                    self._rsi_seed_gains  = []
                    self._rsi_seed_losses = []
            else:
                alpha              = 1.0 / self.rsi_length
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
        # SUPERTREND (10, 1.5)
        #
        # Algorithm is identical to V1; only self.st_multiplier differs.
        # The result is stored in self.supertrend — which is ONLY used
        # inside this class and the V2 pipeline.  It is returned under
        # the key "supertrend_v2" so it never collides with V1's
        # "supertrend" key at the DB write layer.
        # ==========================
        st_value_v2 = st_direction_v2 = None

        if self.prev_close is not None:

            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low  - self.prev_close),
            )

            # ATR: SMA seed → Wilder smoothing
            if self.atr is None:
                self._atr_seed.append(tr)
                if len(self._atr_seed) == self.st_length:
                    self.atr       = sum(self._atr_seed) / self.st_length
                    self._atr_seed = []
            else:
                self.atr = (
                    (self.atr * (self.st_length - 1)) + tr
                ) / self.st_length

            if self.atr is not None:

                hl2         = (high + low) / 2
                basic_upper = hl2 + self.st_multiplier * self.atr   # 1.5 × ATR
                basic_lower = hl2 - self.st_multiplier * self.atr

                if self.final_upper is None:
                    self.final_upper = basic_upper
                    self.final_lower = basic_lower
                    self.supertrend  = (
                        basic_upper if close <= basic_upper else basic_lower
                    )
                else:
                    prev_final_upper = self.final_upper
                    prev_final_lower = self.final_lower

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

                    if self.supertrend == prev_final_upper:
                        self.supertrend = (
                            self.final_upper
                            if close <= self.final_upper
                            else self.final_lower
                        )
                    else:
                        self.supertrend = (
                            self.final_lower
                            if close >= self.final_lower
                            else self.final_upper
                        )

                st_value_v2     = self.supertrend
                st_direction_v2 = (
                    "UP" if self.supertrend == self.final_lower else "DOWN"
                )

        # Update prev_close for next candle's TR and snapshot
        self.prev_close = close

        # ==========================
        # EXTENDED PIVOTS (session frozen)
        # R2, R1, PP, S1, S2, S3 — all from PivotCache.
        # ==========================
        if self.pp is None:
            pivots = PivotCache.get_pivots(self.symbol)
            if pivots:
                self.pp = pivots.get("pp")
                self.r1 = pivots.get("r1")
                self.r2 = pivots.get("r2")
                self.s1 = pivots.get("s1")
                self.s2 = pivots.get("s2")
                self.s3 = pivots.get("s3")

        # ==========================
        # RETURN DICT
        #
        # KEY NAMING RULES:
        #   "supertrend_v2" / "st_direction_v2"  — V2's ST(10, 1.5).
        #       The "_v2" suffix propagates through to the DB column
        #       name without any translation step needed.
        #   "bb_*", "rsi_*", "r1", "s1", "r2", "pp", "s2", "s3"
        #       — shared fields, same key names as in the DB columns.
        #   "prev_close" — internal helper for ConfluenceSignalEngineV2;
        #       not written to the DB.
        # ==========================
        return {
            # Shared Bollinger
            "bb_middle": bb_middle,
            "bb_upper":  bb_upper,
            "bb_lower":  bb_lower,
            "bb_width":  bb_width,

            # Shared RSI
            "rsi_raw":    rsi_raw,
            "rsi_smooth": rsi_smooth,
            "rsi":        rsi_smooth,

            # V2-specific SuperTrend — named with _v2 suffix throughout
            "supertrend_v2":   st_value_v2,
            "st_direction_v2": st_direction_v2,

            # Shared extended pivots
            "pp": self.pp,
            "r1": self.r1,
            "r2": self.r2,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,

            # Crossover helper (consumed by signal engine, not written to DB)
            "prev_close": prev_close_snapshot,
        }
# backend/app/engine/indicator_engine_pine_v1_9.py

from collections import deque   # ── SCALP_V1_EMA_GATE_20260824 ──
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

    def __init__(self, gate_ema_period: Optional[int] = None,
                 gate_slope_lookback: int = 30):
        # ── SCALP_V1_EMA_GATE_20260824 ── optional configurable-period gate
        # EMA (D10.1). period=None (every existing call site) creates NOTHING:
        # zero overhead, zero behavior change for V3/V5/trade_engine users.
        self._gate_period = int(gate_ema_period) if gate_ema_period else None
        self._gate_lookback = max(1, int(gate_slope_lookback or 30))
        self.gate_ema = EMA(self._gate_period) if self._gate_period else None
        self._gate_hist: deque = deque(maxlen=self._gate_lookback + 1)
        # ── SCALP_V1_VWAP_20260825 ── session VWAP of the premium. Two
        # accumulators; warmup candles excluded; reset on IST day rollover.
        self._vwap_pv = 0.0
        self._vwap_v = 0.0
        self._vwap_day = None
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

        # ── SCALP_V1_EMA_GATE_20260824 ── gate EMA + slope over lookback.
        # TMA_V2 doctrine: single-bar deltas are noise; slope is the delta
        # across the full lookback window, and is None until the window fills.
        # ── SCALP_V1_VWAP_20260825 ── session-anchored VWAP accumulation.
        # No warmup flag needed: warmup candles are PRIOR days, so the IST
        # day-rollover reset below wipes them the moment today's first candle
        # arrives — session purity is guaranteed by the reset itself, in both
        # the backtest (per-day contexts) and long-lived live instances.
        # Typical price = (H+L+C)/3, volume-weighted; zero cum volume -> None.
        _cts = getattr(candle, "start_ts", None) or getattr(candle, "ts", None)                or getattr(candle, "end_ts", None)
        if _cts is not None:
            _cday = int((_cts + 19800) // 86400)   # IST day index
            if self._vwap_day != _cday:
                self._vwap_day = _cday
                self._vwap_pv = 0.0
                self._vwap_v = 0.0
        _vol = float(getattr(candle, "volume", 0) or 0)
        if _vol > 0:
            self._vwap_pv += ((h + l + c) / 3.0) * _vol
            self._vwap_v += _vol
        vwap_val = (self._vwap_pv / self._vwap_v) if self._vwap_v > 0 else None

        gate_val = gate_slope = None
        if self.gate_ema is not None:
            gate_val = self.gate_ema.update(c)
            if gate_val is not None:
                self._gate_hist.append(gate_val)
                if len(self._gate_hist) == self._gate_hist.maxlen:
                    gate_slope = gate_val - self._gate_hist[0]

        # Store latest values
        self.values = {
            "ema8": ema8_val,
            "ema20_low": ema20_low,
            "ema20_high": ema20_high,
            "rsi_raw": rsi_raw,
            "rsi_smoothed": rsi_smoothed,
            "rsi_rising": rsi_rising,
            # ── SCALP_V1_EMA_GATE_20260824 ── EXCLUDED from the ready latch
            "gate_ema": gate_val,
            "gate_ema_slope": gate_slope,
            # ── SCALP_V1_VWAP_20260825 ── excluded from the ready latch
            "vwap": vwap_val,
        }

        # 🔒 READY LATCH (once true, always true)
        if not self.ready:
            # ── SCALP_V1_EMA_GATE_20260824 ── latch over the CORE keys only:
            # a slow gate EMA (e.g. 144) must never delay readiness for ANY
            # strategy sharing this engine. The gate itself fails closed on a
            # None slope at the signal site instead.
            _core = ("ema8", "ema20_low", "ema20_high",
                     "rsi_raw", "rsi_smoothed", "rsi_rising")
            self.ready = all(self.values.get(k) is not None for k in _core)

        # Indicator ready log (LIVE ONLY, once)
        if self.ready and not self._ready_logged and not self._is_warmup:
            write_audit_log(
                "[INDICATOR] READY "
                f"EMA8={ema8_val} "
                f"EMA20_L={ema20_low} "
                f"EMA20_H={ema20_high} "
                f"RSI={rsi_smoothed}"
            )
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

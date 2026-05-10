# backend/app/engine/ha_options/ha_signal_engine.py
"""
HA Signal Engine
================
Evaluates entry and exit conditions for the HA_V1 strategy.

Entry logic operates on a rolling window of 3 completed Heikin Ashi candles:
  candles[-3] = N-2  (two bars ago)
  candles[-2] = N-1  (previous bar)
  candles[-1] = N    (just-closed bar)

Entry Condition 1:
  - N-1 is RED and body/wick touches/crosses EMA20_Low
  - N   is GREEN  (the just-closed confirmation candle)

Entry Condition 2:
  - N   is GREEN and body/wick touches/crosses EMA20_Low
  - N-1 is RED

Entry Condition 3:
  - N   is GREEN and body/wick touches/crosses EMA20_Low
  - N-1 is GREEN (does NOT touch EMA20_Low)
  - N-2 is RED

Exit logic:
  - SL hit: current candle CLOSE is at or below the stored SL price
    (checked against close only, NOT intra-candle low)
  - SuperTrend-style signal reversal exits are NOT used here.
    Trade is managed purely by SL/TP (GTT OCO).

All state is pure-Python; no DB, no broker calls.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from collections import deque

from app.indicators.heikin_ashi import HACandle
from app.event_bus.audit_logger import write_audit_log


# ──────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────

@dataclass
class HATradeSignal:
    action: Optional[str] = None           # ENTER_CE / ENTER_PE / None
    reason: Optional[str] = None           # which condition triggered
    rejection_reason: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Signal engine
# ──────────────────────────────────────────────────────────────────

class HASignalEngine:

    def __init__(self, max_trades_per_side: int = 10):
        self.max_trades_per_side = max_trades_per_side

        # In-trade flags (set by trade manager on confirmed entry/exit)
        self.ce_in_trade: bool = False
        self.pe_in_trade: bool = False

        # Daily trade counters
        self.ce_trades_today: int = 0
        self.pe_trades_today: int = 0

        # Rolling buffer of HA candles — we only need the last 3
        self._buf: deque = deque(maxlen=3)

    # ── Daily reset ───────────────────────────────────────────────

    def reset_daily(self):
        self.ce_trades_today = 0
        self.pe_trades_today = 0
        self.ce_in_trade = False
        self.pe_in_trade = False
        self._buf.clear()
        write_audit_log("[HA_SIGNAL] Daily reset")

    # ── External notifications ────────────────────────────────────

    def notify_exit(self, side: str):
        if side == "CE":
            self.ce_in_trade = False
        elif side == "PE":
            self.pe_in_trade = False

    def confirm_entry(self, side: str):
        if side == "CE":
            self.ce_in_trade = True
            self.ce_trades_today += 1
        elif side == "PE":
            self.pe_in_trade = True
            self.pe_trades_today += 1

    # ── Main update (called every closed HA candle) ───────────────

    def update(self, candle: HACandle, ema_low: Optional[float]) -> HATradeSignal:
        """
        Push the latest closed HA candle and evaluate entry signals.

        Returns HATradeSignal with action=ENTER_CE / ENTER_PE / None.
        """
        self._buf.append(candle)

        # Need at least 2 candles for conditions 1 & 2
        if len(self._buf) < 2:
            return HATradeSignal(rejection_reason="WARMING_UP")

        if ema_low is None:
            return HATradeSignal(rejection_reason="EMA_NOT_READY")

        N   = self._buf[-1]   # just-closed candle
        N1  = self._buf[-2]   # previous candle
        N2  = self._buf[-3] if len(self._buf) >= 3 else None

        # ── CE entry (bounce off EMA20_Low = bullish) ──────────────
        ce_signal = self._check_ce_entry(N, N1, N2, ema_low)
        if ce_signal:
            if self.ce_in_trade:
                return HATradeSignal(rejection_reason="CE_ALREADY_IN_TRADE")
            if self.ce_trades_today >= self.max_trades_per_side:
                return HATradeSignal(rejection_reason="CE_MAX_TRADES_REACHED")
            return HATradeSignal(action="ENTER_CE", reason=ce_signal)

        # ── PE entry (same bounce logic, mirror) ───────────────────
        pe_signal = self._check_pe_entry(N, N1, N2, ema_low)
        if pe_signal:
            if self.pe_in_trade:
                return HATradeSignal(rejection_reason="PE_ALREADY_IN_TRADE")
            if self.pe_trades_today >= self.max_trades_per_side:
                return HATradeSignal(rejection_reason="PE_MAX_TRADES_REACHED")
            return HATradeSignal(action="ENTER_PE", reason=pe_signal)

        return HATradeSignal(rejection_reason=self._build_rejection(N, N1, N2, ema_low))

    # ── CE entry conditions ───────────────────────────────────────

    def _check_ce_entry(
        self,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ) -> Optional[str]:
        """
        For CE we treat EMA20_Low as dynamic support.
        A candle touching/crossing EMA20_Low and then reversing upward
        signals a bullish bounce → buy CE.

        Condition 1: N-1 RED touching EMA20_Low, N GREEN
        Condition 2: N GREEN touching EMA20_Low, N-1 RED
        Condition 3: N GREEN touching EMA20_Low, N-1 GREEN (no touch), N-2 RED
        """

        # Condition 1: confirmation candle
        if (
            N1.is_red
            and N1.touches_or_crosses_ema_low(ema_low)
            and N.is_green
        ):
            return "COND1_RED_TOUCH_GREEN_CONFIRM"

        # Condition 2: current candle touches + is green, prev red
        if (
            N.is_green
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_red
        ):
            return "COND2_GREEN_TOUCH_PREV_RED"

        # Condition 3: three-bar pattern
        if (
            N2 is not None
            and N.is_green
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_green
            and not N1.touches_or_crosses_ema_low(ema_low)
            and N2.is_red
        ):
            return "COND3_GREEN_TOUCH_GREEN_RED"

        return None

    # ── PE entry conditions ───────────────────────────────────────

    def _check_pe_entry(
        self,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ) -> Optional[str]:
        """
        For PE we treat EMA20_Low as dynamic resistance approached from above.
        A candle touching/crossing EMA20_Low and then reversing downward
        signals a bearish rejection → buy PE.

        Mirror of CE conditions but with RED confirmation.

        Condition 1: N-1 GREEN touching EMA20_Low, N RED
        Condition 2: N RED touching EMA20_Low, N-1 GREEN
        Condition 3: N RED touching EMA20_Low, N-1 RED (no touch), N-2 GREEN
        """

        # Condition 1: confirmation candle
        if (
            N1.is_green
            and N1.touches_or_crosses_ema_low(ema_low)
            and N.is_red
        ):
            return "PE_COND1_GREEN_TOUCH_RED_CONFIRM"

        # Condition 2: current candle touches + is red, prev green
        if (
            N.is_red
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_green
        ):
            return "PE_COND2_RED_TOUCH_PREV_GREEN"

        # Condition 3: three-bar pattern
        if (
            N2 is not None
            and N.is_red
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_red
            and not N1.touches_or_crosses_ema_low(ema_low)
            and N2.is_green
        ):
            return "PE_COND3_RED_TOUCH_RED_GREEN"

        return None

    # ── Rejection summary for logs ────────────────────────────────

    def _build_rejection(
        self,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ) -> str:
        parts = []
        if self.ce_in_trade:
            parts.append("CE_IN_TRADE")
        if self.pe_in_trade:
            parts.append("PE_IN_TRADE")
        if not N.touches_or_crosses_ema_low(ema_low):
            parts.append(f"NO_EMA_TOUCH(N.low={N.low:.2f} ema={ema_low:.2f})")
        if not parts:
            parts.append("NO_PATTERN_MATCH")
        return " | ".join(parts)

    # ── SL check (close-based, called by trade manager) ──────────

    @staticmethod
    def sl_hit(candle_close: float, sl_price: float) -> bool:
        """
        SL is checked against candle close price only.
        Returns True if the close is at or below the SL level.
        """
        return candle_close <= sl_price
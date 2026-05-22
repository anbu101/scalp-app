# backend/app/engine/ha_options/ha_signal_engine.py
"""
HA Signal Engine
================
Evaluates the three EMA20_Low bounce entry conditions on 1-minute
Heikin Ashi candles.

CRITICAL DESIGN DECISION:
  Entry conditions are IDENTICAL for CE and PE options.
  Both CE and PE option candles are evaluated the same way —
  an option going UP is bullish for that option regardless of whether
  it is a CE or a PE.  The engine does not mirror or invert conditions.

  The side (CE / PE) is determined solely by WHICH symbol's candle
  triggered the condition, not by the condition logic itself.

Entry conditions (all three evaluated on the SAME symbol's HA candles):

  Condition 1 — Red confirmation:
    N-1 is RED  AND  (N-1 body or wick) touches/crosses EMA20_Low
    AND  N (just-closed) is GREEN

  Condition 2 — Immediate green touch:
    N is GREEN  AND  (N body or wick) touches/crosses EMA20_Low
    AND  N-1 is RED

  Condition 3 — Three-bar bounce:
    N   is GREEN  AND  (N body or wick) touches/crosses EMA20_Low
    N-1 is GREEN  AND  N-1 does NOT touch EMA20_Low
    N-2 is RED

SL:
  Previous/most-recent RED HA candle's Low — same for CE and PE.
  Checked against candle CLOSE price only (never intra-candle wick).

EMA20_Low:
  Standard EMA(20) of HA candle Low prices.
  Source = Low, Smoothing = None  (matches TradingView screenshot).
"""

from dataclasses import dataclass
from typing import Optional
from collections import deque

from app.indicators.heikin_ashi import HACandle
from app.event_bus.audit_logger import write_audit_log


# ──────────────────────────────────────────────────────────────────
# Signal result
# ──────────────────────────────────────────────────────────────────

@dataclass
class HAEntrySignal:
    """Result of evaluating one closed HA candle for a single symbol."""
    should_enter: bool = False
    condition:    Optional[str]   = None   # COND1 / COND2 / COND3
    sl_price:     Optional[float] = None   # last red HA candle low
    rejection:    Optional[str]   = None


# ──────────────────────────────────────────────────────────────────
# Per-symbol condition evaluator
# ──────────────────────────────────────────────────────────────────

class HAConditionEvaluator:
    """
    Stateful, per-symbol evaluator.

    Maintains a rolling window of the last 3 completed HA candles
    and the most recent red HA candle low (for SL reference).

    Call push(candle, ema_low) after every completed HA candle.
    """

    def __init__(self):
        # Rolling buffer — newest candle is [-1]
        self._buf: deque = deque(maxlen=3)
        # Most recent red HA candle's low (SL reference)
        self._last_red_low: Optional[float] = None

    def push(self, candle: HACandle, ema_low: Optional[float]) -> HAEntrySignal:
        """
        Push the just-completed HA candle and evaluate entry conditions.

        Returns HAEntrySignal.  should_enter=True means all three
        conditions for one of the patterns matched.

        EMA20_Low must be provided by the caller (computed externally
        so the same EMA state persists across calls).
        """
        # Track last red candle low BEFORE appending (so N-1 / N-2 refs work)
        if candle.is_red:
            self._last_red_low = candle.low

        self._buf.append(candle)

        if ema_low is None:
            return HAEntrySignal(rejection="EMA_NOT_READY")

        if len(self._buf) < 2:
            return HAEntrySignal(rejection="WARMING_UP")

        N  = self._buf[-1]   # just-closed candle
        N1 = self._buf[-2]   # previous candle
        N2 = self._buf[-3] if len(self._buf) >= 3 else None

        # ── Condition 1 ───────────────────────────────────────────
        # N-1 RED touching EMA20_Low  +  N GREEN
        if (
            N1.is_red
            and N1.low <= ema_low          # body or wick touches/crosses
            and N1.high >= ema_low
            and N.is_green
        ):
            return HAEntrySignal(
                should_enter=True,
                condition="COND1",
                sl_price=self._last_red_low,
            )

        # ── Condition 2 ───────────────────────────────────────────
        # N GREEN touching EMA20_Low  +  N-1 RED
        if (
            N.is_green
            and N.low <= ema_low
            and N.high >= ema_low
            and N1.is_red
        ):
            return HAEntrySignal(
                should_enter=True,
                condition="COND2",
                sl_price=self._last_red_low,
            )

        # ── Condition 3 ───────────────────────────────────────────
        # N GREEN touching  +  N-1 GREEN (no touch)  +  N-2 RED
        if (
            N2 is not None
            and N.is_green
            and N.low <= ema_low
            and N.high >= ema_low
            and N1.is_green
            and (N1.low > ema_low or N1.high < ema_low)           # N-1 must NOT touch
            and N2.is_red
        ):
            return HAEntrySignal(
                should_enter=True,
                condition="COND3",
                sl_price=self._last_red_low,
            )

        return HAEntrySignal(
            rejection=self._describe_rejection(N, N1, N2, ema_low)
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _describe_rejection(
        self,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ) -> str:
        parts = []
        if not N.is_green:
            parts.append(f"N_RED(close={N.close:.2f})")
        if ((N.low > ema_low or N.high < ema_low) ):
            parts.append(f"NO_EMA_TOUCH(N.low={N.low:.2f}>ema={ema_low:.2f})")
        if not parts:
            parts.append("NO_PATTERN_MATCH")
        return " | ".join(parts)

    @property
    def last_red_low(self) -> Optional[float]:
        return self._last_red_low


# ──────────────────────────────────────────────────────────────────
# Trade state tracker  (one shared instance per engine)
# ──────────────────────────────────────────────────────────────────

class HASignalEngine:
    """
    Tracks per-side in-trade state and daily trade counts.

    Does NOT evaluate entry conditions — that is done by
    HAConditionEvaluator (one per symbol).

    The side is "CE" or "PE" and is determined by which symbol
    triggered the entry condition in ha_tick_engine.
    """

    def __init__(self, max_trades_per_side: int = 10):
        self.max_trades_per_side = max_trades_per_side

        self.ce_in_trade:     bool = False
        self.pe_in_trade:     bool = False
        self.ce_trades_today: int  = 0
        self.pe_trades_today: int  = 0

    # ── Daily reset ──────────────────────────────────────────────

    def reset_daily(self):
        self.ce_trades_today = 0
        self.pe_trades_today = 0
        self.ce_in_trade     = False
        self.pe_in_trade     = False
        write_audit_log("[HA_SIGNAL] Daily reset")

    # ── Entry gate ───────────────────────────────────────────────

    def can_enter(self, side: str) -> tuple:
        """
        Returns (allowed: bool, reason: str).
        Checks in_trade flag and daily trade ceiling.
        """
        if side == "CE":
            if self.ce_in_trade:
                return False, "CE_ALREADY_IN_TRADE"
            if self.ce_trades_today >= self.max_trades_per_side:
                return False, "CE_MAX_TRADES_REACHED"
        elif side == "PE":
            if self.pe_in_trade:
                return False, "PE_ALREADY_IN_TRADE"
            if self.pe_trades_today >= self.max_trades_per_side:
                return False, "PE_MAX_TRADES_REACHED"
        return True, "OK"

    # ── State mutations ──────────────────────────────────────────

    def confirm_entry(self, side: str):
        if side == "CE":
            self.ce_in_trade      = True
            self.ce_trades_today += 1
        elif side == "PE":
            self.pe_in_trade      = True
            self.pe_trades_today += 1

    def notify_exit(self, side: str):
        if side == "CE":
            self.ce_in_trade = False
        elif side == "PE":
            self.pe_in_trade = False

    # ── SL check (close-based, always) ──────────────────────────

    @staticmethod
    def sl_hit(candle_close: float, sl_price: float) -> bool:
        """
        Returns True when the candle CLOSE is at or below SL.
        This is the ONLY way SL is triggered — no intra-candle wicks.
        Applies identically to PAPER and LIVE modes.
        """
        return candle_close <= sl_price

    @staticmethod
    def tp_hit(candle_close: float, tp_price: float) -> bool:
        """
        Returns True when the candle CLOSE is at or above TP.
        Used in PAPER mode.  LIVE mode uses a GTT SINGLE for TP.
        """
        return candle_close >= tp_price
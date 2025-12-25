from dataclasses import dataclass
from typing import Optional
from datetime import date, timedelta

from app.event_bus.audit_logger import write_audit_log
from app.utils.candle_debug_logger import CandleDebugLogger
from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19


# =========================
# Data structures
# =========================

@dataclass
class Signal:
    is_buy: bool = False
    is_exit: bool = False
    exit_reason: Optional[str] = None
    entry_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


# =========================
# Strategy Engine
# =========================

class StrategyEngine:
    """
    Pine-parity BUY engine (OPTION chart only)

    HARD RULE:
    ✅ Trade ONLY current-week expiry
    ❌ Ignore next-week expiry (even if indicators are valid)
    """

    MIN_RR = 1.0
    MIN_SL = 5.0

    def __init__(self, slot_name: str, symbol: str):
        self.slot_name = slot_name
        self.symbol = symbol

        self.in_trade = False
        self.entry_price = None
        self.sl = None
        self.tp = None

        # 🔍 Candle debug logger (one file per day)
        self.debug_logger = CandleDebugLogger(
            symbol=symbol,
            slot=slot_name,
        )

    # =========================
    # Public API
    # =========================

    def on_candle(self, candle, ind: IndicatorEnginePineV19, conditions: dict) -> Signal:
        signal = Signal()
        snap = ind.snapshot()

        # -------------------------
        # 🔍 DEBUG LOG (EVERY CANDLE)
        # -------------------------
        self.debug_logger.log(
            candle_ts=candle.end_ts,
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
            ind=snap or {},
            checks=conditions,
            buy_allowed=conditions.get("cond_all", False),
        )

        # -------------------------
        # EXIT LOGIC
        # -------------------------
        if self.in_trade:
            if candle.low <= self.sl:
                signal.is_exit = True
                signal.exit_reason = "SL"
                write_audit_log(
                    f"[STRATEGY][{self.slot_name}][{self.symbol}] EXIT_SL price={self.sl}"
                )
                self._reset()
                return signal

            if candle.high >= self.tp:
                signal.is_exit = True
                signal.exit_reason = "TP"
                write_audit_log(
                    f"[STRATEGY][{self.slot_name}][{self.symbol}] EXIT_TP price={self.tp}"
                )
                self._reset()
                return signal

            return signal

        # =================================================
        # ENTRY LOGIC
        # =================================================

        # 🔴 GREEN candle only
        if candle.close <= candle.open:
            return signal

        if snap is None:
            return signal

        if not conditions.get("cond_all"):
            return signal

        # =================================================
        # 🔒 HARD CURRENT-WEEK EXPIRY GUARD (ADDED)
        # =================================================
        if not self._is_current_week_expiry():
            return signal  # silent ignore

        # -------------------------
        # SL from nearest previous RED candle
        # -------------------------
        sl_price = ind.find_previous_red_low()
        if sl_price is None:
            return signal

        risk = candle.close - sl_price

        # -------------------------
        # HARD MIN SL CHECK
        # -------------------------
        min_sl = self.MIN_SL
        try:
            from app.config.strategy_loader import load_strategy_config
            cfg = load_strategy_config()
            min_sl = cfg.get("min_sl_points", min_sl)
        except Exception:
            pass

        if risk < min_sl:
            write_audit_log(
                f"[STRATEGY][{self.slot_name}][{self.symbol}] "
                f"SKIP_SIGNAL → risk {risk:.2f} < min_sl {min_sl}"
            )
            return signal

        # -------------------------
        # BUY
        # -------------------------
        self.in_trade = True
        self.entry_price = candle.close
        self.sl = sl_price
        self.tp = candle.close + (risk * self.MIN_RR)

        signal.is_buy = True
        signal.entry_price = self.entry_price
        signal.sl = self.sl
        signal.tp = self.tp

        write_audit_log(
            f"[STRATEGY][{self.slot_name}][{self.symbol}] BUY_SIGNAL\n"
            f"  entry={self.entry_price}\n"
            f"  sl={self.sl}\n"
            f"  tp={self.tp}\n"
            f"  risk={risk:.2f}"
        )

        return signal

    # =========================
    # Helpers
    # =========================

    def _is_current_week_expiry(self) -> bool:
        """
        Returns True if option belongs to current weekly expiry (Thursday).
        """
        try:
            # Example: NIFTY25D2326300PE → extract expiry code
            # Zerodha weekly options encode expiry implicitly by date in symbol
            today = date.today()

            # Find nearest Thursday
            days_to_thu = (3 - today.weekday()) % 7
            current_expiry = today + timedelta(days=days_to_thu)

            # If expiry already passed today (post-market), move to next week
            if today.weekday() > 3:
                current_expiry += timedelta(days=7)

            return str(current_expiry.year % 100) in self.symbol
        except Exception:
            return False

    def _reset(self):
        self.in_trade = False
        self.entry_price = None
        self.sl = None
        self.tp = None

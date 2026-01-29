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

        if candle.close <= candle.open:
            return signal

        if snap is None:
            return signal

        if not conditions.get("cond_all"):
            return signal

        if not self._is_current_week_expiry():
            return signal

        # -------------------------
        # SL from nearest previous RED candle
        # -------------------------
        sl_price = ind.find_previous_red_low()
        if sl_price is None:
            return signal

        entry_price = candle.close
        raw_risk = entry_price - sl_price

        # -------------------------
        # LOAD CONFIG (SAFE)
        # -------------------------
        min_sl = self.MIN_SL
        rr = self.MIN_RR
        max_sl = None

        try:
            from app.config.strategy_loader import load_strategy_config
            cfg = load_strategy_config()
            min_sl = cfg.get("min_sl_points", min_sl)
            rr = cfg.get("risk_reward_ratio", rr)
            max_sl = cfg.get("max_sl_points")
        except Exception:
            pass

        if raw_risk < min_sl:
            write_audit_log(
                f"[STRATEGY][{self.slot_name}][{self.symbol}] "
                f"SKIP_SIGNAL → risk {raw_risk:.2f} < min_sl {min_sl}"
            )
            return signal

        # -------------------------
        # 🔒 HARD MAX SL CLAMP (ENTRY-REFERENCED)
        # -------------------------
        risk = raw_risk

        if isinstance(max_sl, (int, float)) and max_sl > 0:
            min_sl_price = entry_price - max_sl
            if sl_price < min_sl_price:
                write_audit_log(
                    f"[STRATEGY][{self.slot_name}][{self.symbol}] "
                    f"MAX_SL_APPLIED → sl {sl_price:.2f} raised to {min_sl_price:.2f}"
                )
                sl_price = min_sl_price
                risk = entry_price - sl_price

        # -------------------------
        # BUY
        # -------------------------
        self.in_trade = True
        self.entry_price = entry_price
        self.sl = sl_price
        self.tp = entry_price + (risk * rr)

        signal.is_buy = True
        signal.entry_price = self.entry_price
        signal.sl = self.sl
        signal.tp = self.tp

        write_audit_log(
            f"[STRATEGY][{self.slot_name}][{self.symbol}] BUY_SIGNAL\n"
            f"  entry={self.entry_price}\n"
            f"  sl={self.sl}\n"
            f"  tp={self.tp}\n"
            f"  risk={risk:.2f}\n"
            f"  rr={rr}"
        )

        return signal

    # =========================
    # Helpers
    # =========================

    def _is_current_week_expiry(self) -> bool:
        try:
            today = date.today()
            days_to_thu = (3 - today.weekday()) % 7
            current_expiry = today + timedelta(days=days_to_thu)
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

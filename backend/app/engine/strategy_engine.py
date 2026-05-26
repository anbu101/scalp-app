# backend/app/engine/strategy_engine.py
#
# SCALP_V1 — Option SHORT SELLING mode
#
# Entry signal conditions are unchanged (green candle, EMA, RSI).
# What changed:
#   - We now SELL the option at entry (not buy)
#   - Target (TP) = previous red candle's low  ← was the SL before
#   - Stop Loss    = entry + (risk_distance × RR)  ← price rising is bad for seller
#   - Exit tracking: SL fires on candle.HIGH >= self.sl (premium spikes up)
#                    TP fires on candle.LOW  <= self.tp (premium falls to target)
#   - Signal dataclass uses is_sell (is_buy removed)
#   - target_override config key removed

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
    is_sell: bool = False        # SCALP_V1 short entry signal
    is_exit: bool = False
    exit_reason: Optional[str] = None
    entry_price: Optional[float] = None
    sl: Optional[float] = None   # above entry — premium rising = loss
    tp: Optional[float] = None   # below entry — premium falling = profit


# =========================
# Strategy Engine
# =========================

class StrategyEngine:
    """
    Pine-parity SHORT SELL engine (OPTION chart only).

    Signal fires on same green-candle / EMA / RSI conditions as before.
    The trade direction is now SHORT:
      - Sell the option premium at entry
      - Profit when premium decays to previous red candle low (TP)
      - Cut loss when premium spikes above entry + risk*RR (SL)

    HARD RULE:
    ✅ Trade ONLY current-week expiry
    ❌ Ignore next-week expiry
    """

    MIN_RR  = 0.1
    MIN_SL  = 5.0

    def __init__(self, strategy_id: str, slot_name: str, symbol: str):
        self.strategy_id = strategy_id
        self.slot_name   = slot_name
        self.symbol      = symbol

        self.in_trade    = False
        self.entry_price = None
        self.sl          = None   # above entry
        self.tp          = None   # below entry (= prev red candle low)

        # Candle debug logger (one file per day)
        self.debug_logger = CandleDebugLogger(
            symbol=symbol,
            slot=slot_name,
        )

    # =========================
    # Public API
    # =========================

    def on_candle(self, candle, ind: IndicatorEnginePineV19, conditions: dict) -> Signal:
        signal = Signal()
        snap   = ind.snapshot()

        # ── DEBUG LOG (every candle) ──────────────────────────
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

        # ── EXIT LOGIC (in-trade state tracking) ─────────────
        # For SHORT trades:
        #   SL = premium spikes UP above self.sl  → check HIGH
        #   TP = premium falls  DOWN to self.tp   → check LOW
        if self.in_trade:
            if candle.high >= self.sl:
                signal.is_exit     = True
                signal.exit_reason = "SL"
                write_audit_log(
                    f"[SCALP-STRATEGY][{self.slot_name}][{self.symbol}] "
                    f"EXIT_SL (high={candle.high} >= sl={self.sl})"
                )
                self._reset()
                return signal

            if candle.low <= self.tp:
                signal.is_exit     = True
                signal.exit_reason = "TP"
                write_audit_log(
                    f"[SCALP-STRATEGY][{self.slot_name}][{self.symbol}] "
                    f"EXIT_TP (low={candle.low} <= tp={self.tp})"
                )
                self._reset()
                return signal

            return signal

        # ── ENTRY LOGIC ───────────────────────────────────────

        # Must be a green candle
        if candle.close <= candle.open:
            return signal

        # Indicators must be ready
        if snap is None:
            return signal

        # All conditions gate
        if not conditions.get("cond_all"):
            return signal

        # Must be current-week expiry symbol
        if not self._is_current_week_expiry():
            return signal

        # ── SL distance from previous red candle's LOW ────────
        # prev_red_low is the SAME value that was the SL when buying.
        # For selling it becomes the TARGET (TP).
        prev_red_low = ind.find_previous_red_low()
        if prev_red_low is None:
            return signal

        entry_price    = candle.close
        risk_distance  = entry_price - prev_red_low   # how far TP is below entry

        # ── Load config live ──────────────────────────────────
        min_sl    = self.MIN_SL
        rr        = self.MIN_RR
        max_sl    = None   # max allowed SL distance above entry (optional cap)

        try:
            from app.config.strategy_loader import load_strategy_config
            cfg    = load_strategy_config(self.strategy_id)
            min_sl = cfg.get("min_sl_points",    min_sl)
            rr     = cfg.get("risk_reward_ratio", rr)
            max_sl = cfg.get("max_sl_points")   # None if not set → no cap
        except Exception:
            pass

        # ── Minimum risk distance guard ───────────────────────
        if risk_distance < min_sl:
            write_audit_log(
                f"[SCALP-STRATEGY][{self.slot_name}][{self.symbol}] "
                f"SKIP_SIGNAL → risk_distance {risk_distance:.2f} < min_sl {min_sl}"
            )
            return signal

        # ── Compute SL and TP for the SHORT trade ─────────────
        tp_price = prev_red_low                        # below entry → profit target
        sl_price = entry_price + (risk_distance * rr)  # above entry → stop loss

        # ── Optional: cap the SL distance to max_sl_points ───
        if isinstance(max_sl, (int, float)) and max_sl > 0:
            max_sl_price = entry_price + max_sl
            if sl_price > max_sl_price:
                write_audit_log(
                    f"[SCALP-STRATEGY][{self.slot_name}][{self.symbol}] "
                    f"MAX_SL_APPLIED → sl {sl_price:.2f} capped to {max_sl_price:.2f}"
                )
                sl_price = max_sl_price

        # ── Commit trade state ────────────────────────────────
        self.in_trade    = True
        self.entry_price = entry_price
        self.tp          = tp_price
        self.sl          = sl_price

        signal.is_sell      = True
        signal.entry_price  = entry_price
        signal.sl           = sl_price
        signal.tp           = tp_price

        write_audit_log(
            f"[SCALP-STRATEGY][{self.slot_name}][{self.symbol}] SELL_SIGNAL\n"
            f"  entry={entry_price}\n"
            f"  tp={tp_price:.2f}  (prev red low, {risk_distance:.2f} pts below)\n"
            f"  sl={sl_price:.2f}  (entry + {risk_distance:.2f} × rr={rr})"
        )

        return signal

    # =========================
    # Helpers
    # =========================

    def _is_current_week_expiry(self) -> bool:
        try:
            today           = date.today()
            days_to_thu     = (3 - today.weekday()) % 7
            current_expiry  = today + timedelta(days=days_to_thu)
            if today.weekday() > 3:
                current_expiry += timedelta(days=7)
            return str(current_expiry.year % 100) in self.symbol
        except Exception:
            return False

    def _reset(self):
        self.in_trade    = False
        self.entry_price = None
        self.sl          = None
        self.tp          = None
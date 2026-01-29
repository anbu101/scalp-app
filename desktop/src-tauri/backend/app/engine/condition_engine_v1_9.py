# backend/app/engine/condition_engine_v1_9.py

from typing import Dict
from app.marketdata.candle import Candle


class ConditionEngineV19:
    """
    Evaluates BUY-side conditions for V1.9 strategy.
    Pure logic. No DB. No broker. No state mutation.
    """

    def evaluate(
        self,
        *,
        candle: Candle,
        indicators: Dict,
        is_trading_time: bool,
        no_open_trade: bool,
    ) -> Dict[str, bool]:
        """
        Returns all atomic condition flags + final gate (cond_all)
        """

        close = candle.close
        open_ = candle.open
        high = candle.high

        # -------------------------------------------------
        # 🔒 HARD GATE: ONLY GREEN CANDLES ARE EVALUATED
        # -------------------------------------------------
        cond_close_gt_open = close > open_

        if not cond_close_gt_open:
            return {
                "cond_close_gt_open": False,
                "cond_close_gt_ema8": False,
                "cond_close_ge_ema20": False,
                "cond_close_not_above_ema20": False,
                "cond_not_touching_high": False,
                "cond_rsi_ge_40": False,
                "cond_rsi_le_65": False,
                "cond_rsi_range": False,
                "cond_rsi_rising": False,
                "cond_is_trading_time": is_trading_time,
                "cond_no_open_trade": no_open_trade,
                "cond_all": False,
            }

        # -------------------------------------------------
        # Indicator fetch (AFTER green candle check)
        # -------------------------------------------------
        ema8 = indicators.get("ema8")
        ema20_low = indicators.get("ema20_low")
        ema20_high = indicators.get("ema20_high")
        rsi_raw = indicators.get("rsi_raw")
        rsi_rising = indicators.get("rsi_rising")

        # -------------------------------------------------
        # 🔒 SAFETY: indicator completeness
        # -------------------------------------------------
        if any(v is None for v in (
            ema8,
            ema20_low,
            ema20_high,
            rsi_raw,
            rsi_rising,
        )):
            return {
                "cond_close_gt_open": True,
                "cond_close_gt_ema8": False,
                "cond_close_ge_ema20": False,
                "cond_close_not_above_ema20": False,
                "cond_not_touching_high": False,
                "cond_rsi_ge_40": False,
                "cond_rsi_le_65": False,
                "cond_rsi_range": False,
                "cond_rsi_rising": False,
                "cond_is_trading_time": is_trading_time,
                "cond_no_open_trade": no_open_trade,
                "cond_all": False,
            }

        # -------------------------------------------------
        # Atomic conditions (VALID candle only)
        # -------------------------------------------------
        cond_close_gt_ema8 = close > ema8
        cond_close_ge_ema20 = close >= ema20_low
        cond_close_not_above_ema20 = close <= ema20_high

        # V1.9 rule:
        # when EMA8 < EMA20_high, candle high must be strictly below EMA20_high
        cond_not_touching_high = (
            high < ema20_high if ema8 < ema20_high else True
        )

        # RSI rules (RAW RSI)
        cond_rsi_ge_40 = rsi_raw >= 40
        cond_rsi_le_65 = rsi_raw <= 65
        cond_rsi_range = cond_rsi_ge_40 and cond_rsi_le_65
        cond_rsi_rising = bool(rsi_rising)

        cond_is_trading_time = is_trading_time
        cond_no_open_trade = no_open_trade

        # -------------------------------------------------
        # Final gate (exact Pine logic)
        # -------------------------------------------------
        cond_all = (
            cond_close_gt_open
            and cond_close_gt_ema8
            and cond_close_ge_ema20
            and cond_close_not_above_ema20
            and cond_not_touching_high
            and cond_rsi_range
            and cond_rsi_rising
            and cond_is_trading_time
            and cond_no_open_trade
        )

        return {
            "cond_close_gt_open": cond_close_gt_open,
            "cond_close_gt_ema8": cond_close_gt_ema8,
            "cond_close_ge_ema20": cond_close_ge_ema20,
            "cond_close_not_above_ema20": cond_close_not_above_ema20,
            "cond_not_touching_high": cond_not_touching_high,
            "cond_rsi_ge_40": cond_rsi_ge_40,
            "cond_rsi_le_65": cond_rsi_le_65,
            "cond_rsi_range": cond_rsi_range,
            "cond_rsi_rising": cond_rsi_rising,
            "cond_is_trading_time": cond_is_trading_time,
            "cond_no_open_trade": cond_no_open_trade,
            "cond_all": cond_all,
        }

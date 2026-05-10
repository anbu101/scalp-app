# backend/app/engine/condition_engine_v1_9.py
#
# SCALP_V1 entry condition engine.
# BB_V1 uses app.engine.bb_options.confluence_signal_engine — this file
# is SCALP_V1 ONLY and has zero coupling to BB logic.
#
# ── Entry condition changelog ─────────────────────────────────────
#
# v1.9  (old)                       v2.0  (new)
# ────────────────────────────────  ──────────────────────────────
# Green candle                      Green candle              ✓ kept
# close > EMA8                      close > EMA8              ✓ kept
# close >= EMA20_Low                close >= EMA20_Low        ✓ kept
# close <= EMA20_High               — removed
# Body/wick < EMA20_High            — removed
# RSI 40–65                         — removed
# RSI rising                        — removed
#                                   EMA20_Low < EMA8          ★ new
#
# DB schema (market_timeline) is unchanged — existing columns are
# reused as follows to avoid a migration:
#   cond_close_not_above_ema20  → now stores  cond_ema20_below_ema8
#   cond_not_touching_high      → deprecated, always False
#   cond_rsi_*                  → deprecated, always False
# ─────────────────────────────────────────────────────────────────

from typing import Dict
from app.marketdata.candle import Candle


class ConditionEngineV19:
    """
    Evaluates BUY-side entry conditions for SCALP_V1.
    Pure logic — no DB, no broker, no state mutation.
    """

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def evaluate(
        self,
        *,
        candle: Candle,
        indicators: Dict,
        is_trading_time: bool,
        no_open_trade: bool,
    ) -> Dict[str, bool]:
        """
        Returns all atomic condition flags + final gate (cond_all).

        Keys are kept identical to the v1.9 schema so that
        market_timeline DB writes, the timeline writer, and any
        analytics queries continue to work without changes.
        """

        close = candle.close
        open_ = candle.open

        # ── 1. Hard gate: only green candles are evaluated ──────────
        cond_close_gt_open = close > open_

        if not cond_close_gt_open:
            return self._false_result(is_trading_time, no_open_trade)

        # ── 2. Indicator availability ────────────────────────────────
        ema8      = indicators.get("ema8")
        ema20_low = indicators.get("ema20_low")

        if ema8 is None or ema20_low is None:
            # Indicators not yet warmed up — skip cleanly
            return {
                "cond_close_gt_open":         True,   # candle IS green
                "cond_close_gt_ema8":         False,
                "cond_close_ge_ema20":        False,
                "cond_close_not_above_ema20": False,  # stores ema20_below_ema8
                "cond_not_touching_high":     False,  # deprecated
                "cond_rsi_ge_40":             False,  # deprecated
                "cond_rsi_le_65":             False,  # deprecated
                "cond_rsi_range":             False,  # deprecated
                "cond_rsi_rising":            False,  # deprecated
                "cond_is_trading_time":       is_trading_time,
                "cond_no_open_trade":         no_open_trade,
                "cond_all":                   False,
            }

        # ── 3. Active entry conditions ───────────────────────────────

        # Condition A: close must be above EMA8
        cond_close_gt_ema8 = close > ema8

        # Condition B: close must be at or above EMA20_Low
        cond_close_ge_ema20 = close >= ema20_low

        # Condition C (NEW): EMA20_Low must sit below EMA8
        #   Ensures trend alignment — we only enter when the fast EMA
        #   is above the slow EMA band, confirming upward momentum.
        #   Stored in cond_close_not_above_ema20 column (schema reuse).
        cond_ema20_below_ema8 = ema20_low < ema8

        # ── 4. Session / trade-state gates (unchanged) ───────────────
        cond_is_trading_time = is_trading_time
        cond_no_open_trade   = no_open_trade

        # ── 5. Final gate ────────────────────────────────────────────
        cond_all = (
            cond_close_gt_open
            and cond_close_gt_ema8
            and cond_close_ge_ema20
            and cond_ema20_below_ema8
            and cond_is_trading_time
            and cond_no_open_trade
        )

        return {
            # ── Active conditions ──
            "cond_close_gt_open":         cond_close_gt_open,
            "cond_close_gt_ema8":         cond_close_gt_ema8,
            "cond_close_ge_ema20":        cond_close_ge_ema20,

            # Reused column — now means EMA20_Low < EMA8 (trend alignment)
            "cond_close_not_above_ema20": cond_ema20_below_ema8,

            # ── Deprecated — kept for DB schema compatibility ──
            "cond_not_touching_high":     False,
            "cond_rsi_ge_40":             False,
            "cond_rsi_le_65":             False,
            "cond_rsi_range":             False,
            "cond_rsi_rising":            False,

            # ── Session / trade state ──
            "cond_is_trading_time":       cond_is_trading_time,
            "cond_no_open_trade":         cond_no_open_trade,

            # ── Master gate ──
            "cond_all":                   cond_all,
        }

    # --------------------------------------------------
    # Helper — all-false result for rejected candles
    # --------------------------------------------------

    def _false_result(
        self,
        is_trading_time: bool,
        no_open_trade: bool,
    ) -> Dict[str, bool]:
        return {
            "cond_close_gt_open":         False,
            "cond_close_gt_ema8":         False,
            "cond_close_ge_ema20":        False,
            "cond_close_not_above_ema20": False,
            "cond_not_touching_high":     False,
            "cond_rsi_ge_40":             False,
            "cond_rsi_le_65":             False,
            "cond_rsi_range":             False,
            "cond_rsi_rising":            False,
            "cond_is_trading_time":       is_trading_time,
            "cond_no_open_trade":         no_open_trade,
            "cond_all":                   False,
        }
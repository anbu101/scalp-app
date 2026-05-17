# backend/app/engine/bb_v2/confluence_signal_engine_v2.py
"""
ConfluenceSignalEngineV2

CE Entry (all three must be true simultaneously):
  C1 — close > BB_upper  OR  close > BB_middle  OR  close > BB_lower
  C2 — close crosses above any of: R2, R1, PP, S1, S2, S3
         "crosses above" = prev_close <= level < close
  ST — close > supertrend_v2  (ST 10, 1.5 uptrend)

CE Exit:
  close < supertrend_v2  OR  TP hit  OR  SL hit

PE Entry (all three must be true simultaneously):
  C3 — close < BB_upper  OR  close < BB_middle  OR  close < BB_lower
  C4 — close crosses below any of: R2, R1, PP, S1, S2, S3
         "crosses below" = close < level <= prev_close
  ST — close < supertrend_v2  (ST 10, 1.5 downtrend)

PE Exit:
  close > supertrend_v2  OR  TP hit  OR  SL hit
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeSignalV2:
    action:           Optional[str] = None   # ENTER_CE/PE, EXIT_CE/PE
    reason:           Optional[str] = None
    rejection_reason: Optional[str] = None


class ConfluenceSignalEngineV2:

    def __init__(self, max_trades_per_side: int = 10):
        self.ce_in_trade = False
        self.pe_in_trade = False

        self.ce_trades_today = 0
        self.pe_trades_today = 0

        self.max_trades_per_side = max_trades_per_side

    # ==================================================
    # RESET (call daily at 09:15)
    # ==================================================

    def reset_daily(self):
        self.ce_trades_today = 0
        self.pe_trades_today = 0
        self.ce_in_trade     = False
        self.pe_in_trade     = False

    # ==================================================
    # EXTERNAL STATE CALLBACKS
    # Called by BBTradeManager after confirmed broker action.
    # ==================================================

    def notify_exit(self, side: str):
        if side == "CE":
            self.ce_in_trade = False
        elif side == "PE":
            self.pe_in_trade = False

    def confirm_entry(self, side: str):
        if side == "CE":
            self.ce_in_trade     = True
            self.ce_trades_today += 1
        elif side == "PE":
            self.pe_in_trade     = True
            self.pe_trades_today += 1

    # ==================================================
    # MAIN UPDATE — called every completed 3m candle
    # ==================================================

    def update(self, close: float, indicators: dict) -> TradeSignalV2:

        # --------------------------------------------------
        # Read indicator values.
        # Note: SuperTrend is keyed "supertrend_v2" throughout
        # the V2 pipeline — matching the DB column name exactly.
        # --------------------------------------------------
        bb_upper  = indicators.get("bb_upper")
        bb_middle = indicators.get("bb_middle")
        bb_lower  = indicators.get("bb_lower")

        st = indicators.get("supertrend_v2")   # ← V2's ST(10, 1.5)

        prev = indicators.get("prev_close")    # snapshot from IndicatorBundleV2

        # Pivot levels (all may be None until PivotCache is ready)
        pp = indicators.get("pp")
        r1 = indicators.get("r1")
        r2 = indicators.get("r2")
        s1 = indicators.get("s1")
        s2 = indicators.get("s2")
        s3 = indicators.get("s3")

        # --------------------------------------------------
        # WARMUP GUARD — indicators not ready yet
        # --------------------------------------------------
        if None in [bb_upper, bb_middle, bb_lower, st]:
            return TradeSignalV2(
                action=None,
                rejection_reason="INDICATORS_NOT_READY",
            )

        # --------------------------------------------------
        # EXIT LOGIC (always evaluated before entry)
        # Uses "supertrend_v2" — V2's ST(10, 1.5)
        # --------------------------------------------------
        if self.ce_in_trade and close < st:
            return TradeSignalV2(action="EXIT_CE", reason="SuperTrend_V2")

        if self.pe_in_trade and close > st:
            return TradeSignalV2(action="EXIT_PE", reason="SuperTrend_V2")

        # --------------------------------------------------
        # Need prev_close for crossover checks
        # --------------------------------------------------
        if prev is None:
            return TradeSignalV2(
                action=None,
                rejection_reason="WARMING_UP_NO_PREV_CLOSE",
            )

        # --------------------------------------------------
        # Collect non-null pivot levels for crossover checks
        # --------------------------------------------------
        pivot_levels = [
            lv for lv in [r2, r1, pp, s1, s2, s3]
            if lv is not None
        ]

        # --------------------------------------------------
        # C1 — close above any BB band
        # Equivalent to: close > bb_lower (weakest condition)
        # Evaluated literally per spec.
        # --------------------------------------------------
        c1 = (close > bb_upper) or (close > bb_middle) or (close > bb_lower)

        # --------------------------------------------------
        # C2 — close crosses above any pivot level
        # Crossover: prev_close was at or below the level,
        #            current close is now above it.
        # --------------------------------------------------
        c2 = any(prev <= lv < close for lv in pivot_levels)

        # --------------------------------------------------
        # C3 — close below any BB band
        # Equivalent to: close < bb_upper (weakest condition)
        # --------------------------------------------------
        c3 = (close < bb_upper) or (close < bb_middle) or (close < bb_lower)

        # --------------------------------------------------
        # C4 — close crosses below any pivot level
        # --------------------------------------------------
        c4 = any(close < lv <= prev for lv in pivot_levels)

        # --------------------------------------------------
        # ENTRY EVALUATION
        # --------------------------------------------------
        ce_rejection = None
        pe_rejection = None
        ce_valid     = False
        pe_valid     = False

        # ── CE ──────────────────────────────────────────
        if not self.ce_in_trade:
            if self.ce_trades_today >= self.max_trades_per_side:
                ce_rejection = "CE_MAX_TRADES_REACHED"
            elif not c1:
                ce_rejection = "CE_BELOW_ALL_BB_BANDS"
            elif not c2:
                ce_rejection = "CE_NO_PIVOT_CROSSOVER_UP"
            elif close <= st:
                ce_rejection = "CE_BELOW_SUPERTREND_V2"
            else:
                ce_valid = True
        else:
            ce_rejection = "CE_ALREADY_IN_TRADE"

        # ── PE ──────────────────────────────────────────
        if not self.pe_in_trade:
            if self.pe_trades_today >= self.max_trades_per_side:
                pe_rejection = "PE_MAX_TRADES_REACHED"
            elif not c3:
                pe_rejection = "PE_ABOVE_ALL_BB_BANDS"
            elif not c4:
                pe_rejection = "PE_NO_PIVOT_CROSSOVER_DOWN"
            elif close >= st:
                pe_rejection = "PE_ABOVE_SUPERTREND_V2"
            else:
                pe_valid = True
        else:
            pe_rejection = "PE_ALREADY_IN_TRADE"

        # --------------------------------------------------
        # PRIORITY — mutual exclusivity enforced by SuperTrend
        # (close > st for CE, close < st for PE — can't both
        # be true; this block is a defensive fallback only)
        # --------------------------------------------------
        if ce_valid and pe_valid:
            if close > st:
                pe_valid      = False
                pe_rejection  = "BOTH_VALID_ST_BIAS_CE"
            else:
                ce_valid      = False
                ce_rejection  = "BOTH_VALID_ST_BIAS_PE"

        # --------------------------------------------------
        # EMIT
        # --------------------------------------------------
        if ce_valid:
            triggered = self._crossed_levels_up(prev, close, pivot_levels)
            return TradeSignalV2(
                action="ENTER_CE",
                reason=f"BB+CrossAbove({triggered})+ST_V2_UP",
            )

        if pe_valid:
            triggered = self._crossed_levels_down(prev, close, pivot_levels)
            return TradeSignalV2(
                action="ENTER_PE",
                reason=f"BB+CrossBelow({triggered})+ST_V2_DOWN",
            )

        return TradeSignalV2(
            action=None,
            rejection_reason=f"CE:{ce_rejection} | PE:{pe_rejection}",
        )

    # ==================================================
    # LOGGING HELPERS
    # ==================================================

    @staticmethod
    def _crossed_levels_up(prev: float, close: float, levels: list) -> str:
        crossed = [str(round(lv, 2)) for lv in levels if prev <= lv < close]
        return ",".join(crossed) if crossed else "none"

    @staticmethod
    def _crossed_levels_down(prev: float, close: float, levels: list) -> str:
        crossed = [str(round(lv, 2)) for lv in levels if close < lv <= prev]
        return ",".join(crossed) if crossed else "none"
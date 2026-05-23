# backend/app/engine/bb_v2/confluence_signal_engine_v2.py
"""
ConfluenceSignalEngineV2

SLOT MODEL:
  CE and PE slots are FULLY INDEPENDENT.
  - ce_in_trade=True  → blocks new CE entries only, PE is unaffected
  - pe_in_trade=True  → blocks new PE entries only, CE is unaffected
  - Both slots can be open simultaneously (one CE + one PE)

  Contrast with BB_V1 (ConfluenceSignalEngine) where any open trade
  blocks all new entries on both sides.

CE Entry (all three must be true simultaneously):
  C1 — close > BB_upper  OR  close > BB_middle  OR  close > BB_lower
  C2 — close is above any of: R2, R1, PP, S1, S2, S3
         "above" = close > level  (position check, not crossover)
  ST — close > supertrend_v2  (ST 10, 1.5 uptrend confirmed)

CE Exit:
  close < supertrend_v2  (ST flips to DOWN while CE is open)

PE Entry (all three must be true simultaneously):
  C3 — close < BB_upper  OR  close < BB_middle  OR  close < BB_lower
  C4 — close is below any of: R2, R1, PP, S1, S2, S3
         "below" = close < level  (position check, not crossover)
  ST — close < supertrend_v2  (ST 10, 1.5 downtrend confirmed)

PE Exit:
  close > supertrend_v2  (ST flips to UP while PE is open)

NOTE ON SIMULTANEOUS SIGNALS:
  In practice, CE entry requires close > st_v2 and PE entry requires
  close < st_v2 — these are mutually exclusive by ST direction, so
  both sides cannot enter on the same candle. However, the slot state
  is fully independent: CE exit + PE entry CAN fire on the same candle
  (CE exits as ST flips down; PE entry is evaluated independently
  on that same candle).
"""

from dataclasses import dataclass, field
from typing import Optional, List


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
        # SuperTrend is keyed "supertrend_v2" — matching the
        # DB column name exactly so the dict from IndicatorBundleV2
        # flows through without any renaming.
        # --------------------------------------------------
        bb_upper  = indicators.get("bb_upper")
        bb_middle = indicators.get("bb_middle")
        bb_lower  = indicators.get("bb_lower")

        st   = indicators.get("supertrend_v2")   # V2's ST(10, 1.5)
        prev = indicators.get("prev_close")       # snapshot from IndicatorBundleV2

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
        # EXIT CHECKS — evaluated independently per slot.
        # CE exit and PE exit use opposite ST conditions so they
        # cannot both fire on the same candle. Exits are checked
        # BEFORE entries but a CE exit does NOT suppress PE entry
        # evaluation — the slots are fully independent.
        # --------------------------------------------------
        ce_exit = self.ce_in_trade and close < st
        pe_exit = self.pe_in_trade and close > st

        # --------------------------------------------------
        # Emit exits even without prev_close — exits don't need it.
        # --------------------------------------------------
        if prev is None:
            if ce_exit:
                return TradeSignalV2(action="EXIT_CE", reason="SuperTrend_V2")
            if pe_exit:
                return TradeSignalV2(action="EXIT_PE", reason="SuperTrend_V2")
            return TradeSignalV2(
                action=None,
                rejection_reason="WARMING_UP_NO_PREV_CLOSE",
            )

        # --------------------------------------------------
        # Collect non-null pivot levels for position checks
        # --------------------------------------------------
        pivot_levels = [lv for lv in [r2, r1, pp, s1, s2, s3] if lv is not None]

        # --------------------------------------------------
        # ENTRY CONDITIONS
        # --------------------------------------------------

        # C1 — close above any BB band
        c1 = (close > bb_upper) or (close > bb_middle) or (close > bb_lower)

        # C2 — close is above at least one pivot level (position check)
        #      Changed from crossover (prev <= lv < close) to position
        #      (close > lv) so entries are not blocked when price has
        #      already sustained above a pivot for multiple candles.
        c2 = any(close > lv for lv in pivot_levels)

        # C3 — close below any BB band
        c3 = (close < bb_upper) or (close < bb_middle) or (close < bb_lower)

        # C4 — close is below at least one pivot level (position check)
        #      Changed from crossover (close < lv <= prev) to position
        #      (close < lv) so entries are not blocked when price has
        #      already sustained below a pivot for multiple candles.
        c4 = any(close < lv for lv in pivot_levels)

        # --------------------------------------------------
        # CE ENTRY EVALUATION
        # ce_in_trade blocks only the CE slot.
        # pe_in_trade has NO effect here.
        # --------------------------------------------------
        ce_valid     = False
        ce_rejection = None

        if not self.ce_in_trade:
            if self.ce_trades_today >= self.max_trades_per_side:
                ce_rejection = "CE_MAX_TRADES_REACHED"
            elif not c1:
                ce_rejection = "CE_BELOW_ALL_BB_BANDS"
            elif not c2:
                ce_rejection = "CE_NOT_ABOVE_ANY_PIVOT"
            elif close <= st:
                ce_rejection = "CE_BELOW_SUPERTREND_V2"
            else:
                ce_valid = True
        else:
            ce_rejection = "CE_ALREADY_IN_TRADE"

        # --------------------------------------------------
        # PE ENTRY EVALUATION
        # pe_in_trade blocks only the PE slot.
        # ce_in_trade has NO effect here.
        # --------------------------------------------------
        pe_valid     = False
        pe_rejection = None

        if not self.pe_in_trade:
            if self.pe_trades_today >= self.max_trades_per_side:
                pe_rejection = "PE_MAX_TRADES_REACHED"
            elif not c3:
                pe_rejection = "PE_ABOVE_ALL_BB_BANDS"
            elif not c4:
                pe_rejection = "PE_NOT_BELOW_ANY_PIVOT"
            elif close >= st:
                pe_rejection = "PE_ABOVE_SUPERTREND_V2"
            else:
                pe_valid = True
        else:
            pe_rejection = "PE_ALREADY_IN_TRADE"

        # --------------------------------------------------
        # SIGNAL PRIORITY (one action per candle)
        #
        # Priority order: CE exit > PE exit > CE entry > PE entry
        #
        # This ordering allows the common scenario:
        #   CE exit fires (ST flips down) AND PE entry is valid
        #   on the same candle (ST now down = PE condition met).
        #   We emit CE exit this candle; PE entry fires next candle.
        #
        # Note: "BOTH_VALID" conflict block removed.
        # CE entry requires close > st_v2, PE entry requires close < st_v2.
        # These are mutually exclusive by ST direction — both ce_valid
        # and pe_valid being True simultaneously is architecturally
        # impossible. No tie-breaking needed.
        # --------------------------------------------------
        if ce_exit:
            return TradeSignalV2(action="EXIT_CE", reason="SuperTrend_V2")

        if pe_exit:
            return TradeSignalV2(action="EXIT_PE", reason="SuperTrend_V2")

        if ce_valid:
            above = self._levels_above(close, pivot_levels)
            return TradeSignalV2(
                action="ENTER_CE",
                reason=f"BB+Above({above})+ST_V2_UP",
            )

        if pe_valid:
            below = self._levels_below(close, pivot_levels)
            return TradeSignalV2(
                action="ENTER_PE",
                reason=f"BB+Below({below})+ST_V2_DOWN",
            )

        return TradeSignalV2(
            action=None,
            rejection_reason=f"CE:{ce_rejection} | PE:{pe_rejection}",
        )

    # ==================================================
    # LOGGING HELPERS
    # ==================================================

    @staticmethod
    def _levels_above(close: float, levels: list) -> str:
        """Pivot levels that close is currently above."""
        above = [str(round(lv, 2)) for lv in levels if close > lv]
        return ",".join(above) if above else "none"

    @staticmethod
    def _levels_below(close: float, levels: list) -> str:
        """Pivot levels that close is currently below."""
        below = [str(round(lv, 2)) for lv in levels if close < lv]
        return ",".join(below) if below else "none"
from dataclasses import dataclass
from typing import Optional


# ==================================================
# TRADE SIGNAL OBJECT
# ==================================================

@dataclass
class TradeSignal:
    action: Optional[str] = None          # ENTER_CE / ENTER_PE / EXIT_CE / EXIT_PE
    reason: Optional[str] = None          # Entry or Exit reason
    rejection_reason: Optional[str] = None  # Why entry was rejected


# ==================================================
# CONFLUENCE SIGNAL ENGINE
# ==================================================

class ConfluenceSignalEngine:

    def __init__(self, max_trades_per_side: int = 10):
        self.ce_in_trade = False
        self.pe_in_trade = False

        self.ce_trades_today = 0
        self.pe_trades_today = 0

        self.max_trades_per_side = max_trades_per_side

    # ==================================================
    # RESET (call daily)
    # ==================================================

    def reset_daily(self):
        self.ce_trades_today = 0
        self.pe_trades_today = 0
        self.ce_in_trade = False
        self.pe_in_trade = False

    # ==================================================
    # EXTERNAL EXIT NOTIFICATION
    # ==================================================

    def notify_exit(self, side: str):
        if side == "CE":
            self.ce_in_trade = False
        elif side == "PE":
            self.pe_in_trade = False

    # ==================================================
    # UPDATE (Balanced Evaluation Every Candle)
    # ==================================================

    def update(
        self,
        close: float,
        indicators: dict,
    ) -> TradeSignal:

        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        rsi = indicators.get("rsi")
        st = indicators.get("supertrend")
        r1 = indicators.get("r1")
        s1 = indicators.get("s1")

        # --------------------------------------------------
        # INDICATOR WARMUP CHECK
        # --------------------------------------------------

        if None in [bb_upper, bb_lower, rsi, st, r1, s1]:
            return TradeSignal(
                action=None,
                rejection_reason="INDICATORS_NOT_READY"
            )

        # ==================================================
        # EXIT LOGIC (ALWAYS FIRST)
        # ==================================================

        if self.ce_in_trade and close < st:
            self.ce_in_trade = False
            return TradeSignal(
                action="EXIT_CE",
                reason="SuperTrend"
            )

        if self.pe_in_trade and close > st:
            self.pe_in_trade = False
            return TradeSignal(
                action="EXIT_PE",
                reason="SuperTrend"
            )

        # ==================================================
        # ENTRY EVALUATION (BOTH SIDES ALWAYS CHECKED)
        # ==================================================

        ce_valid = False
        pe_valid = False

        ce_rejection = None
        pe_rejection = None

        # ---------------- CE ENTRY ----------------

        if not self.ce_in_trade:

            if self.ce_trades_today >= self.max_trades_per_side:
                ce_rejection = "CE_MAX_TRADES_REACHED"

            elif close <= bb_upper:
                ce_rejection = "CE_NOT_ABOVE_BB_UPPER"

            elif close <= r1:
                ce_rejection = "CE_NOT_ABOVE_R1"

            elif rsi <= 70:
                ce_rejection = "CE_RSI_BELOW_70"

            else:
                ce_valid = True

        else:
            ce_rejection = "CE_ALREADY_IN_TRADE"

        # ---------------- PE ENTRY ----------------

        if not self.pe_in_trade:

            if self.pe_trades_today >= self.max_trades_per_side:
                pe_rejection = "PE_MAX_TRADES_REACHED"

            elif close >= bb_lower:
                pe_rejection = "PE_NOT_BELOW_BB_LOWER"

            elif close >= s1:
                pe_rejection = "PE_NOT_BELOW_S1"

            elif rsi >= 35:
                pe_rejection = "PE_RSI_ABOVE_35"

            else:
                pe_valid = True

        else:
            pe_rejection = "PE_ALREADY_IN_TRADE"

        # ==================================================
        # PRIORITY HANDLING
        # ==================================================

        # Only CE valid
        if ce_valid and not pe_valid:
            self.ce_in_trade = True
            self.ce_trades_today += 1
            return TradeSignal(
                action="ENTER_CE",
                reason="BB+R1+RSI"
            )

        # Only PE valid
        if pe_valid and not ce_valid:
            self.pe_in_trade = True
            self.pe_trades_today += 1
            return TradeSignal(
                action="ENTER_PE",
                reason="BB+S1+RSI"
            )

        # BOTH valid (extreme volatility case)
        if ce_valid and pe_valid:

            # Bias rule — adjust if needed
            if rsi > 50:
                self.ce_in_trade = True
                self.ce_trades_today += 1
                return TradeSignal(
                    action="ENTER_CE",
                    reason="BOTH_VALID_RSI_BIAS_CE"
                )
            else:
                self.pe_in_trade = True
                self.pe_trades_today += 1
                return TradeSignal(
                    action="ENTER_PE",
                    reason="BOTH_VALID_RSI_BIAS_PE"
                )

        # ==================================================
        # NO ENTRY
        # ==================================================

        rejection_summary = f"CE:{ce_rejection} | PE:{pe_rejection}"

        return TradeSignal(
            action=None,
            rejection_reason=rejection_summary
        )

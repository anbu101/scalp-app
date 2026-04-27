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

    def __init__(self, max_trades_per_side: int = 10, strategy_id: str = "BB_V1"):
        self.strategy_id = strategy_id
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
    # CONFIRM ENTRY (called by BBTradeManager after all
    # gates pass — session window, broker, etc.)
    # Only at this point do we set the in_trade flag and
    # increment the daily counter.
    # ==================================================

    def confirm_entry(self, side: str):
        if side == "CE":
            self.ce_in_trade = True
            self.ce_trades_today += 1
        elif side == "PE":
            self.pe_in_trade = True
            self.pe_trades_today += 1

    # ==================================================
    # LIVE CONFIG READ
    # Read st_exit_gap fresh from disk every candle so
    # Settings UI changes take effect immediately.
    # ==================================================

    def _live_st_exit_gap(self) -> float:
        try:
            from app.config.strategy_loader import load_strategy_config
            val = load_strategy_config(self.strategy_id).get("st_exit_gap", 30)
            # None or negative treated as 0 (always exit at any gap)
            return max(0.0, float(val)) if val is not None else 30.0
        except Exception:
            return 30.0

    # ==================================================
    # UPDATE (Balanced Evaluation Every Candle)
    # ==================================================

    def update(
        self,
        close: float,
        indicators: dict,
        candle_open: Optional[float] = None,
    ) -> TradeSignal:

        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        rsi      = indicators.get("rsi_raw")   # FIX: was "rsi" — key never existed
        st       = indicators.get("supertrend")
        r1       = indicators.get("r1")
        s1       = indicators.get("s1")

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
        #
        # Two independent criteria — EITHER triggers an exit:
        #
        # A) SuperTrend flip (no candle-colour restriction):
        #    CE: close < st  →  price fell below ST line
        #    PE: close > st  →  price rose above ST line
        #    Catches hard reversals even when gap was never ≤ threshold.
        #
        # B) Gap proximity + candle colour:
        #    CE: candle is RED (close < open) AND (close - st) ≤ st_exit_gap
        #        Exits early when a bearish candle forms close to ST,
        #        before a full flip happens.
        #    PE: candle is GREEN (close > open) AND (st - close) ≤ st_exit_gap
        #        Same logic mirrored for the short side.
        #
        # NOTE: Do NOT clear *_in_trade flags here.
        # They are cleared by BBTradeManager._exit() via
        # signal_engine.notify_exit() only after the exit order
        # is confirmed at the broker.
        # ==================================================

        st_exit_gap = self._live_st_exit_gap()

        if self.ce_in_trade:
            # Criterion A — SuperTrend flip
            st_flip = close < st
            # Criterion B — bearish candle approaching ST from above
            is_red_candle = (candle_open is not None) and (close < candle_open)
            gap_ce = close - st          # positive when price is above ST
            gap_trigger = is_red_candle and (gap_ce <= st_exit_gap)

            if st_flip or gap_trigger:
                reason = "ST_FLIP_CE" if st_flip else f"ST_GAP_CE({round(gap_ce, 1)}≤{st_exit_gap})"
                return TradeSignal(action="EXIT_CE", reason=reason)

        if self.pe_in_trade:
            # Criterion A — SuperTrend flip
            st_flip = close > st
            # Criterion B — bullish candle approaching ST from below
            is_green_candle = (candle_open is not None) and (close > candle_open)
            gap_pe = st - close          # positive when price is below ST
            gap_trigger = is_green_candle and (gap_pe <= st_exit_gap)

            if st_flip or gap_trigger:
                reason = "ST_FLIP_PE" if st_flip else f"ST_GAP_PE({round(gap_pe, 1)}≤{st_exit_gap})"
                return TradeSignal(action="EXIT_PE", reason=reason)

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
            return TradeSignal(
                action="ENTER_CE",
                reason="BB+R1+RSI"
            )

        # Only PE valid
        if pe_valid and not ce_valid:
            return TradeSignal(
                action="ENTER_PE",
                reason="BB+S1+RSI"
            )

        # BOTH valid (extreme volatility case)
        if ce_valid and pe_valid:

            # Bias rule — adjust if needed
            if rsi > 50:
                return TradeSignal(
                    action="ENTER_CE",
                    reason="BOTH_VALID_RSI_BIAS_CE"
                )
            else:
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
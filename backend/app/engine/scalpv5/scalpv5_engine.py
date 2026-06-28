# backend/app/engine/scalpv5/scalpv5_engine.py
#
# SCALP_V5 — Option BUYING signal engine (3-minute candles).
# ============================================================================
# Reuses SCALP_V1's IndicatorEnginePineV19 VERBATIM (EMA8 / EMA20_low /
# EMA20_high / RSI). DIVERGES from V1 in direction (LONG, not SHORT) and in its
# entry/exit conditions.
#
# This is a STANDALONE engine — it does NOT import or touch the shared
# StrategyEngine, so SCALP_V1..V4 are completely unaffected.
#
# ENTRY (all on the just-completed 3m candle):
#   1. Green candle             : close > open
#   2. EMA8 CROSSES ABOVE EMA20_HIGH (the transition candle ONLY):
#        prev: ema8 <= ema20_high   AND   now: ema8 > ema20_high
#      Fires only on the candle where EMA8 transitions from at-or-below to
#      above EMA20_HIGH. If EMA8 is ALREADY above, NO signal (no re-fire).
#   3. close > ema20_high
#   buy = 1 AND 2 AND 3
#
# CROSSOVER STATE:
#   The crossover needs the PREVIOUS candle's (ema8 vs ema20_high) relationship.
#   This engine is per-symbol (one instance per token), so it stores the prior
#   relationship in self._prev_ema8_above. On the FIRST evaluated candle (warmup
#   just finished / fresh after reconnect) there is no prior bar, so we record
#   the relationship and emit NO signal — a transition cannot be confirmed
#   without a prior bar (matches Pine ta.crossover on the first bar). This also
#   prevents a false entry at startup when EMA8 is already above EMA20_HIGH.
#
# RISK (pure fixed points from config; 0 = disabled — NO RR, NO prev-red-low):
#   entry    = candle.close
#   sl_price = entry - sl_points   (None if sl_points <= 0)
#   tp_price = entry + tp_points   (None if tp_points <= 0)
#
# EXIT (no time-based exit):
#   The tick engine calls should_exit_on_candle() on each COMPLETED held-symbol
#   candle; it returns True when close < ema20_high  → EMA_EXIT. SL/TP/MTM are
#   handled tick-wise by the manager/engine. There is no 1-candle time box.
#
# IN-TRADE TRUTH:
#   Like V3, V5's single-trade gate is DB-backed in the manager (scalpv5_trades),
#   NOT in this engine. This engine is STATELESS w.r.t. open trades for ENTRY —
#   it only emits a Signal; the manager's DB gate decides whether it enters. The
#   ONLY per-symbol state it keeps is the crossover relationship (above), which
#   is indicator state, not trade state.

from dataclasses import dataclass
from typing import Optional
from datetime import date, timedelta

from app.event_bus.audit_logger import write_audit_log
from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19


@dataclass
class V5Signal:
    is_buy:      bool = False
    entry_price: Optional[float] = None
    sl:          Optional[float] = None   # entry - sl_points (None if disabled)
    tp:          Optional[float] = None   # entry + tp_points (None if disabled)
    entry_candle_ts: Optional[int] = None


class ScalpV5Engine:
    """
    Pine-parity LONG entry engine (OPTION chart only), 3-minute candles.

    HARD RULE (same as V1):
      ✅ Trade ONLY current-week expiry
      ❌ Ignore next-week expiry  (enforced via _is_current_week_expiry)

    STATELESS for trades; keeps only the EMA8/EMA20_HIGH crossover relationship
    per symbol. SL/TP are absolute config points; 0 disables that leg.
    """

    def __init__(self, strategy_id: str, slot_name: str, symbol: str):
        self.strategy_id = strategy_id
        self.slot_name   = slot_name
        self.symbol      = symbol

        # Crossover state: was EMA8 above EMA20_HIGH on the PREVIOUS candle?
        # None until the first candle with both EMAs ready is seen.
        self._prev_ema8_above: Optional[bool] = None

    # =========================
    # Public API — ENTRY
    # =========================

    def on_candle(self, candle, ind: IndicatorEnginePineV19,
                  sl_points: float, tp_points: float) -> V5Signal:
        """
        Evaluate the entry filter on a completed candle. sl_points/tp_points are
        passed in by the tick engine (read live from config) so the signal
        carries final SL/TP levels — the manager does not recompute them.

        MUST be called once per completed candle for this symbol (it also
        advances the crossover state).
        """
        signal = V5Signal()
        snap   = ind.snapshot()

        # Indicators must be ready.
        if snap is None:
            return signal

        ema8       = snap.get("ema8")
        ema20_high = snap.get("ema20_high")
        if ema8 is None or ema20_high is None:
            return signal

        # Current relationship for the crossover.
        ema8_above_now = ema8 > ema20_high

        # Advance crossover state, capturing the prior relationship first.
        prev_above = self._prev_ema8_above
        self._prev_ema8_above = ema8_above_now

        # Must be current-week expiry symbol.
        if not self._is_current_week_expiry():
            return signal

        # ── ENTRY GATES ───────────────────────────────────────
        # 1) Green candle
        cond_green = candle.close > candle.open

        # 2) EMA8 CROSSES ABOVE EMA20_HIGH (transition only).
        #    prev_above is None on the first evaluated candle → cannot confirm a
        #    transition → no signal (and no false entry when already above).
        cond_cross = (prev_above is False) and (ema8_above_now is True)

        # 3) close > ema20_high
        cond_close_above = candle.close > ema20_high

        if not (cond_green and cond_cross and cond_close_above):
            return signal

        # ── LEVELS (fixed points; 0 disables a leg) ───────────
        entry_price = candle.close
        sl_price = (entry_price - sl_points) if (sl_points and sl_points > 0) else None
        tp_price = (entry_price + tp_points) if (tp_points and tp_points > 0) else None

        signal.is_buy          = True
        signal.entry_price     = entry_price
        signal.sl              = sl_price
        signal.tp              = tp_price
        signal.entry_candle_ts = candle.end_ts

        write_audit_log(
            f"[SCALP-V5][{self.slot_name}][{self.symbol}] BUY_SIGNAL\n"
            f"  entry={entry_price}\n"
            f"  sl={sl_price}  (entry - {sl_points} pts)\n"
            f"  tp={tp_price}  (entry + {tp_points} pts)\n"
            f"  green={cond_green} ema8_cross_above_ema20high={cond_cross} "
            f"close>ema20_high={cond_close_above} "
            f"(ema8={ema8} ema20_high={ema20_high})"
        )

        return signal

    # =========================
    # Public API — EXIT (candle close below EMA20_HIGH)
    # =========================

    def should_exit_on_candle(self, candle, ind: IndicatorEnginePineV19) -> bool:
        """
        True when the COMPLETED candle closes BELOW EMA20_HIGH → EMA_EXIT.
        Called by the tick engine for the held symbol on each completed candle.
        Indicator-not-ready or missing EMA → no exit (let SL/TP/MTM/EOD handle).
        """
        snap = ind.snapshot()
        if not snap:
            return False
        ema20_high = snap.get("ema20_high")
        if ema20_high is None:
            return False
        return candle.close < ema20_high

    # =========================
    # Helpers
    # =========================

    def _is_current_week_expiry(self) -> bool:
        # Identical logic to SCALP_V1.StrategyEngine._is_current_week_expiry:
        # the symbol's 2-digit year must be present. (Engine-level expiry
        # gating is also enforced in the tick engine via token_expiry ==
        # current_week_expiry, mirroring V1/V3; this is the belt-and-braces.)
        try:
            today           = date.today()
            days_to_thu     = (3 - today.weekday()) % 7
            current_expiry  = today + timedelta(days=days_to_thu)
            if today.weekday() > 3:
                current_expiry += timedelta(days=7)
            return str(current_expiry.year % 100) in self.symbol
        except Exception:
            return False
# backend/app/backtest/sim/fill_model.py
#
# The exit/fill model for SHORT positions (SCALP_V1). This is where the
# intra-candle SL/TP ORDERING problem is handled honestly.
#
# SCALP_V1 is a SHORT: SL is ABOVE entry (premium rising = loss), TP is BELOW
# entry (premium falling = profit). On any 1-minute candle after entry:
#
#   SL touched  iff  candle.high >= sl
#   TP touched  iff  candle.low  <= tp
#
# Four cases:
#   1. neither touched      -> position stays open
#   2. only SL touched      -> exit SL at sl
#   3. only TP touched      -> exit TP at tp
#   4. BOTH touched in the same minute  -> AMBIGUOUS. 1m OHLC cannot say which
#      came first. Resolution:
#        (a) if 1s data exists for this contract+minute, replay the seconds and
#            take whichever level the price reached FIRST. Not ambiguous anymore.
#        (b) else apply the PESSIMISTIC rule: assume SL filled first (worst case
#            for a short), and flag the trade ambiguous_fill=True so you can see
#            how many results leaned on the assumption.
#
# This matches the live StrategyEngine exit semantics (high>=sl, low<=tp) while
# adding the ordering resolution that live ticks give you for free but 1m data
# does not.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.backtest.data.candle_source import BTCandle, BTSecond


@dataclass
class FillResult:
    exited: bool
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None      # 'SL' | 'TP'
    ambiguous: bool = False                # resolved by pessimistic rule (no 1s)


def resolve_exit_on_candle(
    *,
    candle: BTCandle,
    sl: float,
    tp: float,
    seconds: Optional[List[BTSecond]] = None,
) -> FillResult:
    """Decide whether a SHORT position exits on this 1m candle.

    `seconds` is the 1s series for this contract+minute if available; when given
    and the candle is ambiguous, it adjudicates true ordering.
    """
    sl_hit = candle.high >= sl
    tp_hit = candle.low <= tp

    # Case 1
    if not sl_hit and not tp_hit:
        return FillResult(exited=False)

    # Case 2 / 3 — unambiguous single touch
    if sl_hit and not tp_hit:
        return FillResult(True, exit_price=sl, exit_reason="SL", ambiguous=False)
    if tp_hit and not sl_hit:
        return FillResult(True, exit_price=tp, exit_reason="TP", ambiguous=False)

    # Case 4 — BOTH touched this minute.
    # (a) adjudicate with 1s if present
    if seconds:
        ordered = _resolve_with_seconds(seconds=seconds, sl=sl, tp=tp)
        if ordered is not None:
            reason, price = ordered
            return FillResult(True, exit_price=price, exit_reason=reason,
                              ambiguous=False)
        # seconds present but neither crossed within them (data gap) -> fall
        # through to pessimistic.

    # (b) pessimistic: assume SL first for a short. Flag it.
    return FillResult(True, exit_price=sl, exit_reason="SL", ambiguous=True)


def _resolve_with_seconds(
    *, seconds: List[BTSecond], sl: float, tp: float
) -> Optional[tuple[str, float]]:
    """Walk the 1s bars in order; return ('SL', sl) or ('TP', tp) for whichever
    level is reached first. Within a single 1s bar that itself straddles both
    levels, we still cannot order sub-second events, so we keep the pessimistic
    SL-first convention for THAT bar — but only that bar, not the whole minute.
    Returns None if neither level is crossed in the provided seconds."""
    for s in seconds:
        s_sl = s.high >= sl
        s_tp = s.low <= tp
        if s_sl and s_tp:
            # both within this one second -> pessimistic within-second
            return ("SL", sl)
        if s_sl:
            return ("SL", sl)
        if s_tp:
            return ("TP", tp)
    return None
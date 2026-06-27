# backend/app/backtest/sim/hedge_fill_model.py
#
# Dual-trigger exit resolution for the SCALP_V3/V4 hedge trade on a 1-minute
# candle. Three independent triggers, checked against the relevant contract's
# OHLC for the candle:
#
#   SIG_SL   : signal contract high >= signal_sl   (signal premium rose to SL)
#   SIG_TP   : signal contract low  <= signal_tp   (signal premium fell to TP)
#   HEDGE_SL : hedge contract  low  <= hedge_sl    (hedge premium fell to its stop)
#
# PESSIMISM (the hedge is a LONG, so "worst" = exiting LOW):
#   When >1 trigger is reachable in the same minute and 1s data can't order
#   them, assume the LOSS-side outcome for the long hedge wins. SIG_TP is the
#   only *good* exit (signal hit its target → we wanted out); SIG_SL and
#   HEDGE_SL are loss-side. So on ambiguity, a loss-side trigger beats SIG_TP,
#   and between the two loss-side triggers HEDGE_SL (direct hedge stop) is taken.
#   ambiguous_fill=1 in those cases; 1s data (when present) resolves the true
#   order and clears the flag.
#
# EXIT PRICE: the hedge contract's CLOSE on the exit candle — matches PAPER V3,
# which exits at hedge LTP (not exactly at the stop). This is what we validate
# against. (If you later want HEDGE_SL modelled at the stop price, that's a
# one-line change in the runner.)

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class HedgeFillResult:
    exited: bool
    exit_reason: Optional[str]    # SIG_SL | SIG_TP | HEDGE_SL
    ambiguous: bool


def resolve_hedge_exit_on_candle(
    *,
    signal_high: float, signal_low: float,
    hedge_low: float,
    signal_sl: float, signal_tp: float, hedge_sl: float,
    signal_seconds: Optional[List[dict]] = None,
    hedge_seconds: Optional[List[dict]] = None,
) -> HedgeFillResult:
    sig_sl_hit = signal_high >= signal_sl
    sig_tp_hit = signal_low <= signal_tp
    hedge_sl_hit = hedge_low <= hedge_sl

    hits = [r for r, h in (("SIG_SL", sig_sl_hit),
                           ("SIG_TP", sig_tp_hit),
                           ("HEDGE_SL", hedge_sl_hit)) if h]

    if not hits:
        return HedgeFillResult(exited=False, exit_reason=None, ambiguous=False)

    if len(hits) == 1:
        return HedgeFillResult(exited=True, exit_reason=hits[0], ambiguous=False)

    # Multiple triggers in one candle — try 1s adjudication first.
    resolved = _adjudicate_1s(
        signal_seconds=signal_seconds, hedge_seconds=hedge_seconds,
        signal_sl=signal_sl, signal_tp=signal_tp, hedge_sl=hedge_sl,
        want=set(hits),
    )
    if resolved is not None:
        return HedgeFillResult(exited=True, exit_reason=resolved, ambiguous=False)

    # Pessimistic for a LONG hedge: loss-side beats SIG_TP; HEDGE_SL first.
    if "HEDGE_SL" in hits:
        return HedgeFillResult(exited=True, exit_reason="HEDGE_SL", ambiguous=True)
    if "SIG_SL" in hits:
        return HedgeFillResult(exited=True, exit_reason="SIG_SL", ambiguous=True)
    # Only SIG_TP left (shouldn't reach here since len>1) — safe fallback.
    return HedgeFillResult(exited=True, exit_reason="SIG_TP", ambiguous=True)


def _adjudicate_1s(*, signal_seconds, hedge_seconds,
                   signal_sl, signal_tp, hedge_sl, want) -> Optional[str]:
    """Walk 1-second bars in time order; return the FIRST trigger that fires.
    Needs both series aligned by second. Returns None if 1s data is unavailable
    or insufficient to order the contested triggers."""
    if not signal_seconds and not hedge_seconds:
        return None

    # Build a ts -> (signal_bar, hedge_bar) timeline.
    by_ts = {}
    for b in (signal_seconds or []):
        by_ts.setdefault(b["ts"], {})["sig"] = b
    for b in (hedge_seconds or []):
        by_ts.setdefault(b["ts"], {})["hed"] = b
    if not by_ts:
        return None

    for ts in sorted(by_ts.keys()):
        pair = by_ts[ts]
        sig = pair.get("sig")
        hed = pair.get("hed")
        if sig is not None:
            if "SIG_SL" in want and sig["high"] >= signal_sl:
                return "SIG_SL"
            if "SIG_TP" in want and sig["low"] <= signal_tp:
                return "SIG_TP"
        if hed is not None:
            if "HEDGE_SL" in want and hed["low"] <= hedge_sl:
                return "HEDGE_SL"
    return None
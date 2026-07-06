# backend/app/engine/ic_v1/ic_selection.py
#
# IC_V1 — Entry-instant strike selection (LIVE)
# ============================================================================
# At entry_time the engine needs the CURRENT weekly CE/PE chains' LTPs at that
# instant, then applies the SAME nearest-below-cap rule as the backtest:
#
#   SHORTS (L1/L2, premium_max ~₹85): highest premium <= cap. FAIL CLOSED —
#     no eligible strike => NO ENTRY TODAY (wrapper alerts via Telegram).
#   WINGS  (L3/L4, premium_max ~₹4) : highest premium <= cap. FAIL OPEN —
#     fall back to the cheapest available strike (flagged wing_fallback).
#     No candidates at all => wing absent; D6 policy in the group manager
#     then decides strangle-degrade vs skip (allow_strangle_degrade,
#     default False => skip the day).
#
# DIVERGENCE LEDGER: live uses bulk REST quote LTPs at entry_time; backtest
# uses the close of the 1m candle ENDING at entry_time. Documented, accepted.
#
# I/O SEPARATION: build_chain_candidates() and select_ic_strikes() are pure
# (unit-tested). snapshot_weekly_chain() is the only function that touches
# kite / instruments — it is a thin adapter.
#
# Chain width: strikes within ±CHAIN_STRIKE_RANGE of spot. Wings at ₹4 can sit
# far OTM; 1500 points each side on NIFTY covers them with margin while
# keeping the bulk quote call well under Kite's 500-instrument cap
# (1500/50 * 2 sides * 2 types + 2 ≈ 122 symbols).
# ============================================================================

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.engine.ic_v1.ic_live_core import select_strike, StrikePick

CHAIN_STRIKE_RANGE = 1500     # points each side of spot
KITE_QUOTE_BATCH   = 400      # stay under Kite's per-call instrument cap


# ============================================================================
# Result container
# ============================================================================

@dataclass
class ICSelection:
    ok: bool                          # False => NO ENTRY TODAY
    skip_reason: Optional[str] = None # "NO_SHORT_CE" | "NO_SHORT_PE" |
                                      # "NO_WING_CE" | "NO_WING_PE" |
                                      # "EMPTY_CHAIN" | "NO_EXPIRY"
    expiry: Optional[date] = None
    picks: Dict[str, StrikePick] = field(default_factory=dict)  # leg_id -> pick
    tokens: Dict[str, int] = field(default_factory=dict)        # leg_id -> token
    wing_fallback: bool = False
    wing_absent: List[str] = field(default_factory=list)        # leg ids absent


# ============================================================================
# PURE — candidate assembly + selection
# ============================================================================

def build_chain_candidates(
    rows: List[dict],
    ltp_by_symbol: Dict[str, float],
) -> Tuple[List[Tuple[int, str, float]], List[Tuple[int, str, float]], Dict[str, int]]:
    """
    rows: [{"tradingsymbol","instrument_token","strike","instrument_type"}...]
          (already filtered to the target weekly expiry)
    Returns (ce_candidates, pe_candidates, token_by_symbol) where candidates
    are (strike, symbol, ltp) with ltp > 0 only — zero/absent LTP excluded,
    same as the backtest excludes empty candles.
    """
    ce, pe, tokens = [], [], {}
    for r in rows:
        sym = r["tradingsymbol"]
        tokens[sym] = int(r["instrument_token"])
        ltp = ltp_by_symbol.get(sym)
        if not ltp or ltp <= 0:
            continue
        entry = (int(r["strike"]), sym, float(ltp))
        if r["instrument_type"] == "CE":
            ce.append(entry)
        else:
            pe.append(entry)
    return ce, pe, tokens


def select_ic_strikes(
    legs: List[dict],
    ce_candidates: List[Tuple[int, str, float]],
    pe_candidates: List[Tuple[int, str, float]],
    token_by_symbol: Dict[str, int],
    expiry: date,
) -> ICSelection:
    """
    legs: normalized config legs [{"id","action","opt_type","premium_max",
          "lots",...}] — legs with lots<=0 are DISABLED and skipped entirely
          (lots 0 on L3/L4 = pure short strangle, per backtest semantics).
    """
    sel = ICSelection(ok=True, expiry=expiry)

    if not ce_candidates and not pe_candidates:
        return ICSelection(ok=False, skip_reason="EMPTY_CHAIN", expiry=expiry)

    for leg in legs:
        if int(leg.get("lots", 0)) <= 0:
            continue   # disabled leg — never selected, never traded

        is_short = leg["action"] == "SELL"
        cands = ce_candidates if leg["opt_type"] == "CE" else pe_candidates

        pick = select_strike(
            cands,
            cap=float(leg["premium_max"]),
            fallback_cheapest=not is_short,   # shorts fail CLOSED, wings OPEN
        )

        if pick is None:
            if is_short:
                return ICSelection(
                    ok=False,
                    skip_reason=f"NO_SHORT_{leg['opt_type']}",
                    expiry=expiry,
                )
            # wing absent — record; group manager applies D6 policy
            sel.wing_absent.append(leg["id"])
            continue

        if pick.fallback:
            sel.wing_fallback = True

        sel.picks[leg["id"]] = pick
        sel.tokens[leg["id"]] = token_by_symbol.get(pick.symbol, 0)

    # Guard against the same strike being picked twice on one side (can only
    # happen with misconfigured caps, e.g. short cap < wing cap). Fail closed:
    # a condor with coincident legs is not the strategy.
    seen = {}
    for lid, p in sel.picks.items():
        key = p.symbol
        if key in seen:
            return ICSelection(
                ok=False,
                skip_reason=f"DUPLICATE_STRIKE_{seen[key]}_{lid}",
                expiry=expiry,
            )
        seen[key] = lid

    return sel


# ============================================================================
# I/O ADAPTER — the only impure function
# ============================================================================

def snapshot_weekly_chain(
    kite,
    api_key: str,
    access_token: str,
    *,
    quote_fn: Optional[Callable] = None,
) -> Tuple[Optional[date], List[dict], Dict[str, float]]:
    """
    Returns (expiry, rows, ltp_by_symbol) for the CURRENT weekly NIFTY chain
    within ±CHAIN_STRIKE_RANGE of spot. On ANY failure returns
    (None, [], {}) — the caller treats that as NO ENTRY TODAY (fail closed).

    quote_fn(symbols)->{sym: ltp} is injectable for tests; defaults to
    batched kite.quote().
    """
    try:
        from app.fetcher.zerodha_instruments import (
            load_instruments_df,
            get_nifty_spot,
        )

        df = load_instruments_df(api_key, access_token)
        if df is None or df.empty:
            write_audit_log("[IC_V1][SELECT] instruments df empty — NO ENTRY")
            return None, [], {}

        opts = df[
            (df["exchange"] == "NFO")
            & (df["name"] == "NIFTY")
            & (df["instrument_type"].isin(["CE", "PE"]))
            & (df["expiry"] >= date.today())
        ]
        if opts.empty:
            write_audit_log("[IC_V1][SELECT] no NIFTY options — NO ENTRY")
            return None, [], {}

        expiry = sorted(opts["expiry"].unique())[0]   # nearest = current weekly
        week = opts[opts["expiry"] == expiry]

        spot = get_nifty_spot(api_key, access_token)
        if not spot or spot <= 0:
            write_audit_log("[IC_V1][SELECT] spot unavailable — NO ENTRY")
            return None, [], {}

        week = week[
            (week["strike"] >= spot - CHAIN_STRIKE_RANGE)
            & (week["strike"] <= spot + CHAIN_STRIKE_RANGE)
        ]

        rows = week[
            ["tradingsymbol", "instrument_token", "strike", "instrument_type"]
        ].to_dict("records")

        symbols = [r["tradingsymbol"] for r in rows]

        if quote_fn is None:
            def quote_fn(syms):
                out = {}
                for i in range(0, len(syms), KITE_QUOTE_BATCH):
                    batch = [f"NFO:{s}" for s in syms[i : i + KITE_QUOTE_BATCH]]
                    q = kite.quote(batch)
                    for k, v in q.items():
                        out[k.split(":", 1)[1]] = float(
                            v.get("last_price") or 0.0
                        )
                return out

        ltp_by_symbol = quote_fn(symbols)

        write_audit_log(
            f"[IC_V1][SELECT] chain snapshot expiry={expiry} spot={spot:.1f} "
            f"symbols={len(symbols)} quoted={sum(1 for v in ltp_by_symbol.values() if v > 0)}"
        )
        return expiry, rows, ltp_by_symbol

    except Exception as e:
        write_audit_log(f"[IC_V1][SELECT][ERROR] {repr(e)} — NO ENTRY (fail closed)")
        return None, [], {}
# engine/strike_selector.py
# Simple strike selector for NIFTY/option contracts.
# - Exposes pick_strikes(instruments, min_price, max_price)
# - instruments: list of dicts with keys:
#     {
#       "symbol": "NIFTY23DEC21500CE",    # optional
#       "strike": 21500,                  # numeric strike
#       "option_type": "CE" or "PE",      # string
#       "last_price": 178.5,              # last traded price (ltp)
#       "expiry": "2023-12-28"            # optional
#       "volume": 1234                    # optional (used as tie-breaker)
#     }
# Returns:
#   {"CE": instrument_or_None, "PE": instrument_or_None}
# Both CE and PE will be non-None if possible; if not, nearest fallback is returned.

from typing import List, Dict, Any, Optional, Tuple
import math

def _midpoint(min_price: float, max_price: float) -> float:
    return (min_price + max_price) / 2.0

def _score_candidate(candidate_lp: float, target_mid: float) -> float:
    """Lower score = better. We choose candidate with smallest absolute distance to midpoint."""
    return abs(candidate_lp - target_mid)

def _best_from_list(candidates: List[Dict[str, Any]], min_price: float, max_price: float) -> Optional[Dict[str, Any]]:
    """
    If any candidate's last_price is inside [min_price, max_price], pick the one whose last_price
    is closest to the mid-point. If none are inside, pick the one whose last_price is closest to
    the mid-point (fallback).
    """
    if not candidates:
        return None

    midpoint = _midpoint(min_price, max_price)

    # Separate in-range and out-of-range
    in_range = []
    out_range = []
    for c in candidates:
        ltp = c.get("last_price")
        if ltp is None:
            # If no last_price, we can fallback to strike (less ideal). Use strike as proxy.
            ltp = float(c.get("strike", 0))
        if min_price <= ltp <= max_price:
            in_range.append((c, _score_candidate(ltp, midpoint)))
        else:
            out_range.append((c, _score_candidate(ltp, midpoint)))

    # Sort by score asc; tie-breaker: use volume (higher better), then strike (closer to midpoint)
    def _sort_key(pair: Tuple[Dict[str, Any], float]):
        c, score = pair
        volume = -float(c.get("volume", 0))  # negative because we want higher volume first
        strike_dist = abs(float(c.get("strike", 0)) - midpoint)
        return (score, volume, strike_dist)

    if in_range:
        in_range.sort(key=_sort_key)
        return in_range[0][0]
    else:
        out_range.sort(key=_sort_key)
        return out_range[0][0]

def pick_strikes(instruments: List[Dict[str, Any]], min_price: float, max_price: float) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Main entry point.

    instruments: list of option dicts (see top of file)
    min_price, max_price: user-provided price band (inclusive)

    Returns dict: {"CE": {...} | None, "PE": {...} | None}
    """
    if min_price is None or max_price is None:
        raise ValueError("min_price and max_price must be provided")

    if min_price > max_price:
        min_price, max_price = max_price, min_price  # swap to be safe

    # Normalize option_type keys (uppercase)
    ce_list = []
    pe_list = []
    for inst in instruments:
        t = inst.get("option_type", "").upper()
        if t == "CE":
            ce_list.append(inst)
        elif t == "PE":
            pe_list.append(inst)
        else:
            # if unknown, try to infer by symbol suffix
            sym = inst.get("symbol", "")
            if sym.endswith("CE"):
                ce_list.append(inst)
            elif sym.endswith("PE"):
                pe_list.append(inst)

    chosen_ce = _best_from_list(ce_list, min_price, max_price)
    chosen_pe = _best_from_list(pe_list, min_price, max_price)

    return {"CE": chosen_ce, "PE": chosen_pe}


# ----- small helper for testing manually -----
if __name__ == "__main__":
    sample = [
        {"symbol":"NIFTY-21500CE","strike":21500,"option_type":"CE","last_price":180.0,"volume":100},
        {"symbol":"NIFTY-21600CE","strike":21600,"option_type":"CE","last_price":160.0,"volume":50},
        {"symbol":"NIFTY-21500PE","strike":21500,"option_type":"PE","last_price":170.0,"volume":200},
        {"symbol":"NIFTY-21600PE","strike":21600,"option_type":"PE","last_price":90.0,"volume":10}
    ]
    print(pick_strikes(sample, 150.0, 200.0))

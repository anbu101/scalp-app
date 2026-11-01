# backend/app/engine/vet/vet_wing_search.py
#
# ── VET_WING_FAR_20261101 ── far-wing search for VET_V1 live/paper
# ============================================================================
# THE FINDING (28 Oct 2026 live log): after the 12:00 FLIP close the manager
# skipped every 5-minute re-entry until the 15:00 cutoff with
#     "no REAL wing <= 3 on PE — entry SKIPPED (never bare)"
# because the armed universe is TMA2TickEngine's fixed ±30-strike band around
# the boot ATM (universe=122), and every PE priced <= Rs 3 sat beyond it. The
# backtest's REAL path searches every strike it has a print for, so it would
# have found a real wing (and its SYN path models one anyway).
#
# THE FIX: before skipping, look further out on THIS expiry's instrument list
# (same underlying, same side, OTM of the main leg, up to max_strikes away),
# quote the candidates in ONE REST call and take the DEAREST real contract at
# or under the cap — the same rule _select_wing applies inside the band. A
# candidate counts as real only if it has a positive last price AND traded
# today (volume > 0): a strike nobody has printed is not a hedge you can rely
# on to fill, and the backtest's REAL path needs a print too.
#
# Pure function: no broker, no app imports, injected quote + sleep, so the
# suite drives it with a pandas frame and a stub. The caller (the selection
# loop) subscribes the chosen contract; the manager validates the row again
# and keeps "never bare": no row → the entry is skipped exactly as before.
# ============================================================================

from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, Optional, Tuple

FENCE = "VET_WING_FAR_20261101"
WING_SEARCH_MAX_STRIKES_DEFAULT = 90     # strikes from the MAIN, OTM-ward
QUOTE_CHUNK = 500                        # Kite quote: <= 500 instruments/call
EXCHANGE = "NFO"


def _iso(x) -> str:
    return str(x)[:10]


def _quote_once(quote_batch: Callable, keys, sleep) -> Dict:
    """One call; a rate-limit reply waits out the window ONCE and retries
    (house pattern: SCALP_SKEW_QUOTE / ORDER_LTP_RETRY). Anything else, or a
    second 429, propagates."""
    try:
        return quote_batch(keys) or {}
    except Exception as e:
        if "too many requests" not in repr(e).lower():
            raise
        sleep(1.1)
        return quote_batch(keys) or {}


def find_far_wing(*, instruments_df, quote_batch: Callable, side: str,
                  main_strike: float, main_symbol: Optional[str],
                  expiry_iso: str, cap: float, max_strikes: int,
                  exclude: Iterable[str] = (), band: Optional[Tuple] = None,
                  step: float = 50.0, underlying: str = "NIFTY",
                  require_volume: bool = True,
                  sleep: Callable = time.sleep) -> Tuple[Optional[Dict], Dict]:
    """→ (row | None, info).

    row  = {tradingsymbol, token, strike, expiry, instrument_type, ltp,
            volume, _far: {...distance facts...}}
    info = {reason, candidates, quoted, cheapest, max_strikes}   (always)
    Never raises.
    """
    side = str(side).upper()
    info: Dict = {"reason": "", "candidates": 0, "quoted": 0,
                  "cheapest": None, "max_strikes": int(max_strikes or 0)}
    try:
        cap = float(cap or 0)
        mk = float(main_strike or 0)
        maxk = int(max_strikes or 0)
        step = float(step or 50.0)
    except (TypeError, ValueError):
        info["reason"] = "bad search arguments"
        return None, info
    if side not in ("CE", "PE") or cap <= 0 or mk <= 0 or maxk <= 0:
        info["reason"] = (f"search not run (side={side} cap={cap:g} "
                          f"main_strike={mk:g} max_strikes={maxk})")
        return None, info
    if instruments_df is None:
        info["reason"] = "no instruments list loaded"
        return None, info

    # ── candidates: this expiry, this side, OTM of the main, within reach ──
    excl = {str(s) for s in (exclude or ()) if s}
    if main_symbol:
        excl.add(str(main_symbol))
    reach = maxk * step
    try:
        df = instruments_df
        rows = df[(df["name"] == underlying)
                  & (df["segment"] == f"{EXCHANGE}-OPT")
                  & (df["instrument_type"] == side)]
        rows = rows[rows["expiry"].map(_iso) == _iso(expiry_iso)]
        if side == "PE":
            rows = rows[(rows["strike"] < mk) & (rows["strike"] >= mk - reach)]
        else:
            rows = rows[(rows["strike"] > mk) & (rows["strike"] <= mk + reach)]
        cands = []
        for _, r in rows.iterrows():
            sym = str(r["tradingsymbol"])
            if sym in excl:
                continue
            cands.append({"tradingsymbol": sym,
                          "token": int(r["instrument_token"]),
                          "strike": float(r["strike"]),
                          "expiry": _iso(expiry_iso),
                          "instrument_type": side})
    except Exception as e:
        info["reason"] = f"instrument filter failed: {e!r}"
        return None, info
    cands.sort(key=lambda c: abs(c["strike"] - mk))     # nearest first
    info["candidates"] = len(cands)
    if not cands:
        info["reason"] = (f"no unpriced {side} contracts listed for {expiry_iso} "
                          f"within {maxk} strikes OTM of {mk:g}")
        return None, info

    # ── quote them (one call per <=500) ──
    quotes: Dict = {}
    try:
        for i in range(0, len(cands), QUOTE_CHUNK):
            keys = [f"{EXCHANGE}:{c['tradingsymbol']}"
                    for c in cands[i:i + QUOTE_CHUNK]]
            quotes.update(_quote_once(quote_batch, keys, sleep))
    except Exception as e:
        info["reason"] = f"quote failed: {e!r}"
        return None, info

    best = None
    cheapest = None
    for c in cands:
        q = quotes.get(f"{EXCHANGE}:{c['tradingsymbol']}") or {}
        try:
            px = float(q.get("last_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        vol = q.get("volume")
        if px <= 0:
            continue
        info["quoted"] += 1
        if cheapest is None or px < cheapest[1]:
            cheapest = (c["tradingsymbol"], px)
        if require_volume and vol is not None and int(vol or 0) <= 0:
            continue                       # listed, never printed today
        if px > cap:
            continue
        if best is None or px > best["ltp"]:
            best = dict(c, ltp=px, volume=(int(vol) if vol is not None else None))
    info["cheapest"] = cheapest
    if best is None:
        info["reason"] = (f"searched {len(cands)} {side} contracts out to {maxk} "
                          f"strikes OTM of {mk:g} ({info['quoted']} priced) — "
                          f"none traded today at <= {cap:g}"
                          + (f" (cheapest {cheapest[0]} @ {cheapest[1]:g})"
                             if cheapest else ""))
        return None, info

    # ── distance facts for the log ──
    k = best["strike"]
    far = {"strikes_from_main": int(round(abs(k - mk) / step)),
           "points_from_main": round(abs(k - mk), 2),
           "in_band": True, "beyond_band_strikes": 0,
           "beyond_band_points": 0.0, "band": None,
           "candidates": len(cands), "quoted": info["quoted"]}
    if band and len(band) == 2 and band[0] and band[1]:
        lo, hi = float(band[0]), float(band[1])
        far["band"] = (lo, hi)
        out = (lo - k) if k < lo else (k - hi) if k > hi else 0.0
        far["in_band"] = out <= 0
        far["beyond_band_points"] = round(max(0.0, out), 2)
        far["beyond_band_strikes"] = int(round(max(0.0, out) / step))
    best["_far"] = far
    info["reason"] = "found"
    return best, info

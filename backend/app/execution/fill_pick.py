# backend/app/execution/fill_pick.py
#
# ── FILL_TRUTH_20261102 ── which broker order closed THIS leg?
#
# 2026-11-02 incident (IC_V2 LIVE, NIFTY26N0323350CE, L1·ADJ — a LONG leg):
# the ADJ leg's SELL SL-GTT filled at 55.85 (11:48:07). The GTT monitor hit
# the house GTT race (status=triggered before orders[].result is populated)
# and fell back to scanning the order book for "the newest COMPLETE BUY on
# the symbol" — a rule written for SHORT legs. For a long leg that is the
# leg's own ENTRY order (BUY 80.15 at 09:46:29), so the close was booked at
# 80.15 = entry: ₹0 instead of −₹1,579.50. The strategy ledger, Dashboard,
# replay, parity and Week/Month P/L all inherited the fiction, while Kite
# (and the qty-only broker check) were right.
#
# The rule every fallback must obey — one place, shared:
#   * the exit SIDE is the opposite of the leg's entry side
#     (SHORT leg exits with BUY, LONG leg exits with SELL);
#   * the order must be at/after the leg's entry (when both times are known);
#   * order ids that belong to ANY leg's entry, or that already closed
#     another leg, are never eligible;
#   * when the GTT exposed its own order id(s), ONLY those orders count;
#   * the EARLIEST eligible fill after entry wins (a later same-side order on
#     the same strike belongs to a later leg — e.g. an ADJ bought on the
#     strike whose short was just stopped).
#   * quantities: fills are accumulated earliest-first up to the leg's qty
#     (freeze slices), VWAP returned.
# Nothing here talks to the broker: callers pass the order book they read.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

FENCE = "FILL_TRUTH_20261102"
IST = timezone(timedelta(hours=5, minutes=30))
ENTRY_SLACK_S = 5          # order_timestamp is broker wall-clock, entry_ts app clock


def _g(o: dict, *names, default=None):
    for n in names:
        v = o.get(n)
        if v not in (None, ""):
            return v
    return default


def order_ts(o: dict) -> Optional[int]:
    """Epoch seconds of a Kite (or Angel) order row; None when unknown."""
    v = _g(o, "exchange_timestamp", "order_timestamp", "exchange_update_timestamp",
           "updatetime", "exchtime")
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        d = v if isinstance(v, datetime) else None
        if d is None:
            s = str(v).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
                try:
                    d = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if d is None:
                d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)          # Kite timestamps are IST wall-clock
        return int(d.timestamp())
    except Exception:
        return None


def exit_side_for(is_short: bool) -> str:
    return "BUY" if is_short else "SELL"


def pick_exit_fill(orders: Iterable[dict], *, symbol: str, exit_side: str,
                   not_before_ts: Optional[int] = None,
                   exclude_ids: Iterable[str] = (),
                   only_ids: Optional[Iterable[str]] = None,
                   want_qty: Optional[int] = None) -> Tuple[Optional[float], List[str]]:
    """(vwap, [order_ids]) of the leg's exit fill(s), or (None, [])."""
    side = str(exit_side or "").upper()
    excl = {str(x) for x in (exclude_ids or ()) if x}
    only = {str(x) for x in only_ids} if only_ids is not None else None
    cands = []
    for i, o in enumerate(orders or []):
        if str(_g(o, "tradingsymbol", "symbol", default="")) != symbol:
            continue
        if str(_g(o, "transaction_type", "transactiontype", default="")).upper() != side:
            continue
        if str(_g(o, "status", "orderstatus", default="")).upper() != "COMPLETE":
            continue
        oid = str(_g(o, "order_id", "orderid", default=""))
        if not oid or oid in excl:
            continue
        if only is not None and oid not in only:
            continue
        try:
            px = float(_g(o, "average_price", "averageprice", default=0) or 0)
            qv = _g(o, "filled_quantity", "filledshares", "filledquantity", "quantity")
            # a row without any quantity field: size unknown — count it as
            # the whole leg (COMPLETE means it filled what it asked for)
            q = int(float(qv)) if qv is not None else int(want_qty or 1)
        except (TypeError, ValueError):
            continue
        if px <= 0 or q <= 0:
            continue
        ts = order_ts(o)
        if not_before_ts and ts is not None and ts < int(not_before_ts) - ENTRY_SLACK_S:
            continue
        cands.append((ts if ts is not None else float("inf"), i, oid, q, px))
    if not cands:
        return None, []
    cands.sort(key=lambda c: (c[0], c[1]))       # earliest first, book order breaks ties
    got_q, got_v, ids = 0, 0.0, []
    for _, _, oid, q, px in cands:
        take = q if not want_qty else min(q, int(want_qty) - got_q)
        if take <= 0:
            break
        got_q += take
        got_v += take * px
        ids.append(oid)
        if not want_qty:
            break
    if got_q <= 0:
        return None, []
    return round(got_v / got_q, 4), ids


def implausible_exit(price: Optional[float], *, entry: float, sl: Optional[float],
                     tp: Optional[float], reason: str) -> Optional[str]:
    """A GTT exit is a fill at/near its trigger. Returns a short description
    when the booked price is far from every trigger the leg had (the class of
    error above: an SL close booked at the entry price)."""
    if not price or reason not in ("SL", "TP"):
        return None
    refs = [x for x in (sl, tp) if x]
    if not refs:
        return None
    near = min(abs(price - r) / max(r, 1e-9) for r in refs)
    if near <= 0.20:
        return None
    eq = " (= entry)" if entry and abs(price - entry) < 0.01 else ""
    return (f"{reason} fill {price:.2f}{eq} is {near * 100:.0f}% away from every "
            f"trigger (SL {sl}, TP {tp})")

from fastapi import APIRouter
from app.brokers.zerodha_auth import get_kite
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["positions"])


@router.get("/positions/today")
def positions_today():
    kite = get_kite()
    if not kite:
        return _empty_response()

    try:
        positions = kite.positions().get("net", [])
    except Exception:
        return _empty_response()

    open_pos = []
    closed_pos = []

    realised   = 0.0
    unrealised = 0.0

    # --------------------------------------------------
    # POS_DECOMP BEGIN
    # A single Kite net row can carry BOTH a realised leg (matched buy/sell
    # qty closed earlier today) AND an open leg (current non-zero quantity)
    # for the SAME tradingsymbol — happens when a strike is closed and then
    # re-entered the same day. We split every row into realised + open parts.
    #
    #   matched_qty  = min(day_buy_quantity, day_sell_quantity)   -> closed leg
    #   open_qty     = quantity (signed)                          -> open leg
    #
    # Realised is computed from SETTLED day prices (not Kite's cached
    # `unrealised` field, which can be stale):
    #
    #   realised = matched_qty * (day_sell_price - day_buy_price) * multiplier
    #
    # `multiplier` comes from the row (NFO options = 1, but never assume —
    # read it). A symbol may legitimately appear in BOTH closed_pos and
    # open_pos.
    # --------------------------------------------------
    raw_open   = []   # rows with a live open leg (quantity != 0)
    raw_closed = []   # synthetic realised-leg rows (one per symbol w/ matched qty)

    for p in positions:
        p = dict(p)

        day_buy_qty   = int(p.get("day_buy_quantity")  or 0)
        day_sell_qty  = int(p.get("day_sell_quantity") or 0)
        day_buy_price  = float(p.get("day_buy_price")  or 0)
        day_sell_price = float(p.get("day_sell_price") or 0)
        qty           = int(p.get("quantity") or 0)
        multiplier    = float(p.get("multiplier") or 1)
        matched_qty   = min(day_buy_qty, day_sell_qty)

        # ---- realised leg (closed portion) ----
        if matched_qty > 0:
            realised_leg = round(
                matched_qty * (day_sell_price - day_buy_price) * multiplier, 2
            )

            closed_row = dict(p)
            # PositionRow renders day_buy_quantity as the closed-row qty;
            # show the matched (closed) size, not gross buys.
            closed_row["day_buy_quantity"] = matched_qty
            closed_row["realised"] = realised_leg
            closed_row["pnl"]      = realised_leg
            raw_closed.append(closed_row)

        elif qty == 0 and (day_buy_qty > 0 or day_sell_qty > 0):
            # Fully-closed row where matched_qty is 0 only if one side is 0,
            # which shouldn't reach here, but guard anyway: fall back to Kite's
            # realised/pnl so a fully-closed symbol is never dropped.
            realised_leg = round(float(p.get("realised") or p.get("pnl") or 0), 2)
            closed_row = dict(p)
            closed_row["day_buy_quantity"] = day_buy_qty or day_sell_qty
            closed_row["realised"] = realised_leg
            closed_row["pnl"]      = realised_leg
            raw_closed.append(closed_row)

        # ---- open leg (live portion) ----
        if qty != 0:
            raw_open.append(p)
    # POS_DECOMP END

    # --------------------------------------------------
    # Fetch live LTP for ALL open positions in one call.
    #
    # WHY:
    #   Zerodha's positions() response contains an `unrealised` field that
    #   is computed on their servers and cached — it does NOT update on
    #   every API call. kite.ltp() is always real-time.
    # --------------------------------------------------
    live_ltps: dict = {}

    if raw_open:
        symbols = [f"NFO:{p['tradingsymbol']}" for p in raw_open]

        for i in range(0, len(symbols), 50):
            batch = symbols[i:i + 50]
            try:
                ltp_data = kite.ltp(batch)
                for full_sym, data in ltp_data.items():
                    plain = full_sym.split(":", 1)[-1]
                    price = data.get("last_price")
                    if price and price > 0:
                        live_ltps[plain] = float(price)
            except Exception as e:
                write_audit_log(f"[POSITIONS] kite.ltp() failed: {e}")

    # --------------------------------------------------
    # Build open positions with live P&L
    #
    # IMPORTANT: avg_price here is `average_price` = the avg of the CURRENT
    # open leg only (Kite recomputes it after a square-off+reopen), so
    # (ltp - avg_price) * qty is the unrealised of the open leg ONLY and does
    # not double-count the realised leg already booked in closed_pos.
    # --------------------------------------------------
    for p in raw_open:
        sym       = p["tradingsymbol"]
        avg_price = float(p.get("average_price") or 0)
        qty       = int(p["quantity"])
        ltp       = live_ltps.get(sym)

        if ltp is not None and ltp > 0:
            live_pnl = round((ltp - avg_price) * qty, 2)
        else:
            live_pnl = float(p.get("unrealised") or p.get("pnl") or 0)
            write_audit_log(
                f"[POSITIONS] No live LTP for {sym} — falling back to cached pnl"
            )

        p["pnl"]        = live_pnl
        p["unrealised"] = live_pnl
        p["ltp"]        = ltp if ltp else p.get("last_price")

        slot      = _map_position_to_slot(p)
        p["slot"]    = slot
        p["managed"] = slot is not None

        open_pos.append(p)
        unrealised += live_pnl

    # --------------------------------------------------
    # Build closed positions (realised P&L is final — no LTP needed)
    # --------------------------------------------------
    for p in raw_closed:
        p["managed"] = False
        closed_pos.append(p)
        realised += float(p.get("realised") or p.get("pnl") or 0)

    return {
        "open": open_pos,
        "closed": closed_pos,
        "totals": {
            "realised":   round(realised,   2),
            "unrealised": round(unrealised, 2),
            "total":      round(realised + unrealised, 2),
        },
        "slots": _compute_slot_health(),
    }


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _empty_response():
    return {
        "open": [],
        "closed": [],
        "totals": {
            "realised":   0.0,
            "unrealised": 0.0,
            "total":      0.0,
        },
        "slots": {},
    }


def _map_position_to_slot(position: dict):
    """
    Read-only mapping: broker position -> TradeStateManager slot
    MULTI-STRATEGY SAFE
    """
    symbol = position.get("tradingsymbol")
    qty    = abs(position.get("quantity", 0))

    for strategy_slots in TradeStateManager._REGISTRY.values():
        for name, mgr in strategy_slots.items():
            trade = mgr.active_trade
            if not trade:
                continue
            if trade.symbol == symbol and trade.qty == qty:
                return f"{mgr.strategy_id}:{name}"

    return None


def _compute_slot_health():
    """
    UI-only reconciliation view.
    MULTI-STRATEGY SAFE
    """
    health = {}

    for strategy_id, strategy_slots in TradeStateManager._REGISTRY.items():
        for slot_name, mgr in strategy_slots.items():
            trade = mgr.active_trade
            key   = f"{strategy_id}:{slot_name}"
            health[key] = trade.state if trade else "ARMED"

    return health
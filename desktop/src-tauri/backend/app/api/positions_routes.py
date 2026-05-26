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
    # Separate open vs closed first
    # --------------------------------------------------
    raw_open   = []
    raw_closed = []

    for p in positions:
        p = dict(p)
        if p["quantity"] != 0:
            raw_open.append(p)
        elif p["day_buy_quantity"] > 0 or p["day_sell_quantity"] > 0:
            raw_closed.append(p)

    # --------------------------------------------------
    # Fetch live LTP for ALL open positions in one call.
    #
    # WHY:
    #   Zerodha's positions() response contains an `unrealised` field that
    #   is computed on their servers and cached — it does NOT update on
    #   every API call. Polling faster makes no difference.
    #
    #   kite.ltp() is always real-time. We call it here for exactly the
    #   symbols we have open and compute pnl = (ltp - avg_price) * qty.
    #   This is the only way to get a live number.
    # --------------------------------------------------
    live_ltps: dict = {}

    if raw_open:
        symbols = [f"NFO:{p['tradingsymbol']}" for p in raw_open]

        # kite.ltp() accepts up to 50 symbols per call
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i + 50]
            try:
                ltp_data = kite.ltp(batch)
                for full_sym, data in ltp_data.items():
                    # "NFO:BANKNIFTY26MAY54000CE" -> "BANKNIFTY26MAY54000CE"
                    plain = full_sym.split(":", 1)[-1]
                    price = data.get("last_price")
                    if price and price > 0:
                        live_ltps[plain] = float(price)
            except Exception as e:
                write_audit_log(f"[POSITIONS] kite.ltp() failed: {e}")

    # --------------------------------------------------
    # Build open positions with live P&L
    # --------------------------------------------------
    for p in raw_open:
        sym       = p["tradingsymbol"]
        avg_price = float(p.get("average_price") or 0)
        qty       = int(p["quantity"])
        ltp       = live_ltps.get(sym)

        if ltp is not None and ltp > 0:
            # Real-time: computed from fresh kite.ltp()
            live_pnl = round((ltp - avg_price) * qty, 2)
        else:
            # Fallback: Zerodha's cached value (only if ltp() itself failed)
            live_pnl = float(p.get("unrealised") or p.get("pnl") or 0)
            write_audit_log(
                f"[POSITIONS] No live LTP for {sym} — falling back to cached pnl"
            )

        p["pnl"]        = live_pnl   # consumed by Dashboard PositionRow
        p["unrealised"] = live_pnl   # keep both keys consistent
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
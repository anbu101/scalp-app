from fastapi import APIRouter
from app.brokers.zerodha_auth import get_kite
from app.trading.trade_state_manager import TradeStateManager

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

    realised = 0.0
    unrealised = 0.0

    for p in positions:
        # Defensive copy for UI safety
        p = dict(p)

        # -------------------------
        # OPEN POSITION
        # -------------------------
        if p["quantity"] != 0:
            slot = _map_position_to_slot(p)
            p["slot"] = slot
            p["managed"] = slot is not None

            open_pos.append(p)
            unrealised += p.get("unrealised", 0.0)
            continue

        # -------------------------
        # CLOSED POSITION (TODAY)
        # -------------------------
        if (
            p["quantity"] == 0
            and (p["day_buy_quantity"] > 0 or p["day_sell_quantity"] > 0)
        ):
            p["managed"] = False
            closed_pos.append(p)
            realised += p.get("realised", 0.0)

    return {
        "open": open_pos,
        "closed": closed_pos,
        "totals": {
            "realised": round(realised, 2),
            "unrealised": round(unrealised, 2),
            "total": round(realised + unrealised, 2),
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
            "realised": 0.0,
            "unrealised": 0.0,
            "total": 0.0,
        },
        "slots": {},
    }


def _map_position_to_slot(position: dict):
    """
    Read-only mapping: broker position -> TradeStateManager slot
    """
    symbol = position.get("tradingsymbol")
    qty = abs(position.get("quantity", 0))

    for name, mgr in TradeStateManager._REGISTRY.items():
        trade = mgr.active_trade
        if not trade:
            continue

        if trade.symbol == symbol and trade.qty == qty:
            return name

    return None


def _compute_slot_health():
    """
    UI-only reconciliation view.
    Does NOT mutate anything.
    """
    health = {}

    for slot, mgr in TradeStateManager._REGISTRY.items():
        trade = mgr.active_trade

        if not trade:
            health[slot] = "ARMED"
            continue

        health[slot] = trade.state


    return health

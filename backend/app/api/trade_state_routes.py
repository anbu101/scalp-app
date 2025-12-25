from fastapi import APIRouter
from app.trading.trade_state_manager import TradeStateManager

router = APIRouter(tags=["trade-state"])


@router.get("/trade/state")
def get_trade_state():
    """
    Authoritative trade + slot state for UI.

    States:
    - ARMED
    - IN_TRADE
    - EXITING
    - CLOSED

    If IN_TRADE / EXITING:
    includes trade details.
    """
    result = {}

    for slot, mgr in TradeStateManager._REGISTRY.items():
        # -------------------------
        # DEFAULT (NO TRADE)
        # -------------------------
        payload = {
            "state": "ARMED"
        }

        trade = mgr.active_trade

        if trade:
            payload = {
                "state": trade.state,
                "symbol": trade.symbol,
                "buy_price": trade.buy_price,
                "sl": trade.sl_price,
                "tp": trade.tp_price,
                "qty": trade.qty,
                "tp_hit": trade.tp_triggered,
            }

        result[slot] = payload

    return result

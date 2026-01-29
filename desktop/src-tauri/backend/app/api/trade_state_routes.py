from fastapi import APIRouter
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["trade-state"])


@router.get("/trade/state")
def get_trade_state():
    """
    Authoritative trade + slot state for UI.

    Slot states expected by UI:
    - ARMED
    - BUY_PLACED
    - BUY_FILLED
    - PROTECTED
    - IN_TRADE
    - EXITING
    - CLOSED

    Always returns a payload per slot.
    NEVER throws (UI-safe).
    """

    result = {}

    try:
        registry = TradeStateManager._REGISTRY
    except Exception as e:
        write_audit_log(
            f"[TRADE_STATE][ERROR] Failed to access registry: {e}"
        )
        return result

    for slot_name, manager in registry.items():

        # -------------------------
        # DEFAULT: NO ACTIVE TRADE
        # -------------------------
        payload = {
            "state": "ARMED",
            "symbol": None,
            "buy_price": None,
            "sl_price": None,
            "tp_price": None,
            "qty": None,
            "tp_hit": False,
        }

        try:
            trade = manager.active_trade

            if trade:
                payload.update({
                    "state": getattr(trade, "state", "UNKNOWN"),
                    "symbol": getattr(trade, "symbol", None),
                    "buy_price": getattr(trade, "buy_price", None),
                    "sl_price": getattr(trade, "sl_price", None),
                    "tp_price": getattr(trade, "tp_price", None),
                    "qty": getattr(trade, "qty", None),
                    "tp_hit": getattr(trade, "tp_triggered", False),
                })

        except Exception as e:
            write_audit_log(
                f"[TRADE_STATE][WARN] Slot read failed "
                f"SLOT={slot_name} ERR={e}"
            )

        result[slot_name] = payload

    return result

from typing import Dict, Set, Tuple

from app.trading.trade_state_manager import TradeStateManager
from app.db.trades_repo import close_trade
from app.event_bus.audit_logger import write_audit_log

_PROCESSED_EVENTS: Set[Tuple[str, str]] = set()


def on_order_update(update: Dict):
    """
    Zerodha ORDER WebSocket handler.

    Handles:
    - SL-M fill → DB close
    - Idempotent per (order_id, status)
    """

    order_id = update.get("order_id")
    status = update.get("status")

    if not order_id or not status:
        return

    key = (order_id, status)
    if key in _PROCESSED_EVENTS:
        return
    _PROCESSED_EVENTS.add(key)

    write_audit_log(
        f"[ORDER-UPDATE] ORDER_ID={order_id} STATUS={status}"
    )

    # Only act on completed orders
    if status != "COMPLETE":
        return

    # --------------------------------------------------
    # SL-M FILLED → CLOSE TRADE
    # --------------------------------------------------
    for slot_name, mgr in TradeStateManager._REGISTRY.items():
        trade = mgr.active_trade
        if not trade:
            continue

        if trade.sl_order_id == order_id:
            write_audit_log(
                f"[SL-FILL] SLOT={slot_name} SYMBOL={trade.symbol}"
            )

            exit_price = update.get("average_price", trade.sl_price)

            # Close in DB
            close_trade(
                trade_id=trade.trade_id,
                exit_price=exit_price,
                exit_order_id=order_id,
                exit_reason="SL",
            )

            # Close slot state
            mgr._close_trade("SL")
            return

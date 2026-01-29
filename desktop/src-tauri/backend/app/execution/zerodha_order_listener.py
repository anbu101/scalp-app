from typing import Dict, Set, Tuple

from app.event_bus.audit_logger import write_audit_log

# Keep for idempotency if listener is ever enabled
_PROCESSED_EVENTS: Set[Tuple[str, str]] = set()


def on_order_update(update: Dict):
    """
    Zerodha ORDER WebSocket handler.

    CURRENT MODE:
    - GTT-only exits
    - NO SL-M orders
    - NO order-driven state mutation

    This listener is kept ONLY for logging / future use.
    """

    order_id = update.get("order_id")
    status = update.get("status")

    if not order_id or not status:
        return

    key = (order_id, status)
    if key in _PROCESSED_EVENTS:
        return
    _PROCESSED_EVENTS.add(key)

    # Log only — do NOT act
    write_audit_log(
        f"[ORDER-UPDATE][IGNORED] ORDER_ID={order_id} STATUS={status}"
    )

    # ❌ No trade closing here
    # ❌ No SL handling
    # ❌ No DB writes
    return

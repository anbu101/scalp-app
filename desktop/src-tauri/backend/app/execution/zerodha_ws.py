"""
DEPRECATED — DO NOT USE KiteTicker HERE

ZerodhaWebSocket is intentionally disabled.

Reason:
- Zerodha allows only ONE KiteTicker connection per session
- ZerodhaTickEngine is the sole WS owner
- Order updates are handled via REST reconciliation

This file exists ONLY for backward compatibility.
"""

from app.event_bus.audit_logger import write_audit_log
from app.execution.zerodha_order_listener import on_order_update


class ZerodhaWebSocket:
    """
    DEPRECATED: Order updates are handled via REST reconciliation.
    """

    def __init__(self):
        write_audit_log(
            "[ZERODHA-WS][DEPRECATED] ZerodhaWebSocket instantiated — DISABLED"
        )

    # -------------------------
    # NO-OP METHODS
    # -------------------------

    def connect(self):
        write_audit_log(
            "[ZERODHA-WS][BLOCKED] connect() ignored — "
            "single WS policy enforced"
        )
        return

    def on_connect(self, ws, response):
        return

    def on_close(self, ws, code, reason):
        return

    def on_error(self, ws, code, reason):
        return

    def on_order_update(self, ws, data):
        # Allow manual injection if ever needed
        on_order_update(data)

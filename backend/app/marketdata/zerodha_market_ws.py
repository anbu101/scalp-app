# backend/app/marketdata/zerodha_market_ws.py

from app.event_bus.audit_logger import write_audit_log

class ZerodhaMarketWS:
    """
    DISABLED.

    Market data WebSocket is AUTHORITATIVELY handled by:
    - ZerodhaTickEngine

    This class is intentionally a NO-OP to avoid
    multiple KiteTicker connections.
    """

    def __init__(self, *args, **kwargs):
        write_audit_log("[MARKET-WS] Disabled (using ZerodhaTickEngine only)")

    def connect(self):
        pass

    def subscribe(self, tokens):
        pass

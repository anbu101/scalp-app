# backend/app/execution/zerodha_ws.py

from kiteconnect import KiteTicker
from app.brokers.zerodha_auth import get_kite
from app.execution.zerodha_order_listener import on_order_update


class ZerodhaWebSocket:
    """
    Zerodha WebSocket for ORDER UPDATES only.

    Auth is reused from get_kite().
    No dependency on zerodha_config.
    """

    def __init__(self):
        kite = get_kite()

        self.kws = KiteTicker(
            kite.api_key,
            kite.access_token,
        )

        # Wire callbacks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.on_order_update = self.on_order_update

    # -------------------------
    # WS lifecycle
    # -------------------------

    def connect(self):
        self.kws.connect(threaded=True)

    def on_connect(self, ws, response):
        print("[ZERODHA-WS] Connected")

    def on_close(self, ws, code, reason):
        print("[ZERODHA-WS] Closed", code, reason)

    def on_error(self, ws, code, reason):
        print("[ZERODHA-WS] Error", code, reason)

    # -------------------------
    # ORDER UPDATES
    # -------------------------

    def on_order_update(self, ws, data):
        on_order_update(data)

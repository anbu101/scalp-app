import time
from typing import Dict

class MockBroker:
    def __init__(self, store=None):
        self.store = store

    def place_market_buy(self, symbol: str, qty: int, price: float, sl: float=None, tp: float=None) -> Dict:
        now = int(time.time())
        order = {
            "time": now,
            "symbol": symbol,
            "side": "BUY",
            "qty": qty,
            "price": price,
            "sl": sl,
            "tp": tp,
            "status": "filled"
        }
        print("[MOCK ORDER PLACED]", order)
        return order

    def place_limit_buy(self, symbol: str, qty: int, price: float, sl: float=None, tp: float=None) -> Dict:
        return self.place_market_buy(symbol, qty, price, sl, tp)

    def cancel_order(self, order_id: str):
        print("[MOCK CANCEL]", order_id)
        return {"cancelled": True}
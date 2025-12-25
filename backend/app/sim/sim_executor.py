import uuid
from typing import List, Dict

class SimExecutor:
    """
    Minimal executor that matches ZerodhaExecutor interface.
    NO logic lives here — only state recording.
    """

    def __init__(self, price_provider):
        self.price_provider = price_provider
        self._positions: Dict[str, Dict] = {}
        self._orders: Dict[str, Dict] = {}

    # -------------------------
    # Orders
    # -------------------------

    def place_buy(self, *, symbol: str, token: int, qty: int):
        price = self.price_provider.get_ltp(symbol)
        order_id = f"SIM-BUY-{uuid.uuid4().hex[:8]}"

        self._positions[symbol] = {
            "tradingsymbol": symbol,
            "quantity": qty,
            "average_price": price,
        }

        return order_id, price, qty

    def place_sl(self, *, symbol: str, qty: int, sl_price: float):
        order_id = f"SIM-SL-{uuid.uuid4().hex[:8]}"
        self._orders[order_id] = {
            "symbol": symbol,
            "type": "SL",
            "price": sl_price,
            "qty": qty,
        }
        return order_id

    def place_exit(self, *, symbol: str, qty: int, reason: str):
        order_id = f"SIM-EXIT-{uuid.uuid4().hex[:8]}"
        self._positions.pop(symbol, None)
        return order_id

    def cancel_order(self, order_id: str):
        self._orders.pop(order_id, None)

    # -------------------------
    # Queries
    # -------------------------

    def get_open_positions(self) -> List[Dict]:
        return list(self._positions.values())

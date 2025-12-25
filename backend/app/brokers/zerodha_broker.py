from typing import Dict, List
from kiteconnect import KiteConnect

from app.brokers.broker_interface import BrokerInterface


class ZerodhaBroker(BrokerInterface):
    def __init__(self, kite: KiteConnect):
        self.kite = kite

    # ---------------- BUY ----------------

    def place_market_buy(self, symbol: str, qty: int) -> str:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=self.kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=self.kite.PRODUCT_MIS,
        )
        return order_id

    # ---------------- SELL (EXIT) ----------------

    def place_market_sell(self, symbol: str, qty: int) -> str:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=self.kite.PRODUCT_MIS,
        )
        return order_id

    # ---------------- POSITIONS ----------------

    def get_net_positions(self) -> Dict[str, int]:
        """
        Returns net quantity per tradingsymbol.
        """
        positions = self.kite.positions().get("net", [])
        pos_map: Dict[str, int] = {}

        for p in positions:
            symbol = p.get("tradingsymbol")
            qty = p.get("quantity", 0)
            if symbol:
                pos_map[symbol] = pos_map.get(symbol, 0) + qty

        return pos_map

    # ---------------- MARKET DATA ----------------

    def get_ltps(self, symbols: List[str]) -> Dict[str, float]:
        """
        Returns LTP per symbol using kite.ltp().
        """
        ltp_data = self.kite.ltp(symbols)
        result: Dict[str, float] = {}

        for sym, payload in ltp_data.items():
            result[sym] = payload.get("last_price")

        return result

    # ---------------- ORDER LOOKUP ----------------

    def get_order(self, order_id: str) -> Dict:
        orders = self.kite.orders()
        for o in orders:
            if o.get("order_id") == order_id:
                return o
        return {}

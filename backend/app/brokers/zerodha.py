# backend/broker/zerodha.py

from typing import Dict
from datetime import datetime

from app.engine.logger import log



class ZerodhaBroker:
    # ----------------------------------------------------------

    def get_positions_by_symbol(self) -> Dict[str, int]:
        """
        Returns net quantity per symbol.
        """
        positions = kite.positions()["net"]
        pos_map: Dict[str, int] = {}

        for p in positions:
            symbol = p["tradingsymbol"]
            qty = p["quantity"]
            pos_map[symbol] = pos_map.get(symbol, 0) + qty

        return pos_map

    # ----------------------------------------------------------

    def place_market_exit(self, symbol: str, qty: int, tag: str) -> str:
        """
        Places MARKET SELL exit.
        """
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_MARKET,
            tag=tag,
        )

        return order_id

    # ----------------------------------------------------------

    def get_order_status(self, order_id: str) -> str:
        """
        Returns: COMPLETE | REJECTED | CANCELLED | OPEN
        """
        orders = kite.orders()

        for o in orders:
            if o["order_id"] == order_id:
                return o["status"]

        return "OPEN"

    # ----------------------------------------------------------

    def get_last_exit_price(self, symbol: str) -> float:
        """
        Best-effort average exit price.
        """
        orders = kite.orders()
        exits = [
            o for o in orders
            if o["tradingsymbol"] == symbol
            and o["transaction_type"] == kite.TRANSACTION_TYPE_SELL
            and o["status"] == "COMPLETE"
        ]

        if not exits:
            return 0.0

        exits.sort(key=lambda x: x["exchange_timestamp"] or datetime.min, reverse=True)
        return exits[0].get("average_price", 0.0)

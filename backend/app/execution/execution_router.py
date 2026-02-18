from app.execution.base_executor import BaseOrderExecutor


class ExecutionRouter(BaseOrderExecutor):
    """
    Broker-agnostic execution router.
    Currently wraps Zerodha executor.
    """

    def __init__(self, underlying_executor: BaseOrderExecutor):
        self._executor = underlying_executor

    # --------------------------------------------------
    # BUY
    # --------------------------------------------------

    def place_buy(self, symbol, token, qty):
        return self._executor.place_buy(symbol, token, qty)

    def get_last_avg_price(self, order_id):
        return self._executor.get_last_avg_price(order_id)

    # --------------------------------------------------
    # GTT
    # --------------------------------------------------

    def place_gtt_oco(self, symbol, qty, sl_price, tp_price):
        return self._executor.place_gtt_oco(
            symbol=symbol,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
        )

    def get_gtts(self):
        return self._executor.get_gtts()

    # --------------------------------------------------
    # SL (legacy safety)
    # --------------------------------------------------

    def place_sl(self, symbol, qty, sl_price):
        return self._executor.place_sl(symbol, qty, sl_price)

    # --------------------------------------------------
    # EXIT
    # --------------------------------------------------

    def place_exit(self, symbol, qty, reason):
        return self._executor.place_exit(
            symbol=symbol,
            qty=qty,
            reason=reason,
        )

    # --------------------------------------------------
    # POSITIONS / ORDERS
    # --------------------------------------------------

    def get_open_positions(self):
        return self._executor.get_open_positions()

    def cancel_order(self, order_id: str):
        return self._executor.cancel_order(order_id)

    def get_orders(self):
        return self._executor.get_orders()

    # --------------------------------------------------
    # SYMBOL RESOLUTION
    # --------------------------------------------------

    def resolve_symbol(self, symbol: str) -> str:
        return self._executor.resolve_symbol(symbol)

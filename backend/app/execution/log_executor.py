from app.execution.base_executor import BaseOrderExecutor


class LogOrderExecutor(BaseOrderExecutor):

    def place_buy(
        self,
        symbol: str,
        token: int,
        qty: int,
        price: float,
        sl_price: float,
        tp_price: float,
    ) -> str:
        print(
            f"[BUY] {symbol} @ {price} | QTY={qty} | SL={sl_price} TP={tp_price}"
        )
        return "LOG_ORDER"

    def place_exit(
        self,
        order_id: str,
        symbol: str,
        qty: int,
        price: float,
        reason: str,
    ):
        print(
            f"[EXIT-{reason}] {symbol} @ {price} | QTY={qty} | ORDER={order_id}"
        )

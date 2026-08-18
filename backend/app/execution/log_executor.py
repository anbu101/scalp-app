from app.execution.base_executor import BaseOrderExecutor


class LogOrderExecutor(BaseOrderExecutor):

    # ── ACC2_PST ── LIMIT BUY (log-only sink)
    def place_limit_buy(self, symbol, qty, price, product="MIS", tag=""):
        print(f"[LOG-EXEC] LIMIT BUY {symbol} x{qty} @{price} product={product}")
        return "LOG-LIMIT-BUY"

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
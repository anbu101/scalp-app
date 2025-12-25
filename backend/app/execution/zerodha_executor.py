from typing import Optional, List, Dict
import time

from kiteconnect import KiteConnect

from app.execution.base_executor import BaseOrderExecutor
from app.config.trading_config import MAX_QTY_PER_ORDER
from app.brokers.zerodha_manager import ZerodhaManager


class ZerodhaOrderExecutor(BaseOrderExecutor):
    """
    Zerodha Order Executor

    IMPORTANT DESIGN RULE:
    - Executor NEVER creates KiteConnect
    - Executor NEVER loads tokens
    - Executor NEVER decides login state
    - Executor only EXECUTES when broker is READY
    """

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager = broker_manager

    # -------------------------
    # INTERNAL HELPERS
    # -------------------------

    def _kite(self) -> Optional[KiteConnect]:
        if not self.broker_manager.is_ready():
            return None
        return self.broker_manager.get_kite()

    # -------------------------
    # BUY (MARKET | NRML READY)
    # -------------------------

    def place_buy(
        self,
        symbol: str,
        token: int,
        qty: int,
    ):
        if qty > MAX_QTY_PER_ORDER:
            raise RuntimeError("Qty exceeds MAX_QTY_PER_ORDER")

        kite = self._kite()
        if not kite:
            print(f"[ZERODHA-DRY-BUY] {symbol} QTY={qty} (BROKER NOT READY)")
            return "DRY_ORDER", 0.0, 0

        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_MARKET,
            product=kite.PRODUCT_NRML,   # 🔒 GTT requires NRML
        )

        print(
            f"[ZERODHA-BUY-PLACED] "
            f"ORDER_ID={order_id} SYMBOL={symbol} QTY={qty}"
        )

        # avg_price unknown initially
        return order_id, 0.0, qty

    # -------------------------
    # AVG PRICE FETCH (NEW)
    # -------------------------

    def get_last_avg_price(self, order_id: str) -> float:
        """
        Poll orders API to get avg fill price.
        Returns 0.0 if not yet available.
        """
        kite = self._kite()
        if not kite:
            return 0.0

        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return float(o.get("average_price") or 0.0)
        except Exception as e:
            print(f"[ZERODHA][WARN] Avg price fetch failed {e}")

        return 0.0

    # -------------------------
    # GTT OCO (SL + TP) — NEW
    # -------------------------

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
    ) -> str:
        """
        Places OCO GTT:
        - One trigger for SL
        - One trigger for TP
        Both place LIMIT SELL orders.
        """

        kite = self._kite()
        if not kite:
            print(f"[ZERODHA-DRY-GTT] {symbol} QTY={qty}")
            return "DRY_GTT"

        # ---- Price buffers (near-market behavior) ----
        sl_limit = round(sl_price * 0.995, 2)   # ~ -0.5%
        tp_limit = round(tp_price * 0.997, 2)   # ~ -0.3%

        try:
            gtt_id = kite.place_gtt(
                trigger_type=kite.GTT_TYPE_OCO,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NFO,
                trigger_values=[sl_price, tp_price],
                last_price=tp_price,  # informational
                orders=[
                    {
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": sl_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                    {
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": tp_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                ],
            )

            print(
                f"[ZERODHA-GTT-PLACED] "
                f"GTT_ID={gtt_id} SYMBOL={symbol} "
                f"SL={sl_price}/{sl_limit} TP={tp_price}/{tp_limit}"
            )

            return str(gtt_id)

        except Exception as e:
            print(f"[ZERODHA][ERROR] GTT placement failed {e}")
            raise

    # -------------------------
    # LEGACY / SAFETY METHODS (UNCHANGED)
    # -------------------------

    def place_sl(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
    ) -> str:
        kite = self._kite()
        if not kite:
            print(f"[ZERODHA-DRY-SL] {symbol} QTY={qty} SL={sl_price}")
            return "DRY_SL_ORDER"

        sl_order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_SLM,
            trigger_price=sl_price,
            product=kite.PRODUCT_MIS,
        )

        print(
            f"[ZERODHA-SL-PLACED] "
            f"ORDER_ID={sl_order_id} SL={sl_price}"
        )

        return sl_order_id

    def place_exit(self, symbol: str, qty: int, reason: str) -> str:
        kite = self._kite()
        if not kite:
            print(f"[ZERODHA-DRY-EXIT] {symbol} QTY={qty} REASON={reason}")
            return "DRY_EXIT_ORDER"

        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_MARKET,
            product=kite.PRODUCT_MIS,
        )

        return order_id

    # -------------------------
    # HELPERS (UNCHANGED)
    # -------------------------

    def cancel_order(self, order_id: str):
        kite = self._kite()
        if not kite:
            print(f"[ZERODHA-DRY-CANCEL] {order_id}")
            return

        kite.cancel_order(
            variety=kite.VARIETY_REGULAR,
            order_id=order_id,
        )

        print(f"[ZERODHA-CANCELLED] ORDER_ID={order_id}")

    def get_orders(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        return kite.orders()

    def get_open_positions(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []

        positions = kite.positions()
        return [
            p for p in positions.get("net", [])
            if p.get("quantity", 0) != 0
        ]

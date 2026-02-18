from typing import Optional
from kiteconnect import KiteConnect
from app.event_bus.audit_logger import write_audit_log


class BBExecutionAdapter:

    def __init__(self, kite: KiteConnect):
        self.kite = kite

    # --------------------------------------------------
    # ENTRY
    # --------------------------------------------------

    def place_market_entry(
        self,
        symbol: str,
        quantity: int,
    ) -> Optional[str]:

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_MARKET,
                product=self.kite.PRODUCT_NRML,
            )

            write_audit_log(f"[BB] Entry order placed {order_id}")
            return order_id

        except Exception as e:
            write_audit_log(f"[BB] Entry order failed: {e}")
            return None

    # --------------------------------------------------
    # SL ORDER
    # --------------------------------------------------

    def place_sl_order(
        self,
        symbol: str,
        quantity: int,
        trigger_price: float,
    ) -> Optional[str]:

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_SLM,
                trigger_price=trigger_price,
                product=self.kite.PRODUCT_NRML,
            )

            write_audit_log(f"[BB] SL order placed {order_id}")
            return order_id

        except Exception as e:
            write_audit_log(f"[BB] SL order failed: {e}")
            return None

    # --------------------------------------------------
    # TP ORDER
    # --------------------------------------------------

    def place_tp_order(
        self,
        symbol: str,
        quantity: int,
        price: float,
    ) -> Optional[str]:

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=price,
                product=self.kite.PRODUCT_NRML,
            )

            write_audit_log(f"[BB] TP order placed {order_id}")
            return order_id

        except Exception as e:
            write_audit_log(f"[BB] TP order failed: {e}")
            return None

    # --------------------------------------------------
    # CANCEL
    # --------------------------------------------------

    def cancel_order(self, order_id: str):
        try:
            self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
            )
        except Exception as e:
            write_audit_log(f"[BB] Cancel failed: {e}")

from typing import Optional, List, Dict
import time

from kiteconnect import KiteConnect

from app.execution.base_executor import BaseOrderExecutor
from app.config.trading_config import MAX_QTY_PER_ORDER
from app.config.strategy_loader import load_strategy_config
from app.brokers.zerodha_manager import ZerodhaManager
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log


class TradingDisabledError(RuntimeError):
    pass


class ZerodhaOrderExecutor(BaseOrderExecutor):
    """
    Zerodha Order Executor (FINAL AUTHORITY)

    HARD RULES:
    - Executor ENFORCES trade_on
    - Executor ENFORCES broker readiness (NO FORCED REFRESH)
    - Executor ENFORCES INSTRUMENT LOT SIZE (AUTHORITATIVE)
    """

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager = broker_manager
        self._instrument_cache: Dict[str, int] = {}

    # -------------------------
    # INTERNAL HELPERS
    # -------------------------

    def _kite(self) -> Optional[KiteConnect]:
        """
        🔒 CRITICAL FIX:
        DO NOT refresh broker here.
        Executor must NOT destroy valid sessions.
        """
        if not self.broker_manager.is_trade_ready():
            return None

        return self.broker_manager.get_trade_kite()

    def _ensure_trading_enabled(self):
        cfg = load_strategy_config()
        if not cfg.get("trade_on", False):
            raise TradingDisabledError("TRADING_DISABLED (executor gate)")

    def _get_lot_size(self, kite: KiteConnect, symbol: str) -> int:
        """
        AUTHORITATIVE lot size resolver.
        Cached after first successful lookup.
        """
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        try:
            instruments = kite.instruments("NFO")
        except Exception as e:
            raise RuntimeError(
                f"INSTRUMENT_FETCH_FAILED SYMBOL={symbol} ERR={e}"
            )

        for inst in instruments:
            if inst.get("tradingsymbol") == symbol:
                lot_size = int(inst.get("lot_size") or 0)
                if lot_size <= 0:
                    break

                self._instrument_cache[symbol] = lot_size
                return lot_size

        raise RuntimeError(f"LOT_SIZE_NOT_FOUND SYMBOL={symbol}")

    # -------------------------
    # BUY (MARKET | NRML)
    # -------------------------

    def place_buy(
        self,
        symbol: str,
        token: int,
        qty: int,
    ):
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")

        if qty > MAX_QTY_PER_ORDER:
            raise RuntimeError("Qty exceeds MAX_QTY_PER_ORDER")

        kite = self._kite()
        if not kite:
            raise RuntimeError(
                f"BROKER_NOT_READY_IN_EXECUTOR SYMBOL={symbol}"
            )

        # -------------------------
        # 🔒 LOT SIZE ENFORCEMENT (AUTHORITATIVE)
        # -------------------------
        lot_size = self._get_lot_size(kite, symbol)

        if qty % lot_size != 0:
            raise RuntimeError(
                f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_MARKET,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(
            f"[ZERODHA-BUY-PLACED] "
            f"ORDER_ID={order_id} SYMBOL={symbol} QTY={qty}"
        )

        return order_id, 0.0, qty

    # -------------------------
    # AVG PRICE FETCH
    # -------------------------

    def get_last_avg_price(self, order_id: str) -> float:
        kite = self._kite()
        if not kite:
            return 0.0

        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return float(o.get("average_price") or 0.0)
        except Exception as e:
            write_audit_log(
                f"[ZERODHA][WARN] Avg price fetch failed ERR={e}"
            )

        return 0.0

    # -------------------------
    # GTT OCO (SL + TP)
    # -------------------------

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
    ) -> str:
        self._ensure_trading_enabled()

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT")

        ltp = LTPStore.get(symbol)
        if ltp is None:
            raise RuntimeError("LTP unavailable for GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger = r(sl_price)
        tp_trigger = r(tp_price)

        sl_limit = r(sl_price * 0.995)
        tp_limit = r(tp_price * 0.997)

        safe_last_price = round(
            (sl_trigger + tp_trigger) / 2,
            2
        )

        if not (sl_trigger < safe_last_price < tp_trigger):
            raise RuntimeError(
                f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price} TP={tp_trigger}"
            )

        gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[sl_trigger, tp_trigger],
            last_price=safe_last_price,
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

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] "
            f"GTT_ID={gtt_id} SYMBOL={symbol} "
            f"SL={sl_trigger}/{sl_limit} "
            f"TP={tp_trigger}/{tp_limit}"
        )

        return str(gtt_id)

    # -------------------------
    # LEGACY / SAFETY
    # -------------------------

    def cancel_order(self, order_id: str):
        kite = self._kite()
        if not kite:
            return

        kite.cancel_order(
            variety=kite.VARIETY_REGULAR,
            order_id=order_id,
        )

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

    # -------------------------
    # ABSTRACT SAFETY
    # -------------------------

    def place_sl(self, symbol: str, qty: int, sl_price: float) -> str:
        raise RuntimeError("place_sl() not supported in GTT-only mode")

    def place_exit(self, symbol: str, qty: int, reason: str) -> str:
        raise RuntimeError("place_exit() not supported in GTT-only mode")

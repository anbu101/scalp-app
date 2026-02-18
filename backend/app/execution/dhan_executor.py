from typing import Tuple, List, Dict
from app.execution.base_executor import BaseOrderExecutor


class DhanOrderExecutor(BaseOrderExecutor):
    """
    Dhan broker executor (SKELETON)

    ⚠ Not implemented yet.
    Only created to enable multi-broker architecture safely.
    """

    def __init__(self):
        # Later: inject Dhan client / auth manager
        pass

    # -------------------------
    # BUY
    # -------------------------

    def place_buy(
        self,
        symbol: str,
        token: int,
        qty: int,
    ) -> Tuple[str, float, int]:
        raise NotImplementedError("Dhan place_buy not implemented")

    def get_last_avg_price(self, order_id: str) -> float:
        raise NotImplementedError("Dhan get_last_avg_price not implemented")

    # -------------------------
    # GTT
    # -------------------------

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
    ) -> str:
        raise NotImplementedError("Dhan place_gtt_oco not implemented")

    # -------------------------
    # SL
    # -------------------------

    def place_sl(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
    ) -> str:
        raise NotImplementedError("Dhan place_sl not implemented")

    # -------------------------
    # EXIT
    # -------------------------

    def place_exit(
        self,
        symbol: str,
        qty: int,
        reason: str,
    ):
        raise NotImplementedError("Dhan place_exit not implemented")

    # -------------------------
    # RECON
    # -------------------------

    def get_open_positions(self) -> List[Dict]:
        raise NotImplementedError("Dhan get_open_positions not implemented")

    def cancel_order(self, order_id: str):
        raise NotImplementedError("Dhan cancel_order not implemented")

    def get_orders(self) -> List[Dict]:
        raise NotImplementedError("Dhan get_orders not implemented")

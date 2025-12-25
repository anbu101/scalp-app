from abc import ABC, abstractmethod
from typing import Tuple, List, Dict


class BaseOrderExecutor(ABC):

    @abstractmethod
    def place_buy(
        self,
        symbol: str,
        token: int,
        qty: int,
    ) -> Tuple[str, float, int]:
        """
        Returns:
            (buy_order_id, avg_price, filled_qty)
        """
        pass

    # -------------------------
    # NEW (GTT ONLY FLOW)
    # -------------------------

    @abstractmethod
    def get_last_avg_price(self, order_id: str) -> float:
        """
        Returns avg fill price if available, else 0.0
        """
        pass

    @abstractmethod
    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
    ) -> str:
        """
        Places OCO GTT and returns gtt_id
        """
        pass

    # -------------------------
    # LEGACY / BACKWARD SAFETY
    # -------------------------

    @abstractmethod
    def place_sl(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
    ) -> str:
        pass

    @abstractmethod
    def place_exit(
        self,
        symbol: str,
        qty: int,
        reason: str,
    ):
        pass

    @abstractmethod
    def get_open_positions(self) -> List[Dict]:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str):
        pass

    @abstractmethod
    def get_orders(self) -> List[Dict]:
        pass

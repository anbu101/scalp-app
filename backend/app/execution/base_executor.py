from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class BaseOrderExecutor(ABC):

    # ── ACC2_PST ── LIMIT-BUY primitive. PST previously reached into
    # ZerodhaOrderExecutor._relay_call() directly for its resting TP order,
    # which no other executor implements and which blocked PST from binding
    # to a secondary account. Promoted to the public contract so every
    # executor supplies its own (relay-aware) implementation.
    # product: "MIS" (intraday) | "NRML" (carry) — brokers map internally.
    @abstractmethod
    def place_limit_buy(self, symbol: str, qty: int, price: float,
                        product: str = "MIS", tag: str = "") -> Optional[str]:
        ...

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

    @abstractmethod
    def get_gtts(self) -> List[Dict]:
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
    def resolve_symbol(self, symbol: str) -> str:
        """
        Converts canonical symbol to broker-specific symbol.
        For Zerodha: return symbol as-is.
        For Dhan: convert to Dhan format.
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
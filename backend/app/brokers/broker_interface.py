from abc import ABC, abstractmethod
from typing import Dict, List


class BrokerInterface(ABC):

    # ---------------- ENTRY ----------------

    @abstractmethod
    def place_market_buy(self, symbol: str, qty: int) -> str:
        """
        Place market BUY order.
        """
        pass

    # ---------------- EXIT ----------------

    @abstractmethod
    def place_market_sell(self, symbol: str, qty: int) -> str:
        """
        Place market SELL exit order.
        """
        pass

    # ---------------- POSITIONS ----------------

    @abstractmethod
    def get_net_positions(self) -> Dict[str, int]:
        """
        Returns net quantity per symbol.
        Example: { "NIFTY25JAN18000CE": 50 }
        """
        pass

    # ---------------- MARKET DATA ----------------

    @abstractmethod
    def get_ltps(self, symbols: List[str]) -> Dict[str, float]:
        """
        Returns last traded price per symbol.
        Example: { "NIFTY25JAN18000CE": 128.5 }
        """
        pass

    # ---------------- ORDERS ----------------

    @abstractmethod
    def get_order(self, order_id: str) -> Dict:
        """
        Returns raw order dict from broker.
        Must include at least 'status'.
        """
        pass

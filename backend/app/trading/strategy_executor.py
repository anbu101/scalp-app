from typing import List

from app.trading.trade_state_manager import TradeStateManager
from app.brokers.broker_interface import BrokerInterface


class StrategyExecutor:
    """
    Executes strategy for up to:
    - 2 CE trades
    - 2 PE trades

    Each trade is independent.
    """

    def __init__(
        self,
        broker: BrokerInterface,
        ce_symbols: List[str],
        pe_symbols: List[str],
        qty: int,
        tp_points: float,
        sl_points: float,
    ):
        self.broker = broker

        # -------------------------
        # CE slots (max 2)
        # -------------------------
        self.ce_trades: List[TradeStateManager] = [
            TradeStateManager(
                symbol=symbol,
                qty=qty,
                tp_points=tp_points,
                sl_points=sl_points,
            )
            for symbol in ce_symbols[:2]
        ]

        # -------------------------
        # PE slots (max 2)
        # -------------------------
        self.pe_trades: List[TradeStateManager] = [
            TradeStateManager(
                symbol=symbol,
                qty=qty,
                tp_points=tp_points,
                sl_points=sl_points,
            )
            for symbol in pe_symbols[:2]
        ]

    # ---------- SIGNAL ENTRY ----------

    def on_buy_ce_signal(self):
        for trade in self.ce_trades:
            if trade.can_take_trade():
                trade.on_buy_signal(self.broker)

    def on_buy_pe_signal(self):
        for trade in self.pe_trades:
            if trade.can_take_trade():
                trade.on_buy_signal(self.broker)

    # ---------- ORDER UPDATES ----------

    def on_order_update(self, order_update: dict):
        """
        order_update example:
        {
            "order_id": "...",
            "status": "COMPLETE",
            "avg_price": 220.0,
            "type": "BUY" | "OCO"
        }
        """

        for trade in self.ce_trades + self.pe_trades:
            if order_update["order_id"] == trade.state.buy_order_id:
                trade.on_buy_filled(
                    avg_price=order_update["avg_price"],
                    broker=self.broker,
                )

            elif order_update["order_id"] == trade.state.oco_order_id:
                trade.on_exit_filled()

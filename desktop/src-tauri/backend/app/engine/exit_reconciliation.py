# backend/app/engine/exit_reconciliation.py

import time
from datetime import datetime
from typing import Dict

from app.engine.trade_store import TradeStore
from app.engine.price_cache import PriceCache
from app.engine.logger import log

from app.brokers.broker_interface import BrokerInterface


LOOP_INTERVAL_SECONDS = 1


class ExitReconciliationEngine:
    def __init__(self, broker: BrokerInterface):
        self.trade_store = TradeStore()
        self.broker = broker
        self.price_cache = PriceCache()  # ✅ FIX: broker injected

    # -------------------------------------------------------------

    def run_forever(self):
        log("[EXIT] Exit reconciliation engine started")
        while True:
            try:
                self.run_once()
            except Exception as e:
                log(f"[EXIT][ERROR] {e}")
            time.sleep(LOOP_INTERVAL_SECONDS)

    # -------------------------------------------------------------

    def run_once(self):
        trades = self.trade_store.get_open_trades()
        if not trades:
            return

        symbols = list({t["symbol"] for t in trades})

        # Fetch once per loop
        ltp_map = self.price_cache.get_ltps(symbols)
        broker_positions = self.broker.get_net_positions()

        for trade in trades:
            self._process_trade(trade, ltp_map, broker_positions)

    # -------------------------------------------------------------

    def _process_trade(
        self,
        trade: Dict,
        ltp_map: Dict[str, float],
        broker_positions: Dict[str, int],
    ):
        symbol = trade["symbol"]

        if trade["status"] == "EXIT_CONFIRMED":
            return

        broker_qty = broker_positions.get(symbol, 0)

        # Broker already closed position
        if broker_qty == 0:
            self._finalize_exit(trade, "BROKER_ALREADY_CLOSED")
            return

        # Pending exit reconciliation
        if trade["status"] == "EXIT_PENDING":
            self._reconcile_pending_exit(trade, broker_qty)
            return

        # Fresh SL / TP evaluation
        ltp = ltp_map.get(symbol)
        if ltp is None:
            return

        if ltp <= trade["sl_price"]:
            exit_reason = "SL"
        elif ltp >= trade["tp_price"]:
            exit_reason = "TP"
        else:
            return

        log(f"[EXIT] {exit_reason} hit | {symbol} | LTP={ltp}")
        self._place_exit(trade, exit_reason)

    # -------------------------------------------------------------

    def _place_exit(self, trade: Dict, exit_reason: str):
        if trade.get("exit_order_id"):
            return  # hard lock

        symbol = trade["symbol"]
        qty = trade["qty"]

        order_id = self.broker.place_market_sell(symbol, qty)

        trade["status"] = "EXIT_PENDING"
        trade["exit_order_id"] = order_id
        trade["exit_reason"] = exit_reason

        self.trade_store.update_trade(trade)

        log(f"[EXIT] Order placed | {symbol} | order_id={order_id}")

    # -------------------------------------------------------------

    def _reconcile_pending_exit(self, trade: Dict, broker_qty: int):
        symbol = trade["symbol"]
        order_id = trade.get("exit_order_id")

        if broker_qty == 0:
            self._finalize_exit(trade, "POSITION_CLOSED_EXTERNALLY")
            return

        if not order_id:
            return

        order = self.broker.get_order(order_id)
        status = order.get("status")

        if status == "COMPLETE":
            self._finalize_exit(trade, "ORDER_FILLED")

        elif status in ("REJECTED", "CANCELLED"):
            log(f"[EXIT] Exit order failed | {symbol} | retrying")
            trade["exit_order_id"] = None
            trade["status"] = "OPEN"
            self.trade_store.update_trade(trade)

    # -------------------------------------------------------------

    def _finalize_exit(self, trade: Dict, reason: str):
        if trade["status"] == "EXIT_CONFIRMED":
            return

        trade["status"] = "EXIT_CONFIRMED"
        trade["closed_at"] = datetime.utcnow().isoformat()
        trade["final_exit_price"] = 0.0
        trade["exit_reason"] = reason

        self.trade_store.update_trade(trade)

        log(f"[EXIT] Confirmed | {trade['symbol']} | reason={reason}")

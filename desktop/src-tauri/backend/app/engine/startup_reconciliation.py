from app.engine.trade_store import TradeStore
from app.engine.logger import log


class StartupReconciliation:
    def __init__(self, broker):
        self.broker = broker
        self.trade_store = TradeStore()

    def run(self):
        log("[STARTUP] Reconciling broker state")

        trades = self.trade_store.get_open_trades()
        if not trades:
            return

        positions = self.broker.get_net_positions()  # ONE REST CALL
        orders = self.broker.get_orders()            # ONE REST CALL

        pos_map = {p["tradingsymbol"]: p["quantity"] for p in positions}
        order_map = {o["order_id"]: o for o in orders}

        for trade in trades:
            symbol = trade["symbol"]
            broker_qty = pos_map.get(symbol, 0)

            # Position already closed
            if broker_qty == 0:
                trade["status"] = "EXIT_CONFIRMED"
                trade["exit_reason"] = "STARTUP_RECONCILE_CLOSED"
                self.trade_store.update_trade(trade)
                continue

            # Exit order pending
            exit_oid = trade.get("exit_order_id")
            if exit_oid and exit_oid in order_map:
                o = order_map[exit_oid]
                if o["status"] == "COMPLETE":
                    trade["status"] = "EXIT_CONFIRMED"
                    trade["exit_reason"] = "STARTUP_RECONCILE_EXIT_FILLED"
                    self.trade_store.update_trade(trade)

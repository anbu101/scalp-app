import time
from typing import Dict

from app.event_bus.audit_logger import write_audit_log
from app.trading.trade_state_manager import TradeStateManager
from app.execution.base_executor import BaseOrderExecutor


LOOP_INTERVAL = 60  # seconds


class BrokerReconciliationJob:
    """
    Minimal safety reconciliation between Broker and DB / Slots.
    Broker is SOURCE OF TRUTH.

    GTT-ONLY MODEL:
    - NO SL-M orders
    - NO order placement here
    - Position presence == trade open
    """

    def __init__(self, executor: BaseOrderExecutor):
        self.executor = executor

    # -------------------------------------------------

    def run_forever(self):
        write_audit_log("[RECON] Broker reconciliation started")
        while True:
            try:
                self.run_once()
            except Exception as e:
                write_audit_log(f"[RECON][ERROR] {e}")
            time.sleep(LOOP_INTERVAL)

    # -------------------------------------------------

    def run_once(self):
        broker_positions = self._get_broker_positions()
        slot_map = TradeStateManager._REGISTRY

        # -------------------------------------------------
        # 1️⃣ Broker OPEN but DB / Slot missing
        # -------------------------------------------------
        for symbol, pos in broker_positions.items():
            if pos["qty"] == 0:
                continue

            slot = self._find_slot_by_symbol(symbol)

            if not slot or not slot.active_trade:
                write_audit_log(
                    f"[RECON][RECOVER] Broker position without DB trade "
                    f"SYMBOL={symbol} QTY={pos['qty']}"
                )
                self._recover_trade(symbol, pos)
                continue

        # -------------------------------------------------
        # 2️⃣ DB OPEN but Broker CLOSED
        # -------------------------------------------------
        for slot in slot_map.values():
            trade = slot.active_trade
            if not trade:
                continue

            broker_qty = broker_positions.get(trade.symbol, {}).get("qty", 0)

            if broker_qty == 0:
                write_audit_log(
                    f"[RECON][FORCE_CLOSE] DB trade open but broker closed "
                    f"SLOT={slot.name} SYMBOL={trade.symbol}"
                )
                slot._close_trade("BROKER_RECON")

        # -------------------------------------------------
        # 3️⃣ NO SL RECONCILIATION (GTT ONLY)
        # -------------------------------------------------
        # Intentionally empty
        # SL / TP protection is handled exclusively via GTT

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _get_broker_positions(self) -> Dict[str, Dict]:
        positions = self.executor.get_open_positions()
        out = {}

        for p in positions:
            out[p["tradingsymbol"]] = {
                "qty": abs(p["quantity"]),
                "avg_price": p.get("average_price"),
            }

        return out

    def _find_slot_by_symbol(self, symbol: str):
        for slot in TradeStateManager._REGISTRY.values():
            if slot.active_trade and slot.active_trade.symbol == symbol:
                return slot
        return None

    def _recover_trade(self, symbol: str, pos: Dict):
        """
        Minimal recovery:
        - Log only
        - Manual intervention required
        - NEVER place SL / EXIT automatically
        """
        write_audit_log(
            f"[RECON][MANUAL_REQUIRED] "
            f"Recovered broker position needs manual attention "
            f"SYMBOL={symbol} QTY={pos['qty']}"
        )

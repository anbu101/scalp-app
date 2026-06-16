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

    MULTI-STRATEGY SAFE
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
        # 🔒 SAFE broker fetch
        try:
            broker_positions = self._get_broker_positions()
        except Exception as e:
            write_audit_log(
                f"[RECON][WARN] Broker fetch failed, retry next cycle: {e}"
            )
            return

        # =====================================================
        # 🔥 MULTI-STRATEGY SAFE ITERATION (CRITICAL FIX)
        # =====================================================

        # -------------------------------------------------
        # 1️⃣ Broker OPEN but DB / Slot missing
        # -------------------------------------------------
        for symbol, pos in broker_positions.items():
            if pos["qty"] == 0:
                continue

            slot = self._find_slot_by_symbol(symbol)

            if not slot or not slot.active_trade:
                self._recover_trade(symbol, pos)
                continue

        # -------------------------------------------------
        # 2️⃣ DB OPEN but Broker CLOSED
        # -------------------------------------------------
        for strategy_slots in TradeStateManager._REGISTRY.values():
            for slot in strategy_slots.values():

                trade = slot.active_trade
                if not trade:
                    continue

                broker_qty = broker_positions.get(trade.symbol, {}).get("qty", 0)

                if broker_qty == 0:
                    write_audit_log(
                        f"[RECON][FORCE_CLOSE] "
                        f"STRATEGY={slot.strategy_id} "
                        f"SLOT={slot.name} SYMBOL={trade.symbol}"
                    )
                    # FIX: TradeStateManager has no _close_trade(); the correct
                    # method is _force_exit(reason). The old name had been
                    # throwing AttributeError every time this branch was hit,
                    # caught by run_forever()'s wrapper and logged as
                    # [RECON][ERROR] '..._close_trade' — meaning this entire
                    # "DB open but broker flat" reconciliation has NEVER actually
                    # closed anything.
                    #
                    # SAFETY: we only reach here when broker_qty == 0 (the broker
                    # shows the position already flat). _force_exit() begins with
                    # a position-verify; finding the position flat, it takes its
                    # ALREADY_FLAT path — closing the DB row WITHOUT sending any
                    # order. So no live order is placed in the normal recon case.
                    # "BROKER_RECON" is not in _force_exit's allowed_reasons and
                    # normalises to BROKER_EXIT (expect a [EXIT_REASON_NORMALIZED]
                    # log line); kept as-is for call-site traceability.
                    slot._force_exit("BROKER_RECON")

        # -------------------------------------------------
        # 3️⃣ NO SL RECONCILIATION (GTT ONLY)
        # -------------------------------------------------
        # Intentionally empty

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
        """
        Search across ALL strategies.
        """
        for strategy_slots in TradeStateManager._REGISTRY.values():
            for slot in strategy_slots.values():
                if (
                    slot.active_trade
                    and slot.active_trade.symbol == symbol
                ):
                    return slot
        return None

    def _recover_trade(self, symbol: str, pos: Dict):
        """
        Minimal recovery:
        - Log only
        - Manual intervention required
        - NEVER place SL / EXIT automatically
        """
        #write_audit_log(
            #f"[RECON][MANUAL_REQUIRED] "
            #f"Recovered broker position needs manual attention "
            #f"SYMBOL={symbol} QTY={pos['qty']}"
        #)
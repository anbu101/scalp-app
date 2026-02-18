import asyncio
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log


RECONCILE_INTERVAL_SEC = 10


async def gtt_reconciliation_loop(strategy_id: str):
    write_audit_log(
        f"[RECON] GTT reconciliation loop started (strategy={strategy_id})"
    )

    while True:
        strategy_slots = TradeStateManager._REGISTRY.get(strategy_id, {})

        for mgr in strategy_slots.values():
            try:
                mgr.reconcile_with_broker()
            except Exception as e:
                write_audit_log(
                    f"[RECON][GTT][ERROR] "
                    f"STRATEGY={strategy_id} "
                    f"SLOT={mgr.name} ERR={e}"
                )

        await asyncio.sleep(RECONCILE_INTERVAL_SEC)


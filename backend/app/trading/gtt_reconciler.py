import asyncio
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log


RECONCILE_INTERVAL_SEC = 10


async def gtt_reconciliation_loop():
    write_audit_log("[RECON] GTT reconciliation loop started")

    while True:
        for mgr in TradeStateManager._REGISTRY.values():
            try:
                mgr.reconcile_with_broker()
            except Exception as e:
                write_audit_log(
                    f"[RECON][GTT][ERROR] "
                    f"SLOT={mgr.name} ERR={e}"
                )

        await asyncio.sleep(RECONCILE_INTERVAL_SEC)

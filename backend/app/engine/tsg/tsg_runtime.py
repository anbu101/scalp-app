# backend/app/engine/tsg/tsg_runtime.py
#
# TSG_V1 — Runtime Entrypoint (mirrors ic_runtime.py; LD10 Phase 1)
# ============================================================================
# Launched from api_server startup behind the STRATEGIES flag + license
# gate. Builds the TsgManager + TsgEngine singletons; registers the kill
# adapter (LD7). FAIL-CLOSED BOOTSTRAP (house doctrine): the engine starts
# even while the broker is not ready; the EXECUTOR is pre-built even for
# PAPER so a mid-session PAPER→LIVE flip never needs a restart; a light
# supervision loop re-attaches a late executor.
# ============================================================================

import asyncio
from typing import Optional

from app.event_bus.audit_logger import write_audit_log

from app.engine.tsg.tsg_manager import TsgManager, STRATEGY_ID
from app.engine.tsg.tsg_engine import TsgEngine

_MANAGER: Optional[TsgManager] = None
_ENGINE: Optional[TsgEngine] = None


def get_tsg_manager() -> Optional[TsgManager]:
    return _MANAGER


def get_tsg_engine() -> Optional[TsgEngine]:
    return _ENGINE


async def tsg_v1_runtime(broker_manager, *args, **kwargs):
    """Async entrypoint. Never raises out — a runtime crash must not take
    the server down; it logs, alerts, and the strategy is simply absent."""
    global _MANAGER, _ENGINE
    try:
        await asyncio.sleep(0)
        if _MANAGER is not None:
            write_audit_log("[TSG][RUNTIME] already initialized — ignoring")
            return

        executor = None
        try:
            from app.execution.zerodha_executor import ZerodhaOrderExecutor
            executor = get_executor_for_strategy('TSG_V1')
            write_audit_log("[TSG][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[TSG][RUNTIME] executor build failed ({e!r}) "
                            f"— PAPER unaffected; LIVE will alert at entry")

        _MANAGER = TsgManager(executor=executor)
        _ENGINE = TsgEngine(_MANAGER, broker_manager)
        _ENGINE.start()

        # LD7: register with the kill framework
        try:
            from app.execution import kill_switch
            kill_switch.register_adapter(STRATEGY_ID,
                                         lambda: _MANAGER.kill_all())
            write_audit_log("[TSG][RUNTIME] kill adapter registered")
        except Exception as e:
            write_audit_log(f"[TSG][RUNTIME] kill adapter failed: {e!r}")

        write_audit_log("[TSG][RUNTIME] TSG_V1 runtime up (engine=on)")

        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.zerodha_executor import \
                        ZerodhaOrderExecutor
                    _MANAGER.attach_executor(
                        get_executor_for_strategy('TSG_V1'))
                    write_audit_log("[TSG][RUNTIME] executor re-attached")
                except Exception:
                    pass
    except Exception as e:
        write_audit_log(f"[TSG][RUNTIME][FATAL] {e!r}")
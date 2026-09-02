# backend/app/engine/brk/brk_runtime.py
#
# ── BRK_V1 RUNTIME ── async entrypoint (TSG pattern). Fence BRK_V1_LIVE_20260902.
# Never raises out; kill adapter registered at boot; executor re-attach loop.

from __future__ import annotations

import asyncio
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.engine.brk.brk_manager import BrkManager, STRATEGY_ID
from app.engine.brk.brk_engine import BrkEngine

_MANAGER: Optional[BrkManager] = None
_ENGINE: Optional[BrkEngine] = None


def get_brk_manager() -> Optional[BrkManager]:
    return _MANAGER


def get_brk_engine() -> Optional[BrkEngine]:
    return _ENGINE


async def brk_v1_runtime(broker_manager, *args, **kwargs):
    global _MANAGER, _ENGINE
    try:
        await asyncio.sleep(0)
        if _MANAGER is not None:
            write_audit_log("[BRK][RUNTIME] already initialized — ignoring")
            return
        executor = None
        try:
            from app.execution.executor_factory import get_executor_for_strategy
            executor = get_executor_for_strategy(STRATEGY_ID)
            write_audit_log("[BRK][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[BRK][RUNTIME] executor build failed ({e!r}) "
                            f"— PAPER unaffected; LIVE will alert at entry")
        _MANAGER = BrkManager(executor=executor)
        _ENGINE = BrkEngine(_MANAGER, broker_manager)
        _MANAGER.quote_fn = lambda s: (_ENGINE._quote_many([s]) or {}).get(s)
        _ENGINE.start()
        try:
            from app.execution import kill_switch
            kill_switch.register_adapter(STRATEGY_ID,
                                         lambda: _MANAGER.kill_all())
            write_audit_log("[BRK][RUNTIME] kill adapter registered")
        except Exception as e:
            write_audit_log(f"[BRK][RUNTIME] kill adapter failed: {e!r}")
        write_audit_log("[BRK][RUNTIME] BRK_V1 runtime up (engine=on)")
        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.executor_factory import \
                        get_executor_for_strategy
                    _MANAGER.attach_executor(
                        get_executor_for_strategy(STRATEGY_ID))
                    write_audit_log("[BRK][RUNTIME] executor re-attached")
                except Exception:
                    pass
    except Exception as e:
        write_audit_log(f"[BRK][RUNTIME][FATAL] {e!r}")
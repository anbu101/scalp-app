# backend/app/engine/emc/emc_runtime.py
#
# ── EMC_V1 RUNTIME ── async entrypoint (ORB/BRK pattern). Fence: EMC_V1_LIVE_20261011
# Never raises out; kill adapter registered at boot; executor re-attach loop.
# Restart restore (open row, day counters) happens inside the engine's boot,
# after the day's config is read.

from __future__ import annotations

import asyncio
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.engine.emc.emc_manager import EmcManager, STRATEGY_ID
from app.engine.emc.emc_engine import EmcEngine

_MANAGER: Optional[EmcManager] = None
_ENGINE: Optional[EmcEngine] = None


def get_emc_manager() -> Optional[EmcManager]:
    return _MANAGER


def get_emc_engine() -> Optional[EmcEngine]:
    return _ENGINE


async def emc_v1_runtime(broker_manager, *args, **kwargs):
    global _MANAGER, _ENGINE
    try:
        await asyncio.sleep(0)
        if _MANAGER is not None:
            write_audit_log("[EMC][RUNTIME] already initialized — ignoring")
            return
        executor = None
        try:
            from app.execution.executor_factory import get_executor_for_strategy
            executor = get_executor_for_strategy(STRATEGY_ID)
            write_audit_log("[EMC][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[EMC][RUNTIME] executor build failed ({e!r}) — PAPER unaffected; LIVE will alert "
                            f"at entry")
        _MANAGER = EmcManager(executor=executor)
        _ENGINE = EmcEngine(_MANAGER, broker_manager)
        _ENGINE.start()
        try:
            from app.execution import kill_switch
            kill_switch.register_adapter(STRATEGY_ID, lambda: _MANAGER.kill_adapter())
            write_audit_log("[EMC][RUNTIME] kill adapter registered")
        except Exception as e:
            write_audit_log(f"[EMC][RUNTIME] kill adapter failed: {e!r}")
        write_audit_log("[EMC][RUNTIME] EMC_V1 runtime up (engine=on)")
        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.executor_factory import get_executor_for_strategy
                    _MANAGER.attach_executor(get_executor_for_strategy(STRATEGY_ID))
                    write_audit_log("[EMC][RUNTIME] executor re-attached")
                except Exception:
                    pass
    except Exception as e:
        write_audit_log(f"[EMC][RUNTIME][FATAL] {e!r}")
        try:
            from app.api.telegram_api import notify_system_alert
            notify_system_alert({"message": f"EMC_V1 runtime died: {e!r}", "severity": "error"})
        except Exception:
            pass

# backend/app/engine/orb/orb_runtime.py
#
# ── ORB_V1 RUNTIME ── async entrypoint (BRK/TSG pattern). Fence: ORB_LIVE_20260903
# Never raises out; kill adapter registered at boot; executor re-attach loop;
# resume_from_db BEFORE the engine starts so a mid-day restart warm-replays.

from __future__ import annotations

import asyncio
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.engine.orb.orb_manager import OrbManager, STRATEGY_ID
from app.engine.orb.orb_engine import OrbEngine

_MANAGER: Optional[OrbManager] = None
_ENGINE: Optional[OrbEngine] = None


def get_orb_manager() -> Optional[OrbManager]:
    return _MANAGER


def get_orb_engine() -> Optional[OrbEngine]:
    return _ENGINE


async def orb_v1_runtime(broker_manager, *args, **kwargs):
    global _MANAGER, _ENGINE
    try:
        await asyncio.sleep(0)
        if _MANAGER is not None:
            write_audit_log("[ORB][RUNTIME] already initialized — ignoring")
            return
        executor = None
        try:
            from app.execution.executor_factory import get_executor_for_strategy
            executor = get_executor_for_strategy(STRATEGY_ID)
            write_audit_log("[ORB][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[ORB][RUNTIME] executor build failed ({e!r}) "
                            f"— PAPER unaffected; LIVE will alert at entry")
        _MANAGER = OrbManager(executor=executor)
        _MANAGER.resume_from_db()
        _ENGINE = OrbEngine(_MANAGER, broker_manager)
        _ENGINE.start()
        try:
            from app.execution import kill_switch
            kill_switch.register_adapter(STRATEGY_ID,
                                         lambda: _MANAGER.kill_all())
            write_audit_log("[ORB][RUNTIME] kill adapter registered")
        except Exception as e:
            write_audit_log(f"[ORB][RUNTIME] kill adapter failed: {e!r}")
        write_audit_log("[ORB][RUNTIME] ORB_V1 runtime up (engine=on)")
        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.executor_factory import \
                        get_executor_for_strategy
                    _MANAGER.attach_executor(
                        get_executor_for_strategy(STRATEGY_ID))
                    write_audit_log("[ORB][RUNTIME] executor re-attached")
                except Exception:
                    pass
    except Exception as e:
        write_audit_log(f"[ORB][RUNTIME][FATAL] {e!r}")

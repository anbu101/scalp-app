# backend/app/engine/lvb/lvb_runtime.py
#
# ── LVB_V1_20261023 ── async entrypoint (EMC/ORB pattern). Never raises out;
# kill adapter registered at boot; executor re-attach loop. Restart restore
# (open trades, stale-day expiry) happens in the engine's first in-window tick.

from __future__ import annotations

import asyncio
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.engine.lvb.lvb_core import STRATEGY_ID
from app.engine.lvb.lvb_repo import LvbRepo
from app.engine.lvb.lvb_manager import LvbManager
from app.engine.lvb.lvb_engine import LvbEngine

_MANAGER: Optional[LvbManager] = None
_ENGINE: Optional[LvbEngine] = None
_REPO: Optional[LvbRepo] = None


def get_lvb_manager() -> Optional[LvbManager]:
    return _MANAGER


def get_lvb_engine() -> Optional[LvbEngine]:
    return _ENGINE


def get_lvb_repo() -> LvbRepo:
    """The routes need the table even when the runtime never launched
    (strategy disabled / unlicensed) so conditions can still be listed."""
    global _REPO
    if _REPO is None:
        _REPO = LvbRepo()
    return _REPO


async def lvb_v1_runtime(broker_manager, *args, **kwargs):
    global _MANAGER, _ENGINE
    try:
        await asyncio.sleep(0)
        if _MANAGER is not None:
            write_audit_log("[LVB][RUNTIME] already initialized — ignoring")
            return
        executor = None
        try:
            from app.execution.executor_factory import get_executor_for_strategy
            executor = get_executor_for_strategy(STRATEGY_ID)
            write_audit_log("[LVB][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[LVB][RUNTIME] executor build failed ({e!r}) — PAPER unaffected; LIVE alerts at entry")
        repo = get_lvb_repo()
        _MANAGER = LvbManager(repo, executor=executor)
        _ENGINE = LvbEngine(_MANAGER, repo, broker_manager)
        _ENGINE.start()
        try:
            from app.execution import kill_switch
            kill_switch.register_adapter(STRATEGY_ID, lambda: _MANAGER.kill_adapter())
            write_audit_log("[LVB][RUNTIME] kill adapter registered")
        except Exception as e:
            write_audit_log(f"[LVB][RUNTIME] kill adapter failed: {e!r}")
        write_audit_log("[LVB][RUNTIME] LVB_V1 runtime up (engine=on)")
        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.executor_factory import get_executor_for_strategy
                    _MANAGER.attach_executor(get_executor_for_strategy(STRATEGY_ID))
                    write_audit_log("[LVB][RUNTIME] executor re-attached")
                except Exception:
                    pass
    except Exception as e:
        write_audit_log(f"[LVB][RUNTIME][FATAL] {e!r}")
        try:
            from app.api.telegram_api import notify_system_alert
            notify_system_alert({"message": f"LVB_V1 runtime died: {e!r}", "severity": "error"})
        except Exception:
            pass

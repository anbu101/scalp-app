# backend/app/engine/ic/ic_runtime.py
#
# IC (shared V1/V2) — Runtime Entrypoint
# ============================================================================
# ── IC_SPLIT (2026-08-04) ── ONE runtime PER STRATEGY ("IC_V1" | "IC_V2"),
# launched from api_server startup behind each strategy's enabled flag +
# license gate (BB_ENGINE_REGISTRY precedent):
#
#     for sid in ("IC_V1", "IC_V2"):
#         if STRATEGIES.get(sid, {}).get("enabled", False) and \
#                 license_state.license_allows_strategy(sid):
#             asyncio.create_task(ic_runtime(zerodha_manager, sid))
#
# Each instance owns its OWN ICGroupManager, ICEngine, ICGTTMonitor triple in
# IC_REGISTRY[sid]; accessors take the strategy id explicitly — there is no
# ambient "the IC strategy" anymore. IC_V1 (exit_mode=EOD, no adjust, no
# carry) and IC_V2 (NEXT_OPEN / ONE_NIGHT_MAX + ADJ_ON_MTC) differ ONLY by
# their per-strategy config; the engine code is identical.
#
# FAIL-CLOSED BOOTSTRAP (unchanged per instance):
#   * The engine STARTS even while the broker is not ready — the engine's own
#     entry gates (broker.is_ready() at entry time) decide per-day. A broker
#     that comes up at 09:10 must not cost the 09:18 entry just because
#     startup ordering raced.
#   * The EXECUTOR is built as soon as the broker manager exists (even for
#     PAPER — house rule: pre-build so a PAPER→LIVE flip mid-session never
#     needs a restart). Executor construction failure → engine still runs,
#     enter_day() in LIVE will fail its first order and D6-unwind to nothing;
#     resolve_execution_mode's degraded path + alerts make it loud. PAPER is
#     unaffected.
#   * ltp_resolver = REST-primary (data kite), per house doctrine.
# ============================================================================

import asyncio
from typing import Dict, Optional

from app.event_bus.audit_logger import write_audit_log

from app.engine.ic.ic_group_manager import ICGroupManager
from app.engine.ic.ic_engine import ICEngine
from app.engine.ic.ic_gtt_monitor import ICGTTMonitor
from app.execution.executor_factory import get_executor_for_strategy  # ACC2_W31_IMPORTFIX 20260818

IC_STRATEGY_IDS = ("IC_V1", "IC_V2")


class _ICRuntime:
    __slots__ = ("manager", "engine", "monitor")

    def __init__(self):
        self.manager: Optional[ICGroupManager] = None
        self.engine:  Optional[ICEngine] = None
        self.monitor: Optional[ICGTTMonitor] = None


IC_REGISTRY: Dict[str, _ICRuntime] = {}


def get_ic_manager(strategy_id: str) -> Optional[ICGroupManager]:
    rt = IC_REGISTRY.get(strategy_id)
    return rt.manager if rt else None


def get_ic_engine(strategy_id: str) -> Optional[ICEngine]:
    rt = IC_REGISTRY.get(strategy_id)
    return rt.engine if rt else None


def get_ic_monitor(strategy_id: str) -> Optional[ICGTTMonitor]:
    rt = IC_REGISTRY.get(strategy_id)
    return rt.monitor if rt else None


def _make_ltp_resolver(broker_manager):
    """REST-primary single-symbol LTP for exit-price resolution."""
    def resolve(symbol: str):
        try:
            kite = broker_manager.get_data_kite()
            if kite is None:
                return None
            q = kite.ltp(f"NFO:{symbol}")
            row = q.get(f"NFO:{symbol}") or {}
            v = float(row.get("last_price") or 0.0)
            return v if v > 0 else None
        except Exception:
            return None
    return resolve


async def ic_runtime(broker_manager, strategy_id: str, *args, **kwargs):
    """Async entrypoint for ONE IC strategy instance. Never raises out — a
    runtime crash must not take the server down; it logs, alerts, and that
    strategy is simply absent (the sibling instance is unaffected)."""
    try:
        await asyncio.sleep(0)

        if strategy_id not in IC_STRATEGY_IDS:
            write_audit_log(f"[IC][RUNTIME] unknown strategy_id "
                            f"{strategy_id!r} — refusing to launch")
            return

        if strategy_id in IC_REGISTRY:
            write_audit_log(f"[IC][{strategy_id}][RUNTIME] already "
                            f"initialized — ignoring relaunch")
            return
        rt = _ICRuntime()
        IC_REGISTRY[strategy_id] = rt

        executor = None
        try:
            from app.execution.zerodha_executor import ZerodhaOrderExecutor
            executor = get_executor_for_strategy(strategy_id)
            write_audit_log(f"[IC][{strategy_id}][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[IC][{strategy_id}][RUNTIME] executor build "
                            f"failed ({e!r}) — PAPER unaffected; LIVE will "
                            f"alert at entry")

        rt.manager = ICGroupManager(
            strategy_id=strategy_id,
            executor=executor,
            ltp_resolver=_make_ltp_resolver(broker_manager),
        )
        rt.engine = ICEngine(rt.manager, broker_manager)
        rt.engine.start()

        if executor is not None:
            rt.monitor = ICGTTMonitor(executor, rt.manager)
            rt.monitor.start()

        write_audit_log(f"[IC][{strategy_id}][RUNTIME] runtime up "
                        f"(engine=on monitor={'on' if rt.monitor else 'off'})")

        # light supervision: if the executor arrived late (broker manager was
        # rebuilt), attach it once available so a mid-session LIVE flip works.
        while True:
            await asyncio.sleep(60)
            if rt.manager.executor is None:
                try:
                    from app.execution.zerodha_executor import ZerodhaOrderExecutor
                    ex = get_executor_for_strategy(strategy_id)
                    rt.manager.attach_executor(ex)
                    rt.monitor = ICGTTMonitor(ex, rt.manager)
                    rt.monitor.start()
                    write_audit_log(f"[IC][{strategy_id}][RUNTIME] executor "
                                    f"attached late; monitor started")
                except Exception:
                    pass

    except Exception as e:
        write_audit_log(f"[IC][{strategy_id}][RUNTIME][FATAL] {repr(e)}")
        try:
            from app.event_bus.inapp_events import record_alert
            record_alert("IC_RUNTIME_DOWN",
                         f"{strategy_id} runtime crashed: {e!r} — strategy inactive",
                         severity="error", strategy_id=strategy_id)
        except Exception:
            pass
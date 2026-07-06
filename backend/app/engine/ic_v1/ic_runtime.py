# backend/app/engine/ic_v1/ic_runtime.py
#
# IC_V1 — Runtime Entrypoint
# ============================================================================
# Launched from api_server startup (wiring day) exactly like SCALP_V2's
# standalone pattern, behind the STRATEGIES enabled flag + license gate:
#
#     if STRATEGIES.get("IC_V1", {}).get("enabled", False) and \
#             license_state.license_allows_strategy("IC_V1"):
#         asyncio.create_task(ic_v1_runtime(zerodha_manager))
#
# Builds — once — the ICGroupManager, ICEngine, ICGTTMonitor singletons and
# exposes accessors for api routes / the EOD job / diagnostics.
#
# FAIL-CLOSED BOOTSTRAP:
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
from typing import Optional

from app.event_bus.audit_logger import write_audit_log

from app.engine.ic_v1.ic_group_manager import ICGroupManager
from app.engine.ic_v1.ic_engine import ICEngine
from app.engine.ic_v1.ic_gtt_monitor import ICGTTMonitor

_MANAGER: Optional[ICGroupManager] = None
_ENGINE:  Optional[ICEngine] = None
_MONITOR: Optional[ICGTTMonitor] = None


def get_ic_manager() -> Optional[ICGroupManager]:
    return _MANAGER


def get_ic_engine() -> Optional[ICEngine]:
    return _ENGINE


def get_ic_monitor() -> Optional[ICGTTMonitor]:
    return _MONITOR


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


async def ic_v1_runtime(broker_manager, *args, **kwargs):
    """Async entrypoint. Never raises out — a runtime crash must not take the
    server down; it logs, alerts, and the strategy is simply absent."""
    global _MANAGER, _ENGINE, _MONITOR
    try:
        await asyncio.sleep(0)

        if _MANAGER is not None:
            write_audit_log("[IC][RUNTIME] already initialized — ignoring relaunch")
            return

        executor = None
        try:
            from app.execution.zerodha_executor import ZerodhaOrderExecutor
            executor = ZerodhaOrderExecutor(broker_manager)
            write_audit_log("[IC][RUNTIME] executor pre-built")
        except Exception as e:
            write_audit_log(f"[IC][RUNTIME] executor build failed ({e!r}) — "
                            f"PAPER unaffected; LIVE will alert at entry")

        _MANAGER = ICGroupManager(
            executor=executor,
            ltp_resolver=_make_ltp_resolver(broker_manager),
        )
        _ENGINE = ICEngine(_MANAGER, broker_manager)
        _ENGINE.start()

        if executor is not None:
            _MONITOR = ICGTTMonitor(executor, _MANAGER)
            _MONITOR.start()

        write_audit_log("[IC][RUNTIME] IC_V1 runtime up "
                        f"(engine=on monitor={'on' if _MONITOR else 'off'})")

        # light supervision: if the executor arrived late (broker manager was
        # rebuilt), attach it once available so a mid-session LIVE flip works.
        while True:
            await asyncio.sleep(60)
            if _MANAGER.executor is None:
                try:
                    from app.execution.zerodha_executor import ZerodhaOrderExecutor
                    ex = ZerodhaOrderExecutor(broker_manager)
                    _MANAGER.attach_executor(ex)
                    _MONITOR = ICGTTMonitor(ex, _MANAGER)
                    _MONITOR.start()
                    write_audit_log("[IC][RUNTIME] executor attached late; monitor started")
                except Exception:
                    pass

    except Exception as e:
        write_audit_log(f"[IC][RUNTIME][FATAL] {repr(e)}")
        try:
            from app.event_bus.inapp_events import record_alert
            record_alert("IC_RUNTIME_DOWN",
                         f"IC_V1 runtime crashed: {e!r} — strategy inactive",
                         severity="error", strategy_id="IC_V1")
        except Exception:
            pass

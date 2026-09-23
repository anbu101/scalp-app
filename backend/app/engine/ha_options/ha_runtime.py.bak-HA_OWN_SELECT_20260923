# backend/app/engine/ha_options/ha_runtime.py
"""
HA Runtime
==========
Async startup entrypoint for HA_V1.  Mirrors bb_runtime.py exactly:
  - Waits for data session
  - Resolves trade mode from global + strategy config
  - Constructs HAOptionsTickEngine
  - Runs daily reset loop

Called by StrategyRuntimeManager.start("HA_V1", broker_manager).

EXECUTOR LIFECYCLE (FIX):
  Previously the executor was built ONLY when the startup trade_mode resolved to
  "LIVE". But HATradeManager._mode() re-reads strategy config on every entry and
  can resolve to LIVE at RUNTIME (e.g. user flips HA to LIVE after launch, or the
  global trade_on flag was off at boot so startup forced PAPER). When that
  happened the executor was still None and the first live BUY crashed with
  'NoneType' object has no attribute 'broker_manager'.

  Now: build the executor whenever the TRADE session is ready, regardless of the
  startup mode. The executor is harmless in PAPER (never used on the paper path),
  and present the instant the manager resolves to LIVE at runtime. If the trade
  session is not ready, executor stays None and live entries fail-safe (the trade
  manager guards against a None executor and emits a clean alert instead of
  crashing).
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.global_loader import load_global_config
from app.config.strategy_loader import load_strategy_config
from app.engine.ha_options.ha_tick_engine import HAOptionsTickEngine
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.execution.executor_factory import get_executor_for_strategy  # ACC2_W31_IMPORTFIX 20260818


async def start_ha_runtime(broker_manager):

    write_audit_log("[HA-RUNTIME] Initializing HA_V1")

    global_cfg = load_global_config()
    ha_cfg     = load_strategy_config("HA_V1")

    global_trade_on = global_cfg.get("trade_on", False)
    ha_mode         = ha_cfg.get("trade_execution_mode", "PAPER")

    if not global_trade_on:
        trade_mode = "PAPER"
        write_audit_log("[HA-RUNTIME] Global trade_on=FALSE → Forcing PAPER mode")
    else:
        trade_mode = ha_mode
        write_audit_log(f"[HA-RUNTIME] Global trade_on=TRUE → Using HA mode={ha_mode}")

    write_audit_log(f"[HA-RUNTIME] Final Trade mode = {trade_mode}")

    last_log_time = 0

    while not broker_manager.is_data_ready():
        now = time.time()
        if now - last_log_time > 60:
            write_audit_log("[HA-RUNTIME] Waiting for DATA session...")
            last_log_time = now
        await asyncio.sleep(5)

    kite_data = broker_manager.get_data_kite()

    # ── Build the executor whenever the TRADE session is ready ──────────
    # NOT gated on startup trade_mode == "LIVE": the trade manager can resolve
    # to LIVE at runtime, and it needs a real executor when it does. In PAPER
    # the executor simply goes unused.
    executor = None

    if trade_mode == "LIVE":
        # Startup LIVE: trade session MUST be ready or we refuse to start live.
        if not broker_manager.is_trade_ready():
            raise RuntimeError(
                "[HA-RUNTIME] Cannot start LIVE mode — trade session not ready"
            )
        executor = get_executor_for_strategy('HA_V1')
        write_audit_log("[HA-RUNTIME] Executor built (startup LIVE)")
    else:
        # Startup PAPER/OFF: still build the executor IF the trade session is
        # ready, so a runtime switch to LIVE works without a restart. If it
        # isn't ready, leave it None — live entries fail-safe via the trade
        # manager's guard, and the next reconcile/restart can build it.
        try:
            if broker_manager.is_trade_ready():
                executor = get_executor_for_strategy('HA_V1')
                write_audit_log(
                    "[HA-RUNTIME] Executor pre-built (startup "
                    f"{trade_mode}; trade session ready — runtime LIVE switch supported)"
                )
            else:
                write_audit_log(
                    "[HA-RUNTIME] Trade session not ready — executor=None for now. "
                    "Live entries will fail-safe until a trade session is available."
                )
        except Exception as e:
            executor = None
            write_audit_log(f"[HA-RUNTIME][EXECUTOR_PREBUILD_WARN] {repr(e)}")

    engine = None

    while True:
        try:
            if not broker_manager.is_data_ready():
                write_audit_log("[HA-RUNTIME] DATA session lost — waiting...")
                await asyncio.sleep(5)
                continue

            # If we still have no executor but the trade session has since
            # become ready, build it now so a later LIVE switch is covered.
            if executor is None:
                try:
                    if broker_manager.is_trade_ready():
                        executor = get_executor_for_strategy('HA_V1')
                        write_audit_log(
                            "[HA-RUNTIME] Executor built late (trade session now ready)"
                        )
                        # Hand it to a running engine's trade manager if needed.
                        if engine is not None:
                            try:
                                engine.executor = executor
                                engine._trade_manager.executor = executor
                                write_audit_log(
                                    "[HA-RUNTIME] Injected late executor into running engine"
                                )
                            except Exception as e:
                                write_audit_log(
                                    f"[HA-RUNTIME][EXECUTOR_INJECT_WARN] {repr(e)}"
                                )
                except Exception as e:
                    write_audit_log(f"[HA-RUNTIME][EXECUTOR_LATE_WARN] {repr(e)}")

            if engine is None:
                write_audit_log("[HA-RUNTIME] Starting HA engine")

                engine = HAOptionsTickEngine(
                    executor=executor,
                    config=ha_cfg,
                    trade_mode=trade_mode,
                )
                engine.start()

                write_audit_log("[HA-RUNTIME] Engine started")

            await _daily_reset_loop(engine)

        except Exception as e:
            write_audit_log(f"[HA-RUNTIME][ENGINE_ERROR] {repr(e)}")
            engine = None
            await asyncio.sleep(5)


async def _daily_reset_loop(engine: HAOptionsTickEngine):
    last_reset_date: Optional[str] = None

    while True:
        now = datetime.now()

        if now.strftime("%H:%M") >= "09:15":
            today_str = now.strftime("%Y-%m-%d")
            if last_reset_date != today_str:
                write_audit_log("[HA-RUNTIME] Daily trade counter reset")
                try:
                    engine._signal_engine.reset_daily()
                except Exception as e:
                    write_audit_log(f"[HA-RUNTIME][RESET_ERROR] {repr(e)}")
                last_reset_date = today_str

        await asyncio.sleep(30)
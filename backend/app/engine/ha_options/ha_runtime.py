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

    executor = None

    if trade_mode == "LIVE":
        if not broker_manager.is_trade_ready():
            raise RuntimeError(
                "[HA-RUNTIME] Cannot start LIVE mode — trade session not ready"
            )
        executor = ZerodhaOrderExecutor(broker_manager)

    engine = None

    while True:
        try:
            if not broker_manager.is_data_ready():
                write_audit_log("[HA-RUNTIME] DATA session lost — waiting...")
                await asyncio.sleep(5)
                continue

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
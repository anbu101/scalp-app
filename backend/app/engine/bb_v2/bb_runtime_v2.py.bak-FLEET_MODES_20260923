# backend/app/engine/bb_v2/bb_runtime_v2.py
"""
BB_V2 Runtime Entrypoint.

Mirrors bb_runtime.py exactly but starts BBOptionsTickEngineV2.
"""

import asyncio
from datetime import datetime
from typing import Optional
import time

from app.event_bus.audit_logger import write_audit_log
from app.config.global_loader import load_global_config
from app.config.strategy_loader import load_strategy_config

from app.engine.bb_v2.bb_tick_engine_v2 import BBOptionsTickEngineV2
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.execution.executor_factory import get_executor_for_strategy  # ACC2_W31_IMPORTFIX 20260818


async def start_bb_v2_runtime(broker_manager):

    write_audit_log("[BB_V2-RUNTIME] Initializing BB_V2")

    global_cfg = load_global_config()
    bb_cfg     = load_strategy_config("BB_V2")

    global_trade_on = global_cfg.get("trade_on", False)
    bb_mode         = bb_cfg.get("trade_execution_mode", "PAPER")

    if not global_trade_on:
        trade_mode = "PAPER"
        write_audit_log("[BB_V2-RUNTIME] Global trade_on=FALSE → Forcing PAPER")
    else:
        trade_mode = bb_mode
        write_audit_log(
            f"[BB_V2-RUNTIME] Global trade_on=TRUE → Using mode={bb_mode}"
        )

    write_audit_log(f"[BB_V2-RUNTIME] Final Trade mode = {trade_mode}")

    last_log_time = 0

    while not broker_manager.is_data_ready():
        now = time.time()
        if now - last_log_time > 60:
            write_audit_log("[BB_V2-RUNTIME] Waiting for DATA session...")
            last_log_time = now
        await asyncio.sleep(5)

    kite_data = broker_manager.get_data_kite()

    # ------------------------------------------------------
    # EXECUTOR
    #
    # Build the executor whenever the TRADE session is ready — even when the
    # engine starts in PAPER. This lets a mid-session PAPER->LIVE flip arm the
    # live path using an executor that was constructed and validated at startup
    # (in this async runtime loop), NOT mid-trade on the tick thread.
    #
    #   LIVE start  : executor is REQUIRED — raise if the trade session isn't
    #                 ready (unchanged behaviour).
    #   PAPER start : build the executor opportunistically if the trade session
    #                 happens to be ready; otherwise leave it None and let the
    #                 engine arm lazily later (engine.ensure_live_armed()).
    #
    # Holding an executor while in PAPER is harmless: nothing calls it until a
    # live entry resolves.
    # ------------------------------------------------------

    executor = None

    if trade_mode == "LIVE":
        if not broker_manager.is_trade_ready():
            raise RuntimeError(
                "[BB_V2-RUNTIME] Cannot start LIVE — trade session not ready"
            )
        executor = get_executor_for_strategy('BB_V2')

    else:
        # PAPER start — try to pre-build the executor so a later flip to LIVE
        # can arm without constructing one on the tick thread. Best-effort only.
        try:
            if broker_manager.is_trade_ready():
                executor = get_executor_for_strategy('BB_V2')
                write_audit_log(
                    "[BB_V2-RUNTIME] PAPER start, trade session ready -> executor "
                    "pre-built so a mid-session flip to LIVE can arm cleanly."
                )
            else:
                write_audit_log(
                    "[BB_V2-RUNTIME] PAPER start, trade session NOT ready -> no "
                    "executor yet; engine will arm lazily if flipped to LIVE."
                )
        except Exception as e:
            executor = None
            write_audit_log(
                f"[BB_V2-RUNTIME] PAPER start executor pre-build failed "
                f"({repr(e)}) -> engine will arm lazily if flipped to LIVE."
            )

    engine = None

    while True:
        try:
            if not broker_manager.is_data_ready():
                write_audit_log("[BB_V2-RUNTIME] DATA session lost — waiting...")
                await asyncio.sleep(5)
                continue

            kite_data = broker_manager.get_data_kite()

            if engine is None:
                write_audit_log("[BB_V2-RUNTIME] Starting BB_V2 engine")

                engine = BBOptionsTickEngineV2(
                    kite_data=kite_data,
                    executor=executor,
                    config=bb_cfg,
                    trade_mode=trade_mode,
                    broker_manager=broker_manager,
                )

                engine.start()
                write_audit_log("[BB_V2-RUNTIME] Engine started")

            await _daily_reset_loop_v2(engine)

        except Exception as e:
            write_audit_log(f"[BB_V2-RUNTIME][ENGINE_ERROR] {repr(e)}")
            engine = None
            await asyncio.sleep(5)


async def _daily_reset_loop_v2(engine):
    last_reset_date: Optional[str] = None

    while True:
        now = datetime.now()

        if now.strftime("%H:%M") >= "09:15":
            today_str = now.strftime("%Y-%m-%d")

            if last_reset_date != today_str:
                write_audit_log("[BB_V2-RUNTIME] Daily trade counter reset")
                try:
                    engine.signal_engine.reset_daily()
                except Exception as e:
                    write_audit_log(f"[BB_V2-RUNTIME][RESET_ERROR] {repr(e)}")

                last_reset_date = today_str

        await asyncio.sleep(30)
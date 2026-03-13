import asyncio
from datetime import datetime
from typing import Optional
import time

from app.event_bus.audit_logger import write_audit_log
from app.config.global_loader import load_global_config
from app.config.strategy_loader import load_strategy_config

from app.engine.bb_options.bb_tick_engine import BBOptionsTickEngine
from app.execution.zerodha_executor import ZerodhaOrderExecutor


# ==========================================================
# BB RUNTIME ENTRYPOINT
# ==========================================================

async def start_bb_runtime(broker_manager):

    write_audit_log("[BB-RUNTIME] Initializing BB_V1")

    # ------------------------------------------------------
    # LOAD CONFIGS
    # ------------------------------------------------------

    global_cfg = load_global_config()
    bb_cfg = load_strategy_config("BB_V1")

    global_trade_on = global_cfg.get("trade_on", False)
    bb_mode = bb_cfg.get("trade_execution_mode", "PAPER")

    # ------------------------------------------------------
    # SAFE TRADE MODE RESOLUTION
    # ------------------------------------------------------

    if not global_trade_on:
        trade_mode = "PAPER"
        write_audit_log(
            "[BB-RUNTIME] Global trade_on=FALSE → Forcing PAPER mode"
        )
    else:
        trade_mode = bb_mode
        write_audit_log(
            f"[BB-RUNTIME] Global trade_on=TRUE → Using BB mode={bb_mode}"
        )

    write_audit_log(f"[BB-RUNTIME] Final Trade mode = {trade_mode}")

    # ------------------------------------------------------
    # 🚨 CRITICAL FIX: USE DATA SESSION (NOT TRADE SESSION)
    # ------------------------------------------------------

    last_log_time = 0

    while not broker_manager.is_data_ready():

        now = time.time()

        if now - last_log_time > 60:
            write_audit_log("[BB-RUNTIME] Waiting for DATA session...")
            last_log_time = now

        await asyncio.sleep(5)

    kite_data = broker_manager.get_data_kite()

    # ------------------------------------------------------
    # EXECUTOR (LIVE ONLY)
    # ------------------------------------------------------

    executor = None

    if trade_mode == "LIVE":

        if not broker_manager.is_trade_ready():
            raise RuntimeError(
                "[BB-RUNTIME] Cannot start LIVE mode — trade session not ready"
            )

        executor = ZerodhaOrderExecutor(broker_manager)

        if executor is None:
            raise RuntimeError(
                "[BB-RUNTIME] LIVE mode requires valid executor"
            )

    # ------------------------------------------------------
    # CREATE ENGINE
    # ------------------------------------------------------

    engine = None

    while True:

        try:

            if not broker_manager.is_data_ready():
                write_audit_log("[BB-RUNTIME] DATA session lost — waiting...")
                await asyncio.sleep(5)
                continue

            kite_data = broker_manager.get_data_kite()

            if engine is None:

                write_audit_log("[BB-RUNTIME] Starting BB engine")

                engine = BBOptionsTickEngine(
                    kite_data=kite_data,
                    executor=executor,
                    config=bb_cfg,
                    trade_mode=trade_mode,
                )

                engine.start()

                write_audit_log("[BB-RUNTIME] Engine started")

            await _daily_reset_loop(engine)

        except Exception as e:

            write_audit_log(f"[BB-RUNTIME][ENGINE_ERROR] {repr(e)}")

            engine = None
            await asyncio.sleep(5)


# ==========================================================
# DAILY RESET AT MARKET OPEN
# ==========================================================

async def _daily_reset_loop(engine):

    last_reset_date: Optional[str] = None

    while True:

        now = datetime.now()

        # Reset once per day after 09:15
        if now.strftime("%H:%M") >= "09:15":

            today_str = now.strftime("%Y-%m-%d")

            if last_reset_date != today_str:

                write_audit_log("[BB-RUNTIME] Daily trade counter reset")

                try:
                    engine.signal_engine.reset_daily()
                except Exception as e:
                    write_audit_log(
                        f"[BB-RUNTIME][RESET_ERROR] {repr(e)}"
                    )

                last_reset_date = today_str

        await asyncio.sleep(30)
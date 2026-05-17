# backend/app/strategy/strategy_runtime.py

import asyncio
from app.event_bus.audit_logger import write_audit_log
from app.engine.selection_engine import selection_loop
from app.trading.gtt_reconciler import gtt_reconciliation_loop
from app.engine.bb_options.bb_runtime import start_bb_runtime
from app.engine.ha_options.ha_runtime import start_ha_runtime
from app.engine.bb_v2.bb_runtime_v2 import start_bb_v2_runtime 

class StrategyRuntimeManager:

    _RUNNING = {}

    @classmethod
    def start(cls, strategy_id: str, broker_manager):
        if strategy_id in cls._RUNNING:
            write_audit_log(f"[RUNTIME] Strategy {strategy_id} already running")
            return

        write_audit_log(f"[RUNTIME] Starting strategy {strategy_id}")

        # -------------------------------------------------
        # SCALP STRATEGY
        # -------------------------------------------------
        if strategy_id == "SCALP_V1":

            selection_task = asyncio.create_task(
                selection_loop(strategy_id, broker_manager)
            )

            gtt_task = asyncio.create_task(
                gtt_reconciliation_loop(strategy_id)
            )

            cls._RUNNING[strategy_id] = {
                "selection_task": selection_task,
                "gtt_task": gtt_task,
                "status": "RUNNING",
            }

        # -------------------------------------------------
        # BB STRATEGY
        # -------------------------------------------------
        elif strategy_id == "BB_V1":

            bb_task = asyncio.create_task(
                start_bb_runtime(broker_manager)
            )

            cls._RUNNING[strategy_id] = {
                "bb_task": bb_task,
                "status": "RUNNING",
            }

        # -------------------------------------------------
        # BB_V2 STRATEGY                                 NEW
        # -------------------------------------------------
        elif strategy_id == "BB_V2":

            bb_v2_task = asyncio.create_task(
                start_bb_v2_runtime(broker_manager)
            )

            cls._RUNNING[strategy_id] = {
                "bb_v2_task": bb_v2_task,
                "status":     "RUNNING",
            }

        # -------------------------------------------------
        # HA STRATEGY
        # HA_V1 piggybacks on the SCALP_V1 WS tick engine
        # (which is already started by selection_loop).
        # It only needs its own runtime loop for HA candle
        # processing, signal evaluation, and trade management.
        # -------------------------------------------------
        elif strategy_id == "HA_V1":

            ha_task = asyncio.create_task(
                start_ha_runtime(broker_manager)
            )

            cls._RUNNING[strategy_id] = {
                "ha_task": ha_task,
                "status": "RUNNING",
            }

        else:
            write_audit_log(f"[RUNTIME] Unknown strategy {strategy_id}")

    @classmethod
    def stop(cls, strategy_id: str):
        runtime = cls._RUNNING.get(strategy_id)
        if not runtime:
            return

        write_audit_log(f"[RUNTIME] Stopping strategy {strategy_id}")

        for task in runtime.values():
            if hasattr(task, "cancel"):
                task.cancel()

        cls._RUNNING[strategy_id]["status"] = "STOPPED"

    @classmethod
    def status(cls, strategy_id: str):
        runtime = cls._RUNNING.get(strategy_id)
        if not runtime:
            return "STOPPED"
        return runtime["status"]

    @classmethod
    def list_all(cls):
        return {
            k: v["status"]
            for k, v in cls._RUNNING.items()
        }
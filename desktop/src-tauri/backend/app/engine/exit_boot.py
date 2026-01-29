# backend/engine/exit_boot.py

import threading
import time

from app.engine.exit_reconciliation import ExitReconciliationEngine
from app.engine.logger import log
from app.brokers.broker_interface import BrokerInterface


_exit_thread = None


def start_exit_engine(broker: BrokerInterface):
    """
    Starts exit reconciliation loop.
    Must be called only AFTER broker (kite) is authenticated.
    """
    global _exit_thread

    if _exit_thread and _exit_thread.is_alive():
        log("[EXIT] Exit engine already running")
        return

    engine = ExitReconciliationEngine(broker)

    _exit_thread = threading.Thread(
        target=engine.run_forever,
        name="ExitReconciliationLoop",
        daemon=True,
    )

    _exit_thread.start()
    log("[EXIT] Exit reconciliation loop started")


def wait_forever():
    """
    Keeps main thread alive if needed (standalone mode).
    """
    while True:
        time.sleep(60)

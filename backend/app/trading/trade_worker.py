import time
import json
from pathlib import Path

from app.event_bus.audit_logger import write_audit_log
from app.trading.trade_state_manager import TradeStateManager
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.brokers.zerodha_manager import ZerodhaManager

# -------------------------------------------------
# Trade intent queue (shared with WS process)
# -------------------------------------------------

INTENT_DIR = Path("/data/trade_intents")
INTENT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL = 0.2  # seconds


def run_trade_worker():
    write_audit_log("[TRADE_WORKER] Started")

    broker = ZerodhaManager()
    executor = ZerodhaOrderExecutor(broker)

    while True:
        try:
            intents = sorted(INTENT_DIR.glob("intent_*.json"))

            for path in intents:
                try:
                    data = json.loads(path.read_text())

                    write_audit_log(
                        f"[TRADE_WORKER] Processing intent {path.name}"
                    )

                    # Route via existing SignalRouter logic
                    from app.engine.signal_router import signal_router
                    signal_router.route_buy_signal(**data)

                    path.unlink()

                except Exception as e:
                    write_audit_log(
                        f"[TRADE_WORKER][ERROR] {path.name} ERR={repr(e)}"
                    )
                    # leave file for retry

        except Exception as e:
            write_audit_log(f"[TRADE_WORKER][FATAL] {repr(e)}")

        time.sleep(POLL_INTERVAL)

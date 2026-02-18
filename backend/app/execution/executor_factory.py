from app.execution.execution_router import ExecutionRouter
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.execution.dhan_executor import DhanOrderExecutor
from app.brokers.zerodha_manager import ZerodhaManager


# --------------------------------------------------
# SINGLETONS (SAFE)
# --------------------------------------------------

_zerodha_manager = ZerodhaManager()
_zerodha_executor = ZerodhaOrderExecutor(_zerodha_manager)


# --------------------------------------------------
# FACTORY
# --------------------------------------------------

def get_executor_for_broker(broker_name: str):
    broker_name = broker_name.upper()

    if broker_name == "ZERODHA":
        return ExecutionRouter(_zerodha_executor)

    if broker_name == "DHAN":
        # Currently skeleton only
        return DhanOrderExecutor()

    raise Exception(f"Unsupported broker: {broker_name}")

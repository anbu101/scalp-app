# backend/app/execution/executor_factory.py

from app.execution.execution_router import ExecutionRouter
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.execution.dhan_executor import DhanOrderExecutor
from app.brokers.zerodha_manager import ZerodhaManager

# ============================================================
# ACC2 BEGIN — secondary account (Angel One) wiring
from app.brokers.angel_manager import AngelManager
from app.execution.angel_executor import AngelOneExecutor
from app.config.account_bindings import resolve_broker
# ACC2 END
# ============================================================

# ── ACCT_SWITCH_20261030 ── one account-aware facade per bindable strategy:
# follows the Settings binding without a restart, never while a LIVE
# position is open or an order sequence is in flight.
import threading
from app.execution.strategy_executor import StrategyExecutor
_strategy_executors = {}
_se_lock = threading.Lock()
# ============================================================


# --------------------------------------------------
# SINGLETONS (SAFE)
# --------------------------------------------------

_zerodha_manager = ZerodhaManager()
_zerodha_executor = ZerodhaOrderExecutor(_zerodha_manager)

# ============================================================
# ACC2 BEGIN — Angel singletons are LAZY: users without a secondary
# account must pay zero boot cost and see zero login attempts.
_angel_manager = None
_angel_executor = None


def get_angel_manager() -> AngelManager:
    global _angel_manager
    if _angel_manager is None:
        _angel_manager = AngelManager()
    return _angel_manager


def _get_angel_executor() -> AngelOneExecutor:
    global _angel_executor
    if _angel_executor is None:
        _angel_executor = AngelOneExecutor(get_angel_manager())
    return _angel_executor
# ACC2 END
# ============================================================


# --------------------------------------------------
# FACTORY
# --------------------------------------------------

def get_executor_for_broker(broker_name: str):
    broker_name = broker_name.upper()

    if broker_name == "ZERODHA":
        return ExecutionRouter(_zerodha_executor)

    # ============================================================
    # ACC2 BEGIN
    if broker_name == "ANGELONE":
        return ExecutionRouter(_get_angel_executor())
    # ACC2 END
    # ============================================================

    if broker_name == "DHAN":
        # Currently skeleton only
        return DhanOrderExecutor()

    raise Exception(f"Unsupported broker: {broker_name}")


# ============================================================
# ACC2 BEGIN — D2c strategy-level resolution
# Reads the user's per-strategy binding (bindings.json); absent or
# invalid binding falls back to the strategy_registry default, which
# today is ZERODHA for every strategy — so with no bindings file this
# function is byte-for-byte equivalent to the pre-ACC2 behaviour.
# ============================================================

def get_executor_for_strategy(strategy_id: str):
    try:
        from app.strategy.strategy_registry import STRATEGIES
        registry_default = (
            STRATEGIES.get(strategy_id, {}).get("broker", "ZERODHA"))
    except Exception:
        registry_default = "ZERODHA"

    # ── ACCT_SWITCH_20261030 ── bindable strategies share ONE facade that
    # re-reads the binding when it is safe (flat + quiet); see
    # execution/strategy_executor.py. Non-bindable ids are unchanged.
    from app.config.account_bindings import BINDABLE_STRATEGIES
    if strategy_id in BINDABLE_STRATEGIES:
        with _se_lock:
            se = _strategy_executors.get(strategy_id)
            if se is None:
                se = StrategyExecutor(
                    strategy_id,
                    build=lambda b: get_executor_for_broker(b),
                    resolve=lambda: resolve_broker(strategy_id, registry_default))
                _strategy_executors[strategy_id] = se
            return se

    broker = resolve_broker(strategy_id, registry_default)
    return get_executor_for_broker(broker)


def get_broker_for_strategy(strategy_id: str) -> str:
    """Binding name only (for display chips / symbol tripwire keys)."""
    try:
        from app.strategy.strategy_registry import STRATEGIES
        registry_default = (
            STRATEGIES.get(strategy_id, {}).get("broker", "ZERODHA"))
    except Exception:
        registry_default = "ZERODHA"
    return resolve_broker(strategy_id, registry_default)
# ACC2 END
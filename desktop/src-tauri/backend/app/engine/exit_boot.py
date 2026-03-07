# backend/engine/exit_boot.py

"""
⚠️ DEPRECATED EXIT ENGINE BOOTSTRAP

Legacy JSON-based exit reconciliation engine has been fully
replaced by:

- TradeStateManager (LIVE)
- SQLite trades
- GTT-based SL/TP
- BrokerReconciliationJob

This module is intentionally disabled.
"""

def start_exit_engine(broker):
    pass

def wait_forever():
    pass

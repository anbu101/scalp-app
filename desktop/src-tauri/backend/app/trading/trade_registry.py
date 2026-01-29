"""
DEPRECATED: trade_registry.py

This file is kept ONLY for backward compatibility.
DO NOT add logic here.

Single source of truth:
- TradeStateManager (app/trading/trade_state_manager.py)
"""

def is_active(*args, **kwargs):
    return False


def set_active(*args, **kwargs):
    # no-op
    return


def clear_active(*args, **kwargs):
    # no-op
    return

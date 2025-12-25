"""
DEPRECATED: trade_state.py

This module exists ONLY for backward compatibility.
DO NOT add logic here.

Single source of truth:
- TradeStateManager (trade_state_manager.py)
"""

from app.trading.trade_state_manager import TradeStateManager


def is_in_trade(slot: str) -> bool:
    """
    Legacy compatibility function.

    Returns True if the given slot is currently IN_TRADE.
    Delegates to TradeStateManager persisted state.
    """
    manager = TradeStateManager.get(slot)
    return manager.in_trade

"""
SelectionManager (legacy compatibility)

NOTE:
- Option selection is now handled by:
  - selection_engine.py
  - selection_scheduler.py
- Selection is LIST-based (2 CE + 2 PE)
- Persistence is centralized in save_selection()

This manager is kept ONLY to avoid startup errors
and must not perform selection or persistence.
"""

from typing import Dict, List


class SelectionManager:
    """
    Legacy-safe selection manager.

    This class is intentionally minimal.
    It does NOT:
    - compute selection
    - persist selection
    - assume CE/PE singletons
    """

    def __init__(
        self,
        instruments: List[Dict],
        index_symbol: str,
        atm_range: int,
        strike_step: int,
    ):
        self.instruments = instruments
        self.index_symbol = index_symbol
        self.atm_range = atm_range
        self.strike_step = strike_step

    def tick(self):
        """
        No-op.
        Selection is handled elsewhere.
        """
        return

    def set_in_trade(self, side: str, value: bool):
        """
        No-op.
        Trade state handled by TradeStateManager.
        """
        return

    def get_current_selection(self):
        """
        Legacy API – returns empty list.
        Actual selection is served via /selection/current
        """
        return []

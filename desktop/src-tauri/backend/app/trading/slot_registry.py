# backend/app/trading/slot_registry.py

from pathlib import Path
from typing import Dict, Optional

from app.trading.trade_state_manager import TradeStateManager
from app.execution.base_executor import BaseOrderExecutor


class SlotRegistry:
    """
    Maintains exactly 2 CE slots and 2 PE slots.
    Each slot owns ONE TradeStateManager at a time.
    """

    def __init__(self, executor: BaseOrderExecutor, state_dir: Path):
        self.slots: Dict[str, TradeStateManager] = {
            "CE_1": TradeStateManager(
                name="CE_1",
                executor=executor,
                state_file=state_dir / "CE_1.json",
            ),
            "CE_2": TradeStateManager(
                name="CE_2",
                executor=executor,
                state_file=state_dir / "CE_2.json",
            ),
            "PE_1": TradeStateManager(
                name="PE_1",
                executor=executor,
                state_file=state_dir / "PE_1.json",
            ),
            "PE_2": TradeStateManager(
                name="PE_2",
                executor=executor,
                state_file=state_dir / "PE_2.json",
            ),
        }

    # -------------------------
    # Access helpers
    # -------------------------

    def get_all(self):
        return self.slots.values()

    def get_slot(self, slot_name: str) -> TradeStateManager:
        return self.slots[slot_name]

    def find_by_symbol(self, symbol: str) -> Optional[TradeStateManager]:
        for mgr in self.slots.values():
            if mgr.active_trade and mgr.active_trade.symbol == symbol:
                return mgr
        return None

    def get_free_slot(self, side: str) -> Optional[TradeStateManager]:
        """
        side: 'CE' or 'PE'
        """
        for name, mgr in self.slots.items():
            if name.startswith(side) and not mgr.in_trade:
                return mgr
        return None

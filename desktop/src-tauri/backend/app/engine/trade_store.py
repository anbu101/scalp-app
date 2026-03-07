# backend/app/engine/trade_store.py

"""
⚠️ DEPRECATED MODULE

This JSON-based TradeStore has been fully replaced by
SQLite-based persistence (trades table).

This file is intentionally disabled to prevent legacy polling
and filesystem errors.

If this module is still being imported somewhere,
it will safely return empty results without file access.
"""

from typing import Dict, List


class TradeStore:
    def __init__(self):
        pass

    def get_open_trades(self) -> List[Dict]:
        return []

    def get_all_trades(self) -> List[Dict]:
        return []

    def update_trade(self, updated_trade: Dict):
        pass

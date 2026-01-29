# backend/engine/trade_store.py

import json
import os
import threading
from typing import Dict, List

from app.engine.logger import log


TRADE_STORE_FILE = "data/trades.json"
_LOCK = threading.Lock()


class TradeStore:
    def __init__(self):
        os.makedirs(os.path.dirname(TRADE_STORE_FILE), exist_ok=True)
        if not os.path.exists(TRADE_STORE_FILE):
            self._write_all([])

    # -------------------------------------------------------------

    def get_open_trades(self) -> List[Dict]:
        trades = self._read_all()
        return [
            t for t in trades
            if t.get("status") in ("OPEN", "EXIT_PENDING")
        ]

    def get_all_trades(self) -> List[Dict]:
        return self._read_all()

    def update_trade(self, updated_trade: Dict):
        with _LOCK:
            trades = self._read_all()
            for i, t in enumerate(trades):
                if t["trade_id"] == updated_trade["trade_id"]:
                    trades[i] = updated_trade
                    break
            else:
                trades.append(updated_trade)

            self._write_all(trades)

    # -------------------------------------------------------------

    def _read_all(self) -> List[Dict]:
        with _LOCK:
            try:
                with open(TRADE_STORE_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                log(f"[STORE][ERROR] Read failed: {e}")
                return []

    def _write_all(self, trades: List[Dict]):
        with open(TRADE_STORE_FILE, "w") as f:
            json.dump(trades, f, indent=2)

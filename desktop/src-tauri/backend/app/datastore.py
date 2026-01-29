import json
import os
from typing import List, Dict

class DataStore:
    def __init__(self, filename="data.json"):
        self.config_path = "config.json"
        self.trades_path = "trades.csv"
        # simple in-memory candles holder for MVP
        self._candles = {}
        if os.path.exists(self.config_path):
            with open(self.config_path) as f:
                self._config = json.load(f)
        else:
            self._config = {}

    def save_config(self, cfg: Dict):
        self._config.update(cfg)
        with open(self.config_path, "w") as f:
            json.dump(self._config, f)

    def save_selected_strikes(self, selected: Dict):
        
        if selected is None:
            selected = {}
        # merge into existing config
        cfg = self.get_config() or {}
        cfg["selected_strikes"] = selected
        self.save_config(cfg)

    def get_selected_strikes(self) -> Dict:
        """Return the currently saved selected_strikes (or empty dict)."""
        cfg = self.get_config() or {}
        return cfg.get("selected_strikes", {})

    def get_config(self):
        return self._config

    def ingest_candles(self, symbol: str, candles: List[Dict]):
        # candles = list of dicts with open/high/low/close, oldest first
        self._candles[symbol] = candles

    def get_recent_candles(self, symbol: str, lookback: int=240):
        arr = self._candles.get(symbol, [])
        return arr[-lookback:]

    def log_trade(self, order: Dict):
        # append to trades file (CSV-like simple append)
        import csv, os
        fields = ["time","symbol","side","qty","price","sl","tp","status"]
        exists = os.path.exists(self.trades_path)
        with open(self.trades_path, "a", newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(fields)
            writer.writerow([order.get(k,"") for k in fields])
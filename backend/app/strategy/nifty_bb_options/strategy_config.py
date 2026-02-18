import json
import os
from pathlib import Path
from typing import Dict, Any


CONFIG_DIR = Path("app/config")
CONFIG_FILE = CONFIG_DIR / "nifty_bb_options_config.json"


DEFAULT_CONFIG: Dict[str, Any] = {
    "timeframe_minutes": 3,

    "bollinger": {
        "period": 20,
        "std_dev": 2
    },

    "breakout": {
        "type": "close"
    },

    "option_selection": {
        "max_premium": 300,
        "scan_strikes": 10
    },

    "risk": {
        "stop_loss_percent": 20,
        "take_profit_percent": 0
    },

    "limits": {
        "max_trades_per_side_per_day": 10
    },

    "timing": {
        "start_time": "09:20",
        "stop_new_entries": "14:45",
        "force_exit": "15:15"
    }
}


class StrategyConfig:
    def __init__(self):
        self.config = self._load_or_create()

    def _load_or_create(self) -> Dict[str, Any]:
        if not CONFIG_DIR.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if not CONFIG_FILE.exists():
            self._save(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()

        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)

        return self._validate(data)

    def _save(self, data: Dict[str, Any]):
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def save(self):
        self._save(self.config)

    def _validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Basic safety validation
        """
        if data["timeframe_minutes"] != 3:
            raise ValueError("This strategy only supports 3-minute timeframe.")

        if data["option_selection"]["max_premium"] <= 0:
            raise ValueError("Max premium must be positive.")

        if data["risk"]["stop_loss_percent"] <= 0:
            raise ValueError("Stop loss percent must be > 0.")

        return data


# Singleton-style loader
strategy_config = StrategyConfig()

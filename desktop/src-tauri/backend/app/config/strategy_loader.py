import json
import os
from copy import deepcopy

CONFIG_PATH = os.path.expanduser("~/.scalp-app/strategy_config.json")

# 🔒 SINGLE SOURCE OF TRUTH DEFAULTS
DEFAULT_CONFIG = {
    # 🔑 GLOBAL TRADE SWITCH (ONLY ONE)
    "trade_on": False,

    # -------------------------
    # Risk Management
    # -------------------------
    "min_sl_points": 5,
    "max_sl_points": 0,          # 🔴 NEW (0 = disabled)
    "risk_reward_ratio": 1.0,

    # -------------------------
    # Target Override
    # -------------------------
    "target_override": {
        "enabled": False,
        "points": 0
    },

    # -------------------------
    # Trading Sessions
    # -------------------------
    "session": {
        "primary": {
            "start": "09:15",
            "end": "15:20"
        },
        "secondary": {
            "enabled": False,
            "start": "10:00",
            "end": "14:30"
        }
    },

    # -------------------------
    # Option Filters
    # -------------------------
    "option_premium": {
        "min": 100,
        "max": 300
    },

    # -------------------------
    # Quantity
    # -------------------------
    "quantity": {
        "lots": 1,
        "lot_size": 65
    },

    # 🔒 REQUIRED — must ALWAYS exist
    "trade_side_mode": "BOTH"   # CE / PE / BOTH
}


def load_strategy_config() -> dict:
    """
    Always returns a COMPLETE config.
    Never returns {}.
    Never enables trading implicitly.
    """
    if not os.path.exists(CONFIG_PATH):
        save_strategy_config(DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except Exception:
        save_strategy_config(DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    # ---- Merge with defaults (forward compatible) ----
    merged = deepcopy(DEFAULT_CONFIG)
    deep_update(merged, cfg)

    return merged


def save_strategy_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def deep_update(base: dict, incoming: dict):
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v

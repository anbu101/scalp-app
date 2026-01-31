import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

# --------------------------------------------------
# CONFIG PATH (CROSS-PLATFORM SAFE)
# --------------------------------------------------

CONFIG_PATH = Path.home() / ".scalp-app" / "strategy_config.json"

# --------------------------------------------------
# 🔒 SINGLE SOURCE OF TRUTH DEFAULTS
# --------------------------------------------------

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

# --------------------------------------------------
# LOAD CONFIG
# --------------------------------------------------

def load_strategy_config() -> dict:
    """
    Always returns a COMPLETE config.
    Never returns {}.
    Never enables trading implicitly.
    """
    if not CONFIG_PATH.exists():
        save_strategy_config(DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        save_strategy_config(DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    # ---- Merge with defaults (forward compatible) ----
    merged = deepcopy(DEFAULT_CONFIG)
    deep_update(merged, cfg)

    return merged

# --------------------------------------------------
# SAVE CONFIG (WINDOWS-SAFE, ATOMIC)
# --------------------------------------------------

def save_strategy_config(cfg: dict):
    """
    Atomic write to avoid PermissionError on Windows.
    Safe across macOS / Linux / Windows.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file first (same directory)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(CONFIG_PATH.parent),
        prefix="strategy_config_",
        suffix=".json"
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace (Windows-safe)
        os.replace(tmp_path, CONFIG_PATH)

    finally:
        # Cleanup temp file if something went wrong
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

# --------------------------------------------------
# DEEP UPDATE
# --------------------------------------------------

def deep_update(base: dict, incoming: dict):
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v

# backend/app/config/strategy_loader.py

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

# --------------------------------------------------
# STRATEGY CONFIG ROOT
# --------------------------------------------------

STRATEGY_DIR = Path.home() / ".scalp-app" / "strategies"
STRATEGY_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------
# DEFAULT CONFIGS PER STRATEGY
# --------------------------------------------------

DEFAULT_STRATEGY_CONFIGS = {

    # ==================================================
    # SCALP_V1 DEFAULT
    # ==================================================
    "SCALP_V1": {
        "min_sl_points": 5,
        "max_sl_points": 0,
        "risk_reward_ratio": 1.0,

        "target_override": {
            "enabled": False,
            "points": 0
        },

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

        "option_premium": {
            "min": 100,
            "max": 300
        },

        "quantity": {
            "lots": 1,
            "lot_size": 65
        },

        "trade_side_mode": "BOTH",
        "trade_execution_mode": "LIVE"
    },

    # ==================================================
    # BB_V1 DEFAULT
    # ==================================================
    "BB_V1": {
        "trade_execution_mode": "PAPER",
        "sl_pct":               20,    # 20% stop loss
        "tp_pct":               100,   # 100% take profit (doubles the premium)
        "max_premium":          300,
        "max_trades_per_side":  10,
        "ce_lots":              1,
        "pe_lots":              1,
        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:15",
    }
}


# --------------------------------------------------
# PATH HELPER
# --------------------------------------------------

def _get_strategy_path(strategy_id: str) -> Path:
    return STRATEGY_DIR / f"{strategy_id}.json"


# --------------------------------------------------
# LOAD STRATEGY CONFIG
# --------------------------------------------------

def load_strategy_config(strategy_id: str) -> dict:
    path = _get_strategy_path(strategy_id)

    default = deepcopy(DEFAULT_STRATEGY_CONFIGS.get(strategy_id, {}))

    if not path.exists():
        save_strategy_config(strategy_id, default)
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        save_strategy_config(strategy_id, default)
        return default

    merged = deepcopy(default)
    deep_update(merged, cfg)

    return merged


# --------------------------------------------------
# SAVE STRATEGY CONFIG (ATOMIC SAFE)
# --------------------------------------------------

def save_strategy_config(strategy_id: str, cfg: dict):
    path = _get_strategy_path(strategy_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"{strategy_id}_",
        suffix=".json"
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)

    finally:
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
# backend/app/config/strategy_loader.py

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

STRATEGY_DIR = Path.home() / ".scalp-app" / "strategies"
STRATEGY_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_STRATEGY_CONFIGS = {

    # ==================================================
    # SCALP_V1 — Option SHORT SELLING
    # target_override removed (fixed-target concept does
    # not apply to short selling; TP = prev red candle low)
    # ==================================================
    "SCALP_V1": {
        "min_sl_points":     5,
        "max_sl_points":     0,
        "risk_reward_ratio": 1.0,

        "session": {
            "primary": {
                "start": "09:15",
                "end":   "15:20"
            },
            "secondary": {
                "enabled": False,
                "start":   "10:00",
                "end":     "14:30"
            }
        },

        "option_premium": {
            "min": 100,
            "max": 300
        },

        "quantity": {
            "lots":     1,
            "lot_size": 65
        },

        "trade_side_mode":      "BOTH",
        "trade_execution_mode": "LIVE"
    },

    # ==================================================
    # BB_V1 DEFAULT  (unchanged)
    # ==================================================
    "BB_V1": {
        "trade_execution_mode": "PAPER",

        "sl_pct":  20,
        "tp_pct":  100,
        "lots":    1,

        "multiple_targets": False,
        "tp1_pct":          50,
        "tp2_pct":          100,
        "lots_leg1":        1,
        "lots_leg2":        1,
        "trailing_sl":      False,

        "max_premium":         300,
        "max_trades_per_side": 10,

        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:15",

        "st_exit_gap": 30,
    },

    # ==================================================
    # BB_V2 DEFAULT  (unchanged)
    # ==================================================
    "BB_V2": {
        "trade_execution_mode": "PAPER",

        "sl_pct": 20,
        "tp_pct": 100,

        "ce_lots": 1,
        "pe_lots": 1,

        "max_premium":         300,
        "max_trades_per_side": 10,

        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:15",
    },

    # ==================================================
    # HA_V1 DEFAULT  (unchanged)
    # ==================================================
    "HA_V1": {
        "trade_execution_mode": "PAPER",

        "risk_reward_ratio": 2.0,

        "target_override": {
            "enabled": False,
            "points":  0
        },

        "option_premium": {
            "min": 50,
            "max": 300
        },

        "quantity": {
            "lots":     1,
            "lot_size": 65
        },

        "max_trades_per_side": 10,

        "session": {
            "primary": {
                "start": "09:15",
                "end":   "15:20"
            },
            "secondary": {
                "enabled": False,
                "start":   "09:15",
                "end":     "15:20"
            }
        },

        "trade_side_mode": "BOTH",
    },

    # ==================================================
    # SCALP_V2 DEFAULT — 3-class order-splitting SHORT
    # ==================================================
    # Read by:
    #   - group manager : trade_execution_mode, exit_stagger_seconds,
    #                      classes.{A,B,C}.premium.{min,max}, classes.{A,B,C}.lots,
    #                      quantity.lot_size
    #   - selection loop: classes.{A,B,C}.premium.{min,max}
    #   - tick engine   : session.primary.{start,end}
    #
    # Default bands are non-overlapping (the UI enforces non-overlap on save).
    # All lots default to 1 (lot_size shared, NIFTY=65). exit_stagger_seconds
    # is the global staggered-exit window (seconds) after the first leg's TP/SL.
    # Master SL/TP is derived from risk_reward_ratio / min_sl_points /
    # max_sl_points using the SAME StrategyEngine math as SCALP_V1, then
    # propagated by percentage to slave legs.
    # ==================================================
    "SCALP_V2": {
        "trade_execution_mode": "PAPER",

        # Master-applied risk params (cloned SCALP_V1 entry math)
        "min_sl_points":     5,
        "max_sl_points":     0,
        "risk_reward_ratio": 1.0,

        # Global staggered-exit window (seconds) after first leg hits TP/SL
        "exit_stagger_seconds": 15,

        # Per-class premium bands (non-overlapping) + lots
        "classes": {
            "A": {
                "premium": {"min": 140, "max": 160},
                "lots":    1
            },
            "B": {
                "premium": {"min": 161, "max": 180},
                "lots":    1
            },
            "C": {
                "premium": {"min": 181, "max": 200},
                "lots":    1
            }
        },

        "quantity": {
            "lot_size": 65
        },

        "session": {
            "primary": {
                "start": "09:15",
                "end":   "15:20"
            },
            "secondary": {
                "enabled": False,
                "start":   "10:00",
                "end":     "14:30"
            }
        },

        "trade_side_mode": "BOTH",
    },
}


def _get_strategy_path(strategy_id: str) -> Path:
    return STRATEGY_DIR / f"{strategy_id}.json"


def load_strategy_config(strategy_id: str) -> dict:
    path    = _get_strategy_path(strategy_id)
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

    # --------------------------------------------------
    # MIGRATION: strip target_override from persisted
    # SCALP_V1 configs (it no longer applies).
    # --------------------------------------------------
    if strategy_id == "SCALP_V1":
        merged.pop("target_override", None)

    # --------------------------------------------------
    # MIGRATION: ce_lots / pe_lots → lots  (BB_V1)
    # --------------------------------------------------
    if strategy_id == "BB_V1":
        if merged.get("lots") == 1:
            old = cfg.get("ce_lots") or cfg.get("pe_lots")
            if old and int(old) > 1:
                merged["lots"] = int(old)

        total = merged.get("lots", 1)
        l1    = merged.get("lots_leg1", 1)
        l2    = merged.get("lots_leg2", 1)
        if merged.get("multiple_targets") and (l1 + l2 != total):
            merged["lots_leg1"] = (total + 1) // 2
            merged["lots_leg2"] = total // 2

    # --------------------------------------------------
    # MIGRATION: BB_V2 ce_lots / pe_lots guard
    # --------------------------------------------------
    if strategy_id == "BB_V2":
        if merged.get("lots") == 1:
            old = cfg.get("ce_lots") or cfg.get("pe_lots")
            if old and int(old) > 1:
                merged["lots"] = int(old)

    return merged


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


def deep_update(base: dict, incoming: dict):
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
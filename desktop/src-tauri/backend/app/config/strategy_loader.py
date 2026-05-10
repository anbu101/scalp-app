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

        # --------------------------------------------------
        # CORE RISK (single-target mode)
        # sl_pct applies to both legs always.
        # tp_pct applies only when multiple_targets=False.
        # --------------------------------------------------
        "sl_pct":  20,    # 20% stop loss
        "tp_pct":  100,   # 100% take profit (single-target mode only)

        # --------------------------------------------------
        # LOTS
        # Replaces ce_lots / pe_lots.  Both sides always use
        # the same lot count (symmetry enforced in UI).
        # --------------------------------------------------
        "lots":  1,

        # --------------------------------------------------
        # PARTIAL PROFIT BOOKING (multiple_targets mode)
        #
        # multiple_targets : enable two-leg exit
        # tp1_pct          : take-profit % for leg 1 (first exit)
        # tp2_pct          : take-profit % for leg 2 (runner)
        # lots_leg1        : lots assigned to leg 1  (lots_leg1 + lots_leg2 == lots)
        # lots_leg2        : lots assigned to leg 2
        # trailing_sl      : after leg 1 TP hit, move leg 2 SL to breakeven
        # --------------------------------------------------
        "multiple_targets": False,
        "tp1_pct":          50,     # e.g. book 50% gain on leg 1
        "tp2_pct":          100,    # e.g. double on leg 2
        "lots_leg1":        1,      # must be < lots; sum with lots_leg2 == lots
        "lots_leg2":        1,
        "trailing_sl":      False,

        # --------------------------------------------------
        # OPTION SELECTION
        # --------------------------------------------------
        "max_premium":         300,
        "max_trades_per_side": 10,

        # --------------------------------------------------
        # SESSION
        # --------------------------------------------------
        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:15",

        # --------------------------------------------------
        # EXIT CRITERIA
        # --------------------------------------------------
        "st_exit_gap": 30,   # exit when close within N points of SuperTrend
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

    # --------------------------------------------------
    # MIGRATION: ce_lots / pe_lots → lots
    # Old configs written before this version stored
    # ce_lots and pe_lots separately.  If the new "lots"
    # field is still at its default (1) but old fields
    # exist, adopt the old value so behaviour is unchanged.
    # --------------------------------------------------
    if strategy_id == "BB_V1":
        if merged.get("lots") == 1:
            old = cfg.get("ce_lots") or cfg.get("pe_lots")
            if old and int(old) > 1:
                merged["lots"] = int(old)

        # Guard: lots_leg1 + lots_leg2 must equal lots.
        # Silently fix if they don't (can happen when the user
        # increases lots without updating the leg split).
        total = merged.get("lots", 1)
        l1    = merged.get("lots_leg1", 1)
        l2    = merged.get("lots_leg2", 1)
        if merged.get("multiple_targets") and (l1 + l2 != total):
            # Fall back to even split, bias leg 1 if odd
            merged["lots_leg1"] = (total + 1) // 2
            merged["lots_leg2"] = total // 2

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
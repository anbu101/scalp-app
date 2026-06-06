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
    # SCALP_V2 DEFAULT — V1 clone + 3-leg order split (SHORT)
    # ==================================================
    # Read by:
    #   - group manager : trade_execution_mode, min_sl_points, max_sl_points,
    #                      risk_reward_ratio, max_loss, max_profit,
    #                      quantity.{leg1_lots,leg2_lots,leg3_lots,lot_size},
    #                      session.primary.{start,end}, trade_side_mode
    #   - selection loop: option_premium.{min,max}, timeframe
    #   - tick engine   : timeframe, session
    #
    # Upstream is identical to SCALP_V1 (single premium range, same signal
    # generation). Divergence is only at placement: each signal is split into
    # 3 legs — signal strike (L1, signal's exact TP/SL) + the +1 and -1 strikes
    # (L2/L3, pct-derived TP/SL). Exit is all-or-nothing. One group at a time.
    # ==================================================
    "SCALP_V2": {
        "trade_execution_mode": "PAPER",

        "timeframe": "1m",

        # Signal entry math (cloned from SCALP_V1)
        "min_sl_points":     5,
        "max_sl_points":     0,
        "risk_reward_ratio": 1.0,

        # Daily risk limits (rupees, 0 = disabled)
        "max_loss":   0,
        "max_profit": 0,

        "option_premium": {
            "min": 150,
            "max": 200
        },

        # Per-leg lots (L1 = signal strike, L2 = +1 strike, L3 = -1 strike)
        "quantity": {
            "leg1_lots": 5,
            "leg2_lots": 5,
            "leg3_lots": 5,
            "lot_size":  65
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

    # ==================================================
    # SCALP_V3 DEFAULT — TEST option-BUYING hedge clone of SCALP_V1
    # ==================================================
    # Reuses SCALP_V1's selection + signal generation verbatim. DIVERGES at
    # execution: the signalling contract (e.g. 24500CE) is TRACKED for its own
    # SL/TP but NEVER traded; instead V3 BUYS the highest-premium opposite-side
    # option (e.g. 24450PE) and protects it with an SL-only GTT at
    # (hedge_fill - max_sl_points). The hedge exits when EITHER the signal
    # contract hits its SL/TP, OR the hedge's own SL fires.
    #
    # Read by:
    #   - StrategyEngine(SCALP_V3) : min_sl_points, risk_reward_ratio,
    #                                max_sl_points  (signal-contract SL/TP math)
    #   - scalp_v3_manager         : max_sl_points (ALSO = hedge SL distance:
    #                                  hedge_sl = hedge_fill - max_sl_points),
    #                                quantity.{lots,lot_size}, session.primary,
    #                                trade_execution_mode, max_loss/max_profit
    #   - scalp_v3_engine          : option_premium.{min,max}, trade_side_mode
    #
    # NOTE: max_sl_points does DOUBLE DUTY — it caps the signal-contract SL
    # (entry + max_sl) AND sets the hedge protective-stop distance
    # (fill - max_sl). This matches the SCALP_V3 spec (one MAX_SL field in the
    # UI governs both). To decouple later, add a separate hedge_sl_points key.
    #
    # NOTE: trade_side_mode here gates the SIGNAL side, not the traded side
    # (the traded instrument is always the opposite of the signal). "CE" =>
    # only CE signals fire => only PE hedges bought. "BOTH" = no restriction.
    #
    # Isolation: no other strategy reads this entry. Removing SCALP_V3 = delete
    # this dict key + the scalp_v3 package + drop the scalp_v3_trades table.
    # ==================================================
    "SCALP_V3": {
        "trade_execution_mode": "PAPER",

        # Signal-contract entry math (cloned from SCALP_V1).
        # max_sl_points ALSO sets the hedge SL distance (see note above).
        "min_sl_points":     5,
        "max_sl_points":     20,
        "risk_reward_ratio": 1.7,

        # Daily risk limits (rupees, 0 = disabled). MTM guard is OFF for V3
        # for now (test strategy); EOD square-off is the live-position backstop.
        "max_loss":   0,
        "max_profit": 0,

        "session": {
            "primary": {
                "start": "09:30",
                "end":   "15:20"
            },
            "secondary": {
                "enabled": False,
                "start":   "10:00",
                "end":     "14:30"
            }
        },

        "option_premium": {
            "min": 150,
            "max": 200
        },

        "quantity": {
            "lots":     15,
            "lot_size": 65
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

    # --------------------------------------------------
    # MIGRATION: SCALP_V2 3-class → 3-leg model.
    # Strip stale class/stagger keys; ensure per-leg lots exist so no leg is
    # silently zero-qty when loading an old (pre-redesign) config file.
    # --------------------------------------------------
    if strategy_id == "SCALP_V2":
        merged.pop("classes", None)
        merged.pop("exit_stagger_seconds", None)
        q = merged.setdefault("quantity", {})
        q.setdefault("leg1_lots", 5)
        q.setdefault("leg2_lots", 5)
        q.setdefault("leg3_lots", 5)
        q.setdefault("lot_size", 65)

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
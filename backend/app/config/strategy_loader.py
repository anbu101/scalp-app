# backend/app/config/strategy_loader.py

import json
import os
import tempfile
import contextvars
from copy import deepcopy
from pathlib import Path

from app.event_bus.audit_logger import write_audit_log

STRATEGY_DIR = Path.home() / ".scalp-app" / "strategies"
STRATEGY_DIR.mkdir(parents=True, exist_ok=True)

# ====================================================================
# BACKTEST CONFIG OVERRIDE  (BT_CONFIG_OVERRIDE BEGIN)
# --------------------------------------------------------------------
# The live signal engine (StrategyEngine.on_candle) loads its SL/RR params by
# calling load_strategy_config(strategy_id) INLINE — i.e. it reads the on-disk
# Settings file. The backtest must instead feed the params entered on the
# Backtest page. Editing the live engine is forbidden and reimplementing
# on_candle would risk drift, so we inject the override HERE, at the single
# chokepoint every reader already uses.
#
# Mechanism: a ContextVar holding {strategy_id: override_dict}. The backtest
# runner SETS it (in the same thread that drives on_candle) for the duration of
# a run, then RESETS it. load_strategy_config merges the override OVER the
# normal merged config on every return path.
#
# SAFETY: a ContextVar is scoped to the current execution context. When unset
# (every live code path) load_strategy_config behaves EXACTLY as before — zero
# impact on live trading. A ContextVar is NOT inherited by threads started via
# threading.Thread, so a live worker thread cannot see a backtest override.
# ====================================================================

_BT_CONFIG_OVERRIDE = contextvars.ContextVar("scalp_bt_config_override", default=None)


def set_backtest_config_override(overrides_by_strategy: dict):
    """Install per-strategy config overrides for the current execution context.
    overrides_by_strategy maps strategy_id -> partial config dict (deep-merged
    over the on-disk/default config). Returns a token for clear_…(). Call from
    the SAME thread that will invoke on_candle."""
    return _BT_CONFIG_OVERRIDE.set(dict(overrides_by_strategy or {}))


def clear_backtest_config_override(token=None):
    """Remove the override installed by set_backtest_config_override()."""
    try:
        if token is not None:
            _BT_CONFIG_OVERRIDE.reset(token)
        else:
            _BT_CONFIG_OVERRIDE.set(None)
    except Exception:
        _BT_CONFIG_OVERRIDE.set(None)


def _apply_bt_override(strategy_id: str, cfg: dict) -> dict:
    """If a backtest override is active for this strategy, deep-merge it over
    cfg and return. Otherwise return cfg unchanged. Never reads disk."""
    ov = _BT_CONFIG_OVERRIDE.get()
    if not ov:
        return cfg
    strat_ov = ov.get(strategy_id)
    if not strat_ov:
        return cfg
    merged = deepcopy(cfg)
    deep_update(merged, strat_ov)
    return merged
# BT_CONFIG_OVERRIDE END

DEFAULT_STRATEGY_CONFIGS = {

    # ==================================================
    # SCALP_V1 — Option SHORT SELLING
    # target_override removed (fixed-target concept does
    # not apply to short selling; TP = prev red candle low)
    #
    # SAFETY: default execution mode is PAPER, NOT LIVE.
    # A default of LIVE meant that any read-failure fallback
    # (see load_strategy_config) silently armed live order
    # routing. Paper is the only safe default — a missed live
    # trade is opportunity cost; an unintended live order is
    # real money at risk.
    #
    # SL PARAMS (config keys unchanged; UI labels use clearer names):
    #   min_sl_points      → "Risk Min SL"  — skip entry if risk_distance < this
    #   risk_max_sl_points → "Risk Max SL"  — skip entry if risk_distance > this (0 = off)
    #   max_sl_points      → "Max SL Cap"   — clamp final sl_price (0 = off)
    # ==================================================
    "SCALP_V1": {
        "min_sl_points":      5,
        "max_sl_points":      0,
        "risk_max_sl_points": 0,
        "risk_reward_ratio":  1.0,

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
        "trade_execution_mode": "PAPER"
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
    #
    # SL PARAMS: identical semantics to SCALP_V1 — min_sl_points / max_sl_points
    # / risk_max_sl_points are consumed in StrategyEngine.on_candle (the V2 tick
    # engine routes signals through the same engine). risk_max_sl_points rejects
    # the signal upstream; the 3-leg split never sees it.
    # ==================================================
    "SCALP_V2": {
        "trade_execution_mode": "PAPER",

        "timeframe": "1m",

        # Signal entry math (cloned from SCALP_V1)
        "min_sl_points":      5,
        "max_sl_points":      0,
        "risk_max_sl_points": 0,
        "risk_reward_ratio":  1.0,

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
    #   - StrategyEngine(SCALP_V3) : min_sl_points, risk_max_sl_points,
    #                                risk_reward_ratio, max_sl_points
    #                                (signal-contract SL/TP math + entry gates)
    #   - scalp_v3_manager         : hedge_sl_points (= hedge SL distance:
    #                                  hedge_sl = hedge_fill - hedge_sl_points),
    #                                quantity.{lots,lot_size}, session.primary,
    #                                trade_execution_mode, max_loss/max_profit
    #   - scalp_v3_engine          : option_premium.{min,max}, trade_side_mode
    #
    # NOTE: max_sl_points caps the SIGNAL-contract SL (entry + max_sl) ONLY.
    # risk_max_sl_points rejects the signal upstream if risk_distance exceeds it
    # (0 = off). The hedge protective-stop distance is a SEPARATE field,
    # hedge_sl_points (fill - hedge_sl_points), read by scalp_v3_manager.
    # Option-A fallback: if hedge_sl_points is absent from an old config, the
    # manager falls back to max_sl_points so behaviour is unchanged until set.
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
        # max_sl_points caps the SIGNAL contract SL ONLY (no longer the hedge).
        # risk_max_sl_points rejects the signal if risk_distance > this (0 = off).
        "min_sl_points":      5,
        "max_sl_points":      20,
        "risk_max_sl_points": 0,
        "risk_reward_ratio":  1.7,
 
        # Hedge SL-only GTT distance (points below the hedge fill). DECOUPLED
        # from max_sl_points. If absent in an old config file, the manager
        # falls back to max_sl_points (Option A) so existing behaviour is
        # preserved until the user sets this in the UI.
        "hedge_sl_points":   20,
 
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
 
    # ==================================================
    # SCALP_V4 DEFAULT — SCALP_V3 + one extra entry gate
    # ==================================================
    # IDENTICAL to SCALP_V3 in every respect, including how each key is read
    # (StrategyEngine(SCALP_V4) for signal SL/TP math + entry gates;
    # scalp_v4_manager for the hedge SL distance + quantity + session +
    # execution mode; scalp_v4_engine for option_premium + trade_side_mode).
    # The ONLY behavioural difference is an extra entry rule applied as a veto
    # in the V4 tick engine (a SELL signal is dropped when EMA8 > EMA20_High) —
    # that veto needs no config key, so this default is a clone of the SCALP_V3
    # default plus the same risk_max_sl_points addition.
    #
    # max_sl_points does the same DOUBLE DUTY as in V3 (caps the signal-contract
    # SL AND, via the manager fallback, sets the hedge protective-stop distance
    # when hedge_sl_points is absent). risk_max_sl_points rejects the signal
    # upstream if risk_distance exceeds it (0 = off). trade_side_mode gates the
    # SIGNAL side (traded instrument is always the opposite).
    #
    # Isolation: no other strategy reads this entry. Removing SCALP_V4 = delete
    # this dict key + the scalp_v4 package + drop the scalp_v4_trades table.
    # ==================================================
    "SCALP_V4": {
        "trade_execution_mode": "PAPER",
 
        "min_sl_points":      5,
        "max_sl_points":      20,
        "risk_max_sl_points": 0,
        "risk_reward_ratio":  1.7,
 
        # Hedge SL-only GTT distance (points below the hedge fill). DECOUPLED
        # from max_sl_points (same as V3). Old configs fall back to
        # max_sl_points in the manager (Option A).
        "hedge_sl_points":   20,
 
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
    # ── SCALP_V5 BEGIN ──
    # ==================================================
    # SCALP_V5 DEFAULT — TEST option-BUYING, 3-minute candles
    # ==================================================
    # Read by:
    #   - StrategyEngine? NO — V5 uses its OWN ScalpV5Engine (4-gate LONG).
    #   - scalpv5_tick_engine : sl_points, tp_points (read live per candle),
    #                            timeframe, session.primary, trade_side_mode
    #   - scalpv5_manager     : trade_execution_mode, quantity.{lots,lot_size},
    #                            session.primary, max_loss, max_profit (self-MTM)
    #   - scalpv5_selection_loop : option_premium.{min,max}, timeframe
    #
    # SL/TP semantics (NEW — pure fixed points, NOT V1's risk-distance model):
    #   sl_points → SL = entry - sl_points   (0 = disabled)
    #   tp_points → TP = entry + tp_points   (0 = disabled)
    # When SL=0 & TP=0 the trade runs purely to its EMA exit (candle closes < EMA20_HIGH).
    #
    # max_loss / max_profit (rupees, 0 = disabled) enforced by the V5 manager's
    # self-contained MTM (V5 writes scalpv5_trades, which the shared risk guards
    # do not read). On breach: force-exit + session re-entry block.
    #
    # SAFETY: default execution mode is PAPER. enabled=False in the registry.
    #
    # Isolation: no other strategy reads this entry.
    # ==================================================
    "SCALP_V5": {
        "trade_execution_mode": "PAPER",
 
        "timeframe": "3m",
 
        # Fixed-point SL/TP (0 = disabled)
        "sl_points": 0,
        "tp_points": 0,
 
        # Daily risk limits (rupees, 0 = disabled) — self-contained MTM
        "max_loss":   0,
        "max_profit": 0,
 
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
 
        "trade_side_mode": "BOTH",
    },
    # ── SCALP_V5 END ──
    # ── IC_V1 BEGIN ──
    # ==================================================
    # IC_V1 DEFAULT — time-entry NIFTY weekly IRON CONDOR
    # ==================================================
    # Ships OFF: deploying the wiring changes nothing until the mode is
    # flipped in Settings. legs[] schema is IDENTICAL to the backtest (§3 of
    # IC_V1_STRATEGY_HANDOFF): lots 0 disables a leg (0 on L3/L4 = pure short
    # strangle); sl_val/tp_val 0 = disabled; sl_mode/tp_mode: "pct" | "pts".
    # lot_size is USER-SET (Settings) — never hardcoded in engine code.
    # freeze_qty: NSE per-order freeze limit (1800 for NIFTY, Mar-2026); the
    # group manager slices any leg qty above floor(freeze/lot_size)*lot_size.
    # ==================================================
    "IC_V1": {
        "trade_execution_mode": "OFF",
        "entry_time": "09:18",
        "exit_time":  "15:28",
        "entry_late_grace_s": 120,
        "freeze_qty": 1800,
        "allow_strangle_degrade": False,
        "margin_guard": True,

        "quantity": {
            "lot_size": 65
        },

        "legs": [
            {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 24,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L2"},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 24,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L1"},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 24,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 24,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
        ],
    },
    # ── IC_V1 END ──
}


def _get_strategy_path(strategy_id: str) -> Path:
    return STRATEGY_DIR / f"{strategy_id}.json"


# ── DEGRADED_READ_EX BEGIN ──────────────────────────────────────────
# load_strategy_config_ex is the NEW authoritative loader. It returns
# (config, degraded) where degraded=True means the on-disk config could not
# be read cleanly this instant and the returned dict is the in-memory
# default. Callers that make mode-sensitive decisions (e.g. HA_V1's
# HATradeManager._mode) MUST use this form so a transient I/O fault can be
# told apart from a genuine user setting.
#
# load_strategy_config (below) is a thin wrapper preserving the original
# signature and behaviour for every existing caller — zero behavioural
# change on clean reads.
#
# EXISTS() HOLE (closed 2026-07-06 — fd-exhaustion incident):
#   The previous `if not path.exists(): seed()` pre-check was unsafe under
#   OSError(24, 'Too many open files'): Path.exists() swallows OSError and
#   returns False, mis-routing an EXISTING file into the seed branch —
#   which runs save_strategy_config(default) and CLOBBERS the user's tuned
#   config. We now attempt the open directly and route on exception type:
#     FileNotFoundError → positively-confirmed absent → seed (first run)
#     any other error   → degraded → in-memory default, file UNTOUCHED
# ────────────────────────────────────────────────────────────────────

def load_strategy_config_ex(strategy_id: str):
    """
    Load a strategy config, merging the persisted file over the hardcoded
    default. Returns (config, degraded).

    SAFETY (revised — the 2026-06-15 paper→live flip postmortem):
      Previously, ANY read failure on an EXISTING file ran
      `save_strategy_config(strategy_id, default)` and returned the default.
      That was catastrophic: a transient I/O fault (the same fault throwing
      "unable to open database file" elsewhere) would OVERWRITE the user's
      real on-disk config with defaults — and SCALP_V1's default was LIVE.
      A momentary glitch thus became a PERMANENT paper→live flip that punched
      real orders until noticed by hand the next day.

      BEHAVIOUR:
        - File CONFIRMED ABSENT (FileNotFoundError from the open itself —
          genuine first run): seed the default to disk and return it. If even
          the seed write fails (I/O fault), return the default in-memory and
          flag degraded.
        - File PRESENT but UNREADABLE this instant (transient I/O, fd
          exhaustion, or genuine corruption): return the default IN MEMORY
          for this single call, but DO NOT TOUCH THE FILE. The next clean
          read recovers the user's real settings. The degraded read is
          logged LOUDLY so it is never again an invisible flip.

      This change protects EVERY key in the file (premium ranges, lot sizes,
      sessions, …), not just the execution mode — a transient read can no
      longer silently reset a user's tuned parameters to defaults.
    """
    path    = _get_strategy_path(strategy_id)
    default = deepcopy(DEFAULT_STRATEGY_CONFIGS.get(strategy_id, {}))

    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        # Positively-confirmed absent — genuine first run. Seed the default.
        # (Safe: there is no user data to clobber, and the default is now
        # PAPER for every strategy.) Best-effort: a seed-write failure under
        # an I/O fault must not raise into the caller.
        try:
            save_strategy_config(strategy_id, default)
        except Exception as e:
            write_audit_log(
                f"[CONFIG][SEED_FAILED] {strategy_id}: config file absent and "
                f"the seed write failed ({e!r}) — using in-memory default this "
                f"call (degraded)."
            )
            return _apply_bt_override(strategy_id, default), True
        return _apply_bt_override(strategy_id, default), False
    except Exception as e:
        # File EXISTS (or existence could not be confirmed) but could not be
        # read/parsed right now. DO NOT WRITE. Return the default in-memory
        # only; leave the on-disk file intact so a later clean read recovers
        # the user's real config. Log loudly so this is traceable in seconds,
        # not hours.
        write_audit_log(
            f"[CONFIG][READ_DEGRADED] {strategy_id}: existing config could not be "
            f"read ({e!r}) — using IN-MEMORY default for THIS call only, file left "
            f"UNTOUCHED. Execution mode falls back to PAPER. If this repeats, the "
            f"machine has an I/O/disk problem that must be fixed."
        )
        return _apply_bt_override(strategy_id, default), True

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

    # BACKTEST override (no-op when unset) — LAST step so it wins over disk.
    return _apply_bt_override(strategy_id, merged), False


def load_strategy_config(strategy_id: str) -> dict:
    """
    Original public loader — thin wrapper over load_strategy_config_ex.
    Identical return value and semantics for every existing caller; the
    degraded flag is simply dropped. Callers that must distinguish a degraded
    read (HA_V1 mode resolution) use load_strategy_config_ex directly.
    """
    cfg, _degraded = load_strategy_config_ex(strategy_id)
    return cfg
# ── DEGRADED_READ_EX END ────────────────────────────────────────────


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
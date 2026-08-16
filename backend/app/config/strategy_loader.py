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
        "max_sl_points":      20,
        "risk_max_sl_points": 0,
        "risk_reward_ratio":  1.7,

        "session": {
            "primary": {
                "start": "09:30",
                "end":   "15:15"
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
            "lots":     10,
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

        "sl_pct":  25,
        "tp_pct":  100,
        "lots":    10,

        "multiple_targets": False,
        "tp1_pct":          15,
        "tp2_pct":          80,
        "lots_leg1":        1,
        "lots_leg2":        9,
        "trailing_sl":      False,

        "max_premium":         305,
        "max_trades_per_side": 10,

        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:14",

        "st_exit_gap": 0,
    },

    # ==================================================
    # BB_V2 DEFAULT  (unchanged)
    # ==================================================
    "BB_V2": {
        "trade_execution_mode": "PAPER",

        "sl_pct": 25,
        "tp_pct": 75,

        "ce_lots": 10,
        "pe_lots": 10,

        "max_premium":         305,
        "max_trades_per_side": 10,

        "auto_square_off_time": "15:15",
        "session_start":        "09:15",
        "session_end":          "15:14",
    },

    # ==================================================
    # HA_V1 DEFAULT  (unchanged)
    # ==================================================
    "HA_V1": {
        "trade_execution_mode": "PAPER",

        "risk_reward_ratio": 2.8,

        # Screenshot rebaseline 2026-08-04: skip entry if (entry − SL) < this
        "min_sl_points": 18,

        "target_override": {
            "enabled": False,
            "points":  8
        },

        "option_premium": {
            "min": 150,
            "max": 200
        },

        "quantity": {
            "lots":     1,
            "lot_size": 65
        },

        "max_trades_per_side": 1,

        "session": {
            "primary": {
                "start": "09:30",
                "end":   "13:00"
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
                "end":   "11:00"
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
            "lots":     10,
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
        "sl_points": 16,
        "tp_points": 0,
 
        # Daily risk limits (rupees, 0 = disabled) — self-contained MTM
        "max_loss":   0,
        "max_profit": 0,
 
        "session": {
            "primary": {
                "start": "10:00",
                "end":   "15:00"
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
            "lots":     10,
            "lot_size": 65
        },
 
        "trade_side_mode": "BOTH",
    },
    # ── SCALP_V5 END ──
    # ── IC BEGIN (IC_SPLIT: shared V1/V2, 2026-08-04) ──
    # ==================================================
    # TWO defaults for ONE shared engine (app/engine/ic/). legs[] schema is
    # IDENTICAL to the backtest (§3 of IC_V1_STRATEGY_HANDOFF): lots 0
    # disables a leg (0 on L3/L4 = pure short strangle); sl_val/tp_val 0 =
    # disabled; sl_mode/tp_mode: "pct" | "pts". lot_size is USER-SET
    # (Settings) — never hardcoded in engine code. freeze_qty: NSE per-order
    # freeze limit (1800 for NIFTY, Mar-2026); the group manager slices any
    # leg qty above floor(freeze/lot_size)*lot_size. Both ship OFF.
    #
    # IC_V1 — LEGACY condor (backtest IC_V1 parity): exit_mode "EOD" (full
    # square-off at exit_time, continuous engine backstop + 15:25 job), NO
    # adjustments, NO overnight carry. The V2-only keys are present but
    # explicitly OFF — same switch semantics as the backtest engine, where
    # "both switches off" is provably byte-identical to IC_V1.
    # ==================================================
    "IC_V1": {
        "trade_execution_mode": "OFF",
        "entry_time": "09:18",
        "exit_time":  "15:15",  # CAS_2026: square-off pre-CAS-freeze (was 15:28)
        "entry_late_grace_s": 120,
        "freeze_qty": 1800,
        "allow_strangle_degrade": False,
        "margin_guard": True,

        # ── legacy semantics: both IC_V2 switches OFF ──
        "exit_mode": "EOD",
        "adjust_on_sl": False,
        "adjust_only": False,

        # SL-GTT limit buffer (percent above trigger for the buy-back
        # limit). Gap defence layer 1 — the historical 0.3% rests
        # off-market on any fast move.
        "gtt_limit_buffer_pct": 5,

        "quantity": {
            "lot_size": 65
        },

        "legs": [
            {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L2"},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L1"},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
        ],
    },
    # ==================================================
    # IC_V2 — the pre-split live behavior, verbatim (backtest IC_V2 parity).
    # ==================================================
    "IC_V2": {
        "trade_execution_mode": "OFF",
        "entry_time": "09:18",
        "exit_time":  "15:28",
        "entry_late_grace_s": 120,
        "freeze_qty": 1800,
        "allow_strangle_degrade": False,
        "margin_guard": True,

        # ── IC_V2 SEMANTICS (locked 2026-07-26, backtest-validated) ──
        # exit_mode NEXT_OPEN = ONE_NIGHT_MAX: legs open at session end
        # carry ONE night and close at next session's next_open_time
        # unconditionally (incl. expiry day — MORNING_SQUARE_OFF fix).
        # The expiry-day square-off (expiry_exit_time) applies ONLY to
        # legs entered that day. exit_mode "EOD" restores legacy IC_V1.
        "exit_mode": "NEXT_OPEN",
        "next_open_time": "09:16",
        "expiry_exit_time": "15:15",

        # ADJ_ON_MTC: a short stop exit (SL *or* MTC_COST, 2026-07-24
        # reversal) arms a BUY adjustment on the same side after
        # adjust_delay_s. Strike = highest premium <= premium_max at
        # activation, FAIL CLOSED. adjust_only=True runs the condor as a
        # SIMULATION and books only ·ADJ legs (backtest ADJ_ONLY).
        "adjust_on_sl": True,
        "adjust_delay_s": 60,
        "adjust_only": False,
        "adjust": {
            "L1": {"enabled": True, "lots": 10, "premium_max": 85,
                   "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
            "L2": {"enabled": True, "lots": 10, "premium_max": 85,
                   "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
        },

        # SL-GTT limit buffer (percent above trigger for the buy-back
        # limit). Gap defence layer 1 — the historical 0.3% rests
        # off-market on any fast move.
        "gtt_limit_buffer_pct": 5,

        "quantity": {
            "lot_size": 65
        },

        "legs": [
            {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L2"},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10,
             "premium_max": 85, "sl_val": 42, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L1"},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10,
             "premium_max": 4, "sl_val": 0, "sl_mode": "pct",
             "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": False, "mtc_partner": None},
        ],
    },
    # ── IC END ──
    # ==================================================
    # PST_SELL / PST_HEDGE DEFAULTS — paper phase. Same config shape the
    # backtest uses (signal params fixed; legs carry sl_pct /
    # spot_tg_points; risk keys are V3-semantics entry-gates in Phase 1).
    # ==================================================
    "PST_SELL": {
        "trade_execution_mode": "PAPER",
        "premium_max": 150,
        "side_mode": "BOTH",
        "max_trades_per_day": 0,
        "exit_time": "15:15",
        "entry_cutoff_time": "15:00",
        "signal_tf": 3,
        "sma": {"period": 9, "tf": 5},
        "supertrend": {"period": 10, "mult": 2, "tf": 3},
        "legs": [
            {"id": "L1", "lots": 10, "sl_pct": 20, "spot_tg_points": 30},
            {"id": "L2", "lots": 0, "sl_pct": 0, "spot_tg_points": 0},
        ],
        "daily_max_loss": 0, "daily_max_profit": 0,
        "monthly_max_loss": 0, "monthly_max_profit": 0,
    },
    "PST_HEDGE": {
        "trade_execution_mode": "PAPER",
        "premium_max": 150,
        "side_mode": "BOTH",
        "max_trades_per_day": 0,
        "exit_time": "15:15",
        "entry_cutoff_time": "15:00",
        "signal_tf": 3,
        "sma": {"period": 9, "tf": 5},
        "supertrend": {"period": 10, "mult": 2, "tf": 3},
        "legs": [
            {"id": "L1", "lots": 10, "sl_pct": 20, "spot_tg_points": 30},
            {"id": "L2", "lots": 0, "sl_pct": 0, "spot_tg_points": 0},
        ],
        "daily_max_loss": 0, "daily_max_profit": 0,
        "monthly_max_loss": 0, "monthly_max_profit": 0,
    },
    # ── TMA_V1 BEGIN ──
    # ==================================================
    # TMA_V1 DEFAULT — Triple-EMA (5/13/89 @5m spot) credit spread on NIFTY
    # weekly options. Schema per the frozen 2026-07-19 build spec:
    # { mode, trade_mode, cut_neg_mtm_eod, session_start/end, exit_time,
    #   wing_mode (real_fallback|skip — NO synthetic in live),
    #   c1: { sell: {premium_max, lots, sl_pct, tp_pct, sl_unit, tp_unit},
    #         buy: {premium_max, lots}, max_trades_per_day } }
    # sl_unit/tp_unit: PCT | PTS | ABS (per-field; wrong-side ABS clamps
    # off — identical math to the backtest runner's SLTP_UNITS block).
    # Ships mode=PAPER (spec decision — paper AND live built now; go-live
    # readiness is the user's call, flipped in Settings).
    # ==================================================
    "TMA_V1": {
        "trade_execution_mode": "PAPER",
        "trade_mode": "POSITIONAL",        # INTRADAY | POSITIONAL
        "cut_neg_mtm_eod": True,           # "Cut losers, carry winners"
        "session_start": "09:15",
        "session_end":   "15:15",
        "exit_time":     "15:15",
        "wing_mode": "real_fallback",      # real_fallback | skip
        "margin_guard": False,             # screenshot 2026-08-04: guard off

        "quantity": {
            "lot_size": 65
        },

        "c1": {
            "sell": {"premium_max": 150, "lots": 10,
                     "sl_pct": 13, "tp_pct": 1,
                     "sl_unit": "PCT", "tp_unit": "ABS"},
            "buy":  {"premium_max": 5, "lots": 10},
            "max_trades_per_day": 0
        },
    },
    # ── TMA_V1 END ──
    # ── TSG_V1 BEGIN ──
    # TSG_V1 DEFAULT — 09:16 weekly strangle (backtest-validated config
    # 2026-08-02: MTM SL 35k, target 0, IV Δ+4 pts, trail rejected).
    # expiry_lots (LD5): 0/blank = lots; a nonzero value overrides lots on
    # the contract's expiry day only (live/paper-only knob).
    # ==================================================
    "TSG_V1": {
        "trade_execution_mode": "PAPER",
        "entry_time": "09:16",
        "exit_time": "15:15",
        "entry_late_grace_s": 120,
        "lots": 10,
        "expiry_lots": 12,
        "lot_size": 65,
        "mtm_sl": 35000,
        "mtm_target": 0,
        "iv_sl_delta_pts": 4,
        "iv_sl_pct": 25,
        "min_entry_iv": 0.10,   # LD11/IV13 entry-IV floor (validated 2026-08-03)
        "legs": [
            {"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 85},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 85},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 5},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 5},
        ],
    },
    # ── GC_V1 BEGIN ──
    # GC_V1 DEFAULT — LD-sheet 2026-08-15. Ships PAPER fail-closed; the
    # validated NIFTY campaign config replaces these knobs before any LIVE.
    # NIFTY-only by design: no underlying/lot/stock keys exist in live.
    # ==================================================
    "GC_V1": {
        "trade_execution_mode": "PAPER",
        "mode": "SELL",
        "exit_time": "15:15",
        "entry_cutoff_time": "15:00",
        "max_trades_per_day": 5,
        "premium_max": 200,
        "hedge_premium_max": 5,
        "lots": 1,
        "signal_mode": "latest",
        "sl_lookback": 10,
        "c1_range_max_pct": 0.15,
        "max_sl_pct": 0.05,
        "max_profit_day": 0,
        "max_loss_day": 0,
        "max_loss_per_trade": 0,
        "max_profit_per_trade": 0,
    },
    # ── GC_V1 END ──
    # ── TSG_V1 END ──
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
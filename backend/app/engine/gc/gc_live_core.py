# backend/app/engine/gc/gc_live_core.py
#
# ── GC_V1 LIVE CORE (2026-08-15, LD-sheet locked) ── pure decision core.
# ZERO app imports except the PURE backtest engine — that exception is the
# whole point (LD6 parity contract): live does not reimplement GC, it
# REPLAYS gc_v1_engine.simulate_gc_day over the day's 1m candles at every
# close and DIFFS the result against actions already executed. The live
# brain IS the backtest brain; they cannot disagree by construction.
# Replay cost: O(candles) per close, ≤375 candles/day, sub-millisecond.
#
# LD locks reflected here:
#   LD2  both modes (BUY / SELL opp-side)      LD9  NO month cap in live
#   1m only (no resampling; tf_s = 60)         LD10 exit_time ≤ 15:20 clamp
#   LD8  premium-cap selection + ₹-cap hedge (SELL), fail-closed
#
# The manager (impure wrapper) owns: candle fetch, quotes, executor calls,
# paper_trades rows, persistence. This module owns every DECISION.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.backtest.gc.gc_v1_engine import (          # pure module
        TFCandle, GCSpotTrade, simulate_gc_day)
except ImportError:                                      # standalone tests
    from backtest.gc.gc_v1_engine import (               # type: ignore
        TFCandle, GCSpotTrade, simulate_gc_day)

STRATEGY_ID = "GC_V1"
LOT_SIZE = 65
SESSION_OPEN_MIN = 9 * 60 + 15


# ── config ──────────────────────────────────────────────────────────────────

DEFAULT_LIVE_CFG = {
    "trade_execution_mode": "PAPER",   # fail-closed house default
    "mode": "SELL",                    # BUY | SELL (opp side) — LD2 both
    "exit_time": "15:15",              # clamped ≤ 15:20 (LD10)
    "entry_cutoff_time": "13:00",
    "max_trades_per_day": 4,
    "premium_max": 200,
    "hedge_premium_max": 5,            # SELL only; 0 = no hedge (LD8)
    "lots": 1,
    "signal_mode": "latest",
    "sl_lookback": 10,
    "c1_range_max_pct": 0.15,
    "c1_skip_candles": 0,     # ── GC_C1_SKIP ── must mirror the backtest
    "max_sl_pct": 0.3,
    "max_profit_day": 0,               # gross ₹ at 1m closes; 0 = off
    "max_loss_day": 0,
    "max_loss_per_trade": 0,           # combined (sold+hedge) gross MTM
    "max_profit_per_trade": 0,
}


def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v <= 24 * 60 else default_min
    except Exception:
        return default_min


def norm_live_cfg(raw: Optional[dict]) -> dict:
    """Merge onto defaults + clamp. LD10: exit ≤ 15:20 so the generic 15:25
    paper sweep stays a pure backstop (no squareoff exemption needed)."""
    cfg = dict(DEFAULT_LIVE_CFG)
    for k in cfg:
        if raw and k in raw and raw[k] is not None:
            cfg[k] = raw[k]
    cfg["mode"] = "SELL" if str(cfg["mode"]).upper() == "SELL" else "BUY"
    exit_min = min(_hm_to_min(cfg["exit_time"], 15 * 60 + 15), 15 * 60 + 20)
    cfg["exit_time"] = f"{exit_min // 60:02d}:{exit_min % 60:02d}"
    cfg["entry_cutoff_time"] = str(cfg["entry_cutoff_time"] or "13:00")
    cfg["max_trades_per_day"] = max(0, int(cfg["max_trades_per_day"] or 0))
    cfg["premium_max"] = abs(float(cfg["premium_max"] or 0))
    cfg["hedge_premium_max"] = abs(float(cfg["hedge_premium_max"] or 0))
    cfg["lots"] = max(1, int(cfg["lots"] or 1))
    cfg["signal_mode"] = ("first" if str(cfg["signal_mode"]).lower() == "first"
                          else "latest")
    cfg["sl_lookback"] = max(1, int(cfg["sl_lookback"] or 10))
    cfg["c1_range_max_pct"] = abs(float(cfg["c1_range_max_pct"] or 0))
    cfg["c1_skip_candles"] = max(0, int(cfg.get("c1_skip_candles") or 0))
    cfg["max_sl_pct"] = abs(float(cfg["max_sl_pct"] or 0))
    for k in ("max_profit_day", "max_loss_day",
              "max_loss_per_trade", "max_profit_per_trade"):
        cfg[k] = abs(float(cfg[k] or 0))
    return cfg


def engine_cfg_for_day(cfg: dict, day_start_epoch: int,
                       prev_close: Optional[float]) -> dict:
    """Exactly the runner's engine-cfg assembly (1m: tf_s = 60)."""
    return {
        "exit_epoch": day_start_epoch
        + _hm_to_min(cfg["exit_time"], 15 * 60 + 15) * 60,
        "tf_s": 60,
        "max_trades": cfg["max_trades_per_day"],
        "entry_cutoff_epoch": day_start_epoch
        + _hm_to_min(cfg["entry_cutoff_time"], 13 * 60) * 60,
        "signal_mode": cfg["signal_mode"],
        "sl_lookback": cfg["sl_lookback"],
        "c1_range_max_pct": cfg["c1_range_max_pct"],
        "c1_skip_candles": cfg["c1_skip_candles"],
        "prev_close": prev_close,
        "max_sl_pct": cfg["max_sl_pct"],
    }


# ── replay + diff ───────────────────────────────────────────────────────────

@dataclass
class Action:
    """One executable instruction, materialized at the LAST candle only."""
    kind: str                    # "ENTER" | "EXIT"
    trade_seq: int               # index into the sim's trade list
    signal_side: str             # engine signal ("CE"/"PE")
    flip_seq: int
    ts: int                      # decision minute (last1m_ts)
    spot: float                  # decision close (entry) / exit spot
    sl_level: float
    exit_reason: Optional[str] = None


def replay_and_diff(candles: List[TFCandle], prev_tail: List[TFCandle],
                    ecfg: dict, executed_entries: int,
                    executed_exits: int) -> Tuple[Dict, List[Action]]:
    """Re-run the pure engine on candles-so-far; emit only actions whose
    decision candle IS the latest candle and which the manager hasn't
    executed yet. Same-candle SL yields ENTER then EXIT in one call —
    the manager executes both in order (divergence-ledger item: two live
    market orders seconds apart vs one backtest candle)."""
    sim = simulate_gc_day(candles, prev_tail, ecfg)
    if not candles:
        return sim, []
    last_idx = len(candles) - 1
    # ── EOD is BOUNDARY-GATED ── a prefix replay stamps any open trade as
    # "EOD at the latest candle" — a truncation artifact, not an exit. A
    # genuine EOD exists only when the latest candle is the day's final
    # participating one (the NEXT candle's end would cross exit_epoch).
    # SL and cutoff-driven exits are decision-based at their candle and are
    # genuine on any prefix. (Caught by the parity test on day one.)
    tf_s = int(ecfg.get("tf_s") or 60)
    at_eod = (candles[-1].ts + 2 * tf_s) > int(ecfg["exit_epoch"])
    out: List[Action] = []
    trades: List[GCSpotTrade] = sim["trades"]
    entries_after = executed_entries
    for i, t in enumerate(trades):
        entering_now = (i >= executed_entries and t.entry_idx == last_idx)
        if entering_now:
            out.append(Action("ENTER", i, t.signal_side, t.flip_seq,
                              t.entry_ts, t.entry_spot, t.sl_level))
            entries_after += 1
        held = (i < entries_after)                    # entered before or now
        if (held and i >= executed_exits and t.exit_idx == last_idx
                and (t.exit_reason != "EOD" or at_eod)):
            out.append(Action("EXIT", i, t.signal_side, t.flip_seq,
                              t.exit_ts, t.exit_spot or 0.0, t.sl_level,
                              exit_reason=t.exit_reason))
    return sim, out


def stable_history_check(sim_trades: List[GCSpotTrade],
                         executed: List[dict]) -> Optional[str]:
    """Parity tripwire: everything ALREADY executed must still be exactly
    what the replay says happened (entry_ts + side per seq). A mismatch
    means the candle feed rewrote history (vendor correction) — the
    manager halts the day rather than trade a diverged brain."""
    for i, ex in enumerate(executed):
        if i >= len(sim_trades):
            return f"replay lost trade seq {i}"
        t = sim_trades[i]
        if t.entry_ts != ex["entry_ts"] or t.signal_side != ex["signal_side"]:
            return (f"seq {i}: replay says {t.signal_side}@{t.entry_ts}, "
                    f"executed {ex['signal_side']}@{ex['entry_ts']}")
    return None


# ── option-leg planning (LD8) ───────────────────────────────────────────────

@dataclass
class LegPlan:
    action: str                  # "BUY" | "SELL"
    role: str                    # "MAIN" | "HEDGE"
    symbol: str
    ltp: float
    qty: int


def _select_by_premium(cands: List[Tuple[str, float]],
                       cap: float) -> Optional[Tuple[str, float]]:
    """Highest LTP ≤ cap (backtest select_strike semantics)."""
    ok = [c for c in cands if 0 < c[1] <= cap]
    return max(ok, key=lambda c: c[1]) if ok else None


def plan_legs(*, signal_side: str, cfg: dict,
              chain: List[Tuple[str, str, float]]) -> Tuple[List[LegPlan], str]:
    """chain = [(symbol, opt_type 'CE'/'PE', ltp)] for the front weekly.
    Returns (legs, reason). Empty legs + reason = fail-closed skip.
    BUY mode: buy the SIGNAL side. SELL mode: sell the OPPOSITE side
    (D11) + optional same-side BUY hedge ≤ hedge_premium_max; hedge
    wanted but unfillable → skip the entry entirely (never a naked short
    where a hedged one was configured). Basket order: HEDGE FIRST."""
    mode = cfg["mode"]
    qty = cfg["lots"] * LOT_SIZE
    opt_side = (signal_side if mode == "BUY"
                else ("PE" if signal_side == "CE" else "CE"))
    ladder = [(s, p) for (s, t, p) in chain if t == opt_side and p and p > 0]
    if not ladder:
        return [], f"no live {opt_side} quotes"
    main = _select_by_premium(ladder, cfg["premium_max"])
    if main is None:
        return [], f"no {opt_side} strike ≤ {cfg['premium_max']}"
    if mode == "BUY":
        return [LegPlan("BUY", "MAIN", main[0], main[1], qty)], "ok"
    legs = [LegPlan("SELL", "MAIN", main[0], main[1], qty)]
    hcap = cfg["hedge_premium_max"]
    if hcap > 0:
        rest = [c for c in ladder if c[0] != main[0]]
        hedge = _select_by_premium(rest, hcap)
        if hedge is None and rest:
            cheap = min(rest, key=lambda c: c[1])       # flagged fallback
            hedge = cheap if cheap[1] < main[1] else None
        if hedge is None:
            return [], f"hedge ≤ {hcap} unfillable — entry skipped (fail-closed)"
        legs.insert(0, LegPlan("BUY", "HEDGE", hedge[0], hedge[1], qty))
    return legs, "ok"


# ── cap book (day ± / per-trade ±, combined MTM at 1m closes) ───────────────

def combined_open_mtm(position: List[dict],
                      ltp: Dict[str, float]) -> Optional[float]:
    """position rows: {symbol, action BUY/SELL, entry_price, qty}. None when
    any leg lacks a quote (caps must not fire on partial marks)."""
    total = 0.0
    for leg in position:
        px = ltp.get(leg["symbol"])
        if px is None or px <= 0:
            return None
        d = (px - leg["entry_price"]) if leg["action"] == "BUY" \
            else (leg["entry_price"] - px)
        total += d * leg["qty"]
    return total


def cap_cut(*, day_realized: float, open_mtm: float,
            cfg: dict) -> Optional[str]:
    """Backtest-runner priority: DAY caps first (they halt), then per-trade.
    Returns the exit reason or None. LD9: no month cap in live."""
    day_mtm = day_realized + open_mtm
    if cfg["max_profit_day"] > 0 and day_mtm >= cfg["max_profit_day"]:
        return "MAX_PROFIT_DAY"
    if cfg["max_loss_day"] > 0 and day_mtm <= -cfg["max_loss_day"]:
        return "MAX_LOSS_DAY"
    if cfg["max_profit_per_trade"] > 0 and open_mtm >= cfg["max_profit_per_trade"]:
        return "MAX_PROFIT_TRADE"
    if cfg["max_loss_per_trade"] > 0 and open_mtm <= -cfg["max_loss_per_trade"]:
        return "MAX_LOSS_TRADE"
    return None


def cut_halts_day(reason: str) -> bool:
    return reason in ("MAX_PROFIT_DAY", "MAX_LOSS_DAY")


# ── candle plumbing (pure transforms for the manager) ───────────────────────

def to_tf_candles(rows: List[dict]) -> List[TFCandle]:
    """rows: [{ts, open, high, low, close}] 1m START-stamped, ascending.
    1m only (locked): TFCandle.last1m_ts == ts."""
    return [TFCandle(ts=int(r["ts"]), open=float(r["open"]),
                     high=float(r["high"]), low=float(r["low"]),
                     close=float(r["close"]), last1m_ts=int(r["ts"]))
            for r in rows]


def closed_only(candles: List[TFCandle], now_epoch: int) -> List[TFCandle]:
    """Decisions ONLY at closes (LD6): a candle participates once its
    minute has fully elapsed."""
    return [c for c in candles if c.ts + 60 <= now_epoch]
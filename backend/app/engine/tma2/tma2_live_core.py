# backend/app/engine/tma2/tma2_live_core.py
#
# ── TMA_V2 LIVE CORE ── PURE (no app imports on the hot path, unit-tested)
# ============================================================================
# THE PARITY DESIGN (PST doctrine): the live/paper monitor must be an
# event-driven mirror of the backtest's monitor_position_day per-candle body —
# proven by test_tma2_live_core.py, which runs BOTH over randomized days and
# asserts identical exits (ts, price, reason, ambiguity). Any edit here that
# breaks that equivalence fails the test, not the parity.
#
# step_candle() advances one open SHORT (SELL) position through ONE completed
# 1m option candle, byte-identical semantics to the engine loop
# (tma_v1_engine.monitor_position_day, SHORT branch):
#   * GAP/level SL: open >= sl -> fill at OPEN; else high >= sl -> AT sl.
#   * GAP/level TP: open <= tp -> fill at OPEN; else low  <= tp -> AT tp.
#   * SL + TP one candle -> SL wins + ambiguous (house convention).
#   * XOVER: candle ts >= xover_ts and still open -> fill at candle CLOSE.
#   * MTM_CUT (positional opt-in): at the FIRST candle with ts >= mtm_cut_ts
#     the mark strictly BEFORE it (pos.last_close) vs entry decides —
#     mark > entry (short losing) -> close at that mark; else disarm.
#   * hard bound: caller stops feeding candles at ts >= hard_close_ts and
#     calls hard_close() -> exit at the last folded candle (EOD/expiry).
# The hedge (BUY leg) is not monitored — it follows the SELL leg's exit
# minute (spec: SELL leg drives ALL exits).
#
# sltp_levels() replicates the runner's ── SLTP_UNITS ── math exactly
# (PCT | PTS | ABS per field; wrong-side ABS clamps OFF; TP floored 0.05).
# ============================================================================

from __future__ import annotations

from typing import Dict, Optional


def sltp_levels(entry: float, sl_val: float, tp_val: float,
                sl_unit: str = "PCT", tp_unit: str = "PCT",
                legacy_unit: Optional[str] = None):
    """SELL-leg premium levels from the c1.sell config — identical to the
    backtest runner's SLTP_UNITS block. Returns (sl_level, tp_level),
    either None when disabled (val<=0) or clamped off (wrong-side ABS)."""
    ep = float(entry)
    _legacy = str(legacy_unit or "PCT").upper()
    su = str(sl_unit or _legacy).upper()
    tu = str(tp_unit or _legacy).upper()
    slp = float(sl_val or 0)
    tpp = float(tp_val or 0)
    if slp > 0:
        if su == "ABS":
            sl_level = slp if slp > ep else None
        elif su == "PTS":
            sl_level = ep + slp
        else:
            sl_level = ep * (1 + slp / 100.0)
    else:
        sl_level = None
    if tpp > 0:
        if tu == "ABS":
            tp_level = max(0.05, tpp) if tpp < ep else None
        elif tu == "PTS":
            tp_level = max(0.05, ep - tpp)
        else:
            tp_level = max(0.05, ep * (1 - tpp / 100.0))
    else:
        tp_level = None
    return sl_level, tp_level


def new_position(*, entry_ts: int, entry_price: float,
                 sl_price: Optional[float], tp_price: Optional[float],
                 mtm_cut_ts: Optional[int] = None,
                 watch_from: Optional[int] = None) -> Dict:
    """Position state mirroring the engine's pos dict (SHORT / action SELL).
    watch_from defaults to entry_ts + 60 (monitoring starts one candle after
    the fill candle — engine convention); carried days pass watch_from=0."""
    return {
        "entry_ts": int(entry_ts),
        "entry_price": float(entry_price),
        "sl_price": (float(sl_price) if sl_price is not None else None),
        "tp_price": (float(tp_price) if tp_price is not None else None),
        "watch_from": int(entry_ts) + 60 if watch_from is None else int(watch_from),
        "last_close": float(entry_price),
        "last_ts": int(entry_ts),
        "mtm_pending": mtm_cut_ts is not None,
        "mtm_cut_ts": mtm_cut_ts,
    }


def step_candle(pos: Dict, c: Dict,
                xover_ts: Optional[int]) -> Optional[Dict]:
    """Advance the SHORT position through ONE completed 1m option candle.
    Returns {exit_ts, exit_price, exit_reason, ambiguous} or None (survives).
    MUTATES pos.last_close/last_ts and the MTM latch — same as the engine.
    The caller enforces the hard bound (never feed candles at/after
    hard_close_ts) and calls hard_close() when the bound is reached."""
    ts = int(c["ts"])
    if ts < int(pos.get("watch_from") or 0):
        return None

    # ── NEG_MTM_EOD_CUT ── decision at the cut instant uses the mark
    # strictly BEFORE it (last_close — this candle not folded in yet).
    if pos.get("mtm_pending") and pos.get("mtm_cut_ts") is not None \
            and ts >= pos["mtm_cut_ts"]:
        pos["mtm_pending"] = False
        mark = pos.get("last_close", pos["entry_price"])
        if mark > pos["entry_price"]:          # SHORT: mark above entry = losing
            return {"exit_ts": pos.get("last_ts", pos["entry_ts"]),
                    "exit_price": pos.get("last_close", pos["entry_price"]),
                    "exit_reason": "MTM_CUT", "ambiguous": False}

    o, h, l, cl = (float(c["open"]), float(c["high"]),
                   float(c["low"]), float(c["close"]))
    pos["last_close"], pos["last_ts"] = cl, ts

    sl_price, tp_price = pos.get("sl_price"), pos.get("tp_price")
    # SHORT semantics (engine short branch, verbatim):
    gap_sl = sl_price is not None and o >= sl_price
    gap_tp = tp_price is not None and o <= tp_price
    hit_sl = sl_price is not None and h >= sl_price
    hit_tp = tp_price is not None and l <= tp_price
    if hit_sl:
        return {"exit_ts": ts, "exit_price": (o if gap_sl else sl_price),
                "exit_reason": "SL", "ambiguous": bool(hit_tp)}
    if hit_tp:
        return {"exit_ts": ts, "exit_price": (o if gap_tp else tp_price),
                "exit_reason": "TP", "ambiguous": False}
    if xover_ts is not None and ts >= xover_ts:
        return {"exit_ts": ts, "exit_price": cl,
                "exit_reason": "XOVER", "ambiguous": False}
    return None


def mtm_cut_after_data_gap(pos: Dict) -> Optional[Dict]:
    """Engine's post-loop MTM check: the day's data ended BEFORE the cut
    time — apply the check on the best-available mark rather than silently
    carrying a negative position on a data gap."""
    if not pos.get("mtm_pending"):
        return None
    mark = pos.get("last_close", pos["entry_price"])
    if mark > pos["entry_price"]:
        return {"exit_ts": pos.get("last_ts", pos["entry_ts"]),
                "exit_price": pos.get("last_close", pos["entry_price"]),
                "exit_reason": "MTM_CUT", "ambiguous": False}
    return None


def hard_close(pos: Dict, reason: str = "EOD") -> Dict:
    """EOD / expiry square-off: exit at the last folded candle's close
    (engine convention: last candle strictly before the bound)."""
    return {"exit_ts": pos.get("last_ts", pos["entry_ts"]),
            "exit_price": pos.get("last_close", pos["entry_price"]),
            "exit_reason": reason, "ambiguous": False}
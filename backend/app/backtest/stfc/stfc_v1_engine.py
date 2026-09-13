# backend/app/backtest/stfc/stfc_v1_engine.py
#
# ── STFC_V1 ENGINE ── "SuperTrend Flip-Confirm" signal core (pure, no I/O).
#
# Fence: STFC_OPT_20260913
#
# Port of the TradingView "ST Flip-Confirm Scalper v2" indicator and the
# Crypto Lab engine (crypto_stfc_engine.py), kept in lock-step:
#   D1  Bars: 1m SPOT resampled to `tf` minutes on the NSE session grid
#       (buckets from 09:15, same as TradingView's NSE charts). A bar's
#       end minute is recorded so "candle close" exits can land on it.
#   D2  SuperTrend(len, mult) is a bit-for-bit port of Pine's ta.supertrend:
#       ATR = RMA(TR) seeded with the SMA of the first `len` TRs, hl2 source,
#       band ratcheting, direction −1 = up-trend / +1 = down-trend. The
#       indicator state is CONTINUOUS across sessions (TradingView parity —
#       the chart does not reset ATR at 09:15); the runner warms it up on
#       sessions before date_from.
#   D3  Setup machine, per tf bar, in Pine order:
#         1. if armed by the previous bar → this bar is a TRADE bar
#            (entry at its open = the 1m open of its first minute)
#         2. flip on this bar → C1 is ignored; pending := new direction
#         3. else pending and the bar's colour matches → arm the next bar
#       A fresh flip while pending restarts the setup; a doji never
#       confirms. The machine RESETS at each session open (no overnight
#       carry — an arm on the last bar of a day is lost, counted by the
#       runner).
#   D4  Premium fills (pessimistic, same helper as ORB/CBO): a level touched
#       inside a 1m bar books AT the level; gapped through at the open books
#       the open.
#
# Fleet convention: no imports from sibling strategy packages; helpers are
# duplicated here so the module stays self-contained.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SESSION_OPEN_MIN = 9 * 60 + 15        # NSE index grid opens 09:15


@dataclass(frozen=True)
class StfcBar:
    ts: int                            # epoch seconds, bar START, IST grid
    open: float
    high: float
    low: float
    close: float
    end_min: int = 0                   # minute-of-day of the bar's LAST 1m slot


# ─────────────────────────────────────────────────────────────────────────
#  RESAMPLE — session-aligned tf buckets from 09:15
# ─────────────────────────────────────────────────────────────────────────
def resample_session(bars_1m, *, day_start_epoch: int, tf_minutes: int
                     ) -> List[StfcBar]:
    """bars_1m: objects with ts/open/high/low/close (1m, ascending)."""
    out: Dict[int, dict] = {}
    for b in bars_1m:
        mod = (b.ts - day_start_epoch) // 60
        if mod < SESSION_OPEN_MIN:
            continue
        bucket = (mod - SESSION_OPEN_MIN) // tf_minutes
        bts = day_start_epoch + (SESSION_OPEN_MIN + bucket * tf_minutes) * 60
        cur = out.get(bts)
        if cur is None:
            out[bts] = {"o": b.open, "h": b.high, "l": b.low, "c": b.close,
                        "end": SESSION_OPEN_MIN + (bucket + 1) * tf_minutes - 1}
        else:
            cur["h"] = max(cur["h"], b.high)
            cur["l"] = min(cur["l"], b.low)
            cur["c"] = b.close
    return [StfcBar(ts, v["o"], v["h"], v["l"], v["c"], v["end"])
            for ts, v in sorted(out.items())]


# ─────────────────────────────────────────────────────────────────────────
#  SUPERTREND — stateful, TradingView-exact (D2)
# ─────────────────────────────────────────────────────────────────────────
class SuperTrend:
    def __init__(self, length: int, mult: float):
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = int(length)
        self.mult = float(mult)
        self.n = 0                     # bars seen
        self.tr_sum = 0.0
        self.atr: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.prev_upper = 0.0
        self.prev_lower = 0.0
        self.prev_st: Optional[float] = None
        self.direction: Optional[int] = None

    def update(self, b: StfcBar) -> Tuple[Optional[float], Optional[int]]:
        h, l, c = b.high, b.low, b.close
        tr = (h - l) if self.prev_close is None else max(
            h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        atr_was_na = self.atr is None
        if self.n < self.length - 1:
            self.tr_sum += tr
            atr = None
        elif self.n == self.length - 1:
            self.tr_sum += tr
            atr = self.tr_sum / self.length
        else:
            atr = (self.atr * (self.length - 1) + tr) / self.length
        self.n += 1
        if atr is None:
            self.prev_close = c
            self.direction = None
            return None, None
        src = (h + l) / 2.0
        upper = src + self.mult * atr
        lower = src - self.mult * atr
        pc = self.prev_close if self.prev_close is not None else c
        lower = lower if (lower > self.prev_lower or pc < self.prev_lower) else self.prev_lower
        upper = upper if (upper < self.prev_upper or pc > self.prev_upper) else self.prev_upper
        if atr_was_na:
            d = 1
        elif self.prev_st == self.prev_upper:
            d = -1 if c > upper else 1
        else:
            d = 1 if c < lower else -1
        st = lower if d == -1 else upper
        self.prev_upper, self.prev_lower, self.prev_st = upper, lower, st
        self.atr = atr
        self.prev_close = c
        self.direction = d
        return st, d


# ─────────────────────────────────────────────────────────────────────────
#  SETUP MACHINE (D3)
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StfcSignal:
    bar_index: int                     # index of the TRADE bar in the day's list
    side: str                          # CE (up-flip) | PE (down-flip)
    flip_index: int
    confirm_index: int


def session_signals(bars: List[StfcBar], dirs: List[Optional[int]], *,
                    direction: str = "BOTH", diag: Optional[dict] = None
                    ) -> List[StfcSignal]:
    """One session's signals. `dirs[i]` is the SuperTrend direction after
    bar i (None while warming up). The machine starts flat; the previous
    session's arm never carries over."""
    out: List[StfcSignal] = []
    pend = 0
    armed = False
    armed_side = ""
    flip_i = -1
    prev_d: Optional[int] = None
    want_up = direction in ("BOTH", "UP")
    want_dn = direction in ("BOTH", "DOWN")
    for i, b in enumerate(bars):
        if armed:
            out.append(StfcSignal(i, armed_side, flip_i, i - 1))
        armed = False
        d = dirs[i]
        if d is None or prev_d is None:
            prev_d = d
            continue
        is_up, was_up = d < 0, prev_d < 0
        prev_d = d
        flip_up = is_up and not was_up
        flip_dn = (not is_up) and was_up
        if flip_up or flip_dn:
            if diag is not None:
                diag["flips_up" if flip_up else "flips_dn"] = \
                    diag.get("flips_up" if flip_up else "flips_dn", 0) + 1
            pend = (1 if want_up else 0) if flip_up else (-1 if want_dn else 0)
            flip_i = i
            continue
        green = b.close > b.open
        red = b.close < b.open
        if pend == 1 and green:
            armed, armed_side, pend = True, "CE", 0
        elif pend == -1 and red:
            armed, armed_side, pend = True, "PE", 0
    if armed and diag is not None:
        diag["arm_lost_eod"] = diag.get("arm_lost_eod", 0) + 1
    return out


# ─────────────────────────────────────────────────────────────────────────
#  PREMIUM LEVELS & FILLS (D4)
# ─────────────────────────────────────────────────────────────────────────
def prem_level(entry_px: float, mode: str, value: float, *, is_stop: bool
               ) -> Optional[float]:
    if mode == "off" or value <= 0:
        return None
    dist = value if mode == "abs" else entry_px * value / 100.0
    lvl = entry_px - dist if is_stop else entry_px + dist
    return max(0.05, lvl)


def prem_fill(*, level: float, bar, side_is_stop: bool) -> Optional[float]:
    """Fill for a PREMIUM level on the option's 1m bar, or None if untouched.
    Gapped through at the open → the open; otherwise the level."""
    if side_is_stop:
        if bar.open <= level:
            return float(bar.open)
        return level if bar.low <= level else None
    if bar.open >= level:
        return float(bar.open)
    return level if bar.high >= level else None


def strike_step(strikes) -> float:
    """Smallest positive gap between distinct strikes (50 NIFTY / 100 BNF)."""
    s = sorted({float(x) for x in strikes if x is not None})
    gaps = [b - a for a, b in zip(s, s[1:]) if b - a > 0]
    return min(gaps) if gaps else 50.0


def atm_strike(spot: float, step: float) -> float:
    return round(spot / step) * step

# backend/app/backtest/vet/vet_v1_engine.py
#
# ── VET_V1 ENGINE ── dual-EMA trend follower with an SMA±ATR regime
# filter, ported PARITY-BY-CONSTRUCTION from the Pine v5 "Vivek Equity
# Tool" indicator. Spot signals at a selectable timeframe; the runner
# (backtest_vet_runner) maps the ±1/0 condition chain onto NIFTY/stock
# option BUY legs.
#
# PURE MODULE by design (IC/TMA/GC doctrine): no app imports, no DB, no
# I/O. Bars in, per-bar states out. Every branch of the state machine is
# unit-tested against synthetic candles with hand-computed expectations
# (test_vet_engine.py).
#
# ── PINE PARITY NOTES (locked 2026-08-26) ────────────────────────────────
#   P1  ta.ema seeds with the FIRST source value (not an SMA), then
#       alpha = 2/(len+1) recursive. Reproduced exactly.
#   P2  ta.sma is None (na) until `len` bars exist.
#   P3  ta.atr = ta.rma(ta.tr(true), len). tr on the first bar (no prev
#       close) = high - low. ta.rma is None until `len` inputs exist,
#       seeds with SMA(len) at that bar, then Wilder recursive
#       (alpha = 1/len). Reproduced exactly.
#   P4  RANGE TEST IS LITERAL AND LOOSE (source quirk, kept on purpose):
#         (open <= top OR close <= top) AND (open >= bot OR close >= bot)
#       A bar straddling the WHOLE channel (open below bot, close above
#       top) still counts as "range". Do NOT "fix" this — parity first;
#       tightening it is a D-round, not a port decision.
#   P5  dirTrend: range → 0, else close >= sma → +1, else −1. Source is
#       CLOSE (the Pine input default); other sources are not supported.
#   P6  STATE MACHINE, literal Pine order (buy branch wins ties — though
#       buy/sell/close are mutually exclusive by construction):
#         cond := cond[1] != +1 and buyCond  ? +1
#               : cond[1] != −1 and sellCond ? −1
#               : cond[1] !=  0 and closeCond?  0
#               : nz(cond[1])
#       Consequences the runner RELIES on (unit-tested):
#         * RANGE-HOLD: dirTrend dropping to 0 does NOT close a position —
#           closeCond requires dirTrend == ±1. The state carries through
#           chop untouched.
#         * DIRECT FLIP: +1 → −1 (and −1 → +1) in a single bar when the
#           regime and both EMAs invert together. No intermediate flat.
#         * TRANSITION-ONLY: signals are edges of `condition`, never
#           levels — cond[1] != X guards re-entry while already in X.
#   P7  WARMUP DIVERGENCE (deliberate, documented): during the first
#       `trend_len` bars Pine's na-semantics leak a dirTrend of −1
#       (na comparisons fall through ternaries to the else branch). We
#       SUPPRESS instead: a bar is `valid` only once SMA and ATR both
#       exist, and the machine holds condition = 0 until then. The runner
#       seeds ≥ trend_len bars of prior sessions before date_from, so no
#       in-range bar is ever decided by warmup semantics either way.
#
# The engine knows NOTHING about options, premiums, expiries, lots or
# overlays (SL/TP/EOD) — those are runner concerns. The condition chain
# is invariant under every overlay.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

TREND_LEN_DEFAULT = 40
EMA_FAST1_DEFAULT = 10
EMA_FAST2_DEFAULT = 20
RANGE_LEN_DEFAULT = 0.618


@dataclass
class VetBarState:
    """Per-bar indicator + machine state, all values AT THIS BAR'S CLOSE."""
    idx: int
    ema_f1: float
    ema_f2: float
    sma_t: Optional[float]      # None until trend_len bars (P2)
    atr: Optional[float]        # None until trend_len TRs (P3)
    ch_top: Optional[float]
    ch_bot: Optional[float]
    in_range: bool              # P4 literal test; False while invalid
    dir_trend: int              # −1 | 0 | +1 ; 0 while invalid
    condition: int              # −1 | 0 | +1 ; the Pine f_condition
    valid: bool                 # sma_t and atr both available (P7)


def ema_series(vals: Sequence[float], period: int) -> List[float]:
    """Pine ta.ema (P1): seed = first value, alpha = 2/(period+1)."""
    if period < 1:
        raise ValueError("ema period must be >= 1")
    out: List[float] = []
    alpha = 2.0 / (period + 1.0)
    prev: Optional[float] = None
    for v in vals:
        prev = float(v) if prev is None else alpha * float(v) + (1.0 - alpha) * prev
        out.append(prev)
    return out


def sma_series(vals: Sequence[float], period: int) -> List[Optional[float]]:
    """Pine ta.sma (P2): None until `period` values exist."""
    if period < 1:
        raise ValueError("sma period must be >= 1")
    out: List[Optional[float]] = []
    acc = 0.0
    for i, v in enumerate(vals):
        acc += float(v)
        if i >= period:
            acc -= float(vals[i - period])
        out.append(acc / period if i >= period - 1 else None)
    return out


def rma_series(vals: Sequence[float], period: int) -> List[Optional[float]]:
    """Pine ta.rma (P3): None until `period` inputs, seeds with SMA(period)
    at that bar, then Wilder recursive (alpha = 1/period)."""
    if period < 1:
        raise ValueError("rma period must be >= 1")
    out: List[Optional[float]] = []
    prev: Optional[float] = None
    acc = 0.0
    for i, v in enumerate(vals):
        v = float(v)
        if prev is None:
            acc += v
            if i == period - 1:
                prev = acc / period
                out.append(prev)
            else:
                out.append(None)
        else:
            prev = (prev * (period - 1) + v) / period
            out.append(prev)
    return out


def atr_series(bars: Sequence, period: int) -> List[Optional[float]]:
    """Pine ta.atr (P3). `bars` need .high/.low/.close. First TR (no prev
    close) = high − low."""
    trs: List[float] = []
    prev_close: Optional[float] = None
    for b in bars:
        h, lo, c = float(b.high), float(b.low), float(b.close)
        if prev_close is None:
            trs.append(h - lo)
        else:
            trs.append(max(h - lo, abs(h - prev_close), abs(lo - prev_close)))
        prev_close = c
    return rma_series(trs, period)


def vet_states(
    bars: Sequence,
    *,
    ema_fast1: int = EMA_FAST1_DEFAULT,
    ema_fast2: int = EMA_FAST2_DEFAULT,
    trend_len: int = TREND_LEN_DEFAULT,
    range_len: float = RANGE_LEN_DEFAULT,
) -> List[VetBarState]:
    """One pass over `bars` (need .open/.high/.low/.close), returning the
    full per-bar state chain. Deterministic, allocation-light, no lookahead:
    state at index i uses bars[0..i] only."""
    closes = [float(b.close) for b in bars]
    e1 = ema_series(closes, int(ema_fast1))
    e2 = ema_series(closes, int(ema_fast2))
    sm = sma_series(closes, int(trend_len))
    at = atr_series(bars, int(trend_len))

    out: List[VetBarState] = []
    cond = 0
    for i, b in enumerate(bars):
        sma_t, atr = sm[i], at[i]
        valid = sma_t is not None and atr is not None
        if not valid:
            out.append(VetBarState(
                idx=i, ema_f1=e1[i], ema_f2=e2[i], sma_t=sma_t, atr=atr,
                ch_top=None, ch_bot=None, in_range=False, dir_trend=0,
                condition=cond, valid=False))
            continue
        basis = atr * float(range_len)
        top, bot = sma_t + basis, sma_t - basis
        o, c = float(b.open), float(b.close)
        # P4 — literal, loose containment. Kept verbatim.
        in_range = ((o <= top or c <= top) and (o >= bot or c >= bot))
        dir_trend = 0 if in_range else (1 if c >= sma_t else -1)

        buy_cond = dir_trend == 1 and e1[i] > e2[i]
        sell_cond = dir_trend == -1 and e1[i] < e2[i]
        close_cond = ((dir_trend == 1 and e1[i] < e2[i])
                      or (dir_trend == -1 and e1[i] > e2[i]))

        # P6 — literal Pine ternary chain.
        if cond != 1 and buy_cond:
            cond = 1
        elif cond != -1 and sell_cond:
            cond = -1
        elif cond != 0 and close_cond:
            cond = 0
        # else: nz(cond[1]) — carry.

        out.append(VetBarState(
            idx=i, ema_f1=e1[i], ema_f2=e2[i], sma_t=sma_t, atr=atr,
            ch_top=top, ch_bot=bot, in_range=in_range, dir_trend=dir_trend,
            condition=cond, valid=True))
    return out


def transitions(states: Sequence[VetBarState],
                start_idx: int = 0) -> List[Tuple[int, int, int]]:
    """Edges of `condition` from start_idx on: (bar_idx, prev, new).
    A trade decision exists ONLY at an edge (P6 transition-only). The bar
    at start_idx itself compares against the PRIOR bar's condition (or 0
    at the very beginning) so a warmup-carried state entering the tradable
    window does not fabricate an edge."""
    out: List[Tuple[int, int, int]] = []
    prev = states[start_idx - 1].condition if start_idx > 0 else 0
    for st in states[start_idx:]:
        if st.condition != prev:
            out.append((st.idx, prev, st.condition))
        prev = st.condition
    return out

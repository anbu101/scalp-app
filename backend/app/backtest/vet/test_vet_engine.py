# backend/app/backtest/vet/test_vet_engine.py
#
# ── VET_V1 ENGINE TESTS ── synthetic candles, hand-computed expectations
# (house rule). Runs standalone:  python3 test_vet_engine.py
#
# Covers, in order:
#   T1  ta.ema seeding + recursion (P1) against hand math
#   T2  ta.sma None-until-period (P2)
#   T3  ta.rma seed-with-SMA + Wilder recursion, ta.atr first-bar TR (P3)
#   T4  loose range containment quirk — straddling bar counts (P4)
#   T5  warmup suppression: condition pinned 0 while invalid (P7)
#   T6  entry edge, transition-only (no re-entry while held) (P6)
#   T7  RANGE-HOLD: dirTrend → 0 does NOT close the position (P6)
#   T8  close edge: trend intact, EMAs inverted → condition 0 (P6)
#   T9  DIRECT FLIP +1 → −1 in one bar, no intermediate flat (P6)
#   T10 transitions() edge list + start_idx no-fabricated-edge rule

from __future__ import annotations

import sys
from dataclasses import dataclass

try:
    from app.backtest.vet.vet_v1_engine import (
        atr_series, ema_series, rma_series, sma_series, transitions,
        vet_states,
    )
except ImportError:
    from vet_v1_engine import (  # type: ignore
        atr_series, ema_series, rma_series, sma_series, transitions,
        vet_states,
    )


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float


def flat(px: float) -> Bar:
    """A dead-flat bar — TR = 0 once seeded, ATR decays toward 0."""
    return Bar(px, px, px, px)


FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


# ── T1 ── ta.ema parity ─────────────────────────────────────────────────
def t1() -> None:
    vals = [10.0, 20.0, 30.0]
    got = ema_series(vals, 3)          # alpha = 0.5
    # seed 10; 0.5*20+0.5*10 = 15; 0.5*30+0.5*15 = 22.5
    check("T1 ema seed=first, alpha=2/(n+1)",
          got == [10.0, 15.0, 22.5], f"got {got}")


# ── T2 ── ta.sma parity ─────────────────────────────────────────────────
def t2() -> None:
    got = sma_series([1.0, 2.0, 3.0, 4.0], 3)
    check("T2 sma None until period, then rolling mean",
          got == [None, None, 2.0, 3.0], f"got {got}")


# ── T3 ── ta.rma / ta.atr parity ────────────────────────────────────────
def t3() -> None:
    got = rma_series([3.0, 6.0, 9.0, 12.0], 3)
    # seed at idx2 = mean(3,6,9) = 6; idx3 = (6*2 + 12)/3 = 8
    check("T3a rma None,None,seed=SMA,Wilder",
          got == [None, None, 6.0, 8.0], f"got {got}")
    bars = [Bar(10, 12, 9, 11),        # first TR = high-low = 3
            Bar(11, 15, 11, 14),       # TR = max(4, |15-11|, |11-11|) = 4
            Bar(14, 14, 9, 10)]        # TR = max(5, 0, 5) = 5
    atr = atr_series(bars, 3)
    check("T3b atr first-bar TR=H-L, seed at idx2 = 4.0",
          atr == [None, None, 4.0], f"got {atr}")


# ── T4 ── loose containment quirk ───────────────────────────────────────
def t4() -> None:
    # 40 flat warmup bars at 100 → SMA=100, ATR→small but > 0? Flat bars:
    # first TR = 0 (H-L of a flat bar), all TRs 0 → ATR = 0 → channel
    # collapses to the SMA. Use a gentle wiggle instead: alternate 99/101
    # closes with 1-pt ranges so ATR is finite and the channel is real.
    bars = []
    for i in range(60):
        px = 100.0 + (0.5 if i % 2 == 0 else -0.5)
        bars.append(Bar(px, px + 0.5, px - 0.5, px))
    st = vet_states(bars, trend_len=40)
    s = st[-1]
    check("T4a wiggle stays in range (dir 0)",
          s.valid and s.in_range and s.dir_trend == 0,
          f"valid={s.valid} in_range={s.in_range} dir={s.dir_trend}")
    # Straddling bar: open far BELOW bot, close far ABOVE top.
    # open <= top (yes) or close <= top (no) → True
    # open >= bot (no)  or close >= bot (yes) → True  ⇒ in_range (quirk)
    bars.append(Bar(80.0, 125.0, 79.0, 120.0))
    st = vet_states(bars, trend_len=40)
    s = st[-1]
    check("T4b straddling bar counts as range (literal quirk)",
          s.in_range and s.dir_trend == 0,
          f"in_range={s.in_range} dir={s.dir_trend} "
          f"top={s.ch_top} bot={s.ch_bot}")


# ── helpers for machine tests ───────────────────────────────────────────
def ramp_up(bars, n, step=2.0, rng=0.4):
    px = bars[-1].close if bars else 100.0
    for _ in range(n):
        px += step
        bars.append(Bar(px - step * 0.5, px + rng, px - step - rng, px))
    return bars


def ramp_down(bars, n, step=2.0, rng=0.4):
    px = bars[-1].close if bars else 100.0
    for _ in range(n):
        px -= step
        bars.append(Bar(px + step * 0.5, px + step + rng, px - rng, px))
    return bars


# ── T5/T6 ── warmup suppression + entry edge + transition-only ──────────
def t5_t6() -> None:
    bars = ramp_up([], 60)
    st = vet_states(bars, trend_len=40)
    check("T5 condition pinned 0 while invalid",
          all(s.condition == 0 and not s.valid for s in st[:39]),
          f"first valid at {next(i for i, s in enumerate(st) if s.valid)}")
    # steady uptrend: once valid, price >> SMA+channel, EMA10 > EMA20
    check("T6a long entry fires after validity",
          st[-1].condition == 1, f"cond={st[-1].condition} "
          f"dir={st[-1].dir_trend} in_range={st[-1].in_range}")
    edges = transitions(st)
    check("T6b transition-only: exactly one 0→+1 edge on a monotone ramp",
          edges and edges[0][1] == 0 and edges[0][2] == 1
          and sum(1 for e in edges if e[2] == 1) == 1, f"edges={edges}")


# ── T7 ── RANGE-HOLD: chop does not close ───────────────────────────────
def t7() -> None:
    bars = ramp_up([], 60)                       # → condition +1
    st = vet_states(bars, trend_len=40)
    assert st[-1].condition == 1
    # drift sideways AT the last price: the rising SMA(40) reaches price
    # after ~31 bars (probed) and dirTrend decays to 0; EMAs stay f1>f2.
    px = bars[-1].close
    for i in range(60):
        w = 0.3 if i % 2 == 0 else -0.3
        bars.append(Bar(px + w, px + 0.6, px - 0.6, px + w))
    st = vet_states(bars, trend_len=40)
    d0 = next((i for i in range(60, len(st)) if st[i].dir_trend == 0), None)
    check("T7a sideways drift re-enters the channel",
          d0 is not None, "dirTrend never hit 0 — widen the drift window")
    window = st[d0:d0 + 8] if d0 is not None else []
    check("T7b RANGE-HOLD: condition stays +1 through the chop",
          bool(window) and all(s.condition == 1 for s in window),
          f"conds={[s.condition for s in window]}")


# ── T8 ── close edge: trend intact, EMAs inverted ───────────────────────
def t8() -> None:
    # GEOMETRY NOTE (probed): with trend_len=40, SMA catch-up during a
    # stall ((L−1)/2 ≈ 19.5 bars) ties the EMA-inversion time (~16-20
    # bars for 10/20), so a linear pullback reaches the channel first and
    # the machine flips −1 through RANGE-HOLD instead of closing. That is
    # PARITY (Pine does the same) — the close branch is geometrically
    # narrow. trend_len=80 doubles the catch-up time and lets the branch
    # fire deterministically: pullback inverts the EMAs while price is
    # still far above the channel → dir stays +1 → closeCond → 0.
    bars = ramp_up([], 120, step=2.0)
    st = vet_states(bars, trend_len=80)
    assert st[-1].condition == 1
    for _ in range(40):
        px = bars[-1].close - 0.8
        bars.append(Bar(px + 0.4, px + 1.1, px - 0.3, px))
    st = vet_states(bars, trend_len=80)
    c0 = next((i for i in range(120, len(st)) if st[i].condition == 0), None)
    ok = c0 is not None
    s = st[c0] if ok else None
    check("T8 close edge (dir +1, ema10<ema20 → cond 0)",
          ok and s.dir_trend == 1 and s.ema_f1 < s.ema_f2,
          "no close edge" if not ok else
          f"dir={s.dir_trend} e1={s.ema_f1:.2f} e2={s.ema_f2:.2f}")


# ── T9 ── direct flip +1 → −1, no intermediate flat ─────────────────────
def t9() -> None:
    bars = ramp_up([], 60)
    st = vet_states(bars, trend_len=40)
    assert st[-1].condition == 1
    n_before = len(bars)
    ramp_down(bars, 40, step=3.0)                # hard reversal
    st = vet_states(bars, trend_len=40)
    conds = [s.condition for s in st[n_before:]]
    check("T9a reversal reaches condition −1", st[-1].condition == -1,
          f"cond={st[-1].condition}")
    # the +1 → −1 step must be direct: no 0 strictly between the last +1
    # and the first −1 (RANGE-HOLD keeps +1 through the channel crossing,
    # then sellCond flips it in one bar).
    first_m1 = conds.index(-1)
    check("T9b flip is direct (no flat state between +1 and −1)",
          all(c == 1 for c in conds[:first_m1]),
          f"conds up to flip = {conds[:first_m1 + 1]}")


# ── T10 ── transitions() start_idx: no fabricated edge ──────────────────
def t10() -> None:
    bars = ramp_up([], 60)
    st = vet_states(bars, trend_len=40)
    assert st[-1].condition == 1
    late = transitions(st, start_idx=len(st) - 5)   # inside the held +1
    check("T10 no fabricated edge when starting inside a held state",
          late == [], f"got {late}")


if __name__ == "__main__":
    for t in (t1, t2, t3, t4, t5_t6, t7, t8, t9, t10):
        t()
    print(f"\n{'ALL TESTS PASSED' if FAILED == 0 else f'{FAILED} FAILURES'}")
    sys.exit(1 if FAILED else 0)

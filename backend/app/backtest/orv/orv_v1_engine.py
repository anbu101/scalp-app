# backend/app/backtest/orv/orv_v1_engine.py
#
# ── ORV_V1 ENGINE ── "Orbit": ORB-Reversal signal engine on index SPOT.
#
# Fence: ORV_V1_20260903
#
# PURE functions only: no DB, no clock, no config singletons. Everything here
# is unit-tested in test_orv_runner_sim.py and shared verbatim by any future
# live engine — the backtest/live parity surface starts at this file.
#
# SPEC OF RECORD (chat, 2026-09-03), decisions D1-D1.7 locked before code,
# amended by the two whiteboard screenshots (2026-09-03):
#   D1    ORB = high/low of the first orb_minutes (default 90 -> 09:15-10:45)
#         of 5m SPOT candles. The ORB must be COMPLETE (every 5m bucket
#         present) or the day is skipped — fail-closed, counted.
#   D2    Day filter: (ORB_high - ORB_low) must be STRICTLY GREATER than
#         atr_pct% (default 25) of ATR(atr_period=14, daily, Wilder) computed
#         from sessions strictly BEFORE today. No same-day leak.
#   D3    A 5m CLOSE outside the range ARMS that side: close < ORB_low arms
#         the BULL hunt (CE), close > ORB_high arms the BEAR hunt (PE).
#   D1.1  Patterns (whiteboard amendment): BULL side = Hammer (long LOWER
#         wick) or Bullish Engulfing; BEAR side = Shooting-Star shape
#         (long UPPER wick, the "inverted hammer" drawn at a top) or
#         Bearish Engulfing. The wick test MIRRORS per side.
#   D1.2  Signal confirms on the pattern bar's CLOSE; entry is the NEXT 5m
#         bar's open (= that minute's 1m open). No unfinished-bar reads.
#   D1.3  Disarm: while armed, a 5m close BACK INSIDE the range before a
#         pattern fires disarms that side. It can re-arm on a fresh close
#         outside. Toggleable (disarm_on_reentry) so a sweep can falsify it.
#   D1.4  If entry spot is already at-or-beyond the selected target the
#         trade is SKIPPED (the setup's premise is gone). Counted.
#   D1.5  max_trades_per_day (default 2, configurable) and
#         max_trades_per_side (default 1). One position at a time.
#   D1.6  SL anchor = entry SPOT +- sl_points.
#   D1.7  Pattern search window unbounded up to entry_block_time;
#         max_wait_bars ships INERT (0 = off) as a future sweep axis.
#
# ORDERING RULE (documented, tested): on each armed bar the PATTERN check
# runs BEFORE the disarm check — a hammer that closes back inside the range
# is still a signal (arguably the strongest one); the D1.4 target-passed
# guard in the runner catches the degenerate case where that close already
# sits beyond the selected target. A bar that ARMS a side is never its own
# pattern bar (the side must have been armed BEFORE the bar).

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

IST_OFFSET = 5 * 3600 + 30 * 60
SESSION_OPEN_MIN = 9 * 60 + 15        # NSE index grid opens 09:15


@dataclass(frozen=True)
class OrvBar:
    ts: int                            # epoch seconds, bar START, IST grid
    open: float
    high: float
    low: float
    close: float


# ─────────────────────────────────────────────────────────────────────────
#  RESAMPLE — 1m -> tf, anchored on the 09:15 session grid
# ─────────────────────────────────────────────────────────────────────────
def resample_1m(bars_1m: List[OrvBar], *, day_start_epoch: int,
                tf_minutes: int) -> List[OrvBar]:
    """Bucket 1m bars into tf bars anchored at 09:15 IST. A tf bar's ts is
    its FIRST minute's ts. Bars before 09:15 are ignored. Buckets with no
    prints are simply absent (the caller decides whether absence matters —
    the ORB completeness gate does)."""
    out: Dict[int, dict] = {}
    for b in bars_1m:
        mod = (b.ts - day_start_epoch) // 60
        if mod < SESSION_OPEN_MIN:
            continue
        bucket = (mod - SESSION_OPEN_MIN) // tf_minutes
        bts = day_start_epoch + (SESSION_OPEN_MIN + bucket * tf_minutes) * 60
        cur = out.get(bts)
        if cur is None:
            out[bts] = {"o": b.open, "h": b.high, "l": b.low,
                        "c": b.close, "last": b.ts}
        else:
            cur["h"] = max(cur["h"], b.high)
            cur["l"] = min(cur["l"], b.low)
            if b.ts > cur["last"]:
                cur["c"], cur["last"] = b.close, b.ts
    return [OrvBar(ts, v["o"], v["h"], v["l"], v["c"])
            for ts, v in sorted(out.items())]


# ─────────────────────────────────────────────────────────────────────────
#  ORB — D1
# ─────────────────────────────────────────────────────────────────────────
def compute_orb(bars_tf: List[OrvBar], *, day_start_epoch: int,
                orb_minutes: int, tf_minutes: int
                ) -> Optional[Tuple[float, float]]:
    """(high, low) of the tf bars whose START lies inside the first
    orb_minutes of the session. Returns None unless EVERY expected bucket
    is present (fail-closed: a half-covered opening range is not a range)."""
    need = orb_minutes // tf_minutes
    lo_min, hi_min = SESSION_OPEN_MIN, SESSION_OPEN_MIN + orb_minutes
    window = [b for b in bars_tf
              if lo_min <= (b.ts - day_start_epoch) // 60 < hi_min]
    if len(window) != need:
        return None
    return (max(b.high for b in window), min(b.low for b in window))


# ─────────────────────────────────────────────────────────────────────────
#  ATR — D2 (daily, computed by the runner over prior sessions)
# ─────────────────────────────────────────────────────────────────────────
def true_ranges(daily: List[Tuple[float, float, float]]) -> List[float]:
    """daily = [(high, low, close)] ascending. TR[0] = H-L (no prev close)."""
    trs: List[float] = []
    prev_c: Optional[float] = None
    for h, l, c in daily:
        if prev_c is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    return trs


def atr_series(daily: List[Tuple[float, float, float]], *, period: int,
               method: str = "wilder") -> List[Optional[float]]:
    """ATR value AS OF each session's close (index-aligned with `daily`).
    None until `period` TRs exist. wilder = SMA seed then Wilder smoothing;
    sma = plain rolling mean of the last `period` TRs."""
    trs = true_ranges(daily)
    out: List[Optional[float]] = [None] * len(trs)
    if len(trs) < period:
        return out
    if method == "sma":
        run = sum(trs[:period])
        out[period - 1] = run / period
        for i in range(period, len(trs)):
            run += trs[i] - trs[i - period]
            out[i] = run / period
        return out
    atr = sum(trs[:period]) / period
    out[period - 1] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


# ─────────────────────────────────────────────────────────────────────────
#  PATTERNS — D1.1 (whiteboard definitions)
# ─────────────────────────────────────────────────────────────────────────
def _parts(b: OrvBar) -> Tuple[float, float, float, float]:
    body = abs(b.close - b.open)
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    rng = b.high - b.low
    return body, upper, lower, rng


def is_hammer(b: OrvBar, *, wick_body_ratio: float = 2.0,
              opp_wick_ratio: float = 0.5) -> bool:
    """BULL shape: long LOWER wick, small-or-no upper wick, colour-agnostic.
    body == 0 (doji): a dragonfly doji (upper ~ 0) qualifies, a gravestone
    does not — the ratio tests degrade to exactly that."""
    body, upper, lower, rng = _parts(b)
    if rng <= 0:
        return False
    return lower >= wick_body_ratio * body and upper <= opp_wick_ratio * body


def is_shooting_star(b: OrvBar, *, wick_body_ratio: float = 2.0,
                     opp_wick_ratio: float = 0.5) -> bool:
    """BEAR shape (the whiteboard's inverted-hammer-at-a-top): long UPPER
    wick, small-or-no lower wick, colour-agnostic. Mirror of is_hammer."""
    body, upper, lower, rng = _parts(b)
    if rng <= 0:
        return False
    return upper >= wick_body_ratio * body and lower <= opp_wick_ratio * body


def is_bull_engulf(prev: OrvBar, b: OrvBar, *,
                   need_opposite_prev: bool = True) -> bool:
    """GREEN bar whose BODY engulfs the previous bar's body (>= on both
    edges). Classic form (whiteboard: red -> green) requires the previous
    bar red; need_opposite_prev=False relaxes that for sweeps."""
    if b.close <= b.open:
        return False
    if need_opposite_prev and prev.close >= prev.open:
        return False
    return (b.close >= max(prev.open, prev.close)
            and b.open <= min(prev.open, prev.close))


def is_bear_engulf(prev: OrvBar, b: OrvBar, *,
                   need_opposite_prev: bool = True) -> bool:
    """RED bar whose BODY engulfs the previous bar's body. Mirror."""
    if b.close >= b.open:
        return False
    if need_opposite_prev and prev.close <= prev.open:
        return False
    return (b.open >= max(prev.open, prev.close)
            and b.close <= min(prev.open, prev.close))


# ─────────────────────────────────────────────────────────────────────────
#  SIGNALS — D3 + D1.1 + D1.2 + D1.3 arm/disarm state machine
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OrvSignal:
    ts: int                            # pattern bar START ts (confirms at its close)
    side: str                          # 'CE' (bull reversal) | 'PE' (bear reversal)
    pattern: str                       # HAMMER | STAR | BULL_ENG | BEAR_ENG
    spot_close: float                  # pattern bar close (diagnostics only)


def orv_signals(bars_tf: List[OrvBar], *, day_start_epoch: int,
                orb_high: float, orb_low: float, orb_minutes: int,
                hammer_on: bool = True, engulf_on: bool = True,
                wick_body_ratio: float = 2.0, opp_wick_ratio: float = 0.5,
                engulf_need_opposite_prev: bool = True,
                disarm_on_reentry: bool = True,
                max_wait_bars: int = 0,
                diag: Optional[dict] = None) -> List[OrvSignal]:
    """Walk the post-ORB tf bars and emit every raw signal in time order.
    The RUNNER applies budgets, block time, the D1.4 target guard and the
    one-position rule — this function states only what the chart said.

    max_wait_bars (D1.7, default 0 = off): if > 0, an armed side auto-
    disarms after that many bars without a pattern. INERT by default."""
    d = diag if diag is not None else {}
    for k in ("bull_arms", "bear_arms", "bull_disarms", "bear_disarms",
              "bull_wait_disarms", "bear_wait_disarms"):
        d.setdefault(k, 0)

    orb_end_min = SESSION_OPEN_MIN + orb_minutes
    signals: List[OrvSignal] = []
    bull_armed = bear_armed = False
    bull_age = bear_age = 0
    prev: Optional[OrvBar] = None

    for b in bars_tf:
        mod = (b.ts - day_start_epoch) // 60
        if mod < orb_end_min:
            prev = b
            continue

        # ── D1.7 wait-window expiry FIRST: an arm older than max_wait_bars
        # is dead BEFORE this bar's pattern check, so exactly max_wait_bars
        # bars after the arming bar are pattern-eligible. Inert at 0. ──
        if max_wait_bars > 0:
            if bull_armed:
                bull_age += 1
                if bull_age > max_wait_bars:
                    bull_armed = False
                    d["bull_wait_disarms"] += 1
            if bear_armed:
                bear_age += 1
                if bear_age > max_wait_bars:
                    bear_armed = False
                    d["bear_wait_disarms"] += 1

        # ── pattern check next (see ORDERING RULE above) ──
        if bull_armed:
            pat = None
            if hammer_on and is_hammer(b, wick_body_ratio=wick_body_ratio,
                                       opp_wick_ratio=opp_wick_ratio):
                pat = "HAMMER"
            elif engulf_on and prev is not None and is_bull_engulf(
                    prev, b, need_opposite_prev=engulf_need_opposite_prev):
                pat = "BULL_ENG"
            if pat is not None:
                signals.append(OrvSignal(b.ts, "CE", pat, b.close))
                bull_armed = False
        if bear_armed:
            pat = None
            if hammer_on and is_shooting_star(
                    b, wick_body_ratio=wick_body_ratio,
                    opp_wick_ratio=opp_wick_ratio):
                pat = "STAR"
            elif engulf_on and prev is not None and is_bear_engulf(
                    prev, b, need_opposite_prev=engulf_need_opposite_prev):
                pat = "BEAR_ENG"
            if pat is not None:
                signals.append(OrvSignal(b.ts, "PE", pat, b.close))
                bear_armed = False

        # ── D1.3 disarm on close back inside ──
        if disarm_on_reentry:
            if bull_armed and b.close >= orb_low:
                bull_armed = False
                d["bull_disarms"] += 1
            if bear_armed and b.close <= orb_high:
                bear_armed = False
                d["bear_disarms"] += 1

        # ── D3 arm / re-arm from THIS bar's close (never its own pattern).
        # NOTE: a PATTERN bar whose close is still outside the range re-arms
        # its side here — a fresh close outside is a fresh breakout, and the
        # runner's per-side budget (D1.5) is what caps re-entries. ──
        if b.close < orb_low and not bull_armed:
            bull_armed = True
            bull_age = 0
            d["bull_arms"] += 1
        if b.close > orb_high and not bear_armed:
            bear_armed = True
            bear_age = 0
            d["bear_arms"] += 1

        prev = b

    return signals


# ─────────────────────────────────────────────────────────────────────────
#  TARGETS & EXITS — D4 / D1.4 / D1.6 (pure; the runner drives the tape)
# ─────────────────────────────────────────────────────────────────────────
def target_level(*, side: str, mode: str, orb_high: float, orb_low: float,
                 entry_spot: float, custom_pts: float) -> Optional[float]:
    """The SPOT target for a trade, or None when the entry spot already sits
    at-or-beyond it (the D1.4 skip). CE profits UP: T1 = ORB_low,
    T2 = ORB_high, custom = entry + pts. PE profits DOWN: T1 = ORB_high,
    T2 = ORB_low, custom = entry - pts."""
    if side == "CE":
        tp = {"T1": orb_low, "T2": orb_high,
              "custom": entry_spot + custom_pts}[mode]
        return tp if tp > entry_spot else None
    tp = {"T1": orb_high, "T2": orb_low,
          "custom": entry_spot - custom_pts}[mode]
    return tp if tp < entry_spot else None


def resolve_spot_exit(*, side: str, sl_level: float, tp_level: float,
                      spot_bar: OrvBar) -> Optional[str]:
    """Evaluate one 1m SPOT bar against the spot SL/TP of an open trade.
    Returns 'SL' | 'TP' | None. Both breached inside one bar -> SL
    (pessimistic, fleet convention). CE: SL below entry (low <= sl), TP
    above (high >= tp). PE mirrored."""
    if side == "CE":
        sl_hit = spot_bar.low <= sl_level
        tp_hit = spot_bar.high >= tp_level
    else:
        sl_hit = spot_bar.high >= sl_level
        tp_hit = spot_bar.low <= tp_level
    if sl_hit:
        return "SL"
    if tp_hit:
        return "TP"
    return None

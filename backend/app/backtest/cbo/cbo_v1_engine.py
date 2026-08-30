# backend/app/backtest/cbo/cbo_v1_engine.py
#
# ── CBO_V1 ENGINE ── previous-candle breakout on index SPOT.
#
# RULE (as specified, 2026-08-29):
#   Aggregate spot 1m bars onto a tf-minute grid (default 5m). While a tf
#   bar is FORMING, if its running high touches or crosses the PREVIOUS
#   completed tf bar's high -> UP breakout. If its running low touches or
#   crosses the previous bar's low -> DOWN breakout. Detection happens at
#   the close of each 1m sub-bar (D1); the runner fills at the next 1m open.
#
# PURE MODULE by design (IC/TMA/GC/VET doctrine): no app imports, no DB, no
# I/O, no options, no premiums, no charges, no MTM. Bars in, signals out.
# Every branch is unit-tested against synthetic candles with hand-computed
# expectations (test_cbo_engine.py).
#
# ── DESIGN NOTES (locked with the D-round) ───────────────────────────────
#
#   N1  INTRABAR, NOT CLOSE-CONFIRMED. The spec says "touch or crosses",
#       which is an intrabar event. With a 1m corpus the finest legal
#       resolution is the 1m sub-bar, so a trigger is detected when the
#       sub-bar's HIGH/LOW breaches the reference — information that is
#       known at that sub-bar's close, never before it. `trigger_source`
#       = "close" is offered as the strictly-more-conservative lever
#       (sub-bar CLOSE must breach); it is a falsifiable alternative, not
#       the default.
#
#   N2  NO LOOKAHEAD. A signal emitted at `trigger_ts` uses only bars whose
#       ts <= trigger_ts. Since corpus candles are stamped at bar START and
#       cover [ts, ts+60), everything used by the decision has completed by
#       trigger_ts + 60. The runner MUST fill at trigger_ts + 60 or later.
#       `fill_ts` is provided so no caller has to rediscover this.
#
#   N3  RUNNING EXTREME, NOT SUB-BAR EXTREME. The rule reads "current
#       candle touch or crosses above previous high", where "current
#       candle" is the FORMING tf bar. So the test is against the tf bar's
#       running high, not the individual sub-bar's high. With one-signal-
#       per-bar dedupe the first fire is identical either way; the
#       difference only shows up if dedupe is disabled.
#
#   N4  NO CROSS-DAY REFERENCE. The reference resets every day: the first
#       tf bucket of a session has no predecessor, so the earliest possible
#       signal is in the SECOND bucket. This strategy therefore has NO
#       warmup-seeding requirement — the selector-parity trap that costs
#       PST/TMA/SCALP 45-60 minutes of cold-start signals does not apply.
#       Callers pass ONE DAY of bars at a time.
#
#   N5  OUTSIDE SUB-BARS (D8, locked 2026-08-29: PESSIMISTIC). A single
#       sub-bar can breach both the previous high and the previous low.
#       Which side came first is unknowable at 1m resolution. The default
#       `both_side_policy="loss"` does NOT discard the signal: it takes
#       the trade and flags it `ambiguous=True`, and the runner forces it
#       to a stop-out on the entry bar itself.
#
#       WHERE THE PESSIMISM LIVES: in the OUTCOME, not the direction. An
#       outside bar touched the entry level AND the stop level, so under a
#       worst-case reading the position was filled and stopped within the
#       same minute — a guaranteed loser regardless of side. Direction is
#       then chosen by the only non-arbitrary rule available (the level
#       NEARER the sub-bar's open is the one a monotonic move from the
#       open reaches first) purely to decide WHICH contract absorbs the
#       loss. Because the two stop distances are the same reference range
#       in mirror image, the choice barely moves the magnitude.
#
#       "skip" (take neither), "up"/"down" (force a side, keep the normal
#       exit path) and "both" remain available so the pessimistic default
#       can itself be falsified against them.
#
#   N6  TOUCH IS INCLUSIVE. ">=" for the high and "<=" for the low, per the
#       literal wording "touch OR crosses". `breakout_buffer_pts` > 0
#       converts it to a strict breach with a cushion and is the lever for
#       testing whether exact-touch fills are noise.
#
# The engine knows NOTHING about which option gets traded, the premium
# band, the ATM skew gate, targets, stops, MTM caps or session windows.
# Those are runner concerns and are invariant under the signal chain.

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

TF_MINUTES_DEFAULT = 5
GRID_ANCHOR_MIN_DEFAULT = 9 * 60 + 15      # 09:15 IST — NSE index bar grid

UP = "UP"
DOWN = "DOWN"


@dataclass(frozen=True)
class CboBar:
    """A 1m spot bar. `ts` is the bar START in epoch seconds (corpus
    convention), so the bar covers [ts, ts + 60)."""
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class CboRef:
    """The completed tf bar a signal was measured against."""
    bucket_ts: int
    high: float
    low: float
    open: float
    close: float
    sub_bars: int


@dataclass(frozen=True)
class CboSignal:
    """One breakout trigger.

    trigger_ts  start ts of the 1m sub-bar whose close detected the breach
    fill_ts     trigger_ts + 60 — the earliest bar a fill may legally use
    direction   UP | DOWN
    ref         the completed tf bar that supplied the level
    level       the exact price breached (ref.high for UP, ref.low for DOWN)
    stop_level  the OPPOSITE extreme of ref — the spec's spot stop
    spot        the sub-bar close at detection (for diagnostics)
    bucket_ts   start ts of the FORMING tf bar the trigger occurred in
    excursion   how far past `level` the running extreme had travelled
    ambiguous   True when this sub-bar breached BOTH levels (N5). The
                runner MUST force such a trade to a stop-out; it is not a
                hint, it is the D8 contract.
    """
    trigger_ts: int
    fill_ts: int
    direction: str
    ref: CboRef
    level: float
    stop_level: float
    spot: float
    bucket_ts: int
    excursion: float
    ambiguous: bool = False


def bucket_start(ts: int, anchor_ts: int, tf_sec: int) -> int:
    """Grid bucket containing `ts`, aligned to `anchor_ts`. Floor division is
    made explicit for ts < anchor_ts (pre-open prints) so the behaviour is
    defined rather than accidental: they land in negative buckets and are
    dropped by the caller's session gate, never merged into the 09:15 bar."""
    return anchor_ts + ((ts - anchor_ts) // tf_sec) * tf_sec


def _agg(bars: Sequence[CboBar]) -> tuple:
    return (max(b.high for b in bars), min(b.low for b in bars),
            bars[0].open, bars[-1].close, len(bars))


def cbo_signals(
    bars_1m: Iterable[CboBar],
    *,
    anchor_ts: Optional[int] = None,
    tf_minutes: int = TF_MINUTES_DEFAULT,
    trigger_source: str = "high",        # "high" (spec) | "close" (strict)
    both_side_policy: str = "loss",      # "loss"(D8) | "skip" | "up" | "down" | "both"
    one_signal_per_bucket: bool = True,
    breakout_buffer_pts: float = 0.0,
    min_ref_range_pts: float = 0.0,
    require_full_ref: bool = False,
) -> List[CboSignal]:
    """Emit every breakout trigger in ONE DAY of 1m spot bars, ascending.

    anchor_ts             grid origin; defaults to the first bar's ts, but
                          the runner should pass day_start + 09:15 IST so a
                          late first print cannot shift the whole grid.
    trigger_source        "high": the tf bar's running high/low breaches the
                          reference (spec-literal, intrabar).
                          "close": the sub-bar CLOSE must breach — strictly
                          fewer and later signals.
                          "tf_close": the COMPLETED tf candle must CLOSE
                          through the level; at most one signal per bucket
                          by construction, and never ambiguous (one close
                          is one price).   ── CBO_TF_CLOSE_20260830 ──
    both_side_policy      what to do when one sub-bar breaches both sides.
                          "loss" (D8 default): take it, flag ambiguous, and
                          let the runner force a stop-out.
    one_signal_per_bucket at most one signal per direction per forming bar.
    breakout_buffer_pts   require the breach to exceed the level by this
                          much; 0.0 keeps "touch" inclusive.
    min_ref_range_pts     ignore references narrower than this (a doji
                          reference makes both levels trivially reachable).
    require_full_ref      only trust a reference built from a complete set
                          of tf_minutes sub-bars, so a data gap cannot
                          produce an artificially narrow high/low.

    Signals are returned in ascending trigger_ts. The caller owns position
    state, so a signal here does NOT imply a trade.
    """
    if trigger_source not in ("high", "close", "tf_close"):
        raise ValueError(
            f"trigger_source must be high|close|tf_close, got {trigger_source!r}")
    if both_side_policy not in ("loss", "skip", "up", "down", "both"):
        raise ValueError(f"both_side_policy invalid: {both_side_policy!r}")
    if tf_minutes <= 0:
        raise ValueError(f"tf_minutes must be positive, got {tf_minutes}")

    bars = sorted(bars_1m, key=lambda b: b.ts)
    if not bars:
        return []

    tf_sec = tf_minutes * 60
    if anchor_ts is None:
        anchor_ts = bars[0].ts

    out: List[CboSignal] = []
    ref: Optional[CboRef] = None
    cur_bucket: Optional[int] = None
    cur_bars: List[CboBar] = []
    run_hi = run_lo = 0.0
    fired: set = set()

    def _close_bucket() -> None:
        nonlocal ref
        if not cur_bars:
            return
        hi, lo, op, cl, n = _agg(cur_bars)
        if require_full_ref and n < tf_minutes:
            ref = None          # incomplete -> refuse to reference it
            return
        if min_ref_range_pts > 0 and (hi - lo) < min_ref_range_pts:
            ref = None
            return
        ref = CboRef(bucket_ts=cur_bucket, high=hi, low=lo,
                     open=op, close=cl, sub_bars=n)

    for bar in bars:
        b = bucket_start(bar.ts, anchor_ts, tf_sec)
        if b != cur_bucket:
            _close_bucket()
            cur_bucket = b
            cur_bars = [bar]
            run_hi, run_lo = bar.high, bar.low
            fired = set()
        else:
            cur_bars.append(bar)
            run_hi = max(run_hi, bar.high)
            run_lo = min(run_lo, bar.low)

        if ref is None:
            continue

        # ── CBO_TF_CLOSE_20260830 ── "tf_close": only the LAST sub-bar of
        # a bucket (known by clock) may fire, and only on ITS close — which
        # is the tf candle's close. Everything downstream (levels, stop,
        # fill_ts = next 1m open) is shared with the other modes.
        if trigger_source == "tf_close" and \
                bar.ts != cur_bucket + tf_sec - 60:
            continue

        up_probe = run_hi if trigger_source == "high" else bar.close
        dn_probe = run_lo if trigger_source == "high" else bar.close

        up_hit = up_probe >= ref.high + breakout_buffer_pts
        dn_hit = dn_probe <= ref.low - breakout_buffer_pts

        if one_signal_per_bucket:
            up_hit = up_hit and UP not in fired
            dn_hit = dn_hit and DOWN not in fired

        ambiguous = bool(up_hit and dn_hit)

        if up_hit and dn_hit:
            # N5: unresolvable at 1m. Resolve by declared policy only.
            if both_side_policy == "skip":
                fired.update((UP, DOWN))      # neither side re-arms this bar
                continue
            if both_side_policy == "loss":
                # D8 PESSIMISTIC. Keep the side a monotonic move from this
                # sub-bar's OPEN would have reached first; the runner turns
                # it into a same-bar stop-out. Ties (open exactly midway)
                # resolve to UP deterministically so the run is repeatable.
                if (bar.open - ref.low) < (ref.high - bar.open):
                    up_hit, dn_hit = False, True
                    fired.add(UP)
                else:
                    up_hit, dn_hit = True, False
                    fired.add(DOWN)
            elif both_side_policy == "up":
                dn_hit = False
                fired.add(DOWN)
            elif both_side_policy == "down":
                up_hit = False
                fired.add(UP)
            # "both" falls through and emits two signals

        for direction, hit in ((UP, up_hit), (DOWN, dn_hit)):
            if not hit:
                continue
            level = ref.high if direction is UP else ref.low
            stop = ref.low if direction is UP else ref.high
            probe = up_probe if direction is UP else dn_probe
            exc = (probe - level) if direction is UP else (level - probe)
            out.append(CboSignal(
                trigger_ts=bar.ts,
                fill_ts=bar.ts + 60,
                direction=direction,
                ref=ref,
                level=round(float(level), 2),
                stop_level=round(float(stop), 2),
                spot=round(float(bar.close), 2),
                bucket_ts=cur_bucket,
                excursion=round(float(exc), 2),
                ambiguous=ambiguous,
            ))
            fired.add(direction)

    return out


def tf_bars(
    bars_1m: Iterable[CboBar],
    *,
    anchor_ts: Optional[int] = None,
    tf_minutes: int = TF_MINUTES_DEFAULT,
) -> List[CboRef]:
    """Completed tf bars for one day. Diagnostics and test support only —
    the signal path never calls this."""
    bars = sorted(bars_1m, key=lambda b: b.ts)
    if not bars:
        return []
    tf_sec = tf_minutes * 60
    if anchor_ts is None:
        anchor_ts = bars[0].ts
    out: List[CboRef] = []
    cur: Optional[int] = None
    acc: List[CboBar] = []
    for bar in bars:
        b = bucket_start(bar.ts, anchor_ts, tf_sec)
        if b != cur:
            if acc:
                hi, lo, op, cl, n = _agg(acc)
                out.append(CboRef(cur, hi, lo, op, cl, n))
            cur, acc = b, [bar]
        else:
            acc.append(bar)
    if acc:
        hi, lo, op, cl, n = _agg(acc)
        out.append(CboRef(cur, hi, lo, op, cl, n))
    return out

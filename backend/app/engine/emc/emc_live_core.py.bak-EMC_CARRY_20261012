# backend/app/engine/emc/emc_live_core.py
#
# ── EMC_V1 LIVE CORE ── PURE (no broker / DB imports; unit-tested)
# Fence: EMC_V1_LIVE_20261011
# ============================================================================
# THE PARITY DESIGN (VET / PST doctrine): this file holds NO decision logic.
# Decisions are app.backtest.emc.emc_v1_engine.EmcDayCore — the very object
# the backtest's simulate_day drives — and every indicator is re-computed by
# the backtest's own functions over the growing day prefix:
#
#   * LiveSpotCrosses — spot 1m candles (prior sessions + today, FROZEN once
#     accepted) → resample_session → spot_crosses on a FRESH EmaPair each
#     time. Same arithmetic, same continuity, same first-candle (gap) rule
#     and the same R1 "advance on every session" warm-up as the runner.
#     A PrefixGuard freezes the strategy if any already-reported cross is
#     ever restated (a corpus gap or duplicated minute would do that).
#   * LiveOptionTrack — the selected contract's prior-session bars + today's
#     frozen 1m candles → a FRESH OptionEmaTrack evaluated at minute k.
#     OptionEmaTrack is causal (proven minute-by-minute against a brute-force
#     recompute in test_emc_runner_sim), so prefix data gives the backtest's
#     value exactly.
#   * live_params(cfg) — the runner's own _merge_cfg + validate + DayParams
#     build: one config vocabulary, no second parser.
#
# WHAT THIS FILE REFUSES (fail closed, by live_problems):
#   * trail_on — the sealed config has it OFF; a live trailing stop would
#     need its own parity study (peak through the previous minute).
#   * fill_mode "close" — a live order cannot fill at a past close; live is
#     inherently the "next_open" model the seal was tested with.
# ============================================================================

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

try:
    from app.backtest.emc.emc_v1_engine import (EmcBar, EmaPair, OptionEmaTrack, DayParams,
                                                resample_session, spot_crosses, SESSION_OPEN_MIN)
except ImportError:                                        # standalone tests
    from emc_v1_engine import (EmcBar, EmaPair, OptionEmaTrack, DayParams,  # type: ignore
                               resample_session, spot_crosses, SESSION_OPEN_MIN)

IST_OFFSET = 5 * 3600 + 30 * 60


def day_start_epoch_of(ts: int) -> int:
    """00:00 IST of the day containing ts (the corpus / runner day anchor)."""
    return ((int(ts) + IST_OFFSET) // 86400) * 86400 - IST_OFFSET


def minute_of(ts: int) -> int:
    return ((int(ts) + IST_OFFSET) % 86400) // 60


def _bar(c) -> EmcBar:
    if isinstance(c, EmcBar):
        return c
    return EmcBar(int(c["ts"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))


def _group_sessions(candles: Sequence) -> List[Tuple[int, List[EmcBar]]]:
    by: Dict[int, List[EmcBar]] = {}
    for c in candles:
        b = _bar(c)
        by.setdefault(day_start_epoch_of(b.ts), []).append(b)
    return [(ds, sorted(v, key=lambda x: x.ts)) for ds, v in sorted(by.items())]


# ─────────────────────────────────────────────────────────────────────────
#  config — the runner's vocabulary, verbatim
# ─────────────────────────────────────────────────────────────────────────
def live_params(cfg: dict) -> Tuple[Optional[DayParams], dict, Optional[str]]:
    """→ (DayParams | None, merged cfg, problem | None). Uses the backtest
    runner's _merge_cfg / validate / _hhmm so the live engine reads a config
    exactly as a backtest run would."""
    from app.backtest.emc.backtest_emc_runner import _merge_cfg, validate, _hhmm
    m = _merge_cfg(cfg or {})
    bad = validate(m) or live_problems(m)
    if bad:
        return None, m, bad
    p = DayParams(
        sel_lo=m["sel_prem_min"], sel_hi=m["sel_prem_max"], sel_pick=m["sel_pick"],
        direction=m["direction"], intrabar=m["opt_check"] == "intrabar",
        opt_min_bars=m["opt_min_bars"] or m["ema_slow"], fill=m["fill_mode"],
        sl_pts=m["sl_pts"], tp_pts=m["tp_pts"], trail_on=m["trail_on"],
        trail_trigger=m["trail_trigger_pts"], trail_gap=m["trail_gap_pts"], xover_exit=m["xover_exit"],
        block_min=_hhmm(m["entry_cutoff"], 14 * 60 + 30), eod_min=_hhmm(m["eod_square_off"], 15 * 60 + 15),
        max_trades=m["max_trades_per_day"], max_losses=m["max_losses_per_day"],
        skip_from=_hhmm(m["arm_skip_from"], -1) if m.get("arm_skip_from") else -1,
        skip_to=_hhmm(m["arm_skip_to"], -1) if m.get("arm_skip_to") else -1)
    return p, m, None


def live_problems(m: dict) -> Optional[str]:
    if m.get("trail_on"):
        return "trailing SL is not supported in paper/live (sealed config has it OFF) — refusing to trade"
    if m.get("fill_mode") == "close":
        return "fill_mode 'close' cannot be executed live (orders fill after the decision) — set next_open"
    if str(m.get("underlying", "NIFTY")).upper() != "NIFTY":
        return "EMC_V1 paper/live is NIFTY-only"
    return None


# ─────────────────────────────────────────────────────────────────────────
#  prefix guard
# ─────────────────────────────────────────────────────────────────────────
class CrossGuard:
    """A cross reported for a completed spot bar must never change or vanish
    as later candles arrive. On violation: FREEZE (no new setups; an open
    position is still managed to its exit)."""

    def __init__(self) -> None:
        self.seen: Dict[int, int] = {}
        self.frozen = False
        self.reason: Optional[str] = None

    def check(self, crosses: Dict[int, int], upto_min: int) -> bool:
        if self.frozen:
            return False
        for m, x in self.seen.items():
            if m <= upto_min and crosses.get(m, 0) != x:
                self.frozen = True
                self.reason = f"spot cross at minute {m} restated {x} → {crosses.get(m, 0)} — prefix instability"
                return False
        for m, x in crosses.items():
            if m <= upto_min:
                self.seen[m] = x
        return True


# ─────────────────────────────────────────────────────────────────────────
#  spot crosses over the growing prefix
# ─────────────────────────────────────────────────────────────────────────
class LiveSpotCrosses:
    def __init__(self, *, fast: int, slow: int, tf: int, warm_sessions_1m: Sequence[Sequence],
                 need_sessions: int, first_candle_cross: bool = True):
        self.fast, self.slow, self.tf = int(fast), int(slow), int(tf)
        self.first_candle_cross = bool(first_candle_cross)
        self.need = int(need_sessions)
        warm: List[EmcBar] = []
        for sess in warm_sessions_1m:
            for b in sess:
                warm.append(_bar(b))
        self.warm_sessions = _group_sessions(warm)
        self.today: List[EmcBar] = []
        self.ds: Optional[int] = None
        self.guard = CrossGuard()
        self.crosses: Dict[int, int] = {}
        self.gap_cross_min: Optional[int] = None
        self.last_vals: Tuple[Optional[float], Optional[float]] = (None, None)

    def warmup_ok(self) -> bool:
        return len(self.warm_sessions) >= self.need

    def add(self, candle) -> bool:
        """Freeze one COMPLETED 1m spot candle of today. Out-of-order or
        duplicate minutes are ignored (False)."""
        b = _bar(candle)
        if self.ds is None:
            self.ds = day_start_epoch_of(b.ts)
        if day_start_epoch_of(b.ts) != self.ds:
            return False
        if self.today and b.ts <= self.today[-1].ts:
            return False
        if minute_of(b.ts) < SESSION_OPEN_MIN:
            return False
        self.today.append(b)
        return True

    def last_minute(self) -> Optional[int]:
        return minute_of(self.today[-1].ts) if self.today else None

    def recompute(self) -> Dict[int, int]:
        """Crosses of today's COMPLETED spot bars, keyed by bar end minute —
        the runner's R1 / R6 computation on the current prefix."""
        if self.ds is None:
            return {}
        pair = EmaPair(self.fast, self.slow)
        for wds, wb in self.warm_sessions:
            for b in resample_session(wb, day_start_epoch=wds, tf_minutes=self.tf):
                pair.update(b.close)
        upto = self.last_minute() or -1
        bars = [b for b in resample_session(self.today, day_start_epoch=self.ds, tf_minutes=self.tf)
                if b.end_min <= upto]                     # completed buckets only
        xs = spot_crosses(bars, pair)
        self.last_vals = pair.values
        self.gap_cross_min = None
        if bars and (bars[0].ts - self.ds) // 60 == SESSION_OPEN_MIN and bars[0].end_min in xs:
            self.gap_cross_min = bars[0].end_min
            if not self.first_candle_cross:
                del xs[bars[0].end_min]
        if not self.guard.check(xs, upto):
            return self.crosses
        self.crosses = xs
        return xs


# ─────────────────────────────────────────────────────────────────────────
#  the selected option's EMA over the growing prefix
# ─────────────────────────────────────────────────────────────────────────
class LiveOptionTrack:
    """Drop-in for the runner's OptionEmaTrack (same .at(k, intrabar))."""

    def __init__(self, *, fast: int, slow: int, tf: int, ds: int, warm_bars: Sequence[EmcBar]):
        self.fast, self.slow, self.tf, self.ds = int(fast), int(slow), int(tf), int(ds)
        self.warm_bars = list(warm_bars)
        self.today: List[EmcBar] = []

    def add(self, candle) -> bool:
        b = _bar(candle)
        if day_start_epoch_of(b.ts) != self.ds:
            return False
        if self.today and b.ts <= self.today[-1].ts:
            return False
        self.today.append(b)
        return True

    def at(self, k: int, intrabar: bool = True):
        known = [b for b in self.today if (b.ts - self.ds) // 60 <= k]
        tr = OptionEmaTrack(fast=self.fast, slow=self.slow, tf=self.tf, ds=self.ds,
                            warm_bars=self.warm_bars, today_1m=known)
        return tr.at(k, intrabar)


def option_warm_from_sessions(sessions: Sequence[Tuple[int, Sequence]], tf: int) -> List[EmcBar]:
    """The runner's option_warm_bars on already-fetched prior sessions
    [(day_start, candles)], oldest first."""
    out: List[EmcBar] = []
    for ds, cs in sessions:
        out.extend(resample_session([_bar(c) for c in cs], day_start_epoch=ds, tf_minutes=tf))
    return out

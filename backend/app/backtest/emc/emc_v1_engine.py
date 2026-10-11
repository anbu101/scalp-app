# backend/app/backtest/emc/emc_v1_engine.py
#
# ── EMC_V1 ENGINE ── "Ember": EMA(9/21) cross on NIFTY SPOT arms an option,
# the option's OWN EMA(9/21) alignment buys it. Pure module (no I/O).
#
# Fence: EMC_V1_20261011
#
# Decisions (the runner owns data access; everything that decides a trade
# lives here so a future paper/live engine can re-run it — parity by
# construction):
#   E1  Bars: 1m resampled to `tf` minutes on the NSE session grid (buckets
#       from 09:15, TradingView NSE parity). A bar's end minute is recorded.
#   E2  EMA = Pine ta.ema: None until `period` values exist, the period-th
#       value is the SMA seed, then alpha = 2/(period+1). SPOT EMAs are
#       continuous across sessions (the chart does not reset at 09:15).
#   E3  Spot cross on a CLOSED spot bar, Pine ta.crossover/ta.crossunder:
#       up   = fast > slow and fast[1] <= slow[1]
#       down = fast < slow and fast[1] >= slow[1]
#       Known at the close of the bar's last minute.
#   E4  Selection at that close: every contract of the side (CE on up, PE on
#       down) priced at its 1m close of that minute (else its last print
#       inside the spot bar); keep premium in [min, max] inclusive; pick the
#       HIGHEST (default) or lowest; ties go to the strike nearer spot. The
#       contract is FIXED for the life of the setup — no re-selection.
#   E5  Option trigger: the selected contract's EMA(fast/slow) on its own
#       `opt_tf` bars. "intrabar" (default) evaluates at every 1m close with
#       the forming bar's close = the latest print (what a live chart shows
#       mid-candle); "bar_close" evaluates only closed bars. CE buys when
#       fast > slow, PE when fast < slow — checked first at the setup minute
#       ("if not already"), then each minute until filled/replaced/expired.
#       Not ready (fewer than opt_min_bars bars of history) = keep waiting.
#   E6  A setup dies when: the opposite spot cross arrives (replaced), the
#       entry cutoff is reached, or the day ends. Never carried overnight.
#   E7  Exits, per 1m option bar, pessimistic order: stop (fixed SL or the
#       trailing stop, whichever is higher) → target. Touch books AT the
#       level; a bar that OPENS through it books the open. The trailing stop
#       uses the peak through the PREVIOUS bar (no same-bar high→low
#       assumption). Then the opposite spot cross (XOVER) at bar close, then
#       EOD at the open of the square-off minute.
#   E9  Arming blackout (EMC_V1_BLACKOUT_20261011): a cross closing on a minute
#       in [skip_from, skip_to) still exits an opposite position and replaces a
#       pending setup, but arms nothing itself.
#   E8  One position at a time. Daily caps: max entries, max LOSING trades
#       (net of charges < 0) — reaching the losing cap halts new setups for
#       the day; an open position is always managed to its exit.
#
# Fleet convention: no imports from sibling strategy packages; helpers are
# duplicated here so the module stays self-contained.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

SESSION_OPEN_MIN = 9 * 60 + 15        # NSE index grid opens 09:15


@dataclass(frozen=True)
class EmcBar:
    ts: int                            # epoch seconds, bar START, IST grid
    open: float
    high: float
    low: float
    close: float
    end_min: int = 0                   # minute-of-day of the bar's LAST 1m slot


# ─────────────────────────────────────────────────────────────────────────
#  E1  RESAMPLE — session-aligned tf buckets from 09:15
# ─────────────────────────────────────────────────────────────────────────
def resample_session(bars_1m, *, day_start_epoch: int, tf_minutes: int) -> List[EmcBar]:
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
            out[bts] = {"o": float(b.open), "h": float(b.high), "l": float(b.low), "c": float(b.close),
                        "end": SESSION_OPEN_MIN + (bucket + 1) * tf_minutes - 1}
        else:
            cur["h"] = max(cur["h"], float(b.high))
            cur["l"] = min(cur["l"], float(b.low))
            cur["c"] = float(b.close)
    return [EmcBar(ts, v["o"], v["h"], v["l"], v["c"], v["end"]) for ts, v in sorted(out.items())]


# ─────────────────────────────────────────────────────────────────────────
#  E2  EMA — Pine ta.ema (SMA seed), incremental with a non-committing peek
# ─────────────────────────────────────────────────────────────────────────
class Ema:
    __slots__ = ("period", "k", "n", "acc", "value")

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("ema period must be >= 1")
        self.period = int(period)
        self.k = 2.0 / (self.period + 1.0)
        self.n = 0
        self.acc = 0.0
        self.value: Optional[float] = None

    def update(self, x: float) -> Optional[float]:
        x = float(x)
        self.n += 1
        if self.value is None:
            self.acc += x
            if self.n == self.period:
                self.value = self.acc / self.period
        else:
            self.value = x * self.k + self.value * (1.0 - self.k)
        return self.value

    def peek(self, x: float) -> Optional[float]:
        """The value update(x) WOULD produce, without committing it."""
        x = float(x)
        if self.value is None:
            return (self.acc + x) / self.period if self.n + 1 == self.period else None
        return x * self.k + self.value * (1.0 - self.k)


class EmaPair:
    __slots__ = ("fast", "slow")

    def __init__(self, fast: int, slow: int):
        self.fast = Ema(fast)
        self.slow = Ema(slow)

    def update(self, x: float) -> Tuple[Optional[float], Optional[float]]:
        return self.fast.update(x), self.slow.update(x)

    def peek(self, x: float) -> Tuple[Optional[float], Optional[float]]:
        return self.fast.peek(x), self.slow.peek(x)

    @property
    def values(self) -> Tuple[Optional[float], Optional[float]]:
        return self.fast.value, self.slow.value


def ema_series(vals: Sequence[float], period: int) -> List[Optional[float]]:
    """Batch form of Ema (same arithmetic) — overlays and tests."""
    e = Ema(period)
    return [e.update(v) for v in vals]


# ─────────────────────────────────────────────────────────────────────────
#  E3  CROSS / ALIGNMENT
# ─────────────────────────────────────────────────────────────────────────
def crossed(pf: Optional[float], ps: Optional[float], f: Optional[float], s: Optional[float]) -> int:
    """+1 crossover, −1 crossunder, 0 none (Pine semantics; any None → 0)."""
    if pf is None or ps is None or f is None or s is None:
        return 0
    if f > s and pf <= ps:
        return 1
    if f < s and pf >= ps:
        return -1
    return 0


def aligned(side: str, f: Optional[float], s: Optional[float]) -> bool:
    if f is None or s is None:
        return False
    return f > s if side == "CE" else f < s


def spot_crosses(bars: Sequence[EmcBar], pair: EmaPair) -> Dict[int, int]:
    """Advance the (continuous) spot EMA pair over one session's closed bars;
    returns {bar end minute: ±1} for every cross on this session."""
    out: Dict[int, int] = {}
    for b in bars:
        pf, ps = pair.values
        f, s = pair.update(b.close)
        x = crossed(pf, ps, f, s)
        if x:
            out[b.end_min] = x
    return out


# ─────────────────────────────────────────────────────────────────────────
#  E4  SELECTION
# ─────────────────────────────────────────────────────────────────────────
def pick_in_band(quotes: Sequence[dict], lo: float, hi: float, *, pick: str = "highest",
                 spot: Optional[float] = None) -> Optional[dict]:
    """quotes: [{sym, strike, price}]. Band inclusive both ends."""
    inb = [q for q in quotes if q.get("price") is not None and lo <= float(q["price"]) <= hi]
    if not inb:
        return None

    def money(q):
        return abs(float(q.get("strike") or 0.0) - float(spot)) if spot is not None else 0.0

    if pick == "lowest":
        return min(inb, key=lambda q: (float(q["price"]), money(q), str(q["sym"])))
    return min(inb, key=lambda q: (-float(q["price"]), money(q), str(q["sym"])))


# ─────────────────────────────────────────────────────────────────────────
#  E5  OPTION EMA TRACK
# ─────────────────────────────────────────────────────────────────────────
class OptionEmaTrack:
    """The selected contract's EMA pair on opt_tf bars: warm history (closed
    bars from prior sessions) + today's 1m prints, evaluated causally."""

    def __init__(self, *, fast: int, slow: int, tf: int, ds: int,
                 warm_bars: Sequence[EmcBar], today_1m: Sequence):
        self.pair = EmaPair(fast, slow)
        self.nbars = 0
        for b in warm_bars:
            self.pair.update(b.close)
            self.nbars += 1
        self.warm_bars = len(warm_bars)
        self.ds = ds
        self.tf = tf
        self.today = resample_session(today_1m, day_start_epoch=ds, tf_minutes=tf)
        self.by_min = {(c.ts - ds) // 60: c for c in today_1m}
        self.idx = 0
        self.last_k = -1

    def at(self, k: int, intrabar: bool = True) -> Tuple[Optional[float], Optional[float], int]:
        """(fast, slow, bars used) as of the CLOSE of minute k. Calls must be
        non-decreasing in k (the runner walks minutes forward)."""
        if k < self.last_k:
            raise ValueError("OptionEmaTrack.at called backwards in time")
        self.last_k = k
        while self.idx < len(self.today) and self.today[self.idx].end_min <= k:
            self.pair.update(self.today[self.idx].close)
            self.nbars += 1
            self.idx += 1
        if intrabar and self.idx < len(self.today):
            b = self.today[self.idx]
            start = (b.ts - self.ds) // 60
            if start <= k:
                for m in range(k, start - 1, -1):
                    c = self.by_min.get(m)
                    if c is not None:
                        f, s = self.pair.peek(c.close)
                        return f, s, self.nbars + 1
        f, s = self.pair.values
        return f, s, self.nbars


# ─────────────────────────────────────────────────────────────────────────
#  E7  FILLS / TRAIL
# ─────────────────────────────────────────────────────────────────────────
def stop_fill(level: float, bar) -> Optional[float]:
    if float(bar.open) <= level:
        return float(bar.open)
    if float(bar.low) <= level:
        return float(level)
    return None


def target_fill(level: float, bar) -> Optional[float]:
    if float(bar.open) >= level:
        return float(bar.open)
    if float(bar.high) >= level:
        return float(level)
    return None


def effective_stop(*, entry: float, peak: float, sl_level: Optional[float], trail_on: bool,
                   trail_trigger: float, trail_gap: float) -> Tuple[Optional[float], bool]:
    """(stop level, trailing?) — trailing once the peak gain reaches the
    trigger; the stop never sits below the fixed SL."""
    if trail_on and trail_gap > 0 and (peak - entry) >= trail_trigger:
        t = peak - trail_gap
        if sl_level is None or t > sl_level:
            return t, True
    return sl_level, False


# ─────────────────────────────────────────────────────────────────────────
#  E4–E8  ONE SESSION
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class DayParams:
    sel_lo: float
    sel_hi: float
    sel_pick: str                      # highest | lowest
    direction: str                     # BOTH | CE | PE
    intrabar: bool
    opt_min_bars: int
    fill: str                          # close | next_open
    sl_pts: float                      # 0 = off
    tp_pts: float                      # 0 = off
    trail_on: bool
    trail_trigger: float
    trail_gap: float
    xover_exit: bool
    block_min: int                     # first minute with NO new entries
    eod_min: int                       # square-off minute (exit at its open)
    max_trades: int                    # 0 = unlimited
    max_losses: int                    # 0 = unlimited
    # ── EMC_V1_BLACKOUT_20261011 ── a spot cross whose candle closes on a minute
    # in [skip_from, skip_to) arms nothing (it still exits / replaces as usual)
    skip_from: int = -1
    skip_to: int = -1


@dataclass
class DayTrade:
    side: str
    sym: str
    strike: Optional[float]
    sel_min: int
    sel_px: float
    entry_min: int
    entry_px: float
    sl: Optional[float]
    tp: Optional[float]
    exit_min: Optional[int] = None
    exit_px: Optional[float] = None
    reason: Optional[str] = None
    waited: int = 0                    # minutes from setup to the trigger
    aligned_at_setup: bool = False
    opt_bars: int = 0
    mae: float = 0.0                   # per unit premium
    mfe: float = 0.0
    entry_spot: Optional[float] = None
    trail_armed: bool = False
    net: float = 0.0


def simulate_day(*, p: DayParams, cross_at: Dict[int, int], quotes_at: Callable[[int, str], List[dict]],
                 bars_of: Callable[[str], Dict[int, object]], track_of: Callable[[dict], object],
                 net_of: Callable[[float, float], float], spot_at: Callable[[int], Optional[float]],
                 diag: dict, session_open: int = SESSION_OPEN_MIN) -> List[DayTrade]:
    """Walk one session minute by minute. Minute k means the 1m bar that
    starts at k; decisions are taken at its CLOSE. Returns closed trades.

    quotes_at(k, side) → [{sym, strike, price}] at the close of minute k
    bars_of(sym)       → {minute: bar(open/high/low/close)}
    track_of(contract) → object with .at(k, intrabar) → (fast, slow, bars)
    net_of(entry, exit)→ net rupees of a round trip (charges included)."""
    D = diag
    trades: List[DayTrade] = []
    pos: Optional[dict] = None
    setup: Optional[dict] = None
    day_entries = 0
    day_losses = 0
    halted = False

    def _bump(key, n=1):
        D[key] = D.get(key, 0) + n

    def can_enter(k: int) -> Optional[str]:
        if halted:
            return "losses"
        if p.max_trades and day_entries >= p.max_trades:
            return "trades"
        if k >= p.block_min:
            return "cutoff"
        return None

    def fill_px(sym: str, k: int, last: Optional[float]) -> Tuple[Optional[float], int]:
        bars = bars_of(sym)
        if p.fill == "next_open":
            nb = bars.get(k + 1)
            if nb is not None and nb.open:
                return float(nb.open), k + 1
            _bump("fill_fallbacks")
        b = bars.get(k)
        if b is not None and b.close:
            return float(b.close), k
        return last, k

    def close_pos(k_exit: int, px: float, reason: str):
        nonlocal pos, day_losses, halted
        t: DayTrade = pos["t"]
        t.exit_min, t.exit_px, t.reason = k_exit, round(float(px), 2), reason
        t.net = net_of(t.entry_px, float(px))
        trades.append(t)
        if t.net < 0:
            day_losses += 1
            if p.max_losses and day_losses >= p.max_losses and not halted:
                halted = True
                _bump("days_halted_losses")
        pos = None

    def try_enter(k: int, first: bool) -> bool:
        """Evaluate the pending setup at the close of k; enter if aligned."""
        nonlocal pos, setup, day_entries
        f, s, n = setup["track"].at(k, p.intrabar)
        ready = f is not None and s is not None and n >= p.opt_min_bars
        if not ready:
            setup["unready"] = True
            return False
        if not aligned(setup["side"], f, s):
            return False
        why = can_enter(k)
        if why:
            return False
        sym = setup["c"]["sym"]
        px, kmin = fill_px(sym, k, None)
        if px is None or px <= 0:
            _bump("sig_no_fill")
            setup = None
            return False
        sl = round(px - p.sl_pts, 2) if p.sl_pts > 0 else None
        tp = round(px + p.tp_pts, 2) if p.tp_pts > 0 else None
        t = DayTrade(side=setup["side"], sym=sym, strike=setup["c"].get("strike"),
                     sel_min=setup["k"], sel_px=float(setup["c"]["price"]),
                     entry_min=kmin, entry_px=round(px, 2), sl=sl, tp=tp,
                     waited=k - setup["k"], aligned_at_setup=first, opt_bars=n,
                     entry_spot=spot_at(k))
        pos = {"t": t, "check_from": k + 1, "peak": px, "last": px}
        day_entries += 1
        _bump("entries")
        _bump("entries_aligned_at_setup" if first else "entries_after_wait")
        if setup.get("unready"):
            _bump("entries_after_warmup_wait")
        setup = None
        return True

    for k in range(session_open, p.eod_min):
        # ── (a) stop / target on this minute's option bar ──
        if pos is not None and pos["check_from"] <= k:
            t: DayTrade = pos["t"]
            b = bars_of(t.sym).get(k)
            if b is None:
                _bump("stale_marks")
            else:
                stop, trailing = effective_stop(entry=t.entry_px, peak=pos["peak"], sl_level=t.sl,
                                                trail_on=p.trail_on, trail_trigger=p.trail_trigger,
                                                trail_gap=p.trail_gap)
                if trailing:
                    t.trail_armed = True
                hit = None
                if stop is not None:
                    px = stop_fill(stop, b)
                    if px is not None:
                        hit = ("TSL" if trailing else "SL", px)
                if hit is None and t.tp is not None:
                    px = target_fill(t.tp, b)
                    if px is not None:
                        hit = ("TP", px)
                t.mae = min(t.mae, float(b.low) - t.entry_px)
                t.mfe = max(t.mfe, float(b.high) - t.entry_px)
                if hit is not None:
                    close_pos(k, hit[1], hit[0])
                else:
                    pos["peak"] = max(pos["peak"], float(b.high))
                    pos["last"] = float(b.close)

        # ── (b) spot cross at the close of minute k ──
        x = cross_at.get(k, 0)
        if x:
            side = "CE" if x > 0 else "PE"
            _bump("crosses_up" if x > 0 else "crosses_dn")
            if pos is not None and pos["t"].side != side and p.xover_exit:
                px, kx = fill_px(pos["t"].sym, k, pos["last"])
                close_pos(kx, px, "XOVER")
            if setup is not None and setup["side"] != side:
                _bump("setups_replaced")
                setup = None
            if p.skip_from >= 0 and p.skip_from <= k < p.skip_to:   # ── EMC_V1_BLACKOUT_20261011 ──
                _bump("sig_dropped_blackout")
            elif p.direction not in ("BOTH", side):
                _bump("sig_dropped_dir")
            elif pos is not None:
                _bump("sig_dropped_open")
            else:
                why = can_enter(k)
                if why:
                    _bump(f"sig_dropped_{why}")
                else:
                    qs = quotes_at(k, side)
                    if not qs:
                        _bump("setup_no_universe")
                    else:
                        c = pick_in_band(qs, p.sel_lo, p.sel_hi, pick=p.sel_pick, spot=spot_at(k))
                        if c is None:
                            _bump("setup_no_band")
                        else:
                            setup = {"side": side, "c": c, "k": k, "track": track_of(c), "unready": False}
                            _bump("setups")
                            try_enter(k, first=True)
                            continue          # this minute's check is done

        # ── (c) pending setup: option alignment at the close of minute k ──
        if setup is not None and pos is None:
            if k >= p.block_min or halted or (p.max_trades and day_entries >= p.max_trades):
                _bump("setups_expired_cutoff")
                setup = None
            else:
                try_enter(k, first=False)

    # ── (d) EOD at the open of the square-off minute ──
    if setup is not None:
        _bump("setups_expired_eod")
        if setup.get("unready"):
            _bump("setups_expired_unready")
    if pos is not None:
        t = pos["t"]
        b = bars_of(t.sym).get(p.eod_min)
        px = float(b.open) if (b is not None and b.open) else pos["last"]
        close_pos(p.eod_min, px, "EOD")
    if day_entries and p.max_trades and day_entries >= p.max_trades:
        _bump("days_capped_trades")
    return trades

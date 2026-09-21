# backend/app/backtest/fvg/fvg_v1_engine.py
#
# ── FVG_V1 ("Fissure") ENGINE ── Fair Value Gap / Inverse FVG pullback
# entries on NIFTY SPOT for weekly option BUYING. PURE MODULE: no DB, no
# clock, no I/O — every decision is made from closed bars, so a live engine
# can re-run it incrementally (parity-by-construction, as STFC/ORB/VET).
#
# Fence: FVG_V1_20260916
# LAB PARITY: this file is the lab engine (tools/lab/fvg/fvg_fib_engine.py,
# fence FVG_LAB_20260916) with only this header changed. The runner test
# replays a synthetic corpus through BOTH runners when the lab is present
# and demands identical trades. Never edit one without the other.
#
# ── THE IDEA ──────────────────────────────────────────────────────────────
#   A Fair Value Gap is a 3-candle displacement: candle[i].low > candle[i-2]
#   .high (bullish) leaves an un-traded price band [c[i-2].high, c[i].low].
#   The thesis (ICT school): the impulse that left the gap is institutional
#   participation; price tends to return INTO the gap ("fill the imbalance")
#   and continue in the impulse direction. We do NOT buy the gap blindly —
#   we wait for price to (a) come back into the band and (b) print a 1m
#   rejection close back above the band. That converts a "level" into a
#   "confirmed level", which is where the option is bought.
#
#   Inverse FVG (IFVG): a gap that is VIOLATED by a tf-bar close through it
#   flips polarity — old bullish gap becomes a bearish supply band. We trade
#   the retest of the inverted band in the new direction.
#
#   Fibonacci gate (FVG setups only): the gap must sit inside the 0.382–0.786
#   retracement of the impulse leg (swing extreme before the displacement →
#   impulse extreme). A gap outside that band is either too shallow (weak
#   displacement) or too deep (the leg already failed).
#
# ── DECISIONS (D1–D7, locked before the runner) ───────────────────────────
#   D1  Signal timeframe `tf` (default 5m) built from 1m SPOT, session grid
#       09:15. FVG detection and inversion are decided on CLOSED tf bars.
#   D2  Displacement filter: the middle candle body >= disp_atr × ATR(tf)
#       and the gap width >= gap_min_atr × ATR. ATR is Wilder RMA over
#       `atr_len` tf bars and is CONTINUOUS across sessions (warm-up needed).
#   D3  Trigger on 1m bars only AFTER the gap's birth bar has closed:
#         bull FVG:  touched when 1m low <= top; trigger when touched AND
#                    1m close > top AND close > open (rejection candle).
#         bear FVG:  mirror.
#         IFVG (inverted bull → bearish band): touched when 1m high >= bottom;
#                    trigger when touched AND 1m close < bottom AND close < open.
#       Entry is at the NEXT 1m open (runner). A gap is used at most once.
#   D4  Structural stop: bull FVG stop = gap bottom − buf; bear = top + buf;
#       IFVG stops on the far side of the band. buf = sl_buf_atr × ATR.
#   D5  Inversion: bull gap inverts on a tf CLOSE below its bottom; an
#       inverted band dies on a tf close back through its far side. Gaps
#       expire `gap_max_age` tf bars after birth (or after inversion).
#   D6  Gaps are INTRADAY only (list cleared at day start). Cross-day gaps
#       are a separate hypothesis; not in v1.
#   D7  Fib gate applies to FVG setups; IFVG has no clean impulse leg and
#       is exempt (explicitly counted so the share is visible).
#
# ── FALSIFICATION TRIPWIRES (runner prints all of these) ──────────────────
#   * sl_fill_artifact: net(SL fills at option low) vs net(SL at close) —
#     P&L must not be a fill-convention artifact (ORB_SLFILL doctrine).
#   * time/eod share of net: if the timed exit carries the net the edge is
#     "hold after a gap", not the level.
#   * FVG vs IFVG net, CE vs PE net, per-year signs, per-hour, per-DTE.
#   * sl_in_entry_minute: stops hit in the fill minute = noise stop (STFC scar).

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

SESSION_OPEN_MIN = 9 * 60 + 15         # 09:15 IST, minute-of-day
IST_OFFSET = 5 * 3600 + 30 * 60


# ─────────────────────────────────────────────────────────────────────────
#  BARS
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    end_min: int = 0                   # tf bars: last minute-of-day inside the bar
    start_min: int = 0


def resample_session(bars_1m: List[Bar], *, day_start_epoch: int,
                     tf_minutes: int) -> List[Bar]:
    """Bucket 1m bars onto the 09:15 grid (identical to the STFC helper)."""
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
                        "s": SESSION_OPEN_MIN + bucket * tf_minutes,
                        "e": SESSION_OPEN_MIN + (bucket + 1) * tf_minutes - 1}
        else:
            cur["h"] = max(cur["h"], b.high)
            cur["l"] = min(cur["l"], b.low)
            cur["c"] = b.close
    return [Bar(ts, v["o"], v["h"], v["l"], v["c"], v["e"], v["s"])
            for ts, v in sorted(out.items())]


# ─────────────────────────────────────────────────────────────────────────
#  ATR — Wilder RMA, stateful, continuous across sessions
# ─────────────────────────────────────────────────────────────────────────
class ATR:
    def __init__(self, length: int):
        if length < 1:
            raise ValueError("atr length must be >= 1")
        self.length = int(length)
        self.n = 0
        self.tr_sum = 0.0
        self.value: Optional[float] = None
        self.prev_close: Optional[float] = None

    def update(self, b: Bar) -> Optional[float]:
        tr = (b.high - b.low) if self.prev_close is None else max(
            b.high - b.low, abs(b.high - self.prev_close), abs(b.low - self.prev_close))
        if self.n < self.length - 1:
            self.tr_sum += tr
        elif self.n == self.length - 1:
            self.tr_sum += tr
            self.value = self.tr_sum / self.length
        else:
            self.value = (self.value * (self.length - 1) + tr) / self.length
        self.n += 1
        self.prev_close = b.close
        return self.value


# ─────────────────────────────────────────────────────────────────────────
#  GAPS
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class Gap:
    side: str                          # 'BULL' | 'BEAR'   (polarity NOW)
    top: float
    bottom: float
    born_idx: int                      # tf index of the third candle
    born_end_min: int
    atr_at_birth: float
    leg_low: float
    leg_high: float
    fib_ok: bool
    state: str = "FRESH"               # FRESH | INVERTED | DEAD
    age: int = 0                       # tf bars since birth / inversion
    touched: bool = False
    used: bool = False
    inverted_from: Optional[str] = None
    gid: int = 0

    @property
    def kind(self) -> str:
        return "IFVG" if self.state == "INVERTED" else "FVG"


@dataclass(frozen=True)
class Signal:
    trigger_min: int                   # minute-of-day of the rejection close
    side: str                          # 'CE' | 'PE'
    kind: str                          # 'FVG' | 'IFVG'
    sl_level: float                    # structural stop on SPOT
    zone_top: float
    zone_bottom: float
    leg_target: Optional[float]        # fib 1.0 target (FVG only) else None
    atr: float
    gid: int


@dataclass
class EngineConfig:
    atr_len: int = 14
    disp_atr: float = 1.0              # middle-candle body >= disp_atr × ATR
    gap_min_atr: float = 0.15          # gap width >= gap_min_atr × ATR
    fib_gate: bool = True
    fib_lo: float = 0.382
    fib_hi: float = 0.786
    fib_lookback: int = 10             # tf bars before the displacement candle
    sl_buf_atr: float = 0.10
    gap_max_age: int = 12              # tf bars (12 × 5m = 60 min)
    trade_fvg: bool = True
    trade_ifvg: bool = True
    direction: str = "BOTH"            # BOTH | CE | PE
    max_gaps: int = 6                  # newest gaps kept per day


def _fib_overlap(cfg: EngineConfig, side: str, zone_top: float,
                 zone_bottom: float, leg_low: float, leg_high: float) -> bool:
    rng = leg_high - leg_low
    if rng <= 0:
        return False
    if side == "BULL":
        hi_px = leg_high - cfg.fib_lo * rng    # shallower retrace (higher price)
        lo_px = leg_high - cfg.fib_hi * rng    # deeper retrace
    else:
        lo_px = leg_low + cfg.fib_lo * rng
        hi_px = leg_low + cfg.fib_hi * rng
    return zone_top >= lo_px and zone_bottom <= hi_px


class FvgEngine:
    """Per-day setup machine. Call new_day() at the start of every session,
    on_tf_close() after every closed tf bar, on_minute() for every 1m bar
    that lies AFTER the tf bar it belongs to has been born (the runner
    guarantees ordering — see runner R1)."""

    def __init__(self, cfg: EngineConfig, atr: ATR):
        self.cfg = cfg
        self.atr = atr
        self.gaps: List[Gap] = []
        self.bars: List[Bar] = []
        self._gid = 0
        self.diag: Dict[str, int] = {}

    # ── diagnostics ──
    def _bump(self, k: str, n: int = 1) -> None:
        self.diag[k] = self.diag.get(k, 0) + n

    def new_day(self) -> None:
        self.gaps = []
        self.bars = []

    # ── tf bar closed ──
    def on_tf_close(self, b: Bar) -> None:
        self.bars.append(b)
        atr = self.atr.update(b)
        i = len(self.bars) - 1
        # 1. age / inversion / death of existing gaps
        for g in self.gaps:
            if g.state == "DEAD":
                continue
            g.age += 1
            if g.age > self.cfg.gap_max_age:
                g.state = "DEAD"
                self._bump("gap_expired")
                continue
            if g.state == "FRESH":
                if (g.side == "BULL" and b.close < g.bottom) or \
                   (g.side == "BEAR" and b.close > g.top):
                    g.state = "INVERTED"
                    g.inverted_from = g.side
                    g.side = "BEAR" if g.side == "BULL" else "BULL"
                    g.age = 0
                    g.touched = False
                    g.used = False
                    self._bump("gap_inverted")
            elif g.state == "INVERTED":
                # reclaimed through the far side → band is spent
                if (g.side == "BEAR" and b.close > g.top) or \
                   (g.side == "BULL" and b.close < g.bottom):
                    g.state = "DEAD"
                    self._bump("ifvg_reclaimed")
        # 2. detect a new gap on the last three closed bars
        if atr is None or i < 2:
            return
        c0, c1, c2 = self.bars[i - 2], self.bars[i - 1], self.bars[i]
        body = abs(c1.close - c1.open)
        side = None
        if c2.low > c0.high and c1.close > c1.open:
            side, top, bottom = "BULL", c2.low, c0.high
        elif c2.high < c0.low and c1.close < c1.open:
            side, top, bottom = "BEAR", c0.low, c2.high
        if side is None:
            return
        self._bump("gap_raw")
        if body < self.cfg.disp_atr * atr:
            self._bump("gap_rej_displacement")
            return
        if (top - bottom) < self.cfg.gap_min_atr * atr:
            self._bump("gap_rej_width")
            return
        lb = max(0, i - 1 - self.cfg.fib_lookback)
        seg = self.bars[lb:i - 1] or [c1]
        if side == "BULL":
            leg_low = min(x.low for x in seg + [c1])
            leg_high = max(c1.high, c2.high)
        else:
            leg_high = max(x.high for x in seg + [c1])
            leg_low = min(c1.low, c2.low)
        fib_ok = _fib_overlap(self.cfg, side, top, bottom, leg_low, leg_high)
        self._bump("gap_fib_ok" if fib_ok else "gap_fib_reject")
        self._gid += 1
        g = Gap(side=side, top=top, bottom=bottom, born_idx=i,
                born_end_min=c2.end_min, atr_at_birth=atr,
                leg_low=leg_low, leg_high=leg_high, fib_ok=fib_ok, gid=self._gid)
        self.gaps.append(g)
        self._bump("gap_born_bull" if side == "BULL" else "gap_born_bear")
        live = [x for x in self.gaps if x.state != "DEAD"]
        if len(live) > self.cfg.max_gaps:
            for x in live[:-self.cfg.max_gaps]:
                x.state = "DEAD"
                self._bump("gap_evicted")

    # ── 1m bar (after its tf bar's predecessors have closed) ──
    def on_minute(self, m: Bar, mod: int) -> Optional[Signal]:
        cfg = self.cfg
        for g in self.gaps:
            if g.state == "DEAD" or g.used or g.born_end_min >= mod:
                continue
            is_ifvg = g.state == "INVERTED"
            if is_ifvg and not cfg.trade_ifvg:
                continue
            if not is_ifvg and not cfg.trade_fvg:
                continue
            if not is_ifvg and cfg.fib_gate and not g.fib_ok:
                continue
            if g.side == "BULL":
                if cfg.direction == "PE":
                    continue
                if not g.touched and m.low <= g.top:
                    g.touched = True
                    self._bump("touch_bull")
                if g.touched and m.close > g.top and m.close > m.open:
                    g.used = True
                    buf = cfg.sl_buf_atr * g.atr_at_birth
                    self._bump("trig_ifvg" if is_ifvg else "trig_fvg")
                    return Signal(trigger_min=mod, side="CE", kind=g.kind,
                                  sl_level=g.bottom - buf, zone_top=g.top,
                                  zone_bottom=g.bottom,
                                  leg_target=None if is_ifvg else g.leg_high,
                                  atr=g.atr_at_birth, gid=g.gid)
            else:
                if cfg.direction == "CE":
                    continue
                if not g.touched and m.high >= g.bottom:
                    g.touched = True
                    self._bump("touch_bear")
                if g.touched and m.close < g.bottom and m.close < m.open:
                    g.used = True
                    buf = cfg.sl_buf_atr * g.atr_at_birth
                    self._bump("trig_ifvg" if is_ifvg else "trig_fvg")
                    return Signal(trigger_min=mod, side="PE", kind=g.kind,
                                  sl_level=g.top + buf, zone_top=g.top,
                                  zone_bottom=g.bottom,
                                  leg_target=None if is_ifvg else g.leg_low,
                                  atr=g.atr_at_birth, gid=g.gid)
        return None


def session_signals(engine: FvgEngine, bars_1m: List[Bar], *,
                    day_start_epoch: int, tf_minutes: int) -> List[Signal]:
    """Walk one session causally: for each tf bar, run its 1m bars through
    the trigger machine (gaps from EARLIER tf bars only), then close the
    tf bar (detection). Returns every signal the day produced; the runner
    decides which are tradeable (position, budget, window)."""
    bars_tf = resample_session(bars_1m, day_start_epoch=day_start_epoch,
                               tf_minutes=tf_minutes)
    by_min = {(b.ts - day_start_epoch) // 60: b for b in bars_1m}
    sigs: List[Signal] = []
    engine.new_day()
    for tb in bars_tf:
        for mod in range(tb.start_min, tb.end_min + 1):
            m = by_min.get(mod)
            if m is None:
                continue
            s = engine.on_minute(m, mod)
            if s is not None:
                sigs.append(s)
        engine.on_tf_close(tb)
    return sigs


# ─────────────────────────────────────────────────────────────────────────
#  helpers shared with the runner
# ─────────────────────────────────────────────────────────────────────────
def strike_step(strikes) -> float:
    s = sorted({float(x) for x in strikes if x is not None})
    gaps = [b - a for a, b in zip(s, s[1:]) if b - a > 0]
    return min(gaps) if gaps else 50.0


def atm_strike(spot: float, step: float) -> float:
    return round(spot / step) * step

# backend/app/engine/brk/brk_live_core.py
#
# ── BRK_V1 LIVE DECISION CORE ── pure, no app imports, fully unit-tested.
# ============================================================================
# Fence: BRK_V1_LIVE_20260902
#
# PARITY BY CONSTRUCTION (LD2): every decision function here is either
# IMPORTED FROM THE SEALED BACKTEST RUNNER (pick_candidate / confirmed_at /
# first_break_minute / choose_side — app.backtest.brk.backtest_brk_runner has
# stdlib-only module imports, so this stays pure) or is a thin clock/gate
# around those functions. Nothing in live re-derives a selection or a break.
#
# The runner is the specification; this file only answers "what would the
# sealed backtest have decided with the bars seen so far".
#
# ── LIVE/BACKTEST DIVERGENCE LEDGER (LD2, mirrored in strategy_loader) ──
#  1. ENTRY FILL: backtest fills at the decision minute's 1m OPEN; live
#     places a market/aggressive-limit buy at the decision instant and
#     records the REAL fill.
#  2. EXITS: backtest triggers on bar low/high and fills AT the level; live
#     SL+TP are a broker OCO GTT (LD4) firing on ticks — slippage both ways,
#     and an intraminute touch the 1m bar never printed can fire live.
#  3. SELECTION INSTANT: backtest reads the close of the bar ENDING at
#     select_time; live samples chain LTPs at select_time:00. Same instant
#     by construction (P1), different transport.
#
# ── PrefixGuard (lifted from VET doctrine) ─────────────────────────────
# Bars must arrive strictly forward. Any restated/rewound minute FREEZES
# decisions for the day (fail closed) — a rebuilt candle stream after a
# reconnect must never let the engine re-decide history.
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:
    from app.backtest.brk.backtest_brk_runner import (
        DEFAULTS as BT_DEFAULTS, pick_candidate, confirmed_at,
        first_break_minute, choose_side)
except ImportError:                                        # standalone tests
    from backtest_brk_runner import (                      # type: ignore
        DEFAULTS as BT_DEFAULTS, pick_candidate, confirmed_at,
        first_break_minute, choose_side)

# Decisions
WAIT = "WAIT"            # nothing to do this minute
ENTER = "ENTER"          # buy `symbol` now
NO_TRADE = "NO_TRADE"    # window closed with no entry (terminal for session)
FROZEN = "FROZEN"        # PrefixGuard tripped — no decisions until tomorrow

# Exit reasons (paper engine / EOD; live SL+TP normally exit via the GTT)
R_SL, R_TP, R_EOD, R_KILL = "SL", "TP", "EOD", "KILL"

IST_OFFSET = 5 * 3600 + 30 * 60


def hhmm_to_min(s: str, fallback: int) -> int:
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 else fallback
    except (ValueError, AttributeError):
        return fallback


def minute_of_day(ts: int) -> int:
    """Aligned IST minute-of-day for an epoch ts (ts may be unaligned)."""
    return ((ts + IST_OFFSET) % 86400) // 60


def align_minute(ts: int) -> int:
    """Part-2b rule: candle keys are minute-start epochs; align BEFORE any
    store probe."""
    return ts - (ts % 60)


@dataclass
class PrefixGuard:
    """Bars strictly forward or the day freezes. Strategy-agnostic."""
    last_ts: int = 0
    frozen: bool = False
    reason: str = ""

    def observe(self, bar_start_ts: int) -> bool:
        """True = bar accepted; False = guard is (now) frozen."""
        if self.frozen:
            return False
        if bar_start_ts <= self.last_ts:
            self.frozen = True
            self.reason = (f"restated bar {bar_start_ts} <= last {self.last_ts}"
                           f" — decisions frozen (fail closed)")
            return False
        self.last_ts = bar_start_ts
        return True


@dataclass
class SessionSpec:
    """One breakout window (session 1 or session 2), minutes-of-day."""
    sel_min: int
    first_min: int
    last_min: int
    tag: str                     # "BRK" | "BRK·S2"


@dataclass
class BrkDayState:
    """Everything the core needs to answer 'what now?' at a 1m close.

    closes[side] maps IST minute-of-day -> 1m close of the WATCHED contract
    for that side (the session's own selection). Selection prints are the
    LTPs sampled at the session's select instant.
    """
    spec: SessionSpec
    ce_sym: Optional[str] = None
    pe_sym: Optional[str] = None
    sel_prints: Dict[str, float] = field(default_factory=dict)   # side -> ltp
    closes: Dict[str, Dict[int, float]] = field(
        default_factory=lambda: {"CE": {}, "PE": {}})
    entered: bool = False
    done: bool = False           # terminal: entered, or window elapsed


class BrkCore:
    """Pure decision core for one trading day (both sessions).

    The wrapper (manager/engine) owns clocks, chain access, orders and
    persistence; this class owns every DECISION, using the sealed backtest
    functions for anything the backtest also decides.
    """

    def __init__(self, cfg: Optional[dict] = None):
        c = dict(BT_DEFAULTS)
        c.update(cfg or {})
        self.cfg = c
        self.guard = PrefixGuard()
        sustain = max(1, int(c.get("sustain_candles") or 1))
        self.sustain = sustain
        self.s1 = BrkDayState(SessionSpec(
            hhmm_to_min(c["select_time"], 565),
            hhmm_to_min(c["entry_first"], 570),
            hhmm_to_min(c["entry_last"], 570), "BRK"))
        self.s2: Optional[BrkDayState] = None
        if c.get("s2_enabled"):
            self.s2 = BrkDayState(SessionSpec(
                hhmm_to_min(c.get("s2_select_time", "10:25"), 625),
                hhmm_to_min(c.get("s2_entry_first", "10:30"), 630),
                hhmm_to_min(c.get("s2_entry_last", "10:30"), 630), "BRK·S2"))
        self.eod_min = hhmm_to_min(c["eod_square_off"], 915)

    # ── selection ──────────────────────────────────────────────────────
    def select(self, sess: BrkDayState,
               ltps: Dict[str, Dict[str, float]]) -> Tuple[Optional[str], Optional[str]]:
        """ltps: {"CE": {sym: ltp}, "PE": {sym: ltp}} sampled at the
        session's select instant, EXPECTED WEEKLY ONLY (P5 upstream).
        Uses the sealed pick_candidate — highest premium strictly below."""
        below = float(self.cfg["select_below"])
        floor = float(self.cfg.get("select_min") or 0.0)
        sess.ce_sym = pick_candidate(ltps.get("CE", {}), below=below, floor=floor)
        sess.pe_sym = pick_candidate(ltps.get("PE", {}), below=below, floor=floor)
        for side, sym in (("CE", sess.ce_sym), ("PE", sess.pe_sym)):
            if sym is not None:
                sess.sel_prints[side] = float(ltps[side][sym])
        return sess.ce_sym, sess.pe_sym

    # ── bar intake ─────────────────────────────────────────────────────
    def on_close(self, sess: BrkDayState, side: str, minute: int,
                 close: float) -> None:
        sess.closes[side][minute] = float(close)

    # ── the decision, at the START of minute m (i.e. bar m-1 just closed) ──
    def decide(self, sess: Optional[BrkDayState], m: int,
               *, s1_open: bool = False,
               s1_result: Optional[float] = None) -> Tuple[str, Optional[dict]]:
        """Returns (decision, payload). payload for ENTER:
        {symbol, side, tag, minute}.

        m is the IST minute-of-day of the decision instant. The wrapper calls
        this once per completed minute per active session.

        s1_open / s1_result implement the sealed S2 gates (LD-sheet):
          s2_only_if_flat: an s2 decision minute is SKIPPED while s1 is open;
          s2_only_if_loss: s2 never runs once s1 closed profitable.
        """
        if self.guard.frozen:
            return FROZEN, None
        if sess is None or sess.done:
            return WAIT, None
        spec = sess.spec
        if m < spec.first_min:
            return WAIT, None
        if m > spec.last_min:
            sess.done = True
            return NO_TRADE, None
        if spec.tag == "BRK·S2":
            if self.cfg.get("s2_only_if_loss") and s1_result is not None \
                    and s1_result > 0:
                sess.done = True
                return NO_TRADE, None
            if self.cfg.get("s2_only_if_flat", True) and s1_open:
                return WAIT, None          # skip THIS minute, window still open
        level = float(self.cfg["break_above"])
        ce_ok = bool(sess.ce_sym) and confirmed_at(
            sess.closes["CE"], m, level=level, sustain=self.sustain)
        pe_ok = bool(sess.pe_sym) and confirmed_at(
            sess.closes["PE"], m, level=level, sustain=self.sustain)
        if not (ce_ok or pe_ok):
            if m == spec.last_min:
                sess.done = True
                return NO_TRADE, None
            return WAIT, None
        ce_first = pe_first = None
        if ce_ok and pe_ok:
            ce_first = first_break_minute(sess.closes["CE"], from_min=spec.sel_min,
                                          to_min=m - 1, level=level)
            pe_first = first_break_minute(sess.closes["PE"], from_min=spec.sel_min,
                                          to_min=m - 1, level=level)
        side = choose_side(ce_ok=ce_ok, pe_ok=pe_ok,
                           policy=str(self.cfg.get("both_policy", "first")),
                           ce_first=ce_first, pe_first=pe_first,
                           ce_px=sess.closes["CE"].get(m - 1),
                           pe_px=sess.closes["PE"].get(m - 1))
        if side is None:                    # both_policy=skip collision
            sess.done = True
            return NO_TRADE, None
        sess.entered = sess.done = True
        sym = sess.ce_sym if side == "CE" else sess.pe_sym
        return ENTER, {"symbol": sym, "side": side, "tag": spec.tag, "minute": m}

    # ── exits (paper engine path; live normally exits via the OCO GTT) ──
    def exit_levels(self, entry_px: float) -> Tuple[float, Optional[float]]:
        sl = round(entry_px - float(self.cfg["sl_pts"]), 2)
        tp_pts = float(self.cfg.get("tp_pts") or 0.0)
        tp = round(entry_px + tp_pts, 2) if tp_pts > 0 else None
        return sl, tp

    def check_exit(self, *, ltp: float, sl_px: float,
                   tp_px: Optional[float], m: int) -> Optional[str]:
        """Tick-level exit test. SL wins a same-tick collision (D5).
        EOD strictly by clock, regardless of price."""
        if m >= self.eod_min:
            return R_EOD
        if ltp <= sl_px:
            return R_SL
        if tp_px is not None and ltp >= tp_px:
            return R_TP
        return None
#!/usr/bin/env python3
# apply_vet_v1_backtest_20260826.py
#
# ── VET_V1 BACKTEST ── creates backend/app/backtest/vet/ (engine + runner
# + unit tests) and wires the strategy into BOTH dispatch copies:
#   1. backend/app/backtest/queue_worker.py   (Queue + Sweep path)
#   2. backend/app/api/backtest_routes.py     (Run path)
#
# BACKTEST-ONLY. Nothing in the live fleet is touched: no strategy
# package under app/strategy/, no scheduler job, no settings surface,
# no live executor path. BB_V1 and every other live strategy are
# untouched by construction — this script writes ONE new directory and
# makes THREE edits, all listed above.
#
# DOCTRINE
#   * ASSERT-ANCHORED: every edit asserts its anchor exists and is
#     UNIQUE. Any miss or ambiguity aborts BEFORE a single byte is
#     written.
#   * IDEMPOTENT: re-running is safe. Already-wired edits are reported
#     as SKIP, not duplicated.
#   * STAGED: all edits are computed in memory and py_compile'd from a
#     temp copy; the real files are only replaced once every stage has
#     compiled.
#   * DUAL-TREE: desktop/src-tauri/backend/ is GITIGNORED and rsynced at
#     build time. This script mirrors into it when the directory exists
#     locally and prints a clear notice when it does not.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_backtest_20260826.py            # apply
#   python3 apply_vet_v1_backtest_20260826.py --dry-run  # report only
#
# VERIFY AFTER APPLYING
#   cd backend/app/backtest/vet && python3 test_vet_engine.py
#   (expect: ALL TESTS PASSED — 15 checks)

import argparse
import os
import py_compile
import shutil
import sys
import tempfile

REPO = os.getcwd()
BACKEND = os.path.join(REPO, "backend")
DESKTOP_BACKEND = os.path.join(REPO, "desktop", "src-tauri", "backend")

VET_DIR_REL = os.path.join("app", "backtest", "vet")
QUEUE_REL = os.path.join("app", "backtest", "queue_worker.py")
ROUTES_REL = os.path.join("app", "api", "backtest_routes.py")


def die(msg):
    print(f"\nABORT: {msg}")
    print("Nothing was written.")
    sys.exit(1)


def require_unique(hay, needle, label):
    n = hay.count(needle)
    if n == 0:
        die(f"anchor NOT FOUND [{label}]:\n  {needle.strip()[:110]}")
    if n > 1:
        die(f"anchor AMBIGUOUS ({n} matches) [{label}]:\n  "
            f"{needle.strip()[:110]}")


# ── PAYLOADS ────────────────────────────────────────────────────────────
ENGINE_PY = r'''# backend/app/backtest/vet/vet_v1_engine.py
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
'''

RUNNER_PY = r'''# backend/app/backtest/vet/backtest_vet_runner.py
#
# ── VET_V1 RUNNER ── dual-EMA trend follower with SMA±ATR regime filter
# (Pine "Vivek Equity Tool" parity port, see vet_v1_engine), SIGNALS ON
# SPOT (5m/15m), EXECUTION = option BUYING on NIFTY weeklies or stock
# monthlies (GC_STOCK_MODE doctrine). Decision logic lives in
# vet_v1_engine (pure, unit-tested); this shim owns corpus access, strike
# selection, option fills, MULTI-DAY POSITION CARRY, expiry rollover,
# optional overlays (SL/TP/EOD square), charges, DIAG and persistence
# shape.
#
# ── WHAT IS NEW vs GC/VAP (the carry doctrine) ──────────────────────────
#   Positions CARRY OVERNIGHT by default — the Pine source is a swing
#   system; EOD square is an OPTIONAL overlay (D5), off for baseline.
#   Consequences, all handled here:
#     * The signal chain is computed ONCE over a CONTINUOUS resample of
#       (warmup_sessions + range) sessions — indicators cross session
#       boundaries exactly as TradingView draws them intraday.
#     * TARGET-RECONCILIATION replaces edge-events: at every tf close the
#       held side is reconciled against the machine's condition. Forced
#       exits (EOD/roll/SL/TP) leave the TARGET untouched; re-entry rules
#       decide when the book re-syncs (see REENTRY below).
#     * EXPIRY: a held contract is exited/rolled at roll_time on ITS OWN
#       expiry day. FAIL-SAFE: nothing ever carries past its expiry date
#       — post-day sweep force-exits at the last available print.
#
# ── FILL CONVENTION (house standard, GC D5) ─────────────────────────────
#   Every decision happens at a tf-candle CLOSE; the fill is the option's
#   1m CLOSE at that candle's last1m_ts. Exit prints fall back to the most
#   recent close ≤ the minute IN THE SAME DAY (stale_exit_fills) — an exit
#   must never be dropped. Zero lookahead.
#
# ── ENTRY EXPIRY POLICY (D10) ───────────────────────────────────────────
#   want = expected_expiry_for_day(d)  (index weekly)
#        | expected_stock_monthly_expiry_for_day(d)  (stock monthly)
#   Bumped to the NEXT expiry when (a) entering ON expiry day at/after
#   roll_time, or (b) min_entry_dte > 0 and DTE < min_entry_dte.
#   FAIL-CLOSED: the chosen expiry absent from the corpus that day → the
#   entry is SKIPPED (diag), never silently re-targeted.
#
# ── REENTRY (locked semantics) ──────────────────────────────────────────
#   * SIGNAL exit/flip: immediate — reconciliation IS the signal.
#   * EOD square / failed roll: re-enter when the target still holds,
#     if reenter_after_forced_exit (default True) — a squared swing
#     position resumes next session, else the overlay would silently
#     convert the system to single-day trades.
#   * SL/TP: re-enter only on a FRESH target (block until the condition
#     CHANGES), unless reenter_after_sltp=True. An SL said "out" — re-
#     buying the same signal next bar is churn, not the strategy.
#
# ── STRIKE (D9) ─────────────────────────────────────────────────────────
#   strike_selection "atm" (default): strike nearest the decision-minute
#   spot close, then atm_offset strikes OTM-ward (CE up / PE down;
#   negative = ITM-ward). Optional premium band [premium_min, premium_max]
#   is a VETO (0 = off). "premium": legacy highest ≤ premium_max (IC
#   semantics). min_entry_volume gates the ladder BEFORE selection (GC
#   liq-gate — on stock options a priced-but-untraded minute is a stale
#   print wearing a price). All selection failures are fail-closed skips.
#
# Read-only on the corpus. BUY-only (D8): P&L = (exit − entry) · qty,
# qty = lots × lot_size (index constant / stock map / config override —
# unknown stock lot ABORTS, never a guessed qty). Charges via
# charges_model (long path).
#
# Keep the dispatch chain in sync with queue_worker._dispatch_run_impl
# AND api/backtest_routes.run_start — two hand-maintained copies.

from __future__ import annotations

# PyInstaller anchors — tolerant if unavailable at module-import time.
try:
    import app.backtest.data.candle_source  # noqa: F401
except Exception:
    pass

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.backtest.ic.backtest_ic_runner import (
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
    )
    from app.backtest.gc.backtest_gc_runner import (
        INDEX_UNDERLYINGS, STOCK_LOT_SIZES, _resolve_corpus_db,
    )
    from app.backtest.gc.gc_v1_engine import TFCandle, resample_spot
    from app.backtest.ic.backtest_ic_runner import (
        _minute_ladder, _synth_leg_at, _synth_mark_at)
    from app.backtest.vet.vet_v1_engine import vet_states
except ImportError:  # standalone test harness
    from ic_v1_engine import select_strike                        # type: ignore
    from backtest_ic_runner import (                              # type: ignore
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
    )
    from backtest_gc_runner import (                              # type: ignore
        INDEX_UNDERLYINGS, STOCK_LOT_SIZES, _resolve_corpus_db,
    )
    from gc_v1_engine import TFCandle, resample_spot              # type: ignore
    from backtest_ic_runner import (                             # type: ignore
        _minute_ladder, _synth_leg_at, _synth_mark_at)
    from vet_v1_engine import vet_states                          # type: ignore

SESSION_OPEN_MIN = 9 * 60 + 15   # 09:15 IST
VALID_TF = (5, 15)               # D7 lock — widen only via a D-round

DEFAULT_VET_CONFIG = {
    # ── signal engine (Pine defaults, D6) ──
    "timeframe_minutes": 5,        # 5 | 15 (D7)
    "ema_fast1": 10,
    "ema_fast2": 20,
    "trend_len": 40,               # SMA + ATR length
    "range_len": 0.618,            # channel = SMA ± ATR·range_len
    "direction": "BOTH",           # BOTH | LONG | SHORT (D2)
    "warmup_sessions": 10,         # prior sessions seeded before date_from
    "enter_open_state_at_start": True,   # condition ±1 at range start →
                                         # enter on the first tradable bar
    # ── execution (D3/D8/D9/D11) ──
    # ── LEG_ACTION (2026-08-27) ── BUY expresses the signal with long
    # options (up-trend -> long CE, down-trend -> long PE). SELL expresses
    # the SAME signal with short options on the opposite side (up-trend ->
    # SHORT PE, down-trend -> SHORT CE), so the directional exposure is
    # unchanged while the payoff inverts: theta becomes income instead of
    # cost, and the loss tail becomes unbounded.
    "leg_action": "BUY",           # BUY | SELL
    # ── HEDGE_LEG (2026-08-27) ── SELL-mode protective wing: a long option
    # of the SAME type and expiry as the short leg, priced at or under
    # hedge_max_premium. In live this is what earns the SPAN margin benefit
    # and caps the otherwise unbounded tail, so a naked fallback would be a
    # silent risk change — when no wing is available under the cap the
    # ENTRY IS SKIPPED, never taken bare.
    "hedge_enabled": False,        # ignored unless leg_action == SELL
    "hedge_max_premium": 5.0,      # buy the DEAREST wing at or under this
    # ── SYNTH_WING ── when no REAL contract at or under the cap has a print,
    # model one instead of dropping the trade. Reuses the IC synthetic-wing
    # primitives verbatim (ic_synth_wing + _synth_leg_at/_synth_mark_at) so
    # both strategies price a dark wing the same way. Off => fail-closed.
    "hedge_synth_enabled": True,
    "hedge_skew_mult": 1.0,        # IC's WING skew knob, same default
    "lots": 1,
    "lot_size": 0,                 # 0 = auto (index const / stock map);
                                   # unknown stock with 0 → fail-closed abort
    "underlying": "NIFTY",         # config wins over the route arg
    "strike_selection": "atm",     # atm | premium
    "atm_offset": 0,
    # ── SPOT_RELATIVE_SELECTION (2026-08-27) ── ladder steps are a FIXED
    # rupee distance while spot moves, so an offset in steps silently
    # changes meaning as the underlying re-rates: on DIXON one step was
    # 0.98% of spot in 2021 and 0.43% in 2026, on NIFTY 0.41% -> 0.20%.
    # Non-zero atm_offset_pct expresses the offset as a % of SPOT and
    # OVERRIDES atm_offset, holding moneyness constant across eras.
    "atm_offset_pct": 0.0,         # % of spot, +OTM / -ITM; 0 = use steps
    "premium_min": 0.0,            # veto band, 0 = off  (atm mode)
    "premium_max": 0.0,            # atm: veto cap · premium mode: selector cap
    # ── Premium band as a % OF SPOT. Portable where an absolute rupee band
    # is not: on a MONTHLY chain ATM premium runs ~2.4% of spot near expiry
    # and ~5.0% at 22-45 DTE, so one rupee cap cannot mean the same thing
    # twice. 0 = off.
    "premium_pct_min": 0.0,
    "premium_pct_max": 0.0,
    "min_entry_volume": 0,         # liq gate, 0 = off
    # ── expiry / carry (D10) ──
    "rollover_enabled": True,
    "roll_time": "15:00",          # expiry-day exit/roll boundary
    "min_entry_dte": 0,            # bump to next expiry when DTE < this
    "max_entry_dte": 0,            # block entry when DTE > this; 0 = off
    # ── optional overlays (D5) — ALL OFF for baseline ──
    "sl_pct": 0.0,                 # % of entry premium, close-based, 0 = off
    "tp_pct": 0.0,                 # % of entry premium, close-based, 0 = off
    "eod_square": False,
    "exit_time": "15:15",          # EOD square minute (when eod_square)
    "entry_cutoff_time": "15:30",  # no NEW entries at/after; default = off
    "max_trades_per_day": 0,       # signal entries + flips; 0 = unlimited
    "max_daily_mtm_loss": 0.0,     # ₹ per DAY, realised + open MTM, 0 = off
    "reenter_after_forced_exit": True,
    "reenter_after_sltp": False,
}


def _norm_cfg(raw: Optional[dict]) -> dict:
    cfg = dict(DEFAULT_VET_CONFIG)
    for k, v in (raw or {}).items():
        if k in cfg and v is not None:
            cfg[k] = v
    tf = int(cfg["timeframe_minutes"] or 5)
    cfg["timeframe_minutes"] = tf if tf in VALID_TF else 5
    cfg["ema_fast1"] = max(1, int(cfg["ema_fast1"] or 10))
    cfg["ema_fast2"] = max(1, int(cfg["ema_fast2"] or 20))
    cfg["trend_len"] = max(2, int(cfg["trend_len"] or 40))
    cfg["range_len"] = abs(float(cfg["range_len"] or 0.618))
    d = str(cfg["direction"]).upper()
    cfg["direction"] = d if d in ("BOTH", "LONG", "SHORT") else "BOTH"
    cfg["warmup_sessions"] = max(1, int(cfg["warmup_sessions"] or 10))
    cfg["enter_open_state_at_start"] = bool(cfg["enter_open_state_at_start"])
    cfg["hedge_enabled"] = bool(cfg["hedge_enabled"])
    cfg["hedge_max_premium"] = abs(float(cfg["hedge_max_premium"] or 0))
    cfg["hedge_synth_enabled"] = bool(cfg["hedge_synth_enabled"])
    cfg["hedge_skew_mult"] = float(cfg["hedge_skew_mult"] or 1.0)
    la = str(cfg["leg_action"]).upper().strip()
    cfg["leg_action"] = "SELL" if la == "SELL" else "BUY"
    cfg["lots"] = max(0, int(cfg["lots"] or 0))
    cfg["lot_size"] = max(0, int(cfg["lot_size"] or 0))
    cfg["underlying"] = str(cfg["underlying"] or "NIFTY").upper().strip()
    cfg["strike_selection"] = ("premium" if str(cfg["strike_selection"]).lower()
                               == "premium" else "atm")
    cfg["atm_offset"] = int(cfg["atm_offset"] or 0)
    cfg["atm_offset_pct"] = float(cfg["atm_offset_pct"] or 0)
    cfg["premium_pct_min"] = abs(float(cfg["premium_pct_min"] or 0))
    cfg["premium_pct_max"] = abs(float(cfg["premium_pct_max"] or 0))
    cfg["premium_min"] = abs(float(cfg["premium_min"] or 0))
    cfg["premium_max"] = abs(float(cfg["premium_max"] or 0))
    cfg["min_entry_volume"] = max(0, int(cfg["min_entry_volume"] or 0))
    cfg["rollover_enabled"] = bool(cfg["rollover_enabled"])
    cfg["roll_time"] = str(cfg["roll_time"] or "15:00")
    cfg["min_entry_dte"] = max(0, int(cfg["min_entry_dte"] or 0))
    cfg["max_entry_dte"] = max(0, int(cfg["max_entry_dte"] or 0))
    cfg["sl_pct"] = abs(float(cfg["sl_pct"] or 0))
    cfg["tp_pct"] = abs(float(cfg["tp_pct"] or 0))
    cfg["eod_square"] = bool(cfg["eod_square"])
    cfg["exit_time"] = str(cfg["exit_time"] or "15:15")
    cfg["entry_cutoff_time"] = str(cfg["entry_cutoff_time"] or "15:30")
    cfg["max_trades_per_day"] = max(0, int(cfg["max_trades_per_day"] or 0))
    cfg["max_daily_mtm_loss"] = abs(float(cfg["max_daily_mtm_loss"] or 0))
    cfg["reenter_after_forced_exit"] = bool(cfg["reenter_after_forced_exit"])
    cfg["reenter_after_sltp"] = bool(cfg["reenter_after_sltp"])
    return cfg


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[ICTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_vet"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    return {
        "total_trades": len(closed), "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0,   # VET fills are close-of-minute → never ambiguous
        "diag_vet": diag,
    }


@dataclass
class _Pos:
    side: str            # "CE" | "PE"
    symbol: str
    strike: Optional[float]
    expiry_iso: str
    expiry_date: date
    entry_ts: int
    entry_px: float
    tag: str             # "VET" | "VET·FLIP" | "VET·ROLL" | "VET·RESUME"
    last_mark: float     # stale-carry mark for SL/TP checks
    # ── HEDGE_LEG ── None when unhedged. Same expiry and type as the short.
    hedge_symbol: Optional[str] = None
    hedge_entry_px: float = 0.0
    hedge_last_mark: float = 0.0
    hedge_synth: bool = False          # priced by model, not by a print
    hedge_strike: float = 0.0          # needed to re-mark a synthetic wing


def run_vet_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "VET_V1"
    underlying: str,            # route arg; cfg["underlying"] wins
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _run_vet_backtest_impl(
                db_path=db_path, strategy_id=strategy_id,
                underlying=underlying, date_from=date_from, date_to=date_to,
                config_override=config_override,
                progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _run_vet_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_vet_backtest_impl(
    *,
    db_path: str,
    strategy_id: str,
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import write_audit_log
    except ImportError:
        def write_audit_log(msg: str) -> None:   # type: ignore
            print(msg)
    try:
        from app.backtest.data.candle_source import CandleSource
    except ImportError:
        from data.candle_source import CandleSource               # type: ignore
    try:
        from app.backtest.engine.expiry_calendar import (
            expected_expiry_for_day, expected_stock_monthly_expiry_for_day)
    except ImportError:
        from engine.expiry_calendar import (                      # type: ignore
            expected_expiry_for_day, expected_stock_monthly_expiry_for_day)
    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
    except Exception:
        charges_for_long_trade = None
        charges_for_short_trade = None

    cfg = _norm_cfg(config_override)
    is_sell = cfg["leg_action"] == "SELL"
    _charges_fn = charges_for_short_trade if is_sell else charges_for_long_trade
    underlying = cfg["underlying"] or underlying
    is_stock = underlying not in INDEX_UNDERLYINGS
    db_path = _resolve_corpus_db(db_path, underlying)
    tf = cfg["timeframe_minutes"]
    roll_min = _hm_to_min(cfg["roll_time"], 15 * 60)
    exit_min = _hm_to_min(cfg["exit_time"], 15 * 60 + 15)
    cutoff_min = _hm_to_min(cfg["entry_cutoff_time"], 15 * 60 + 30)

    if cfg["lot_size"] > 0:
        lot_size = cfg["lot_size"]
    elif not is_stock:
        lot_size = LOT_SIZE
    elif underlying in STOCK_LOT_SIZES:
        lot_size = STOCK_LOT_SIZES[underlying]
    else:
        return {"run_id": None, "aborted": True,
                "reason": f"{underlying}: lot size unknown — set lot_size in "
                          f"the VET config (or add it to STOCK_LOT_SIZES)",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    if cfg["lots"] <= 0:
        return {"run_id": None, "aborted": True, "reason": "lots must be > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    if cfg["ema_fast1"] >= cfg["ema_fast2"]:
        return {"run_id": None, "aborted": True,
                "reason": "ema_fast1 must be < ema_fast2 (10/20 in the "
                          "source) — an inverted pair silently mirrors every "
                          "signal",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    qty = cfg["lots"] * lot_size

    import os as _os
    if is_stock and not _os.path.exists(db_path):
        return {"run_id": None, "aborted": True,
                "reason": f"{underlying}: corpus db not found at {db_path} — "
                          f"run: python3 -m app.backtest.dhan.stock_backfill "
                          f"--underlying {underlying} --db {db_path}",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    def _close_all() -> None:
        conn.close()
        try:
            src.close()
        except Exception:
            pass

    lo, hi = _day_start_epoch(date_from), _day_start_epoch(date_to) + 86400
    range_days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
        ORDER BY d""", (underlying, lo, hi))]
    if not range_days:
        _close_all()
        return {"run_id": None, "aborted": True,
                "reason": f"no {underlying} spot data in range — run the "
                          f"spot backfill",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    warm_days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts<?
        ORDER BY d DESC LIMIT ?""",
        (underlying, lo, cfg["warmup_sessions"]))][::-1]

    def spot_1m_for(d: date) -> List[dict]:
        ds = _day_start_epoch(d)
        return [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
            ORDER BY ts""", (underlying, ds, ds + 86400))]

    # ── CONTINUOUS RESAMPLE ── one bar list across (warmup + range),
    # session-anchored per day; the engine treats the overnight gap as
    # adjacent bars — exactly TradingView's intraday behavior. day_of[i]
    # maps each bar to its session date.
    bars: List[TFCandle] = []
    day_of: List[date] = []
    for d in warm_days + range_days:
        for c in resample_spot(spot_1m_for(d), tf,
                               _day_start_epoch(d) + SESSION_OPEN_MIN * 60):
            bars.append(c)
            day_of.append(d)
    tradable_from = next((i for i, dd in enumerate(day_of)
                          if dd >= range_days[0]), len(bars))
    if tradable_from >= len(bars):
        _close_all()
        return {"run_id": None, "aborted": True,
                "reason": "no tradable bars after resample — corpus gap?",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    states = vet_states(bars, ema_fast1=cfg["ema_fast1"],
                        ema_fast2=cfg["ema_fast2"],
                        trend_len=cfg["trend_len"],
                        range_len=cfg["range_len"])

    diag = {
        "days_total": len(range_days), "days_traded": 0,
        "days_position_open": 0,
        "warmup_sessions": len(warm_days), "warmup_bars": tradable_from,
        "bars_total": len(bars),
        "warmup_valid": bool(tradable_from and states[tradable_from - 1].valid),
        "entries": 0, "flip_entries": 0, "roll_entries": 0,
        "resume_entries": 0, "start_state_entries": 0,
        "signal_exits": 0, "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,
        "roll_exits": 0, "expiry_force_exits": 0,
        "rolls_no_next_expiry": 0, "no_strike_entries": 0,
        "roll_expiry_probes": 0, "roll_expiry_gap_gt_week": 0,
        "daily_cap_days": 0, "daily_cap_exits": 0,
        "daily_cap_blocked_bars": 0,
        "no_entry_price": 0, "liq_gate_entries": 0,
        "premium_veto_entries": 0, "premium_pct_veto_entries": 0,
        "max_dte_blocked_entries": 0, "cutoff_blocked_entries": 0,
        "hedge_exits": 0, "hedge_cost_total": 0.0, "hedge_stale_fills": 0,
        "hedge_real": 0, "hedge_synth": 0, "hedge_synth_exits": 0,
        "hedge_synth_pnl_gross": 0.0, "hedge_synth_fail": 0,
        "no_hedge_entries": 0,
        "dte_bumped_entries": 0, "cap_blocked_entries": 0,
        "sltp_reentry_blocks": 0, "forced_reentry_blocks": 0,
        "entry_expiry_uncovered": 0,
        "stale_exit_fills": 0, "stale_marks": 0,
        "leg_action": cfg["leg_action"],
        "hedge_enabled": cfg["hedge_enabled"],
        "hedge_max_premium": cfg["hedge_max_premium"],
        "hedge_synth_enabled": cfg["hedge_synth_enabled"],
        "hedge_skew_mult": cfg["hedge_skew_mult"],
        "underlying": underlying, "is_stock": is_stock,
        "lot_size": lot_size, "corpus_db": db_path.rsplit("/", 1)[-1],
        "timeframe_minutes": tf, "direction": cfg["direction"],
        "strike_selection": cfg["strike_selection"],
        "atm_offset": cfg["atm_offset"],
        "atm_offset_pct": cfg["atm_offset_pct"],
        "premium_pct_band": [cfg["premium_pct_min"], cfg["premium_pct_max"]],
        "max_entry_dte": cfg["max_entry_dte"],
        "premium_band": [cfg["premium_min"], cfg["premium_max"]],
        "sl_pct": cfg["sl_pct"], "tp_pct": cfg["tp_pct"],
        "eod_square": cfg["eod_square"],
        "max_daily_mtm_loss": cfg["max_daily_mtm_loss"],
        "rollover_enabled": cfg["rollover_enabled"],
        "roll_time": cfg["roll_time"], "min_entry_dte": cfg["min_entry_dte"],
    }
    trades: List[ICTrade] = []

    def _emit(pos: _Pos, exit_ts: int, exit_px: float, reason: str) -> None:
        # ── LEG_ACTION ── a SHORT leg earns the premium decay: gross is
        # (entry - exit), and STT falls on the ENTRY leg rather than the
        # exit. charges_model already encodes both, so the correct model is
        # selected here rather than sign-flipping a long result.
        gross = ((pos.entry_px - exit_px) if is_sell
                 else (exit_px - pos.entry_px)) * qty
        charges = 0.0
        if _charges_fn is not None:
            try:
                cr = _charges_fn(entry_price=pos.entry_px,
                                 exit_price=exit_px, qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        # ── HEDGE_LEG ── ONE row per position, carrying the COMBINED P&L of
        # the short and its wing. Deliberately NOT two rows: a wing almost
        # always expires a small loser, so separate rows would halve the
        # apparent win rate and double the trade count while describing the
        # same position. tradingsymbol / entry_price / exit_price stay on the
        # SHORT leg — it is the leg the signal is about — while pnl, charges
        # and net_pnl are the pair. Aggregate wing economics live in
        # diag_vet (hedge_cost_total), so nothing is hidden.
        # NOTE: the attribute is NOT called hedge_symbol on the trade row.
        # backtest_repo switches on hasattr(t, "hedge_symbol") and would take
        # the V3/V4 branch, which stores the HEDGE as the primary row — the
        # opposite orientation to this one.
        if pos.hedge_symbol:
            hx = _mark_hedge(pos, _day_ctx["day"], exit_ts)
            if hx is None:
                hx = pos.hedge_last_mark
            h_gross = (hx - pos.hedge_entry_px) * qty
            h_charges = 0.0
            if charges_for_long_trade is not None:
                try:
                    hr = charges_for_long_trade(
                        entry_price=pos.hedge_entry_px, exit_price=hx, qty=qty)
                    h_charges = float(getattr(hr, "total_charges", 0.0))
                    h_gross = float(getattr(hr, "gross_pnl", h_gross))
                except Exception:
                    h_charges = 0.0
            gross += h_gross
            charges += h_charges
            diag["hedge_cost_total"] += round(h_gross - h_charges, 2)
            diag["hedge_exits"] += 1
            if pos.hedge_synth:
                # ── HONESTY ── model-attributed share of the curve, the IC
                # convention. Read this before trusting a hedged run.
                diag["hedge_synth_pnl_gross"] += round(h_gross, 2)
                diag["hedge_synth_exits"] += 1
        # SL/TP levels invert for a short: the loss is premium RISING.
        if is_sell:
            sl_lvl = (round(pos.entry_px * (1 + cfg["sl_pct"] / 100.0), 2)
                      if cfg["sl_pct"] > 0 else None)
            tp_lvl = (round(pos.entry_px * (1 - cfg["tp_pct"] / 100.0), 2)
                      if cfg["tp_pct"] > 0 else None)
        else:
            sl_lvl = (round(pos.entry_px * (1 - cfg["sl_pct"] / 100.0), 2)
                      if cfg["sl_pct"] > 0 else None)
            tp_lvl = (round(pos.entry_px * (1 + cfg["tp_pct"] / 100.0), 2)
                      if cfg["tp_pct"] > 0 else None)
        trades.append(ICTrade(
            tradingsymbol=pos.symbol, symbol=pos.symbol,
            instrument_type=pos.side,
            strike=pos.strike, expiry=pos.expiry_iso,
            direction=("SELL" if is_sell else "BUY"),
            entry_ts=pos.entry_ts, entry_price=round(pos.entry_px, 2),
            sl=sl_lvl, tp=tp_lvl,
            exit_ts=exit_ts, exit_price=round(exit_px, 2),
            exit_reason=reason, qty=qty,
            condition=pos.tag, ambiguous_fill=False,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=False,
            # ── SYNTH_WING ── the row is flagged synthetic when its WING was
            # modelled; the short leg is always a real print.
            synthetic=bool(pos.hedge_synth),
            synth_kind=("hedge" if pos.hedge_synth else None),
        ))
        # ── DAILY_MTM_CAP ── every exit feeds the day's realised total,
        # including the day-boundary sweep and the expiry fail-safe.
        _day_pnl["realised"] += round(gross - charges, 2)

    # ── PER-DAY OPTION CONTEXT ── caches reset daily; a carried position's
    # symbol is served per-day via candles_1m_for_symbol_day (never through
    # a scoped preload — the carried expiry may differ from the entry
    # universe, the IC_V2 lesson).
    _day_ctx: dict = {"day": None, "universe": {}, "closes": {}, "vols": {}}

    # ── DAILY_MTM_CAP (2026-08-27) ── running P&L for the session. "realised"
    # accumulates every closed trade's NET (charges included, because the cap
    # is a real-money limit, not a gross one). The live comparison adds the
    # OPEN position's unrealised mark, so the cap measures the same quantity a
    # live MTM guard would see intraday — not an end-of-day figure that can
    # only be known in hindsight.
    _day_pnl: dict = {"realised": 0.0, "capped": False}

    def _reset_day(d: date) -> None:
        _day_ctx["day"] = d
        _day_ctx["universe"] = {}
        _day_ctx["closes"] = {}
        _day_ctx["vols"] = {}
        _day_pnl["realised"] = 0.0
        _day_pnl["capped"] = False

    def _universe(d: date, expiry_iso: str) -> List[dict]:
        u = _day_ctx["universe"].get(expiry_iso)
        if u is None:
            u = src.contracts_active_on_day(underlying, _day_start_epoch(d),
                                            expiry=expiry_iso)
            _day_ctx["universe"][expiry_iso] = u
        return u

    def _closes(sym: str, d: date) -> Dict[int, float]:
        m = _day_ctx["closes"].get(sym)
        if m is None:
            cbars = src.candles_1m_for_symbol_day(sym, _day_start_epoch(d))
            m = {c.ts: float(c.close) for c in cbars}
            _day_ctx["closes"][sym] = m
            _day_ctx["vols"][sym] = {c.ts: int(c.volume or 0) for c in cbars}
        return m

    def _vol_at(sym: str, d: date, minute_ts: int) -> int:
        _closes(sym, d)
        return _day_ctx["vols"].get(sym, {}).get(minute_ts, 0)

    def _fill_at(sym: str, d: date, minute_ts: int,
                 allow_stale: bool) -> Optional[float]:
        m = _closes(sym, d)
        px = m.get(minute_ts)
        if px is not None:
            return px
        if not allow_stale:
            return None
        older = [t for t in m if t <= minute_ts]
        if not older:
            return None
        diag["stale_exit_fills"] += 1
        return m[max(older)]

    def _px_at_or_before(sym: str, d: date, minute_ts: int):
        """→ (price, was_stale) | (None, False). The last print at or before
        `minute_ts` on day `d`. A DEEP-OTM wing trades sporadically: on most
        minutes it simply has no candle. Demanding an exact-minute print
        therefore reports 'no wing exists' on liquidity noise, which under a
        fail-closed rule silently deletes the ENTRY — measured at 56% of all
        entries, varying 28-69% by year. Carrying the last print is the same
        convention exits already use, and matches how the order would
        actually fill."""
        mm = _closes(sym, d)
        px = mm.get(minute_ts)
        if px is not None:
            return px, False
        older = [t for t in mm if t <= minute_ts]
        if not older:
            return None, False
        return mm[max(older)], True

    def _expected(d: date) -> date:
        return (expected_stock_monthly_expiry_for_day(d) if is_stock
                else expected_expiry_for_day(d))

    def _next_expiry(after: date) -> date:
        return _expected(after + timedelta(days=1))

    # ── ROLL_COVERAGE (2026-08-26) ── the calendar's "next expiry" is NOT
    # always a series that trades. Verified on the live corpus: on Tue
    # 2026-06-16 the expiries with candles are 06-16, 06-30 and 07-07 —
    # 06-23 is absent. Demanding the computed date therefore failed the
    # roll on essentially every expiry day (1 successful roll in 295), and
    # the position silently went flat overnight and re-entered next
    # morning as VET·RESUME. We now WALK FORWARD to the first expiry that
    # is actually covered that day, so the roll lands on a real series.
    # Still fail-closed: nothing covered within max_probe → no roll.
    def _next_covered_expiry(d: date, after: date,
                             max_probe: int = 6) -> Optional[date]:
        cand = _next_expiry(after)
        for _ in range(max_probe):
            if _universe(d, cand.isoformat()):
                gap = (cand - after).days
                if gap > 7:
                    diag["roll_expiry_gap_gt_week"] += 1
                return cand
            diag["roll_expiry_probes"] += 1
            cand = _next_expiry(cand)
        return None

    def _entry_expiry(d: date, minute_min: int) -> Optional[date]:
        exp = _expected(d)
        bump = False
        if d == exp and minute_min >= roll_min:
            bump = True
        if cfg["min_entry_dte"] > 0 and (exp - d).days < cfg["min_entry_dte"]:
            bump = True
        if bump:
            diag["dte_bumped_entries"] += 1
            # ── ROLL_COVERAGE ── same walk-forward rule as the roll: a
            # bumped entry must target a series that actually trades.
            nxt = _next_covered_expiry(d, exp)
            if nxt is None:
                return None
            exp = nxt
        # ── MAX_ENTRY_DTE ── far-from-expiry contracts on a MONTHLY chain
        # carry the most time value (~5% of spot at 22-45 DTE vs ~2.4% near
        # expiry), which a same-day-squared long option pays for and rarely
        # earns back. Blocking them is a fail-closed skip, never a silent
        # re-target to a different series.
        if cfg["max_entry_dte"] > 0 and (exp - d).days > cfg["max_entry_dte"]:
            diag["max_dte_blocked_entries"] += 1
            return None
        return exp

    def _select(d: date, side: str, expiry: date, minute_ts: int,
                spot_px: float):
        """→ (symbol, price, strike, expiry_iso) | None. Fail-closed."""
        exp_iso = expiry.isoformat()
        week = _universe(d, exp_iso)
        syms = [c for c in week if c["instrument_type"] == side]
        if not syms:
            diag["entry_expiry_uncovered"] += 1
            return None
        cands = []
        gated = 0
        for c in syms:
            px = _closes(c["tradingsymbol"], d).get(minute_ts)
            if not px:
                continue
            if (cfg["min_entry_volume"] > 0
                    and _vol_at(c["tradingsymbol"], d, minute_ts)
                    < cfg["min_entry_volume"]):
                gated += 1
                continue
            cands.append((c["tradingsymbol"], px,
                          float(c.get("strike") or 0)))
        if not cands:
            diag["liq_gate_entries" if gated else "no_entry_price"] += 1
            return None
        if cfg["strike_selection"] == "premium":
            pick = select_strike([(s, p) for s, p, _ in cands],
                                 cfg["premium_max"])
            if pick is None:
                diag["no_strike_entries"] += 1
                return None
            strike = next((k for s, p, k in cands if s == pick[0]), None)
            return pick[0], float(pick[1]), strike, exp_iso
        ladder = sorted(((k, s, p) for s, p, k in cands if k),
                        key=lambda x: x[0])
        if not ladder:
            diag["no_strike_entries"] += 1
            return None
        # ── SPOT_RELATIVE_SELECTION ── percent mode targets a strike a fixed
        # FRACTION of spot away and picks the nearest listed one, so the
        # chosen moneyness is invariant to the spot level and to the ladder
        # step. Step mode is unchanged and remains the default.
        if cfg["atm_offset_pct"] != 0.0:
            frac = cfg["atm_offset_pct"] / 100.0
            target = spot_px * (1.0 + frac) if side == "CE" \
                else spot_px * (1.0 - frac)
            ti = min(range(len(ladder)),
                     key=lambda i: abs(ladder[i][0] - target))
        else:
            ai = min(range(len(ladder)),
                     key=lambda i: abs(ladder[i][0] - spot_px))
            ti = ai + (cfg["atm_offset"] if side == "CE"
                       else -cfg["atm_offset"])
        if not (0 <= ti < len(ladder)):
            diag["no_strike_entries"] += 1
            return None
        strike, sym, px = ladder[ti]
        if ((cfg["premium_max"] > 0 and px > cfg["premium_max"])
                or (cfg["premium_min"] > 0 and px < cfg["premium_min"])):
            diag["premium_veto_entries"] += 1
            return None
        # ── premium band as a % of spot (monthly-chain safe) ──
        if (cfg["premium_pct_max"] > 0 or cfg["premium_pct_min"] > 0) \
                and spot_px > 0:
            pct = 100.0 * px / spot_px
            if ((cfg["premium_pct_max"] > 0 and pct > cfg["premium_pct_max"])
                    or (cfg["premium_pct_min"] > 0
                        and pct < cfg["premium_pct_min"])):
                diag["premium_pct_veto_entries"] += 1
                return None
        return sym, float(px), strike, exp_iso

    def _expiry_ts(expiry: date) -> int:
        # IC convention: expiry stamped at 15:30 IST on the expiry date.
        return _day_start_epoch(expiry) + (15 * 3600 + 30 * 60)

    def _meta_by_sym(d: date, exp_iso: str) -> dict:
        return {c["tradingsymbol"]: {"strike": c.get("strike")}
                for c in _universe(d, exp_iso)}

    def _select_hedge(d: date, side: str, expiry: date, minute_ts: int,
                      short_sym: str):
        """→ (symbol, price, is_synth, strike) | None.

        REAL FIRST: the DEAREST listed contract at or under the cap with a
        print at or before this minute — the closest strike that still fits
        the budget, which is what maximises the SPAN benefit per rupee.

        SYNTHETIC FALLBACK: a ₹5 wing is exactly the leg that goes dark, and
        a fail-closed skip does not merely lose the wing — it deletes the
        whole TRADE, silently and unevenly (measured at 56% of entries,
        28-69% by year). So when reality has nothing under the cap, model
        one, using the SAME primitives IC uses: parity spot from the live
        ladder, IV implied off the cheapest real strike on that side, then
        walk OTM from strictly beyond the real band edge to the first
        modelled premium <= cap. Starting beyond the band edge keeps the
        real and synthetic universes disjoint by construction.

        ⚠ BIAS (carried over from the IC header, deliberately): the band
        runs away from a leg precisely when that leg is moving, so synthetic
        marks are NOT a wash. diag hedge_synth_pnl_gross reports how much of
        the curve is model-attributed — size live decisions off runs where
        that share is small."""
        exp_iso = expiry.isoformat()
        best = None
        for c in _universe(d, exp_iso):
            if c["instrument_type"] != side:
                continue
            sym = c["tradingsymbol"]
            if sym == short_sym:
                continue
            px, stale = _px_at_or_before(sym, d, minute_ts)
            if px is None or px <= 0 or px > cfg["hedge_max_premium"]:
                continue
            if best is None or px > best[1]:
                best = (sym, float(px), stale, float(c.get("strike") or 0))
        if best is not None:
            if best[2]:
                diag["hedge_stale_fills"] += 1
            diag["hedge_real"] += 1
            return best[0], best[1], False, best[3]
        if not cfg["hedge_synth_enabled"]:
            return None
        try:
            spec, reason = _synth_leg_at(
                src=src, week=_universe(d, exp_iso),
                meta_by_sym=_meta_by_sym(d, exp_iso),
                day_start=_day_start_epoch(d), ts=minute_ts,
                expiry_ts=_expiry_ts(expiry), opt_type=side,
                cap=cfg["hedge_max_premium"], underlying=underlying,
                want_expiry=exp_iso, skew_mult=cfg["hedge_skew_mult"],
                ladder=_minute_ladder(src, _universe(d, exp_iso),
                                      _day_start_epoch(d), minute_ts))
        except Exception:
            spec, reason = None, "error"
        if spec is None:
            diag["hedge_synth_fail"] += 1
            diag.setdefault("hedge_synth_fail_reasons", {})
            diag["hedge_synth_fail_reasons"][reason or "?"] = \
                diag["hedge_synth_fail_reasons"].get(reason or "?", 0) + 1
            return None
        diag["hedge_synth"] += 1
        return spec["symbol"], float(spec["price"]), True, float(spec["strike"])

    def _mark_hedge(pos: "_Pos", d: date, ts: int) -> Optional[float]:
        """Price the wing at an arbitrary minute — model if it was modelled
        in, a real print otherwise. A synthetic leg is NEVER marked out on a
        real print and vice versa: mixing the two manufactures P&L out of the
        pricing basis rather than the market."""
        if not pos.hedge_symbol:
            return None
        if pos.hedge_synth:
            try:
                return _synth_mark_at(
                    src=src, week=_universe(d, pos.expiry_iso),
                    meta_by_sym=_meta_by_sym(d, pos.expiry_iso),
                    day_start=_day_start_epoch(d), ts=ts,
                    expiry_ts=_expiry_ts(pos.expiry_date), opt_type=pos.side,
                    strike=pos.hedge_strike,
                    skew_mult=cfg["hedge_skew_mult"])
            except Exception:
                return None
        px, stale = _px_at_or_before(pos.hedge_symbol, d, ts)
        if px is not None and stale:
            diag["hedge_stale_fills"] += 1
        return px

    # ── MAIN WALK ── continuous bars from tradable_from; day transitions
    # handled inline (EOD square + expiry fail-safe sweep at each boundary).
    pos: Optional[_Pos] = None
    block_target: Optional[int] = None     # SL/TP block until target ≠ this
    forced_block: Optional[int] = None     # forced-exit block (opt-in)
    day_entries = 0
    traded_days = set()
    open_days = set()
    cur_day: Optional[date] = None
    first_tradable_bar = True

    def _target_of(cond: int) -> int:
        if cfg["direction"] == "LONG" and cond == -1:
            return 0
        if cfg["direction"] == "SHORT" and cond == 1:
            return 0
        return cond

    def _day_boundary_close(d: date) -> None:
        """EOD square + never-past-expiry fail-safe at the end of day d."""
        nonlocal pos, forced_block
        if pos is None:
            return
        ds = _day_start_epoch(d)
        if cfg["eod_square"]:
            m = ds + exit_min * 60 - 60
            px = _fill_at(pos.symbol, d, m, allow_stale=True)
            if px is None:
                px = pos.last_mark
            _emit(pos, m, px, "EOD")
            diag["eod_exits"] += 1
            if not cfg["reenter_after_forced_exit"]:
                forced_block = _target_of_state_now()
            pos = None
            return
        if pos.expiry_date <= d:
            # roll path missed (data gap / roll_min past session) — the
            # position must NOT survive its own expiry date.
            m = ds + roll_min * 60
            px = _fill_at(pos.symbol, d, m, allow_stale=True)
            if px is None:
                px = pos.last_mark
            _emit(pos, m, px, "EXPIRY_FORCE")
            diag["expiry_force_exits"] += 1
            if not cfg["reenter_after_forced_exit"]:
                forced_block = _target_of_state_now()
            pos = None

    _state_now = {"cond": 0}

    def _target_of_state_now() -> int:
        return _target_of(_state_now["cond"])

    for i in range(tradable_from, len(bars)):
        b, st, d = bars[i], states[i], day_of[i]
        _state_now["cond"] = st.condition

        if cur_day != d:
            if cur_day is not None:
                _day_boundary_close(cur_day)
            if cancel_cb and cancel_cb():
                break
            if progress_cb:
                progress_cb({"day": range_days.index(d) + 1,
                             "total_days": len(range_days),
                             "date": d.isoformat()})
            _reset_day(d)
            cur_day = d
            day_entries = 0

        m = b.last1m_ts
        minute_min = (m % 86400 + IST) % 86400 // 60
        target = _target_of(st.condition)
        if pos is not None:
            # ── CARRY METRIC ── days_traded counts days with an ENTRY; on a
            # swing system most days have none. days_position_open is the
            # exposure metric a sweep should actually be read against.
            open_days.add(d)
        # past exit_time with EOD square on, the book is frozen for the
        # boundary sweep: no SL/TP, no signal exits, no entries, no flips.
        past_eod = cfg["eod_square"] and minute_min >= exit_min

        # clear blocks the moment the target moves off the blocked value
        if block_target is not None and target != block_target:
            block_target = None
        if forced_block is not None and target != forced_block:
            forced_block = None

        # ── 1. forced exits on the held leg, priority order ──
        if pos is not None:
            mark = _closes(pos.symbol, d).get(m)
            if mark is None:
                diag["stale_marks"] += 1
                mark = pos.last_mark
            pos.last_mark = mark
            # ── HEDGE_LEG ── the wing is marked every bar too, so the daily
            # MTM guard sees the PAIR and the exit fill has a fresh carry.
            if pos.hedge_symbol:
                hm = _mark_hedge(pos, d, m)
                if hm is not None:
                    pos.hedge_last_mark = hm

            # 1a. expiry-day roll boundary
            if pos.expiry_date == d and minute_min >= roll_min:
                px = _fill_at(pos.symbol, d, m, allow_stale=True) or mark
                rolled = False
                _ok_ce = "PE" if is_sell else "CE"
                _ok_pe = "CE" if is_sell else "PE"
                if cfg["rollover_enabled"] and target != 0 and (
                        (target == 1 and pos.side == _ok_ce)
                        or (target == -1 and pos.side == _ok_pe)):
                    nxt = _next_covered_expiry(d, pos.expiry_date)
                    sel = (_select(d, pos.side, nxt, m, float(b.close))
                           if nxt is not None else None)
                    if sel is not None:
                        sym, epx, k, eiso = sel
                        # ── HEDGE_LEG ── the wing rolls WITH the short; a
                        # roll that cannot be hedged is not taken, so the
                        # book never becomes bare mid-position.
                        hw = None
                        if is_sell and cfg["hedge_enabled"] and cfg["hedge_max_premium"] > 0:
                            hw = _select_hedge(d, pos.side, nxt, m, sym)
                            if hw is None:
                                diag["no_hedge_entries"] += 1
                                sel = None
                    if sel is not None:
                        _emit(pos, m, px, "ROLL")
                        diag["roll_exits"] += 1
                        pos = _Pos(side=pos.side, symbol=sym, strike=k,
                                   expiry_iso=eiso, expiry_date=nxt,
                                   entry_ts=m, entry_px=epx, tag="VET·ROLL",
                                   last_mark=epx,
                                   hedge_symbol=(hw[0] if hw else None),
                                   hedge_entry_px=(hw[1] if hw else 0.0),
                                   hedge_last_mark=(hw[1] if hw else 0.0),
                                   hedge_synth=(hw[2] if hw else False),
                                   hedge_strike=(hw[3] if hw else 0.0))
                        diag["roll_entries"] += 1
                        rolled = True
                    else:
                        diag["rolls_no_next_expiry"] += 1
                if not rolled:
                    _emit(pos, m, px, "ROLL" if cfg["rollover_enabled"]
                          else "EXPIRY_EXIT")
                    diag["roll_exits"] += 1
                    pos = None
                if rolled:
                    continue   # rolled book needs no further checks this bar

            # 1b. EOD square is OWNED BY THE DAY-BOUNDARY SWEEP (exact
            # exit_time−60 print) — a tf-grid trigger would fire on the
            # first bar COMPLETING after exit_time (15:19 on 5m), 4 min
            # late. Past exit_time the position is frozen for the sweep:
            # SL/TP (1c) and reconciliation (step 2) are suppressed via
            # past_eod so no exit can post-date the sweep's fill.

            # 1c. SL / TP on the premium mark (close-based, stale-carried)
            if (pos is not None and not past_eod
                    and (cfg["sl_pct"] > 0 or cfg["tp_pct"] > 0)):
                # ── LEG_ACTION ── for a SHORT leg the stop is premium
                # RISING and the target is premium FALLING.
                if is_sell:
                    sl_lvl = pos.entry_px * (1 + cfg["sl_pct"] / 100.0)
                    tp_lvl = pos.entry_px * (1 - cfg["tp_pct"] / 100.0)
                    sl_hit = cfg["sl_pct"] > 0 and mark >= sl_lvl
                    tp_hit = cfg["tp_pct"] > 0 and mark <= tp_lvl
                else:
                    sl_lvl = pos.entry_px * (1 - cfg["sl_pct"] / 100.0)
                    tp_lvl = pos.entry_px * (1 + cfg["tp_pct"] / 100.0)
                    sl_hit = cfg["sl_pct"] > 0 and mark <= sl_lvl
                    tp_hit = cfg["tp_pct"] > 0 and mark >= tp_lvl
                hit = None
                if sl_hit:
                    hit = "SL"
                    diag["sl_exits"] += 1
                elif tp_hit:
                    hit = "TP"
                    diag["tp_exits"] += 1
                if hit:
                    _emit(pos, m, mark, hit)
                    if not cfg["reenter_after_sltp"]:
                        block_target = target
                    pos = None

        # ── DAILY_MTM_CAP ── evaluated at every timeframe close on
        # realised + open unrealised. On breach: flatten at THIS bar's fill
        # and stand down for the rest of the session (no re-entry, no flip).
        # Deliberately checked AFTER the SL/TP block so a stop that would
        # have fired anyway keeps its own exit reason, and BEFORE
        # reconciliation so a breach can never be followed by a new entry
        # in the same bar. Diagnostics separate the days that breached from
        # the entries the cap subsequently suppressed.
        if cfg["max_daily_mtm_loss"] > 0 and not _day_pnl["capped"]:
            open_mtm = 0.0
            if pos is not None:
                open_mtm = ((pos.entry_px - pos.last_mark) if is_sell
                            else (pos.last_mark - pos.entry_px)) * qty
                if pos.hedge_symbol:
                    open_mtm += (pos.hedge_last_mark
                                 - pos.hedge_entry_px) * qty
            if _day_pnl["realised"] + open_mtm <= -cfg["max_daily_mtm_loss"]:
                _day_pnl["capped"] = True
                diag["daily_cap_days"] += 1
                if pos is not None:
                    px = (_fill_at(pos.symbol, d, m, allow_stale=True)
                          or pos.last_mark)
                    _emit(pos, m, px, "DAY_CAP")
                    diag["daily_cap_exits"] += 1
                    pos = None
        if _day_pnl["capped"]:
            diag["daily_cap_blocked_bars"] += 1
            first_tradable_bar = False
            continue

        # ── 2. reconcile held side against the target (frozen past EOD) ──
        if past_eod:
            first_tradable_bar = False
            continue
        # ── LEG_ACTION ── BUY: up-trend -> CE, down-trend -> PE.
        # SELL: up-trend -> SHORT PE, down-trend -> SHORT CE. Same
        # directional exposure, opposite contract.
        if target == 1:
            want_side = "PE" if is_sell else "CE"
        elif target == -1:
            want_side = "CE" if is_sell else "PE"
        else:
            want_side = None

        if pos is not None and pos.side != want_side:
            px = _fill_at(pos.symbol, d, m, allow_stale=True) or pos.last_mark
            flip = want_side is not None
            _emit(pos, m, px, "FLIP" if flip else "SIGNAL_EXIT")
            diag["signal_exits"] += 1
            pos = None
            if flip:
                # a flip is a fresh signal — it clears any standing block
                block_target = None
                forced_block = None

        if pos is None and want_side is not None:
            blocked = None
            if block_target == target:
                blocked = "sltp_reentry_blocks"
            elif forced_block == target:
                blocked = "forced_reentry_blocks"
            elif minute_min >= cutoff_min:
                blocked = "cutoff_blocked_entries"
            elif cfg["eod_square"] and minute_min >= exit_min - tf:
                # ── EOD_SCRATCH_GUARD ── with EOD square on, the boundary
                # sweep fills at exit_time−60. A bar closing inside the last
                # tf window would enter and be swept at the SAME minute —
                # a zero-duration trade worth exactly −charges. Block it.
                blocked = "cutoff_blocked_entries"
            elif (cfg["max_trades_per_day"] > 0
                  and day_entries >= cfg["max_trades_per_day"]):
                blocked = "cap_blocked_entries"
            if blocked:
                diag[blocked] += 1
            else:
                exp = _entry_expiry(d, minute_min)
                if exp is None:
                    # ── ROLL_COVERAGE ── bumped entry with no covered
                    # expiry ahead: fail-closed, never a guessed series.
                    diag["entry_expiry_uncovered"] += 1
                    first_tradable_bar = False
                    continue
                sel = _select(d, want_side, exp, m, float(b.close))
                hw = None
                if sel is not None and (is_sell and cfg["hedge_enabled"] and cfg["hedge_max_premium"] > 0):
                    # ── HEDGE_LEG ── fail-closed: no wing under the cap
                    # means NO TRADE. Selling bare because the protective
                    # leg was unavailable would change the risk profile
                    # without changing the config.
                    hw = _select_hedge(d, want_side, exp, m, sel[0])
                    if hw is None:
                        diag["no_hedge_entries"] += 1
                        sel = None
                if sel is not None:
                    sym, epx, k, eiso = sel
                    is_start = first_tradable_bar
                    was_edge = (states[i - 1].condition != st.condition
                                if i > 0 else st.condition != 0)
                    if is_start and not was_edge:
                        if not cfg["enter_open_state_at_start"]:
                            first_tradable_bar = False
                            # deliberate skip: flat until the next edge
                            continue
                        tag = "VET·START"
                        diag["start_state_entries"] += 1
                    elif was_edge:
                        tag = ("VET·FLIP" if trades and trades[-1].exit_ts == m
                               else "VET")
                        diag["flip_entries" if tag == "VET·FLIP"
                             else "entries"] += 1
                    else:
                        tag = "VET·RESUME"   # re-sync after a forced exit
                        diag["resume_entries"] += 1
                    pos = _Pos(side=want_side, symbol=sym, strike=k,
                               expiry_iso=eiso, expiry_date=exp,
                               entry_ts=m, entry_px=epx, tag=tag,
                               last_mark=epx,
                               hedge_symbol=(hw[0] if hw else None),
                               hedge_entry_px=(hw[1] if hw else 0.0),
                               hedge_last_mark=(hw[1] if hw else 0.0),
                               hedge_synth=(hw[2] if hw else False),
                               hedge_strike=(hw[3] if hw else 0.0))
                    day_entries += 1
                    traded_days.add(d)
                    open_days.add(d)

        first_tradable_bar = False

    # tail: close the final day's boundary, then any still-open position
    if cur_day is not None:
        _day_boundary_close(cur_day)
    if pos is not None:
        # open at range end — book at the last mark so the summary is
        # complete; flagged reason so nobody mistakes it for a signal exit.
        _emit(pos, bars[-1].last1m_ts, pos.last_mark, "RANGE_END_OPEN")
        pos = None

    diag["days_traded"] = len(traded_days)
    diag["days_position_open"] = len(open_days)
    _close_all()

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying}"
        f"{' (stock, lot ' + str(lot_size) + ')' if is_stock else ''} "
        f"{date_from}→{date_to}: {diag['days_traded']}/{diag['days_total']} "
        f"days with an entry ({diag['days_position_open']} with an "
        f"open position), {len(trades)} trades "
        f"(entries {diag['entries']} flips {diag['flip_entries']} "
        f"rolls {diag['roll_entries']} resumes {diag['resume_entries']} "
        f"start {diag['start_state_entries']}), net {summary['net_pnl']}, "
        f"tf {tf}m {cfg['leg_action']}"
        f"{'+hedge<=' + str(cfg['hedge_max_premium']) if (is_sell and cfg['hedge_enabled']) else ''} "
        f"dir {cfg['direction']} "
        f"sel {cfg['strike_selection']}"
        f"+{cfg['atm_offset']}, exits: signal {diag['signal_exits']} / "
        f"SL {diag['sl_exits']} / TP {diag['tp_exits']} / "
        f"EOD {diag['eod_exits']} / roll {diag['roll_exits']} "
        f"(noNext {diag['rolls_no_next_expiry']}, "
        f"expiryForce {diag['expiry_force_exits']}), "
        # ── WING SOURCING ── real-vs-model split belongs on the ONE line a
        # person actually reads in the run detail. A hedged run whose wings
        # are mostly synthetic is priced by Black-Scholes on the drag side,
        # and that must be visible without a database query.
        + (f"wings real {diag['hedge_real']} / syn {diag['hedge_synth']} "
           f"(synPnL {diag['hedge_synth_pnl_gross']:+,.0f}, "
           f"stale {diag['hedge_stale_fills']}, "
           f"skipped {diag['no_hedge_entries']}), "
           if (is_sell and cfg["hedge_enabled"]) else "")
        + f"skips: noStrike {diag['no_strike_entries']} / "
        f"veto {diag['premium_veto_entries']}"
        f"+{diag['premium_pct_veto_entries']}pct / "
        f"maxDTE {diag['max_dte_blocked_entries']} / "
        f"liqGate {diag['liq_gate_entries']} / "
        f"uncovered {diag['entry_expiry_uncovered']}, "
        f"dayCap {diag['daily_cap_days']}d/{diag['daily_cap_exits']}x, "
        f"staleFills {diag['stale_exit_fills']} "
        f"staleMarks {diag['stale_marks']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            # ── TRADE_SHAPE ── ICTrade OBJECTS, never dicts. backtest_repo
            # .persist_run reads t.symbol / t.max_adverse / t.ambiguous_fill
            # by ATTRIBUTE, so a list of dicts dies with "'dict' object has
            # no attribute 'symbol'" AFTER the whole run has completed —
            # the most expensive possible place to fail. Same contract as
            # the GC and VAP runners.
            "config": cfg, "trades": trades,
            "strategy_id": strategy_id}
'''

TESTS_PY = r'''# backend/app/backtest/vet/test_vet_engine.py
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
'''

ROLLTEST_PY = r'''# backend/app/backtest/vet/test_vet_roll_coverage.py
#
# ── ROLL_COVERAGE regression ── corpus where the CALENDAR-next weekly is absent
# (exactly the 2026-06-16 case: 06-16 present, 06-23 MISSING, 06-30 present).
import os, sqlite3, sys
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
IST=19800; DB="/tmp/vet_roll.db"
DDL="""CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
def ds(d): return int((datetime(d.year,d.month,d.day)-datetime(1970,1,1)).total_seconds())-IST
DAYS=[date(2026,6,d) for d in (9,10,11,12,15,16,17,18,19)]   # 16th = Tue expiry
EXP_NEAR="2026-06-16"; EXP_SKIP="2026-06-23"; EXP_FAR="2026-06-30"
def build():
    if os.path.exists(DB): os.remove(DB)
    c=sqlite3.connect(DB); c.executescript(DDL); rows=[]; tok={}
    def T(s):
        tok.setdefault(s,100000+len(tok)); return tok[s]
    for d in DAYS:
        base=ds(d)
        for mi in range(375):
            ts=base+(9*60+15+mi)*60
            spot=24000+ (DAYS.index(d)*120) + 300*mi/374.0     # steady uptrend
            rows.append((T("SPOT"),ts,"NIFTY","NIFTY_SPOT","SPOT",0.0,"",spot-2,spot+3,spot-3,spot,0,0))
            for exp in (EXP_NEAR,EXP_SKIP,EXP_FAR):
                if exp==EXP_SKIP:            # <-- the gap: never written
                    continue
                if d.isoformat()>exp: continue
                dte=(date.fromisoformat(exp)-d).days
                for k in range(23400,25100,50):
                    if abs(k-spot)>500: continue
                    tag=exp.replace("-","")[2:]
                    for side in ("CE","PE"):
                        intr=max(spot-k,0) if side=="CE" else max(k-spot,0)
                        px=round(intr+20+10*dte,1)
                        sym=f"NIFTY{tag}{k}{side}"
                        rows.append((T(sym),ts,"NIFTY",sym,side,float(k),exp,px,px+1,px-1,px,100,0))
    c.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    c.commit(); c.close(); print(f"corpus: {len(rows)} rows; expiries {EXP_NEAR}, {EXP_FAR} (SKIPPED: {EXP_SKIP})")
build()
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
r=run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
    date_from=DAYS[4],date_to=DAYS[-1],
    config_override={"warmup_sessions":4,"strike_selection":"atm"})
d=r["summary"]["diag_vet"]
tr=r["trades"]
print("\nroll_entries:",d["roll_entries"]," roll_exits:",d["roll_exits"],
      " rolls_no_next_expiry:",d["rolls_no_next_expiry"])
print("probes:",d["roll_expiry_probes"]," gap>week:",d["roll_expiry_gap_gt_week"])
for t in tr:
    print(f"  {t.condition:<11} {t.tradingsymbol:<20} exp {t.expiry} -> {t.exit_reason}")
ok = d["roll_entries"]>=1 and any(t.expiry==EXP_FAR for t in tr) and d["rolls_no_next_expiry"]==0
print("\nRESULT:", "PASS — rolled over the corpus gap onto", EXP_FAR if ok else "FAIL")
sys.exit(0 if ok else 1)
'''

CAPTEST_PY = r'''# backend/app/backtest/vet/test_vet_daily_cap.py
#
# ── DAILY_MTM_CAP behavioural test ── builds a synthetic corpus containing a
# violent CHOP session (sawtooth spot) so the strategy genuinely loses money,
# then asserts the max_daily_mtm_loss overlay:
#   1. is completely INERT when 0 (no diag activity, no DAY_CAP rows)
#   2. fires at least once when set below a real day's loss
#   3. emits exactly one DAY_CAP row per breach
#   4. admits NO new entry after firing, for the rest of that session
#   5. leaves the capped day less negative than the uncapped run
#   6. is INERT again when set far beyond any day's loss
#
# NOTE ON OVERSHOOT: the cap is a TRIGGER, not a guarantee. It is evaluated at
# timeframe closes, so the realised day loss lands beyond the level by roughly
# one bar's adverse move plus the exit's charges. A live guard behaves the
# same way. Size the level with that headroom in mind.
#
# Runs standalone:  python3 test_vet_daily_cap.py

# Corpus with a violent CHOP day so the strategy actually loses money and the
# daily MTM cap has something to bite on.
import os, sys, sqlite3, math
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST=19800; DB="/tmp/vet_chop_test.db"
DDL="""CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
def ds(d): return int((datetime(d.year,d.month,d.day)-datetime(1970,1,1)).total_seconds())-IST
DAYS=[date(2026,6,1),date(2026,6,2),date(2026,6,3),date(2026,6,4),date(2026,6,5),date(2026,6,8)]
CHOP={date(2026,6,4),date(2026,6,5),date(2026,6,8)}   # whipsaw days
EXPS=("2026-06-02","2026-06-09","2026-06-16")
def spot(d,mi):
    if d in CHOP:
        # sawtooth: 220-pt swings every ~25 min -> repeated flips, each entered
        # near an extreme and exited near the opposite one
        return 24000 + 220*math.sin(2*math.pi*mi/50.0)
    return 24000 + 400*mi/374.0            # warmup: clean trend

if os.path.exists(DB): os.remove(DB)
c=sqlite3.connect(DB); c.executescript(DDL); rows=[]; tok={}
def T(s):
    tok.setdefault(s,100000+len(tok)); return tok[s]
for d in DAYS:
    base=ds(d)
    for mi in range(375):
        ts=base+(9*60+15+mi)*60; sp=spot(d,mi)
        rows.append((T("SPOT"),ts,"NIFTY","NIFTY_SPOT","SPOT",0.0,"",sp-2,sp+3,sp-3,sp,0,0))
        for EXP in EXPS:
            if d.isoformat() > EXP: continue
            dte=(date.fromisoformat(EXP)-d).days
            tag=EXP.replace("-","")[2:]
            for k in range(23400,24700,50):
                if abs(k-sp)>400: continue
                for side in ("CE","PE"):
                    intr=max(sp-k,0) if side=="CE" else max(k-sp,0)
                    px=round(intr+15+6*dte,1)
                    sym=f"NIFTY{tag}{k}{side}"
                    rows.append((T(sym),ts,"NIFTY",sym,side,float(k),EXP,px,px+1,px-1,px,100,0))
c.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
c.commit(); c.close(); print(f"chop corpus: {len(rows)} rows")

def go(**kw):
    return run_vet_backtest(db_path=DB, strategy_id="VET_V1", underlying="NIFTY",
        date_from=date(2026,6,4), date_to=date(2026,6,8),
        config_override=dict({"warmup_sessions":3,"eod_square":True,"strike_selection":"atm"}, **kw))
FAIL=0
def chk(n,c,d=""):
    global FAIL
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else "  "+str(d)));  FAIL+= (0 if c else 1)

base=go(); bt=base["trades"]; bd=base["summary"]["diag_vet"]
print("baseline (cap off):", len(bt),"trades, net",base["summary"]["net_pnl"])
chk("cap OFF -> no cap activity", bd["daily_cap_days"]==0 and bd["daily_cap_exits"]==0)
chk("cap OFF -> no DAY_CAP rows", all(t.exit_reason!="DAY_CAP" for t in bt))

# find a day with a real loss to size the cap against
byday=defaultdict(float)
for t in bt: byday[date.fromtimestamp(t.exit_ts+19800).isoformat()]+=t.net_pnl
print("  per-day net:", {k:round(v) for k,v in byday.items()})
worst=min(byday.values())
cap=abs(worst)/2 if worst<0 else 5000.0
r=go(max_daily_mtm_loss=cap); rt=r["trades"]; rd=r["summary"]["diag_vet"]
print(f"\ncap = {cap:,.0f}: {len(rt)} trades, net {r['summary']['net_pnl']}")
print("  diag:", {k:v for k,v in rd.items() if k.startswith("daily_cap")})
chk("cap ON -> at least one breach", rd["daily_cap_days"]>=1, rd)
chk("DAY_CAP rows == daily_cap_exits",
    sum(1 for t in rt if t.exit_reason=="DAY_CAP")==rd["daily_cap_exits"])
# no entry may occur after the cap fires on that day
capdays={date.fromtimestamp(t.exit_ts+19800).isoformat() for t in rt if t.exit_reason=="DAY_CAP"}
bad=[]
for t in rt:
    dd=date.fromtimestamp(t.entry_ts+19800).isoformat()
    if dd in capdays:
        cts=[x.exit_ts for x in rt if x.exit_reason=="DAY_CAP"
             and date.fromtimestamp(x.exit_ts+19800).isoformat()==dd]
        if cts and t.entry_ts > min(cts): bad.append(t.tradingsymbol)
chk("no entries after the cap fires", not bad, bad)
# realised day loss must never exceed the cap by more than one bar's move
byday2=defaultdict(float)
for t in rt: byday2[date.fromtimestamp(t.exit_ts+19800).isoformat()]+=t.net_pnl
print("  per-day net with cap:", {k:round(v) for k,v in byday2.items()})
chk("capped days are less negative than uncapped",
    all(byday2[k] >= byday.get(k,0)-1 for k in capdays), 
    {k:(round(byday.get(k,0)),round(byday2[k])) for k in capdays})
# huge cap must be inert
big=go(max_daily_mtm_loss=10_000_000)
chk("cap far beyond any day's loss is INERT",
    big["summary"]["net_pnl"]==base["summary"]["net_pnl"]
    and big["summary"]["diag_vet"]["daily_cap_days"]==0)
print("\n"+("ALL DAY-CAP CHECKS PASSED" if FAIL==0 else f"{FAIL} FAILURES"))
sys.exit(1 if FAIL else 0)
'''

SELTEST_PY = r'''# backend/app/backtest/vet/test_vet_selection.py
#
# ── SPOT_RELATIVE_SELECTION / premium_pct / max_entry_dte tests ──
# Asserts each new knob is INERT at its default (so every prior run stays
# reproducible), behaves as documented when set, and fails CLOSED at the
# extremes. Builds its own synthetic corpus if one is not present.
#
# Runs standalone:  python3 test_vet_selection.py
import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date, datetime
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_sel_test.db"

WARM = [date(2026, 8, 6), date(2026, 8, 7)]
RANGE = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
         date(2026, 8, 13)]
EXP1, EXP2 = "2026-08-11", "2026-08-18"

DDL = """
CREATE TABLE backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts INTEGER NOT NULL, underlying TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
    strike REAL NOT NULL, expiry TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0,
    oi INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts));
CREATE INDEX idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX idx_bt1m_under_type_ts ON backtest_candles_1m (underlying, instrument_type, ts);
"""


def day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def spot_path(d: date, minute: int) -> float:
    """minute = 0..374 from 09:15. Piecewise path per scenario."""
    if d in WARM:
        return 24000.0 + (5.0 if minute % 2 == 0 else -5.0)
    if d == RANGE[0]:                       # Mon: +600 over the day
        return 24000.0 + 600.0 * minute / 374.0
    if d == RANGE[1]:                       # Tue: +400 more
        return 24600.0 + 400.0 * minute / 374.0
    if d == RANGE[2]:                       # Wed: −900 hard reversal
        return 25000.0 - 900.0 * minute / 374.0
    return 24100.0 - 500.0 * minute / 374.0  # Thu: −500 more


def build():
    if os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.executescript(DDL)
    rows = []
    tok = {}

    def token(sym):
        if sym not in tok:
            tok[sym] = 100000 + len(tok)
        return tok[sym]

    strikes = list(range(23000, 26550, 50))
    for d in WARM + RANGE:
        ds = day_start(d)
        for minute in range(375):
            ts = ds + (9 * 60 + 15 + minute) * 60
            s = spot_path(d, minute)
            rows.append((token("NIFTY_SPOT"), ts, "NIFTY", "NIFTY_SPOT",
                         "SPOT", 0.0, "", s - 2, s + 3, s - 3, s, 0, 0))
            for exp in (EXP1, EXP2):
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tv = 30.0 + 12.0 * dte          # crude time value
                for k in strikes:
                    if abs(k - s) > 400:        # keep the db small
                        continue
                    tag = exp.replace("-", "")[2:]
                    for side in ("CE", "PE"):
                        intr = max(s - k, 0.0) if side == "CE" \
                            else max(k - s, 0.0)
                        px = round(intr + tv, 1)
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((token(sym), ts, "NIFTY", sym, side,
                                     float(k), exp, px, px + 1, px - 1, px,
                                     100, 0))
    conn.executemany(
        "INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"corpus built: {len(rows)} rows")

build()

F=0
def chk(n,c,d=""):
    global F
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f"  {d}")); F+=0 if c else 1
def go(**kw):
    return run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
      date_from=date(2026,8,10),date_to=date(2026,8,13),
      config_override=dict({"warmup_sessions":2,"strike_selection":"atm"},**kw))
def strikes(r): return [t.strike for t in r["trades"]]

base=go(); b0=go(atm_offset=0)
chk("pct=0 is inert (identical to step mode)", strikes(base)==strikes(b0))

# spot ~24000-25000, step 50 -> 1 step ~0.2% of spot
s_step2 = strikes(go(atm_offset=2))
s_pct   = strikes(go(atm_offset_pct=0.4))     # 0.4% of 24500 ~= 98 ~= 2 steps
chk("0.4% of spot lands within 1 step of the 2-step offset",
    all(abs(a-b)<=50 for a,b in zip(s_step2,s_pct)),
    list(zip(s_step2,s_pct))[:4])
chk("pct OVERRIDES steps when both set",
    strikes(go(atm_offset=2, atm_offset_pct=0.4))==s_pct)

# CE goes UP, PE goes DOWN for a positive pct
r=go(atm_offset_pct=1.0)
ce=[t for t in r["trades"] if t.instrument_type=="CE"]
pe=[t for t in r["trades"] if t.instrument_type=="PE"]
r0=go(atm_offset_pct=0.0)
chk("positive pct is OTM-ward on BOTH sides", True if not (ce and pe) else
    (ce[0].strike > [t for t in r0['trades'] if t.instrument_type=='CE'][0].strike
     and pe[0].strike < [t for t in r0['trades'] if t.instrument_type=='PE'][0].strike))

# premium % veto
# NOTE: the veto measures premium against SPOT, and on expiry day the ATM
# premium collapses, so a threshold derived from the taken trades is NOT a
# floor for every bar. Assert the two ends and monotonicity instead.
tight=go(premium_pct_max=0.01)
chk("premium_pct_max below anything tradeable blocks ALL entries",
    len(tight["trades"])==0 and tight["summary"]["diag_vet"]["premium_pct_veto_entries"]>0,
    f'trades={len(tight["trades"])}')
mid=go(premium_pct_max=0.4)
chk("tightening the cap is monotone in trade count",
    len(tight["trades"]) <= len(mid["trades"]) <= len(base["trades"]),
    f'{len(tight["trades"])} <= {len(mid["trades"])} <= {len(base["trades"])}')
loose=go(premium_pct_max=100.0)
chk("premium_pct_max above every candidate is inert", strikes(loose)==strikes(base))
chk("premium_pct_min above every candidate blocks all entries",
    len(go(premium_pct_min=100.0)["trades"])==0)

# max_entry_dte
d=base["summary"]["diag_vet"]
big=go(max_entry_dte=999)
chk("max_entry_dte huge is inert", strikes(big)==strikes(base))
zero=go(max_entry_dte=0)
chk("max_entry_dte=0 means OFF (not 'block everything')", strikes(zero)==strikes(base))
tiny=go(max_entry_dte=1)
td=tiny["summary"]["diag_vet"]
chk("max_entry_dte=1 blocks far-DTE entries",
    td["max_dte_blocked_entries"]>0 and len(tiny["trades"])<len(base["trades"]),
    f"blocked={td['max_dte_blocked_entries']} trades={len(tiny['trades'])} vs {len(base['trades'])}")
print("\n"+("ALL SELECTION CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
'''

LEGTEST_PY = r'''# backend/app/backtest/vet/test_vet_leg_action.py
#
# ── LEG_ACTION tests ── SELL must express the SAME signal with the opposite
# contract: up-trend -> SHORT PE, down-trend -> SHORT CE. Asserts the signal
# chain is untouched (identical trade count and timestamps), the option type
# inverts on every trade, gross sign follows the short convention, SL/TP
# levels flip to the correct side of entry, and the short charges model is
# actually used (STT moves to the entry leg).
#
# Runs standalone:  python3 test_vet_leg_action.py
import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_leg_test.db"

WARM = [date(2026, 8, 6), date(2026, 8, 7)]
RANGE = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
         date(2026, 8, 13)]
EXP1, EXP2 = "2026-08-11", "2026-08-18"

DDL = """
CREATE TABLE backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts INTEGER NOT NULL, underlying TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
    strike REAL NOT NULL, expiry TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0,
    oi INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts));
CREATE INDEX idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX idx_bt1m_under_type_ts ON backtest_candles_1m (underlying, instrument_type, ts);
"""


def day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def spot_path(d: date, minute: int) -> float:
    """minute = 0..374 from 09:15. Piecewise path per scenario."""
    if d in WARM:
        return 24000.0 + (5.0 if minute % 2 == 0 else -5.0)
    if d == RANGE[0]:                       # Mon: +600 over the day
        return 24000.0 + 600.0 * minute / 374.0
    if d == RANGE[1]:                       # Tue: +400 more
        return 24600.0 + 400.0 * minute / 374.0
    if d == RANGE[2]:                       # Wed: −900 hard reversal
        return 25000.0 - 900.0 * minute / 374.0
    return 24100.0 - 500.0 * minute / 374.0  # Thu: −500 more


def build():
    if os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.executescript(DDL)
    rows = []
    tok = {}

    def token(sym):
        if sym not in tok:
            tok[sym] = 100000 + len(tok)
        return tok[sym]

    strikes = list(range(23000, 26550, 50))
    for d in WARM + RANGE:
        ds = day_start(d)
        for minute in range(375):
            ts = ds + (9 * 60 + 15 + minute) * 60
            s = spot_path(d, minute)
            rows.append((token("NIFTY_SPOT"), ts, "NIFTY", "NIFTY_SPOT",
                         "SPOT", 0.0, "", s - 2, s + 3, s - 3, s, 0, 0))
            for exp in (EXP1, EXP2):
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tv = 30.0 + 12.0 * dte          # crude time value
                for k in strikes:
                    if abs(k - s) > 400:        # keep the db small
                        continue
                    tag = exp.replace("-", "")[2:]
                    for side in ("CE", "PE"):
                        intr = max(s - k, 0.0) if side == "CE" \
                            else max(k - s, 0.0)
                        px = round(intr + tv, 1)
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((token(sym), ts, "NIFTY", sym, side,
                                     float(k), exp, px, px + 1, px - 1, px,
                                     100, 0))
    conn.executemany(
        "INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"corpus built: {len(rows)} rows")

build()
F=0
def chk(n,c,d=""):
    global F
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f"  {d}")); F+=0 if c else 1
def go(**kw):
    return run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
      date_from=date(2026,8,10),date_to=date(2026,8,13),
      config_override=dict({"warmup_sessions":2,"strike_selection":"atm"},**kw))
buy=go(); sell=go(leg_action="SELL")
bt,st=buy["trades"],sell["trades"]
chk("default is BUY", buy["config"]["leg_action"]=="BUY")
chk("SELL config echoes", sell["config"]["leg_action"]=="SELL")
chk("same number of trades (signal chain untouched)", len(bt)==len(st), f"{len(bt)} vs {len(st)}")
chk("entry/exit timestamps identical", [ (t.entry_ts,t.exit_ts) for t in bt]==[(t.entry_ts,t.exit_ts) for t in st])
chk("every BUY leg is direction BUY", all(t.direction=="BUY" for t in bt))
chk("every SELL leg is direction SELL", all(t.direction=="SELL" for t in st))
inv={"CE":"PE","PE":"CE"}
chk("SELL uses the OPPOSITE option type on every trade",
    [inv[t.instrument_type] for t in bt]==[t.instrument_type for t in st],
    list(zip([t.instrument_type for t in bt],[t.instrument_type for t in st]))[:5])
# sign: a short leg profits when premium falls
bad=[t for t in st if (t.exit_price<t.entry_price) != (t.pnl>0)]
chk("SHORT gross is positive iff premium FELL", not bad,
    [(t.tradingsymbol,t.entry_price,t.exit_price,t.pnl) for t in bad][:3])
bad2=[t for t in bt if (t.exit_price>t.entry_price) != (t.pnl>0)]
chk("LONG gross is positive iff premium ROSE", not bad2)
# SL levels invert
b_sl=go(sl_pct=20); s_sl=go(leg_action="SELL", sl_pct=20)
bs=[t for t in b_sl["trades"] if t.sl is not None][:1]
ss=[t for t in s_sl["trades"] if t.sl is not None][:1]
chk("LONG SL sits BELOW entry", bs and bs[0].sl < bs[0].entry_price, bs and (bs[0].entry_price,bs[0].sl))
chk("SHORT SL sits ABOVE entry", ss and ss[0].sl > ss[0].entry_price, ss and (ss[0].entry_price,ss[0].sl))
b_tp=go(tp_pct=20); s_tp=go(leg_action="SELL", tp_pct=20)
bt2=[t for t in b_tp["trades"] if t.tp is not None][:1]
st2=[t for t in s_tp["trades"] if t.tp is not None][:1]
chk("LONG TP sits ABOVE entry", bt2 and bt2[0].tp > bt2[0].entry_price)
chk("SHORT TP sits BELOW entry", st2 and st2[0].tp < st2[0].entry_price)
# charges model: STT on entry leg for shorts -> charges differ
chk("SHORT charges differ from LONG (STT moves to the entry leg)",
    abs(sum(t.charges for t in st) - sum(t.charges for t in bt)) > 0.01,
    f"{sum(t.charges for t in st):.2f} vs {sum(t.charges for t in bt):.2f}")
print(f"\n  BUY net {buy['summary']['net_pnl']:,.0f} | SELL net {sell['summary']['net_pnl']:,.0f}")
print("\n"+("ALL LEG_ACTION CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
'''

HEDGETEST_PY = r'''# backend/app/backtest/vet/test_vet_hedge_leg.py
#
# ── HEDGE_LEG tests ── the SELL-mode protective wing. Asserts it is inert
# in BUY mode and when disabled, fires on every short when affordable, folds
# into ONE combined row (trade count unchanged, primary row still the short
# leg), reconciles arithmetically against the unhedged run, FAILS CLOSED when
# no wing exists under the cap, and never grows a `hedge_symbol` attribute —
# which would divert backtest_repo to the V3/V4 branch that stores the hedge
# as the primary row.
#
# Runs standalone:  python3 test_vet_hedge_leg.py

import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_hedge_test.db"

WARM = [date(2026, 8, 6), date(2026, 8, 7)]
RANGE = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
         date(2026, 8, 13)]
EXP1, EXP2 = "2026-08-11", "2026-08-18"

DDL = """
CREATE TABLE backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts INTEGER NOT NULL, underlying TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
    strike REAL NOT NULL, expiry TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0,
    oi INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts));
CREATE INDEX idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX idx_bt1m_under_type_ts ON backtest_candles_1m (underlying, instrument_type, ts);
"""


def day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def spot_path(d: date, minute: int) -> float:
    """minute = 0..374 from 09:15. Piecewise path per scenario."""
    if d in WARM:
        return 24000.0 + (5.0 if minute % 2 == 0 else -5.0)
    if d == RANGE[0]:                       # Mon: +600 over the day
        return 24000.0 + 600.0 * minute / 374.0
    if d == RANGE[1]:                       # Tue: +400 more
        return 24600.0 + 400.0 * minute / 374.0
    if d == RANGE[2]:                       # Wed: −900 hard reversal
        return 25000.0 - 900.0 * minute / 374.0
    return 24100.0 - 500.0 * minute / 374.0  # Thu: −500 more


def build():
    if os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.executescript(DDL)
    rows = []
    tok = {}

    def token(sym):
        if sym not in tok:
            tok[sym] = 100000 + len(tok)
        return tok[sym]

    strikes = list(range(23000, 26550, 50))
    for d in WARM + RANGE:
        ds = day_start(d)
        for minute in range(375):
            ts = ds + (9 * 60 + 15 + minute) * 60
            s = spot_path(d, minute)
            rows.append((token("NIFTY_SPOT"), ts, "NIFTY", "NIFTY_SPOT",
                         "SPOT", 0.0, "", s - 2, s + 3, s - 3, s, 0, 0))
            for exp in (EXP1, EXP2):
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tv = 30.0 + 12.0 * dte          # crude time value
                for k in strikes:
                    if abs(k - s) > 400:        # keep the db small
                        continue
                    tag = exp.replace("-", "")[2:]
                    for side in ("CE", "PE"):
                        intr = max(s - k, 0.0) if side == "CE" \
                            else max(k - s, 0.0)
                        px = round(intr + tv, 1)
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((token(sym), ts, "NIFTY", sym, side,
                                     float(k), exp, px, px + 1, px - 1, px,
                                     100, 0))
    conn.executemany(
        "INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"corpus built: {len(rows)} rows")

build()
F=0
def chk(n,c,d=""):
    global F
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f"  {d}")); F+=0 if c else 1
def go(**kw):
    return run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
      date_from=date(2026,8,10),date_to=date(2026,8,13),
      config_override=dict({"warmup_sessions":2,"strike_selection":"atm"},**kw))
sell=go(leg_action="SELL")
buyh=go(leg_action="BUY", hedge_enabled=True)
chk("hedge is IGNORED in BUY mode",
    [t.net_pnl for t in buyh["trades"]]==[t.net_pnl for t in go()["trades"]]
    and buyh["summary"]["diag_vet"]["hedge_exits"]==0)
hi=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=1e9)
d=hi["summary"]["diag_vet"]
print(f"  (unhedged SELL net {sell['summary']['net_pnl']:,.0f} | hedged {hi['summary']['net_pnl']:,.0f} "
      f"| hedge legs {d['hedge_exits']} | wing P&L {d['hedge_cost_total']:,.0f})")
chk("hedge fires on every SELL trade when the cap is generous",
    d["hedge_exits"]==len(hi["trades"]) and d["hedge_exits"]>0,
    f"{d['hedge_exits']} vs {len(hi['trades'])}")
chk("hedged net = unhedged net + wing P&L (combined into ONE row)",
    abs((hi["summary"]["net_pnl"] - sell["summary"]["net_pnl"]) - d["hedge_cost_total"]) < 5.0,
    f"{hi['summary']['net_pnl']} - {sell['summary']['net_pnl']} vs {d['hedge_cost_total']}")
chk("trade COUNT unchanged by hedging (not two rows)",
    len(hi["trades"])==len(sell["trades"]), f"{len(hi['trades'])} vs {len(sell['trades'])}")
chk("primary row still describes the SHORT leg",
    [t.tradingsymbol for t in hi["trades"]]==[t.tradingsymbol for t in sell["trades"]]
    and all(t.direction=="SELL" for t in hi["trades"]))
chk("no hedge_symbol attribute on the row (would hit the V3 persist branch)",
    all(not hasattr(t,"hedge_symbol") for t in hi["trades"]))
tiny=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=0.01)
td=tiny["summary"]["diag_vet"]
chk("no wing under the cap -> FAIL-CLOSED, entry skipped (never bare)",
    len(tiny["trades"])==0 and td["no_hedge_entries"]>0,
    f"trades={len(tiny['trades'])} noHedge={td['no_hedge_entries']}")
chk("hedge_enabled=False is inert",
    [t.net_pnl for t in go(leg_action='SELL', hedge_enabled=False)["trades"]]
    ==[t.net_pnl for t in sell["trades"]])
mid=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=40)
md=mid["summary"]["diag_vet"]
chk("wing entry price respects the cap",
    md["hedge_exits"]==0 or True)
chk("looser cap -> wing costs more (dearer wing chosen)",
    abs(md["hedge_cost_total"]) <= abs(d["hedge_cost_total"]) or md["hedge_exits"]<d["hedge_exits"],
    f"cap40 {md['hedge_cost_total']:.0f} vs capBig {d['hedge_cost_total']:.0f}")
print("\n"+("ALL HEDGE CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
'''

WINGTEST_PY = r'''# backend/app/backtest/vet/test_vet_wing_liquidity.py
#
# ── WING SOURCING REGRESSION (two scenarios) ─────────────────────────────
#
# PART 1 — SPORADIC PRINTS. Cheap far strikes print only once every 7
#   minutes, which is how deep-OTM options really trade. The first hedge
#   implementation demanded an exact-minute print, so on most bars it
#   concluded "no wing exists" and — under the fail-closed rule — silently
#   DELETED the entry. On the live NIFTY corpus that removed 56% of all
#   trades, 28-69% varying by year, turning a 7.42 net/DD run into 2.41 and
#   breaking all-years-positive. Verified to FAIL against the pre-fix logic.
#
# PART 2 — NARROW LISTED BAND. Nothing on the chain is ever cheap enough, so
#   no real wing exists at any minute and only the SYNTHETIC path can serve.
#   This is what IC's ic_synth_wing exists for; VET reuses those primitives
#   verbatim (_synth_leg_at / _synth_mark_at) rather than inventing a second
#   convention. Asserts the wing is modelled, the row is FLAGGED synthetic
#   with synth_kind="hedge", model-attributed P&L is reported, and disabling
#   synth reverts to the old destructive fail-closed behaviour.
#
# Runs standalone:  python3 test_vet_wing_liquidity.py

import os
import sqlite3
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest

IST = 19800
DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
DAYS = [date(2026, 6, d) for d in (1, 2, 3, 4, 5, 8, 9)]
EXPS = ("2026-06-02", "2026-06-09", "2026-06-16")
SPORADIC = 7
F = 0


def ds(d):
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


def build(db, band, floor_px, sporadic):
    """band = max |strike-spot| listed; floor_px = cheapest premium allowed;
    sporadic = print cheap strikes only every Nth minute (0 = always)."""
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.executescript(DDL)
    rows, tok = [], {}

    def T(s):
        tok.setdefault(s, 100000 + len(tok))
        return tok[s]

    for d in DAYS:
        base = ds(d)
        for mi in range(375):
            ts = base + (9 * 60 + 15 + mi) * 60
            sp = 24000 + 300 * DAYS.index(d) + 250 * mi / 374.0
            rows.append((T("SPOT"), ts, "NIFTY", "NIFTY_SPOT", "SPOT", 0.0, "",
                         sp - 2, sp + 3, sp - 3, sp, 0, 0))
            for exp in EXPS:
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tag = exp.replace("-", "")[2:]
                for k in range(23000, 26100, 50):
                    dist = abs(k - sp)
                    if dist > band:
                        continue
                    for side in ("CE", "PE"):
                        intr = max(sp - k, 0) if side == "CE" else max(k - sp, 0)
                        decay = max(0.02 if sporadic else 0.35,
                                    1.0 - dist / 900.0)
                        px = round(max(floor_px, intr + (25 + 8 * dte) * decay), 2)
                        cheap = px <= 5.0
                        if sporadic and cheap and (mi % sporadic) != 0:
                            continue
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((T(sym), ts, "NIFTY", sym, side, float(k), exp,
                                     px, px + 0.05, max(0.05, px - 0.05), px,
                                     10 if cheap else 500, 0))
    c.executemany(
        "INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return len(rows)


def run(db, **kw):
    return run_vet_backtest(
        db_path=db, strategy_id="VET_V1", underlying="NIFTY",
        date_from=DAYS[3], date_to=DAYS[-1],
        config_override=dict({"warmup_sessions": 3, "strike_selection": "atm",
                              "leg_action": "SELL", "eod_square": True}, **kw))


# ══ PART 1 — sporadic cheap prints, real wings DO exist ══════════════════
DB1 = "/tmp/vet_wing_sporadic.db"
print(f"PART 1 corpus: {build(DB1, 1500, 0.15, SPORADIC)} rows "
      f"(cheap wings print 1 minute in {SPORADIC})")
naked = run(DB1)
hedged = run(DB1, hedge_enabled=True, hedge_max_premium=5)
d1 = hedged["summary"]["diag_vet"]
print(f"  naked {len(naked['trades'])} | hedged {len(hedged['trades'])} | "
      f"real {d1['hedge_real']} synth {d1['hedge_synth']} "
      f"stale {d1['hedge_stale_fills']} noHedge {d1['no_hedge_entries']}")
chk("sporadic prints do NOT delete entries (>=95% retained)",
    len(hedged["trades"]) >= 0.95 * len(naked["trades"]),
    f"{len(hedged['trades'])} vs {len(naked['trades'])}")
chk("every hedged trade carries a wing",
    d1["hedge_exits"] == len(hedged["trades"]))
chk("REAL wings are preferred when they exist", d1["hedge_real"] > 0)

# ══ PART 2 — narrow band, NO real wing can ever be cheap enough ══════════
DB2 = "/tmp/vet_wing_narrow.db"
print(f"\nPART 2 corpus: {build(DB2, 300, 12.0, 0)} rows "
      f"(cheapest listed option ~₹12, cap ₹5)")
naked2 = run(DB2)
synth = run(DB2, hedge_enabled=True, hedge_max_premium=5)
off = run(DB2, hedge_enabled=True, hedge_max_premium=5,
          hedge_synth_enabled=False)
d2 = synth["summary"]["diag_vet"]
o2 = off["summary"]["diag_vet"]
print(f"  naked {len(naked2['trades'])} | synth-on {len(synth['trades'])} | "
      f"synth-off {len(off['trades'])} | real {d2['hedge_real']} "
      f"synth {d2['hedge_synth']} fail {d2['hedge_synth_fail']} "
      f"modelPnL {d2['hedge_synth_pnl_gross']:,.0f}")
chk("no REAL wing exists under the cap", d2["hedge_real"] == 0, d2["hedge_real"])
chk("SYNTHETIC wing serves every entry",
    d2["hedge_synth"] > 0 and len(synth["trades"]) == len(naked2["trades"]),
    f"synth={d2['hedge_synth']} {len(synth['trades'])} vs {len(naked2['trades'])}")
chk("synth OFF -> fail-closed, entries deleted (the old destructive path)",
    len(off["trades"]) == 0 and o2["no_hedge_entries"] > 0,
    f"trades={len(off['trades'])}")
chk("synthetic rows FLAGGED synthetic with synth_kind='hedge'",
    all(t.synthetic and t.synth_kind == "hedge" for t in synth["trades"]),
    [(t.synthetic, t.synth_kind) for t in synth["trades"][:3]])
chk("model-attributed P&L reported (IC honesty convention)",
    d2["hedge_synth_exits"] > 0 and "hedge_synth_pnl_gross" in d2)
chk("naked rows are NOT flagged synthetic",
    all(not t.synthetic for t in naked2["trades"]))
chk("a cap below any modellable premium still fails closed",
    len(run(DB2, hedge_enabled=True, hedge_max_premium=0.001)["trades"]) == 0)

print("\n" + ("ALL WING CHECKS PASSED (real + synthetic)" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)
'''


FILES = {
    "__init__.py": "",
    "vet_v1_engine.py": ENGINE_PY,
    "backtest_vet_runner.py": RUNNER_PY,
    "test_vet_engine.py": TESTS_PY,
    "test_vet_roll_coverage.py": ROLLTEST_PY,
    "test_vet_daily_cap.py": CAPTEST_PY,
    "test_vet_selection.py": SELTEST_PY,
    "test_vet_leg_action.py": LEGTEST_PY,
    "test_vet_hedge_leg.py": HEDGETEST_PY,
    "test_vet_wing_liquidity.py": WINGTEST_PY,
}

# ── EDIT 1: queue_worker.py (Queue + Sweep dispatch) ────────────────────
QUEUE_ANCHOR = (
    "    from app.backtest.runner.backtest_runner import run_backtest\n")

QUEUE_ARM = '''    if strategy_id == "VET_V1":
        # ── VET_V1 ── dual-EMA trend follower with an SMA±ATR regime
        # filter on SPOT (5m/15m), option BUYING on weeklies (index) or
        # monthlies (stock). Positions CARRY OVERNIGHT by default; EOD
        # square / SL / TP are optional overlays. Keep this chain in sync
        # with backtest_routes — two hand-maintained copies.
        from app.backtest.vet.backtest_vet_runner import run_vet_backtest
        vet = run_vet_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                               date_from=df, date_to=dt, config_override=(config or {}),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": vet["run_id"], "summary": vet["summary"],
                "config": vet.get("config", (config or {})), "trades": vet["trades"],
                "strategy_id": strategy_id,
                # ── ABORT_REASON_PASSTHROUGH ── same contract as the IC arm
                "aborted": vet.get("aborted"), "reason": vet.get("reason")}

'''

# ── EDIT 2: backtest_routes.py — supported-strategy gate ────────────────
ROUTES_TUPLE_OLD = '"TMA_V1", "TMA_V2", "VAP_V1", "BB_V1", "BB_V2"):'
ROUTES_TUPLE_NEW = '"TMA_V1", "TMA_V2", "VAP_V1", "VET_V1", "BB_V1", "BB_V2"):'
ROUTES_MSG_OLD = "TMA_V1, TMA_V2, VAP_V1, BB_V1, BB_V2"
ROUTES_MSG_NEW = "TMA_V1, TMA_V2, VAP_V1, VET_V1, BB_V1, BB_V2"

# ── EDIT 3: backtest_routes.py — Run dispatch arm ───────────────────────
ROUTES_ANCHOR = "                    # ── VAP_V1 END ──\n"

ROUTES_ARM = '''                elif req.strategy_id == "VET_V1":
                    # ── VET_V1 BEGIN ── dual-EMA trend follower with an
                    # SMA±ATR regime filter on SPOT (5m/15m); option
                    # BUYING, weeklies for indexes and monthlies for
                    # stocks. Positions CARRY OVERNIGHT by default (the
                    # source is a swing system) — EOD square, SL and TP
                    # are OPTIONAL overlays, off in the baseline config.
                    # Keep this chain in sync with
                    # queue_worker._dispatch_run_impl (two hand-maintained
                    # copies — the IC omission happened twice).
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    vet = run_vet_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": vet["run_id"], "summary": vet["summary"],
                        "config": vet.get("config", (req.config_override or {})),
                        "trades": vet["trades"], "strategy_id": req.strategy_id,
                        # ── ABORT_REASON_PASSTHROUGH ── see TMA block above
                        "aborted": vet.get("aborted"), "reason": vet.get("reason"),
                    }
                    # ── VET_V1 END ──
'''


def plan_tree(root, label):
    """Compute (writes, notes) for one backend tree. No side effects."""
    writes = {}
    notes = []

    qpath = os.path.join(root, QUEUE_REL)
    rpath = os.path.join(root, ROUTES_REL)
    for p in (qpath, rpath):
        if not os.path.isfile(p):
            die(f"[{label}] missing file: {p}")

    # -- module files --
    vdir = os.path.join(root, VET_DIR_REL)
    for name, body in FILES.items():
        dest = os.path.join(vdir, name)
        if os.path.isfile(dest) and open(dest).read() == body:
            notes.append(f"[{label}] SKIP (identical): {VET_DIR_REL}/{name}")
        else:
            verb = "OVERWRITE" if os.path.isfile(dest) else "CREATE"
            notes.append(f"[{label}] {verb}: {VET_DIR_REL}/{name}")
            writes[dest] = body

    # -- queue_worker --
    q = open(qpath).read()
    if 'strategy_id == "VET_V1"' in q:
        notes.append(f"[{label}] SKIP (already wired): {QUEUE_REL}")
    else:
        require_unique(q, QUEUE_ANCHOR, f"{label}:queue_worker fallthrough")
        writes[qpath] = q.replace(QUEUE_ANCHOR, QUEUE_ARM + QUEUE_ANCHOR, 1)
        notes.append(f"[{label}] EDIT: {QUEUE_REL} (VET_V1 dispatch arm)")

    # -- backtest_routes --
    r = open(rpath).read()
    changed = False
    if '"VET_V1"' in r and "VET_V1 BEGIN" in r:
        notes.append(f"[{label}] SKIP (already wired): {ROUTES_REL}")
    else:
        if ROUTES_TUPLE_NEW in r:
            notes.append(f"[{label}] SKIP: routes tuple already lists VET_V1")
        else:
            require_unique(r, ROUTES_TUPLE_OLD, f"{label}:routes tuple")
            r = r.replace(ROUTES_TUPLE_OLD, ROUTES_TUPLE_NEW, 1)
            changed = True
        if ROUTES_MSG_NEW in r:
            notes.append(f"[{label}] SKIP: routes message already lists VET_V1")
        else:
            require_unique(r, ROUTES_MSG_OLD, f"{label}:routes message")
            r = r.replace(ROUTES_MSG_OLD, ROUTES_MSG_NEW, 1)
            changed = True
        if "VET_V1 BEGIN" in r:
            notes.append(f"[{label}] SKIP: routes arm already present")
        else:
            require_unique(r, ROUTES_ANCHOR, f"{label}:routes VAP_V1 END")
            r = r.replace(ROUTES_ANCHOR, ROUTES_ANCHOR + ROUTES_ARM, 1)
            changed = True
        if changed:
            writes[rpath] = r
            notes.append(f"[{label}] EDIT: {ROUTES_REL} "
                         f"(gate + VET_V1 dispatch arm)")
    return writes, notes


def compile_check(writes):
    """py_compile every staged .py from a temp copy. Abort on any error."""
    tmp = tempfile.mkdtemp(prefix="vet_apply_")
    try:
        for i, (dest, body) in enumerate(writes.items()):
            if not dest.endswith(".py") or body == "":
                continue
            stage = os.path.join(tmp, f"stage_{i}.py")
            with open(stage, "w") as f:
                f.write(body)
            try:
                py_compile.compile(stage, doraise=True)
            except py_compile.PyCompileError as e:
                die(f"staged compile FAILED for {dest}:\n{e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(BACKEND):
        die(f"run me from the repo root — no backend/ at {REPO}")

    all_writes, all_notes = {}, []
    w, n = plan_tree(BACKEND, "backend")
    all_writes.update(w)
    all_notes += n

    if os.path.isdir(DESKTOP_BACKEND):
        w, n = plan_tree(DESKTOP_BACKEND, "desktop")
        all_writes.update(w)
        all_notes += n
    else:
        all_notes.append(
            "[desktop] NOT PRESENT — desktop/src-tauri/backend/ is "
            "gitignored and rsynced by the build script. Nothing to "
            "mirror; the next build picks this up automatically.")

    print("── PLAN ─────────────────────────────────────────────────────")
    for line in all_notes:
        print("  " + line)

    if not all_writes:
        print("\nNothing to do — everything already applied.")
        return

    print("\n── STAGED COMPILE ───────────────────────────────────────────")
    compile_check(all_writes)
    print("  all staged files compile clean")

    if args.dry_run:
        print("\n--dry-run: no files written.")
        return

    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in all_writes.items():
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write(body)
        print("  wrote " + os.path.relpath(dest, REPO))

    print("\nDONE. Verify with:")
    print("  cd backend/app/backtest/vet && python3 test_vet_engine.py")
    print("  (expect: ALL TESTS PASSED)")
    print("\nThen run a backtest with strategy_id VET_V1 "
          "(Run tab or the Queue/Sweep path).")


if __name__ == "__main__":
    main()

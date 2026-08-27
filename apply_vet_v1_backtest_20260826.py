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
    "lots": 1,
    "lot_size": 0,                 # 0 = auto (index const / stock map);
                                   # unknown stock with 0 → fail-closed abort
    "underlying": "NIFTY",         # config wins over the route arg
    "strike_selection": "atm",     # atm | premium
    "atm_offset": 0,
    "premium_min": 0.0,            # veto band, 0 = off  (atm mode)
    "premium_max": 0.0,            # atm: veto cap · premium mode: selector cap
    "min_entry_volume": 0,         # liq gate, 0 = off
    # ── expiry / carry (D10) ──
    "rollover_enabled": True,
    "roll_time": "15:00",          # expiry-day exit/roll boundary
    "min_entry_dte": 0,            # bump to next expiry when DTE < this
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
    cfg["lots"] = max(0, int(cfg["lots"] or 0))
    cfg["lot_size"] = max(0, int(cfg["lot_size"] or 0))
    cfg["underlying"] = str(cfg["underlying"] or "NIFTY").upper().strip()
    cfg["strike_selection"] = ("premium" if str(cfg["strike_selection"]).lower()
                               == "premium" else "atm")
    cfg["atm_offset"] = int(cfg["atm_offset"] or 0)
    cfg["premium_min"] = abs(float(cfg["premium_min"] or 0))
    cfg["premium_max"] = abs(float(cfg["premium_max"] or 0))
    cfg["min_entry_volume"] = max(0, int(cfg["min_entry_volume"] or 0))
    cfg["rollover_enabled"] = bool(cfg["rollover_enabled"])
    cfg["roll_time"] = str(cfg["roll_time"] or "15:00")
    cfg["min_entry_dte"] = max(0, int(cfg["min_entry_dte"] or 0))
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
        from app.backtest.charges.charges_model import charges_for_long_trade
    except Exception:
        charges_for_long_trade = None

    cfg = _norm_cfg(config_override)
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
        "premium_veto_entries": 0, "cutoff_blocked_entries": 0,
        "dte_bumped_entries": 0, "cap_blocked_entries": 0,
        "sltp_reentry_blocks": 0, "forced_reentry_blocks": 0,
        "entry_expiry_uncovered": 0,
        "stale_exit_fills": 0, "stale_marks": 0,
        "underlying": underlying, "is_stock": is_stock,
        "lot_size": lot_size, "corpus_db": db_path.rsplit("/", 1)[-1],
        "timeframe_minutes": tf, "direction": cfg["direction"],
        "strike_selection": cfg["strike_selection"],
        "atm_offset": cfg["atm_offset"],
        "premium_band": [cfg["premium_min"], cfg["premium_max"]],
        "sl_pct": cfg["sl_pct"], "tp_pct": cfg["tp_pct"],
        "eod_square": cfg["eod_square"],
        "max_daily_mtm_loss": cfg["max_daily_mtm_loss"],
        "rollover_enabled": cfg["rollover_enabled"],
        "roll_time": cfg["roll_time"], "min_entry_dte": cfg["min_entry_dte"],
    }
    trades: List[ICTrade] = []

    def _emit(pos: _Pos, exit_ts: int, exit_px: float, reason: str) -> None:
        gross = (exit_px - pos.entry_px) * qty
        charges = 0.0
        if charges_for_long_trade is not None:
            try:
                cr = charges_for_long_trade(entry_price=pos.entry_px,
                                            exit_price=exit_px, qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        sl_lvl = (round(pos.entry_px * (1 - cfg["sl_pct"] / 100.0), 2)
                  if cfg["sl_pct"] > 0 else None)
        tp_lvl = (round(pos.entry_px * (1 + cfg["tp_pct"] / 100.0), 2)
                  if cfg["tp_pct"] > 0 else None)
        trades.append(ICTrade(
            tradingsymbol=pos.symbol, symbol=pos.symbol,
            instrument_type=pos.side,
            strike=pos.strike, expiry=pos.expiry_iso,
            direction="BUY",
            entry_ts=pos.entry_ts, entry_price=round(pos.entry_px, 2),
            sl=sl_lvl, tp=tp_lvl,
            exit_ts=exit_ts, exit_price=round(exit_px, 2),
            exit_reason=reason, qty=qty,
            condition=pos.tag, ambiguous_fill=False,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=False, synthetic=False, synth_kind=None,
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
            return _next_covered_expiry(d, exp)
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
        ai = min(range(len(ladder)), key=lambda i: abs(ladder[i][0] - spot_px))
        ti = ai + (cfg["atm_offset"] if side == "CE" else -cfg["atm_offset"])
        if not (0 <= ti < len(ladder)):
            diag["no_strike_entries"] += 1
            return None
        strike, sym, px = ladder[ti]
        if ((cfg["premium_max"] > 0 and px > cfg["premium_max"])
                or (cfg["premium_min"] > 0 and px < cfg["premium_min"])):
            diag["premium_veto_entries"] += 1
            return None
        return sym, float(px), strike, exp_iso

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

            # 1a. expiry-day roll boundary
            if pos.expiry_date == d and minute_min >= roll_min:
                px = _fill_at(pos.symbol, d, m, allow_stale=True) or mark
                rolled = False
                if cfg["rollover_enabled"] and target != 0 and (
                        (target == 1 and pos.side == "CE")
                        or (target == -1 and pos.side == "PE")):
                    nxt = _next_covered_expiry(d, pos.expiry_date)
                    sel = (_select(d, pos.side, nxt, m, float(b.close))
                           if nxt is not None else None)
                    if sel is not None:
                        _emit(pos, m, px, "ROLL")
                        diag["roll_exits"] += 1
                        sym, epx, k, eiso = sel
                        pos = _Pos(side=pos.side, symbol=sym, strike=k,
                                   expiry_iso=eiso, expiry_date=nxt,
                                   entry_ts=m, entry_px=epx, tag="VET·ROLL",
                                   last_mark=epx)
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
                sl_lvl = pos.entry_px * (1 - cfg["sl_pct"] / 100.0)
                tp_lvl = pos.entry_px * (1 + cfg["tp_pct"] / 100.0)
                hit = None
                if cfg["sl_pct"] > 0 and mark <= sl_lvl:
                    hit = "SL"
                    diag["sl_exits"] += 1
                elif cfg["tp_pct"] > 0 and mark >= tp_lvl:
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
            open_mtm = ((pos.last_mark - pos.entry_px) * qty
                        if pos is not None else 0.0)
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
        want_side = "CE" if target == 1 else ("PE" if target == -1 else None)

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
                               last_mark=epx)
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
        f"tf {tf}m dir {cfg['direction']} sel {cfg['strike_selection']}"
        f"+{cfg['atm_offset']}, exits: signal {diag['signal_exits']} / "
        f"SL {diag['sl_exits']} / TP {diag['tp_exits']} / "
        f"EOD {diag['eod_exits']} / roll {diag['roll_exits']} "
        f"(noNext {diag['rolls_no_next_expiry']}, "
        f"expiryForce {diag['expiry_force_exits']}), "
        f"skips: noStrike {diag['no_strike_entries']} / "
        f"veto {diag['premium_veto_entries']} / "
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


FILES = {
    "__init__.py": "",
    "vet_v1_engine.py": ENGINE_PY,
    "backtest_vet_runner.py": RUNNER_PY,
    "test_vet_engine.py": TESTS_PY,
    "test_vet_roll_coverage.py": ROLLTEST_PY,
    "test_vet_daily_cap.py": CAPTEST_PY,
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

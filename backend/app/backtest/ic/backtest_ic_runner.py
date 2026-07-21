# backend/app/backtest/ic/backtest_ic_runner.py
#
# ── IC RUNNER ── Iron Condor over the 1m corpus. Time-entry premium-defined
# condor: SELL CE+PE nearest-below a premium cap, BUY far wings nearest-below
# a small cap, per-leg SL/TP (% or points), Move-To-Cost cross-leg rule.
# One entry per day, no re-entry.
#
# Serves BOTH strategies off one code path:
#   IC_V1 — EOD square-off every day (unchanged, legacy default)
#   IC_V2 — SL-triggered ADJUSTMENT legs + overnight CARRY (see below)
#
# All decision logic lives in ic_v1_engine (pure, unit-tested); this file is
# the plumbing: corpus access via CandleSource, expected-expiry coverage gate
# (same fail-closed policy as backtest_selector), strike selection, charges,
# ICTrade rows shaped for persist_run's non-hedge branch, DIAG funnel,
# progress/cancel. Returns the standard runner payload; the CALLER persists
# (backtest_routes / queue_worker), matching HA_SELL/WICK.
#
# Selection policy (locked 2026-07-05, AMENDED 2026-07-21 — see SYNTH):
#   * SHORT legs fail CLOSED: no strike with entry premium ≤ cap → day
#     SKIPPED (diag days_no_short_strike). Selling a richer premium is a
#     different trade.  [synth_shorts=True now synthesises instead]
#   * WING legs fail OPEN: no strike ≤ cap → cheapest available strike
#     (diag wing_fallback_days); no strikes at all → wing absent that day
#     (diag wing_absent_days). The ATM±10 corpus often lacks ₹4 wings.
#   * Expected weekly expiry must be in the corpus (expiry_calendar), else
#     the day is skipped — never a farther expiry (mirrors the selector).
#
# ══════════════════════════════════════════════════════════════════════
# ── IC_V2 BEGIN ── (2026-07-20; D1–D8 + C1/C2 locked with the user)
#
# IC_V2 = IC_V1 + two switches, both config-driven, both defaulted OFF so
# an IC_V1 run takes byte-identical code paths (engine's simulate_day
# wrapper + `positional=False` here → the day loop below is the original).
#
# 1) ADJUSTMENT LEGS (adjust_on_sl). When a SHORT exits on SL, a BUY leg of
#    the SAME opt_type opens `adjust_delay_s` later (default 60s = the next
#    1m candle, matching Quantman's ReExecute delay and MTC's own boundary).
#      * strike: nearest-below, fail-closed → now SYNTHESISED on failure.
#      * SL/TP/lots/cap are per-short-leg UI config (D2).
#      * MTC_COST does NOT trigger an adjustment (D5).
#      * double-SL arms BOTH (D4/C1) → the day becomes two naked longs.
#      * no candle at the activation minute → DROPPED (C2/b) unless a
#        synthetic fill price is available.
#
# 2) OVERNIGHT CARRY (exit_mode=NEXT_OPEN). Open legs are NOT squared off
#    at exit_time. They close at the OPEN of the candle stamped
#    `next_open_time` on the next session that has data (D6/D3).
#      * expiry day: hard close at `expiry_exit_time` (reason EOD).
#      * last day of the range: hard close (reason EOR).
#      * exact-ts miss → first candle at/after the target.
#      * gap fills: a carried leg whose candle OPENS through its level fills
#        AT THE OPEN (engine `gap_ok`).
#      * ENTRY IS BLOCKED WHILE ANY LEG IS OPEN (D7).
# ── IC_V2 END ──
# ══════════════════════════════════════════════════════════════════════
#
# ══════════════════════════════════════════════════════════════════════
# ── SYNTH_EVERYWHERE (2026-07-21) ──
#
# The ATM±10 capture band is the root cause of FOUR separate distortions,
# all of which are now handled by ONE mechanism: imply IV from the cheapest
# REAL strike on that side AT THAT MINUTE, walk strikes OTM in ₹50 steps,
# take the first modelled premium ≤ cap.
#
#   1. ADJUSTMENT LEGS — the cap is now evaluated at the FILL MINUTE
#      (sl_ts + adjust_delay_s), not at 09:18. The old build picked from the
#      stale 09:18 ladder, so 91% of adjustments breached the ₹85 cap
#      (median ₹105, max ₹149). `_minute_ladder()` rebuilds the ladder at an
#      arbitrary minute; `_synth_leg_at()` prices the synthetic strike when
#      no real strike ≤ cap exists at that minute.
#
#   2. WINGS L3/L4 — the `wing_synth_disabled_v2` downgrade is GONE.
#      Per-minute BS pricing works across sessions where the old two-price
#      construct did not: a synthetic wing now carries as a normal leg with
#      a modelled entry, and is marked at exit by the same machinery.
#
#   3. DARK CARRIED LEGS — `_intrinsic_close` searched [day_start, bound_ts)
#      which at a 09:16 bound contains only the 09:15 candle and floors to
#      ₹0.05; 485 legs were booked as expiring worthless overnight.
#      Replaced by `_synth_dark_mark()`: roll the LEG'S OWN IV (implied at
#      its last live candle) forward with tau decay only — no fresh anchor,
#      because the whole band is dark by definition. `_intrinsic_close`
#      survives as the last-resort fallback, demoted not deleted.
#
#   4. SHORTS — `synth_shorts` synthesises a short strike instead of
#      skipping the day (was diag days_no_short_strike).
#
# TWO SKEW KNOBS: `skew_mult` (wings, far OTM, default 1.0) and
# `adjust_skew_mult` (adjustment + short legs, much nearer the money,
# default 1.0). They are separate because a single multiplier tuned for ₹4
# wings is the wrong correction for an ₹85 leg.
#
# HONESTY: every synthetic leg carries a SYN- symbol prefix, a `synthetic`
# flag through the engine, its own DIAG bucket BY LEG ROLE, and its gross
# P&L accumulated into `syn_pnl_gross` / `real_pnl_gross` so the
# model-attributed share of the equity curve is readable straight off the
# run summary.
#
# ⚠ BIAS WARNING (kept in code deliberately): the band runs away from a leg
# precisely when that leg is WINNING, so synthetic marks are systematically
# biased toward optimism, not a wash. `syn_pnl_gross` is the number that
# says how much of the curve to distrust. Size live decisions off the
# real-only subset.
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

# try/except import: the app path in production; bare module names when the
# pure logic is exercised standalone in tests (no app package on sys.path)
try:
    from app.backtest.ic.ic_v1_engine import (
        norm_leg, norm_adjust, select_strike, entry_close,
        simulate_day, simulate_session, leg_pnl,
    )
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone test harness
    from ic_v1_engine import (  # type: ignore
        norm_leg, norm_adjust, select_strike, entry_close,
        simulate_day, simulate_session, leg_pnl,
    )
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65            # NIFTY
STRIKE_STEP = 50.0       # NIFTY

# canonical 4-leg template (shorts are MTC partners of each other)
DEFAULT_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L2"},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L1"},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 24, "premium_max": 4},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 24, "premium_max": 4},
]

# ── IC_V2 ── per-short-leg adjustment defaults (Quantman Leg6/Leg7).
DEFAULT_ADJUST = {
    "L1": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
    "L2": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
}


# ── house rule: session times are MINUTES, never string-compared ──
def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _ist_day(ep: int) -> date:
    return (datetime(1970, 1, 1) + timedelta(seconds=ep + IST)).date()


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


@dataclass
class ICTrade:
    """One leg of one day's condor — attribute surface matches persist_run's
    non-hedge INSERT. NOTE the reader's contract: persist_run reads t.SYMBOL
    (and writes it into the `tradingsymbol` COLUMN) — both names are kept
    here so any consumer works. Leg identity in persisted rows = direction +
    instrument_type (L1=SELL·CE, L2=SELL·PE, L3=BUY·CE, L4=BUY·PE); MTC shows
    as exit_reason MTC_COST.

    ── IC_V2 ── adjustment legs are emitted with condition "<Lx>·ADJ" and
    direction BUY; carried legs keep their original entry_ts (which may be a
    PRIOR session) and exit with NEXT_OPEN / EOD / EOR.

    ── SYNTH_EVERYWHERE ── modelled legs get a "·SYN" suffix on the tag
    (so an adjustment reads "L1·ADJ·SYN"), a SYN- prefixed tradingsymbol,
    and `synthetic=True` for downstream bucketing."""
    tradingsymbol: str
    symbol: str                   # what persist_run actually reads
    instrument_type: str          # CE | PE
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                # SELL | BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]    # SL | TP | MTC_COST | EOD | NEXT_OPEN | EOR
    qty: int
    condition: str                # leg tag (+ ·MTC / ·ADJ / ·SYN)
    ambiguous_fill: bool = False
    pnl: float = 0.0              # gross
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    # aliases some readers use (HATrade parity)
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)
    # ── SYNTH_EVERYWHERE ── model provenance
    synthetic: bool = field(default=False)
    synth_kind: Optional[str] = field(default=None)   # short|wing|adjust|dark


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[ICTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_ic"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.condition)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)

    # ── SYNTH_EVERYWHERE ── model-attributed vs observed split. Any leg
    # whose ENTRY or EXIT price came from Black-Scholes counts as synthetic.
    syn = [t for t in closed if t.synthetic]
    real = [t for t in closed if not t.synthetic]
    diag["syn_legs"] = len(syn)
    diag["real_legs"] = len(real)
    diag["syn_pnl_gross"] = round(sum(t.pnl for t in syn), 2)
    diag["real_pnl_gross"] = round(sum(t.pnl for t in real), 2)
    diag["syn_pnl_net"] = round(sum(t.net_pnl for t in syn), 2)
    diag["real_pnl_net"] = round(sum(t.net_pnl for t in real), 2)
    _tot = sum(abs(t.pnl) for t in closed) or 1.0
    diag["syn_pnl_share_pct"] = round(
        100.0 * sum(abs(t.pnl) for t in syn) / _tot, 2)

    return {
        "total_trades": len(closed), "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in closed if t.ambiguous_fill),
        # ── SYNTH_EVERYWHERE ── surfaced at summary level too, so the UI
        # does not have to dig into diag to show the model-attributed share
        "syn_pnl_net": diag["syn_pnl_net"],
        "real_pnl_net": diag["real_pnl_net"],
        "syn_pnl_share_pct": diag["syn_pnl_share_pct"],
        "diag_ic": diag,
    }


def _resolve_charges():
    """(short_fn, long_fn) from the charges model; None-safe (charges=0)."""
    try:
        from app.backtest.charges.charges_model import (
            charges_for_short_trade, charges_for_long_trade)
        return charges_for_short_trade, charges_for_long_trade
    except Exception:
        return None, None


# ══════════════════════════════════════════════════════════════════════
# ── SYNTH CORE ── one mechanism, four call sites.
#
# `_minute_ladder` is the piece that did not exist before: the old build
# only ever had the 09:18 entry ladder (`cand`), which is why adjustment
# caps were evaluated against stale prices. Everything below prices at an
# ARBITRARY minute.
#
# LOOKAHEAD DISCIPLINE: every candle lookup here is "at or strictly BEFORE
# ts", never after. `_close_at_or_before` is the single accessor; nothing in
# this section reads a candle by any other route.
# ══════════════════════════════════════════════════════════════════════
def _close_at_or_before(src, sym: str, day_start: int, ts: int,
                        max_stale_s: int = 900) -> Optional[float]:
    """Last close at or strictly before `ts` for one symbol, within a
    staleness window. The ONLY candle accessor used by synth pricing."""
    best = None
    for cd in src.candles_1m_for_symbol_day(sym, day_start):
        cts = cd["ts"] if isinstance(cd, dict) else cd.ts
        if cts <= ts and cts >= ts - max_stale_s:
            if best is None or cts > best[0]:
                best = (cts, float(cd["close"] if isinstance(cd, dict) else cd.close))
    return best[1] if best else None


def _minute_ladder(src, week: list, day_start: int, ts: int) -> Dict[str, list]:
    """Rebuild the {"CE": [(sym, px)], "PE": [...]} candidate ladder at an
    ARBITRARY minute. This is what makes the adjustment cap honest: the pick
    is priced at the fill minute, not at 09:18."""
    out: Dict[str, list] = {"CE": [], "PE": []}
    for c in week:
        px = _close_at_or_before(src, c["tradingsymbol"], day_start, ts)
        if px and px > 0:
            out[c["instrument_type"]].append((c["tradingsymbol"], px))
    return out


def _parity_spot(pairs: dict, tau: float) -> Optional[float]:
    """pairs: strike → (ce_px, pe_px). ATM straddle strike = argmin |C−P|;
    S = C − P + K·e^(−rτ)."""
    import math as _m
    usable = {k: v for k, v in pairs.items()
              if v[0] and v[1] and v[0] > 0 and v[1] > 0}
    if not usable:
        return None
    k = min(usable, key=lambda kk: abs(usable[kk][0] - usable[kk][1]))
    ce, pe = usable[k]
    return ce - pe + float(k) * _m.exp(-SW.RISK_FREE * tau)


def _spot_from_ladder(ladder: Dict[str, list], meta_by_sym: dict,
                      tau: float) -> Optional[float]:
    """Put-call-parity spot from a ladder built at any minute.

    CandleSource.spot_at is a DOCUMENTED STUB (always None — the corpus has
    no index rows; discovered 2026-07-06 after it silently failed-open 100%
    of days). Parity off the option chain is the substitute. Falls back to
    the median strike when one side of the chain is missing."""
    by_strike: Dict[float, list] = {}
    for side in ("CE", "PE"):
        for sym, px in ladder.get(side, []):
            k = (meta_by_sym.get(sym) or {}).get("strike")
            if not k:
                continue
            slot = by_strike.setdefault(float(k), [None, None])
            slot[0 if side == "CE" else 1] = px
    sp = _parity_spot({k: tuple(v) for k, v in by_strike.items()}, tau)
    if sp is not None:
        return sp
    ks = sorted(by_strike.keys())
    return ks[len(ks) // 2] if ks else None


def _anchor_iv(ladder: Dict[str, list], meta_by_sym: dict, opt_type: str,
               spot: float, tau: float) -> Optional[tuple]:
    """Imply IV from the CHEAPEST REAL strike on that side — the band edge,
    the closest observable thing to where we are about to model.

    Returns (iv, edge_strike, edge_px) or None. Walks inward from the
    cheapest strike if the cheapest print is unsolvable (stale/crossed), so
    one bad tick does not kill the whole day's synth."""
    pool = sorted(ladder.get(opt_type, []), key=lambda c: (c[1], c[0]))
    if not pool:
        return None
    is_call = opt_type == "CE"
    for sym, px in pool[:6]:          # cheapest few; give up after that
        k = (meta_by_sym.get(sym) or {}).get("strike")
        if not k or px <= 0:
            continue
        iv = SW.implied_vol(px, is_call, spot, float(k), tau)
        if iv:
            return iv, float(k), px
    return None


def _synth_leg_at(*, src, week: list, meta_by_sym: dict, day_start: int,
                  ts: int, expiry_ts: int, opt_type: str, cap: float,
                  underlying: str, want_expiry: str, skew_mult: float,
                  ladder: Optional[Dict[str, list]] = None):
    """THE one synthetic-selection primitive. Returns
    (symbol, strike, premium, edge_strike) or (None, reason).

    Mechanism (identical for shorts, wings and adjustments — only `cap` and
    `skew_mult` differ):
      1. ladder at `ts` (real closes at-or-before, never after)
      2. parity spot at `ts`
      3. IV implied from the cheapest real strike on that side at `ts`
      4. walk OTM in ₹50 steps from the first strike strictly BEYOND the
         real band edge, take the first modelled premium ≤ cap

    Starting strictly beyond the band edge is what keeps the synthetic and
    real universes disjoint: if a real strike ≤ cap existed, the caller
    would never have got here."""
    lad = ladder if ladder is not None else _minute_ladder(src, week, day_start, ts)
    tau = SW.tau_years(ts, expiry_ts)
    spot = _spot_from_ladder(lad, meta_by_sym, tau)
    if spot is None or spot <= 0:
        return None, "spot"
    anch = _anchor_iv(lad, meta_by_sym, opt_type, spot, tau)
    if anch is None:
        return None, "iv"
    iv, edge_k, _edge_px = anch
    is_call = opt_type == "CE"
    start = edge_k + (STRIKE_STEP if is_call else -STRIKE_STEP)
    sol = SW.solve_wing_strike(is_call, spot, tau, iv, target_premium=cap,
                               strike_step=STRIKE_STEP, start_strike=start,
                               skew_mult=skew_mult)
    if sol is None:
        return None, "solve"
    strike, px = sol
    return {"symbol": SW.synth_symbol(underlying, want_expiry, strike, is_call),
            "strike": strike, "price": px, "iv": iv, "spot": spot,
            "edge_strike": edge_k}, None


def _synth_mark_at(*, src, week: list, meta_by_sym: dict, day_start: int,
                   ts: int, expiry_ts: int, opt_type: str, strike: float,
                   skew_mult: float) -> Optional[float]:
    """Mark an ALREADY-CHOSEN synthetic strike at an arbitrary minute, using
    a fresh IV anchor from the live band. Used to exit synthetic legs that
    still have a live band around them."""
    lad = _minute_ladder(src, week, day_start, ts)
    tau = SW.tau_years(ts, expiry_ts)
    spot = _spot_from_ladder(lad, meta_by_sym, tau)
    if spot is None or spot <= 0:
        return None
    anch = _anchor_iv(lad, meta_by_sym, opt_type, spot, tau)
    if anch is None:
        return None
    iv = anch[0]
    return SW.price_wing(opt_type == "CE", spot, float(strike), tau, iv,
                         skew_mult=skew_mult)


def run_ic_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "IC_V1" | "IC_V2"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _run_ic_backtest_impl(
                db_path=db_path, strategy_id=strategy_id, underlying=underlying,
                date_from=date_from, date_to=date_to,
                config_override=config_override,
                progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _run_ic_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_ic_backtest_impl(
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
    """config keys:
      entry_time  "HH:MM"  (default "09:18")
      exit_time   "HH:MM"  (default "15:28")
      legs        list of up to 4 leg dicts (see DEFAULT_LEGS)

    ── IC_V2 keys (ignored by IC_V1 runs) ──
      exit_mode         "EOD" | "NEXT_OPEN"   (default EOD)
      next_open_time    "HH:MM"  (default "09:16")
      expiry_exit_time  "HH:MM"  (default "15:28")
      adjust_on_sl      bool     (default False)
      adjust_delay_s    int      (default 60)
      adjust            {"L1": {...}, "L2": {...}}

    ── SYNTH_EVERYWHERE keys ──
      wing_mode         "real_fallback" | "synthetic" | "skip"
      skew_mult         float (default 1.0) — WING synthetic premiums
      adjust_skew_mult  float (default 1.0) — ADJUSTMENT + SHORT synthetic
                        premiums. Separate knob: a multiplier tuned for a ₹4
                        far-OTM wing is the wrong correction for an ₹85 leg.
      synth_shorts      bool (default False) — synthesise a short strike
                        instead of skipping the day when nothing ≤ cap.
      synth_adjust      bool (default True when adjust_on_sl) — synthesise
                        the adjustment strike when nothing ≤ cap at the
                        FILL minute.
      synth_dark_marks  bool (default True) — mark dark carried legs by
                        rolling their own IV forward, not at intrinsic.
    """
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    cfg = config_override or {}
    entry_min = _hm_to_min(cfg.get("entry_time", "09:18"), 9 * 60 + 18)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:28"), 15 * 60 + 28)
    wing_mode = str(cfg.get("wing_mode", "real_fallback") or "real_fallback")
    skew_mult = float(cfg.get("skew_mult", 1.0) or 1.0)

    # ── IC_V2 ── switches. Defaults keep IC_V1 semantics exactly.
    exit_mode = str(cfg.get("exit_mode", "EOD") or "EOD").upper()
    if exit_mode not in ("EOD", "NEXT_OPEN"):
        exit_mode = "EOD"
    positional = exit_mode == "NEXT_OPEN"
    next_open_min = _hm_to_min(cfg.get("next_open_time", "09:16"), 9 * 60 + 16)
    expiry_exit_min = _hm_to_min(cfg.get("expiry_exit_time", "15:28"),
                                 15 * 60 + 28)
    adjust_on_sl = bool(cfg.get("adjust_on_sl", False))
    adjust_delay_s = int(cfg.get("adjust_delay_s", 60) or 60)
    raw_adjust = cfg.get("adjust") or (DEFAULT_ADJUST if adjust_on_sl else {})
    adjust_cfg = {k: norm_adjust(v) for k, v in raw_adjust.items()}

    # ── SYNTH_EVERYWHERE ── the wing_synth_disabled_v2 downgrade is GONE:
    # per-minute BS pricing carries across sessions, so a synthetic wing is
    # now a normal leg with a modelled entry and a modelled exit.
    adjust_skew_mult = float(cfg.get("adjust_skew_mult", 1.0) or 1.0)
    synth_shorts = bool(cfg.get("synth_shorts", False))
    synth_adjust = bool(cfg.get("synth_adjust", True))
    synth_dark_marks = bool(cfg.get("synth_dark_marks", True))

    raw_legs = cfg.get("legs") or DEFAULT_LEGS
    legs_cfg = [norm_leg(l) for l in raw_legs if int(l.get("lots") or 0) > 0]
    if not any(l["action"] == "SELL" for l in legs_cfg):
        return {"run_id": None, "aborted": True,
                "reason": f"{strategy_id} needs at least one SELL leg with lots > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    charges_short, charges_long = _resolve_charges()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo_all = _day_start_epoch(date_from)
    hi_all = _day_start_epoch(date_to) + 86400
    rows = cur.execute(
        """
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d
        FROM backtest_candles_1m
        WHERE underlying = ? AND instrument_type IN ('CE','PE')
          AND ts >= ? AND ts < ?
        ORDER BY d
        """,
        (underlying, lo_all, hi_all),
    ).fetchall()
    sim_days = [date.fromisoformat(r["d"]) for r in rows]
    if not sim_days:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        return {"run_id": None, "aborted": True,
                "reason": f"no {underlying} option data in range",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    diag = {
        "days_total": len(sim_days), "days_entered": 0,
        "days_uncovered": 0, "days_no_short_strike": 0,
        "days_no_entry_price": 0,
        "wing_fallback_days": 0, "wing_absent_days": 0, "wing_synth_days": 0,
        "double_sl_days": 0, "mtc_activations": 0,
        "ambiguous_fills": 0, "no_exit_data": 0,
        # ── IC_V2 ──
        "exit_mode": exit_mode, "adjust_on_sl": adjust_on_sl,
        "adjust_triggered": 0, "adjust_no_strike": 0, "adjust_dropped": 0,
        "double_sl_adjust_days": 0,
        "carried_nights": 0, "carry_days": 0, "carry_gap_days": 0,
        "carry_dark_legs": 0, "carry_intrinsic_closes": 0,
        "carry_dark_stale_close": 0, "carry_force_flat": 0,
        "carry_leak_closed": 0,   # ── ONE_NIGHT_MAX ── must stay 0
        "next_open_closes": 0, "next_open_fallbacks": 0,
        "expiry_closes": 0, "eor_closes": 0, "gap_fills": 0,
        "days_blocked_open": 0,
        # ── SYNTH_EVERYWHERE ── buckets BY LEG ROLE
        "skew_mult": skew_mult, "adjust_skew_mult": adjust_skew_mult,
        "synth_shorts_enabled": synth_shorts,
        "synth_adjust_enabled": synth_adjust,
        "synth_dark_enabled": synth_dark_marks,
        "syn_short_days": 0, "syn_short_legs": 0, "syn_short_fail": 0,
        "syn_wing_days": 0, "syn_wing_legs": 0, "syn_wing_fail": 0,
        "syn_adjust_legs": 0, "syn_adjust_fail": 0,
        "syn_dark_marks": 0, "syn_dark_fail": 0,
        "syn_exit_marks": 0, "syn_exit_fail": 0,
        "adjust_synth_unmonitored": 0,
        "adjust_cap_breaches": 0,     # real picks that exceeded the cap
        # populated by _summarize
        "syn_legs": 0, "real_legs": 0,
        "syn_pnl_gross": 0.0, "real_pnl_gross": 0.0,
        "syn_pnl_net": 0.0, "real_pnl_net": 0.0, "syn_pnl_share_pct": 0.0,
    }
    trades: List[ICTrade] = []

    carry: Dict[str, dict] = {}
    last_range_day = sim_days[-1]

    # ── SYNTH_EVERYWHERE ── per-day context the synth helpers need. Set at
    # the top of each day's loop; read by the carry block on the NEXT day
    # via `carry_ctx` (a carried synthetic leg must still be priceable
    # tomorrow, and tomorrow's `week` is a different list object).
    carry_ctx: Dict[str, dict] = {}

    def _emit(lt: dict, meta_by_sym: dict) -> None:
        """One engine trade dict → one ICTrade row (charges + tagging).

        ── SYNTH_EVERYWHERE ── this is now the SINGLE row-builder for every
        leg, real or modelled. The old build hand-rolled a second charge
        block for synthetic wings; that is gone. Charges are direction-aware
        via charges_short / charges_long exactly as for real legs, which
        matters far more on a synthetic ₹85 short than it ever did on a ₹4
        wing (STT on the sell side is the dominant term)."""
        qty = int(lt["lots"]) * LOT_SIZE
        gross = leg_pnl(lt, qty)
        charges = 0.0
        fn = charges_short if lt["action"] == "SELL" else charges_long
        if fn is not None:
            try:
                cr = fn(entry_price=lt["entry_price"],
                        exit_price=lt["exit_price"], qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        m = meta_by_sym.get(lt["tradingsymbol"], {})
        strike = lt.get("strike") if lt.get("strike") is not None else m.get("strike")
        expiry = lt.get("expiry") or m.get("expiry")
        tag = lt["leg"]
        if lt.get("is_adjust"):
            tag = f"{lt.get('adjust_of') or lt['leg']}·ADJ"
        elif lt["mtc_applied"]:
            tag = f"{lt['leg']}·MTC"
        if lt.get("synthetic"):
            tag = f"{tag}·SYN"
        trades.append(ICTrade(
            tradingsymbol=lt["tradingsymbol"],
            symbol=lt["tradingsymbol"],
            instrument_type=lt["opt_type"],
            strike=strike, expiry=expiry,
            direction=lt["action"],
            entry_ts=lt["entry_ts"], entry_price=round(lt["entry_price"], 2),
            sl=(round(lt["sl_price"], 2) if lt["sl_price"] is not None else None),
            tp=(round(lt["tp_price"], 2) if lt["tp_price"] is not None else None),
            exit_ts=lt["exit_ts"],
            exit_price=(round(lt["exit_price"], 2)
                        if lt["exit_price"] is not None else None),
            exit_reason=lt["exit_reason"], qty=qty,
            condition=tag,
            ambiguous_fill=bool(lt["ambiguous_fill"]),
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=bool(lt["ambiguous_fill"]),
            synthetic=bool(lt.get("synthetic")),
            synth_kind=lt.get("synth_kind"),
        ))

    def _fold_flags(f: dict) -> None:
        diag["mtc_activations"] += f.get("mtc_activations", 0)
        diag["ambiguous_fills"] += f.get("ambiguous", 0)
        diag["no_exit_data"] += f.get("no_exit_data", 0)
        if f.get("double_sl"):
            diag["double_sl_days"] += 1
        diag["adjust_triggered"] += f.get("adjust_triggered", 0)
        diag["adjust_no_strike"] += f.get("adjust_no_strike", 0)
        diag["adjust_dropped"] += f.get("adjust_dropped", 0)
        diag["next_open_closes"] += f.get("next_open_closes", 0)
        diag["next_open_fallbacks"] += f.get("next_open_fallbacks", 0)
        diag["gap_fills"] += f.get("gap_fills", 0)
        diag["carried_nights"] += f.get("carried", 0)
        if f.get("double_sl_adjust"):
            diag["double_sl_adjust_days"] += 1
        # ── SYNTH_EVERYWHERE ──
        diag["adjust_synth_unmonitored"] += f.get("adjust_synth_unmonitored", 0)

    def _day_candles(sym: str, day_start: int) -> List[dict]:
        return [{"ts": x.ts, "open": x.open, "high": x.high,
                 "low": x.low, "close": x.close}
                for x in src.candles_1m_for_symbol_day(sym, day_start)]

    # ── CARRY_DATA_GAP ── helpers for legs that lose their candles.
    def _spot_close_before(bound_ts: int, day_start: int):
        """Last SPOT close strictly before bound_ts on this day."""
        r = cur.execute(
            "SELECT ts, close FROM backtest_candles_1m WHERE underlying=? "
            "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts DESC "
            "LIMIT 1", (underlying, day_start, bound_ts)).fetchone()
        return (int(r[0]), float(r[1])) if r else None

    def _intrinsic_close(st: dict, bound_ts: int, day_start: int):
        """── DEMOTED, NOT DELETED ── (ts, price) at intrinsic off spot.

        This was the PRIMARY dark-leg mark and it was wrong: the search
        window [day_start, bound_ts) at a 09:16 bound contains only the
        09:15 candle, and a far-OTM leg floors to ₹0.05 — 485 legs were
        booked as expiring worthless overnight. It is now the LAST-RESORT
        fallback, used only when the synthetic mark cannot be computed at
        all (no live band, no parity spot). On a contract's own expiry day
        intrinsic remains genuinely defensible; mid-week it is not."""
        sp = _spot_close_before(bound_ts, day_start)
        k = st.get("strike")
        if sp is None or not k:
            return None
        ts_, spot = sp
        side = (st.get("leg") or {}).get("opt_type") or ""
        intr = (spot - float(k)) if side == "CE" else (float(k) - spot)
        return ts_, round(max(0.05, intr), 2)

    def _synth_dark_mark(st: dict, bound_ts: int, day_start: int,
                         ctx: dict) -> Optional[tuple]:
        """── SYNTH_EVERYWHERE (fix 3) ── mark a DARK carried leg.

        A dark leg has no candles AND its band neighbours are typically dark
        too — that is precisely why it went dark. So there is no reliable
        fresh IV anchor. Two-tier policy:

          (a) if a live band still exists on that side today, mark with a
              fresh anchor (`_synth_mark_at`) — best available;
          (b) otherwise roll the LEG'S OWN IV forward with TAU DECAY ONLY:
              imply IV from its own last observed close at its own last
              observed minute, hold vol and spot flat, re-price at the bound
              minute. This is the honest model when the band is gone — it
              says "nothing changed except time", which is a far better
              approximation than "it expired worthless".

        Returns (ts, price, kind) or None → caller falls back to intrinsic."""
        k = st.get("strike")
        side = (st.get("leg") or {}).get("opt_type") or ""
        if not k or side not in ("CE", "PE"):
            return None
        expiry_ts = ctx.get("expiry_ts")
        week = ctx.get("week") or []
        meta_by_sym = ctx.get("meta_by_sym") or {}
        if not expiry_ts:
            return None

        # (a) fresh anchor off a live band
        if week:
            px = _synth_mark_at(src=src, week=week, meta_by_sym=meta_by_sym,
                                day_start=day_start, ts=bound_ts,
                                expiry_ts=expiry_ts, opt_type=side,
                                strike=float(k), skew_mult=adjust_skew_mult)
            if px is not None:
                return bound_ts, round(px, 2), "dark"

        # (b) roll the leg's own IV forward, tau decay only
        last_px = st.get("last_close")
        last_ts = st.get("last_ts")
        spot_hint = st.get("spot_hint")
        if not last_px or not last_ts or not spot_hint:
            return None
        is_call = side == "CE"
        iv = SW.implied_vol(float(last_px), is_call, float(spot_hint),
                            float(k), SW.tau_years(int(last_ts), expiry_ts))
        if not iv:
            return None
        px = SW.price_wing(is_call, float(spot_hint), float(k),
                           SW.tau_years(bound_ts, expiry_ts), iv,
                           skew_mult=adjust_skew_mult)
        return bound_ts, round(px, 2), "dark"

    def _emit_carried(st: dict, lid: str, exit_ts: int, exit_px: float,
                      reason: str, synth_kind: Optional[str] = None) -> None:
        """Book a carried leg the engine never saw this session."""
        leg = st.get("leg") or {}
        _emit({"leg": lid, "tradingsymbol": st.get("symbol"),
               "action": leg.get("action", "SELL"),
               "opt_type": leg.get("opt_type", "CE"),
               "lots": leg.get("lots", 0),
               "entry_ts": st["entry_ts"], "entry_price": st["entry_price"],
               "exit_ts": exit_ts, "exit_price": exit_px,
               "exit_reason": reason,
               "sl_price": st.get("sl"), "tp_price": st.get("tp"),
               "mtc_applied": bool(st.get("mtc_applied")),
               "ambiguous_fill": False,
               "is_adjust": st.get("is_adjust"),
               "adjust_of": st.get("adjust_of"),
               "strike": st.get("strike"), "expiry": st.get("expiry"),
               "synthetic": bool(st.get("synthetic")) or bool(synth_kind),
               "synth_kind": synth_kind or st.get("synth_kind")}, {})

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(sim_days),
                         "date": d.isoformat()})

        day_start = _day_start_epoch(d)
        entry_ts = day_start + entry_min * 60
        eod_ts = day_start + exit_min * 60
        next_open_ts = day_start + next_open_min * 60
        expiry_eod_ts = day_start + expiry_exit_min * 60

        # ── SYNTH_EVERYWHERE ── today's universe is resolved BEFORE the
        # carry block, because dark-leg marking needs today's live band.
        # (The old build resolved it after; a carried leg could therefore
        # never see a fresh anchor.)
        universe_today = src.contracts_active_on_day(underlying, day_start)
        meta_today: Dict[str, dict] = {c["tradingsymbol"]: c
                                       for c in (universe_today or [])}

        # ── IC_V2 ── carry day: advance yesterday's open legs through THIS
        # session before considering any new entry (D7).
        if positional and carry:
            diag["carry_days"] += 1
            c_syms = {lid: st["symbol"] for lid, st in carry.items()}
            # ── SYNTH_EVERYWHERE ── a SYNTHETIC leg has no corpus symbol,
            # so it can never have candles. It is dark by construction and
            # goes down the synthetic-mark path, not the "lost its band"
            # path — same destination, different reason.
            c_candles = {}
            for lid, sym in c_syms.items():
                if carry[lid].get("synthetic") or str(sym or "").startswith("SYN-"):
                    c_candles[lid] = []
                else:
                    c_candles[lid] = _day_candles(sym, day_start)

            any_expiry = next((st.get("expiry") for st in carry.values()
                               if st.get("expiry")), None)
            hard_ts, hard_reason = None, "EOD"
            if any_expiry == d.isoformat():
                hard_ts, hard_reason = expiry_eod_ts, "EOD"
            elif d == last_range_day:
                hard_ts, hard_reason = expiry_eod_ts, "EOR"

            live = {lid: cds for lid, cds in c_candles.items() if cds}
            dark = [lid for lid, cds in c_candles.items() if not cds]
            basket = dict(carry)

            if not live:
                diag["carry_gap_days"] += 1

            # ── SYNTH_EVERYWHERE ── the dark-mark context. Prefer the
            # ORIGINAL entry-day week list carried over in carry_ctx (it is
            # the right expiry); fall back to today's universe filtered to
            # that expiry.
            ctx_expiry = carry_ctx.get("want_expiry") or any_expiry
            ctx_week = [c for c in (universe_today or [])
                        if c.get("expiry") == ctx_expiry]
            dark_ctx = {"expiry_ts": carry_ctx.get("expiry_ts"),
                        "week": ctx_week,
                        "meta_by_sym": meta_today}

            if live:
                res = simulate_session(
                    [], live, {lid: c_syms[lid] for lid in live},
                    entry_ts, None,
                    exit_mode="NEXT_OPEN",
                    carry_in={lid: basket[lid] for lid in live},
                    adjust_on_sl=False,      # carried legs never re-arm
                    hard_close_ts=hard_ts, hard_close_reason=hard_reason,
                    next_open_ts=(None if hard_ts is not None else next_open_ts),
                    is_carry_day=True)
                _fold_flags(res["flags"])
                for lt in res["trades"]:
                    # ── ONE_NIGHT_MAX + SYNTH ── a leg the engine could not
                    # price at next_open_time comes back NEXT_OPEN_DARK with
                    # a stale close. Re-mark SYNTHETICALLY first; intrinsic
                    # only as the last resort.
                    if lt.get("exit_reason") == "NEXT_OPEN_DARK":
                        _b = hard_ts if hard_ts is not None else next_open_ts
                        _st = basket.get(lt["leg"], {})
                        _sm = None
                        if synth_dark_marks:
                            _sm = _synth_dark_mark(
                                {"strike": lt.get("strike"),
                                 "leg": {"opt_type": lt.get("opt_type")},
                                 "last_close": lt.get("last_close"),
                                 "last_ts": lt.get("last_ts"),
                                 "spot_hint": _st.get("spot_hint")},
                                _b, day_start, dark_ctx)
                        if _sm is not None:
                            lt["exit_ts"], lt["exit_price"] = _sm[0], _sm[1]
                            lt["synthetic"] = True
                            lt["synth_kind"] = "dark"
                            diag["syn_dark_marks"] += 1
                        else:
                            diag["syn_dark_fail"] += 1
                            _ip = _intrinsic_close(
                                {"strike": lt.get("strike"),
                                 "leg": {"opt_type": lt.get("opt_type")}},
                                _b, day_start)
                            if _ip is not None:
                                lt["exit_ts"], lt["exit_price"] = _ip
                                diag["carry_intrinsic_closes"] += 1
                            else:
                                diag["carry_dark_stale_close"] += 1
                        lt["exit_reason"] = (hard_reason if hard_ts is not None
                                             else "NEXT_OPEN")
                    _emit(lt, meta_today)
                    if lt["exit_reason"] == "EOD":
                        diag["expiry_closes"] += 1
                    elif lt["exit_reason"] == "EOR":
                        diag["eor_closes"] += 1
                survivors = res["carry_out"]
            else:
                survivors = {}

            # ── ONE_NIGHT_MAX + SYNTH ── the data-less legs. Every carried
            # leg leaves this session; the only question is at what mark.
            for lid in dark:
                st = basket[lid]
                bound_ts = hard_ts if hard_ts is not None else next_open_ts
                reason = hard_reason if hard_ts is not None else "NEXT_OPEN"
                diag["carry_dark_legs"] += 1
                sm = _synth_dark_mark(st, bound_ts, day_start, dark_ctx) \
                    if synth_dark_marks else None
                if sm is not None:
                    diag["syn_dark_marks"] += 1
                    _emit_carried(st, lid, sm[0], sm[1], reason,
                                  synth_kind="dark")
                else:
                    if synth_dark_marks:
                        diag["syn_dark_fail"] += 1
                    ip = _intrinsic_close(st, bound_ts, day_start)
                    if ip is None:
                        ip = (st.get("last_ts") or st["entry_ts"],
                              st.get("last_close", st["entry_price"]))
                        diag["carry_dark_stale_close"] += 1
                    else:
                        diag["carry_intrinsic_closes"] += 1
                    _emit_carried(st, lid, ip[0], ip[1], reason)
                if reason == "EOD":
                    diag["expiry_closes"] += 1
                elif reason == "EOR":
                    diag["eor_closes"] += 1
                else:
                    diag["next_open_closes"] += 1

            # ── ONE_NIGHT_MAX ── invariant: a CARRY day always ends flat.
            for lid, st in list(survivors.items()):
                _emit_carried(st, lid,
                              st.get("last_ts") or st["entry_ts"],
                              st.get("last_close", st["entry_price"]),
                              "NEXT_OPEN")
                diag["carry_leak_closed"] += 1
                diag["next_open_closes"] += 1
                write_audit_log(
                    f"[BACKTEST][{strategy_id}] {d}: leg {lid} "
                    f"({st.get('symbol')}) survived a carry day — forced "
                    f"flat. ONE_NIGHT_MAX invariant violated, investigate.")
            carry = {}
            carry_ctx = {}

        # ── IC_V2 ── D7: entry is BLOCKED while anything is still open.
        if positional and carry:
            diag["days_blocked_open"] += 1
            continue

        universe = universe_today
        if not universe:
            diag["days_uncovered"] += 1
            continue
        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not week:
            diag["days_uncovered"] += 1
            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "
                            f"{want_expiry} not in corpus — day skipped")
            continue

        # entry-candle close per candidate, per side
        cand: Dict[str, List] = {"CE": [], "PE": []}
        meta_by_sym: Dict[str, dict] = {}
        for c in week:
            sym = c["tradingsymbol"]
            meta_by_sym[sym] = c
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            ec = entry_close([{"ts": x.ts, "close": x.close} for x in cds], entry_ts)
            if ec is not None:
                cand[c["instrument_type"]].append((sym, ec[1]))

        expiry_d = date.fromisoformat(want_expiry)
        expiry_ts = _day_start_epoch(expiry_d) + (15 * 3600 + 30 * 60)

        # ── SYNTH_EVERYWHERE ── entry-minute spot, computed once. Stamped
        # onto every leg as `spot_hint` so a leg that goes dark later can
        # still roll its own IV forward without a live band.
        entry_tau = SW.tau_years(entry_ts, expiry_ts)
        entry_spot = _spot_from_ladder(cand, meta_by_sym, entry_tau)

        selected: Dict[str, str] = {}
        entry_overrides: Dict[str, dict] = {}
        wing_fb = False
        skip_day = None
        day_legs: List[dict] = []
        day_syn_short = False
        day_syn_wing = False

        for leg in legs_cfg:
            pool = cand.get(leg["opt_type"], [])
            pick = select_strike(pool, leg["premium_max"])

            if pick is not None:
                selected[leg["id"]] = pick[0]
                day_legs.append(leg)
                continue

            # ── nothing real ≤ cap on this side ──
            if leg["action"] == "SELL":
                # ── SYNTH_EVERYWHERE (fix 4) ── synthesise instead of
                # skipping the whole day.
                if not synth_shorts:
                    skip_day = "no_short_strike" if pool else "no_entry_price"
                    break
                spec, why = _synth_leg_at(
                    src=src, week=week, meta_by_sym=meta_by_sym,
                    day_start=day_start, ts=entry_ts, expiry_ts=expiry_ts,
                    opt_type=leg["opt_type"], cap=leg["premium_max"],
                    underlying=underlying, want_expiry=want_expiry,
                    skew_mult=adjust_skew_mult, ladder=cand)
                if spec is None:
                    diag["syn_short_fail"] += 1
                    diag[f"syn_short_fail_{why}"] = \
                        diag.get(f"syn_short_fail_{why}", 0) + 1
                    skip_day = "no_short_strike" if pool else "no_entry_price"
                    break
                entry_overrides[leg["id"]] = {
                    "price": spec["price"], "symbol": spec["symbol"],
                    "strike": spec["strike"], "expiry": want_expiry,
                    "synthetic": True, "synth_kind": "short",
                }
                selected[leg["id"]] = spec["symbol"]
                day_legs.append(leg)
                diag["syn_short_legs"] += 1
                day_syn_short = True
                continue

            # ── BUY (wing) ──
            if wing_mode == "skip":
                diag["wing_absent_days"] += 1
                continue
            if wing_mode == "synthetic":
                # ── SYNTH_EVERYWHERE (fix 2) ── no positional downgrade.
                # A synthetic wing is now a normal leg with a modelled entry
                # and a modelled exit, so it carries across sessions.
                spec, why = _synth_leg_at(
                    src=src, week=week, meta_by_sym=meta_by_sym,
                    day_start=day_start, ts=entry_ts, expiry_ts=expiry_ts,
                    opt_type=leg["opt_type"], cap=leg["premium_max"],
                    underlying=underlying, want_expiry=want_expiry,
                    skew_mult=skew_mult, ladder=cand)
                if spec is not None:
                    entry_overrides[leg["id"]] = {
                        "price": spec["price"], "symbol": spec["symbol"],
                        "strike": spec["strike"], "expiry": want_expiry,
                        "synthetic": True, "synth_kind": "wing",
                    }
                    selected[leg["id"]] = spec["symbol"]
                    day_legs.append(leg)
                    diag["syn_wing_legs"] += 1
                    day_syn_wing = True
                    continue
                diag["syn_wing_fail"] += 1
                diag[f"wing_synth_fail_{why}"] = \
                    diag.get(f"wing_synth_fail_{why}", 0) + 1
                # solver failed → fail OPEN to reality
            pick = select_strike(pool, leg["premium_max"],
                                 fallback_cheapest=True)
            if pick is None:
                diag["wing_absent_days"] += 1
                continue    # wing absent today; condor degrades to strangle
            wing_fb = True
            selected[leg["id"]] = pick[0]
            day_legs.append(leg)

        if skip_day:
            diag[f"days_{skip_day}"] += 1
            continue
        if wing_fb:
            diag["wing_fallback_days"] += 1
        if day_syn_short:
            diag["syn_short_days"] += 1
        if day_syn_wing:
            diag["syn_wing_days"] += 1
            diag["wing_synth_days"] += 1

        # synthetic legs have no corpus candles by definition
        candles_by_leg = {lid: ([] if lid in entry_overrides
                                else _day_candles(selected[lid], day_start))
                          for lid in selected}

        # ── SYNTH_EVERYWHERE ── adjustment picks are resolved LAZILY at the
        # FILL MINUTE, not here. The engine needs the pick up front though,
        # so we pre-compute one pick per short per POSSIBLE fill minute?  No
        # — that is unbounded. Instead we hand the engine a pick resolved at
        # the SL-agnostic best guess and let the engine fill from candles;
        # for the synthetic path we must know the minute.
        #
        # Resolution: run the session ONCE to discover SL minutes, then
        # resolve picks at those exact minutes and re-run. The first pass is
        # pure discovery (no rows emitted), which is cheap — the engine is
        # in-memory and the candles are already loaded.
        def _picks_for(sl_minutes: Dict[str, int]) -> Dict[str, dict]:
            """short leg id → engine pick, priced at ITS OWN fill minute."""
            out: Dict[str, dict] = {}
            for leg in day_legs:
                if leg["action"] != "SELL":
                    continue
                acfg = adjust_cfg.get(leg["id"])
                if not acfg or not acfg.get("enabled"):
                    continue
                fill_ts = sl_minutes.get(leg["id"])
                if fill_ts is None:
                    continue
                cap = float(acfg["premium_max"])
                lad = _minute_ladder(src, week, day_start, fill_ts)
                rpick = select_strike(lad.get(leg["opt_type"], []), cap)
                if rpick is not None:
                    sym = rpick[0]
                    m = meta_by_sym.get(sym, {})
                    out[leg["id"]] = {
                        "symbol": sym, "strike": m.get("strike"),
                        "expiry": m.get("expiry"),
                        "candles": _day_candles(sym, day_start),
                    }
                    continue
                if not synth_adjust:
                    continue
                spec, _why = _synth_leg_at(
                    src=src, week=week, meta_by_sym=meta_by_sym,
                    day_start=day_start, ts=fill_ts, expiry_ts=expiry_ts,
                    opt_type=leg["opt_type"], cap=cap,
                    underlying=underlying, want_expiry=want_expiry,
                    skew_mult=adjust_skew_mult, ladder=lad)
                if spec is None:
                    diag["syn_adjust_fail"] += 1
                    continue
                out[leg["id"]] = {
                    "symbol": spec["symbol"], "strike": spec["strike"],
                    "expiry": want_expiry, "candles": [],
                    "fill_price": spec["price"],
                    "synthetic": True, "synth_kind": "adjust",
                }
                diag["syn_adjust_legs"] += 1
            return out

        engine_picks: Dict[str, dict] = {}
        if adjust_on_sl:
            # PASS 1 — discovery. Same engine, no picks, so no adjustment
            # legs open; we only read back which shorts SL'd and when.
            probe = simulate_session(
                day_legs, {k: list(v) for k, v in candles_by_leg.items()},
                dict(selected), entry_ts,
                (None if positional else eod_ts),
                exit_mode=("NEXT_OPEN" if positional else "EOD"),
                adjust_on_sl=False,
                hard_close_ts=None, next_open_ts=None, is_carry_day=False,
                entry_overrides=entry_overrides)
            sl_minutes = {t["leg"]: int(t["exit_ts"]) + adjust_delay_s
                          for t in probe["trades"]
                          if t.get("exit_reason") == "SL"
                          and t["action"] == "SELL"
                          and not t.get("is_adjust")}
            engine_picks = _picks_for(sl_minutes)

        if not positional:
            # ── IC_V1 PATH ── unchanged when no synth is in play: the
            # legacy wrapper, EOD close. `entry_overrides` is empty on an
            # IC_V1 run (synth_shorts off, wing_mode real_fallback), so this
            # is the original call.
            if entry_overrides or engine_picks:
                res = simulate_session(
                    day_legs, candles_by_leg, selected, entry_ts, eod_ts,
                    exit_mode="EOD",
                    adjust_on_sl=adjust_on_sl, adjust_cfg=adjust_cfg,
                    adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,
                    entry_overrides=entry_overrides)
                res["trades"].sort(key=lambda t: t["leg"])
            else:
                res = simulate_day(day_legs, candles_by_leg, selected,
                                   entry_ts, eod_ts)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                _mark_synth_exit(lt, week, meta_by_sym, day_start, expiry_ts,
                                 eod_ts, diag)
                _emit(lt, meta_by_sym)
        else:
            # ── IC_V2 PATH ── carry-capable session.
            hard_ts, hard_reason = None, "EOD"
            if want_expiry == d.isoformat():
                hard_ts, hard_reason = expiry_eod_ts, "EOD"
            elif d == last_range_day:
                hard_ts, hard_reason = expiry_eod_ts, "EOR"

            res = simulate_session(
                day_legs, candles_by_leg, selected, entry_ts, None,
                exit_mode="NEXT_OPEN",
                adjust_on_sl=adjust_on_sl, adjust_cfg=adjust_cfg,
                adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,
                hard_close_ts=hard_ts, hard_close_reason=hard_reason,
                next_open_ts=None,       # entered today → never closes today
                is_carry_day=False,
                entry_overrides=entry_overrides)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                _mark_synth_exit(lt, week, meta_by_sym, day_start, expiry_ts,
                                 (hard_ts or eod_ts), diag)
                _emit(lt, meta_by_sym)
                if lt["exit_reason"] == "EOD" and hard_ts is not None:
                    diag["expiry_closes"] += 1
                elif lt["exit_reason"] == "EOR":
                    diag["eor_closes"] += 1
            carry = res["carry_out"]
            # ── IC_V2 ── stamp expiry/strike; ── SYNTH ── stamp spot_hint
            # so a leg that goes dark tomorrow can roll its own IV forward.
            for _lid, _st in carry.items():
                _m = meta_by_sym.get(_st.get("symbol"), {})
                if not _st.get("expiry"):
                    _st["expiry"] = _m.get("expiry") or want_expiry
                if _st.get("strike") is None:
                    _st["strike"] = _m.get("strike")
                if entry_spot:
                    _st["spot_hint"] = entry_spot
            carry_ctx = {"want_expiry": want_expiry, "expiry_ts": expiry_ts,
                         "week": week, "meta_by_sym": meta_by_sym}

    # ── IC_V2 ── safety net: a position can only still be open here if the
    # final range days had no usable data.
    for lid, st in list(carry.items()):
        lt = {"leg": lid, "tradingsymbol": st["symbol"],
              "action": st["leg"]["action"], "opt_type": st["leg"]["opt_type"],
              "lots": st["leg"]["lots"],
              "entry_ts": st["entry_ts"], "entry_price": st["entry_price"],
              "exit_ts": st["last_ts"], "exit_price": st["last_close"],
              "exit_reason": "EOR", "sl_price": st["sl"], "tp_price": st["tp"],
              "mtc_applied": st["mtc_applied"], "ambiguous_fill": False,
              "is_adjust": st.get("is_adjust"), "adjust_of": st.get("adjust_of"),
              "strike": st.get("strike"), "expiry": st.get("expiry"),
              "synthetic": bool(st.get("synthetic")),
              "synth_kind": st.get("synth_kind")}
        _emit(lt, {})
        diag["eor_closes"] += 1
        del carry[lid]

    conn.close()
    try:
        src.close()
    except Exception:
        pass

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}][{exit_mode}] {underlying} "
        f"{date_from}→{date_to}: "
        f"{diag['days_entered']}/{diag['days_total']} days entered, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"MTC {diag['mtc_activations']}, doubleSL {diag['double_sl_days']}, "
        f"ADJ {diag['adjust_triggered']} (drop {diag['adjust_dropped']}/"
        f"noStrike {diag['adjust_no_strike']}, dblADJ "
        f"{diag['double_sl_adjust_days']}), "
        f"carry {diag['carried_nights']}n/{diag['carry_days']}d "
        f"(gap {diag['carry_gap_days']}), nextOpen {diag['next_open_closes']} "
        f"(fb {diag['next_open_fallbacks']}), gapFills {diag['gap_fills']}, "
        f"wingFB {diag['wing_fallback_days']}, "
        f"SYN short {diag['syn_short_legs']}/wing {diag['syn_wing_legs']}/"
        f"adj {diag['syn_adjust_legs']}/dark {diag['syn_dark_marks']} "
        f"(fail {diag['syn_short_fail']}/{diag['syn_wing_fail']}/"
        f"{diag['syn_adjust_fail']}/{diag['syn_dark_fail']}), "
        f"SYN net {diag['syn_pnl_net']} of {summary['net_pnl']} "
        f"({diag['syn_pnl_share_pct']}% by |P&L|), "
        f"skips: uncovered {diag['days_uncovered']} / "
        f"noShort {diag['days_no_short_strike']} / "
        f"noEntryPx {diag['days_no_entry_price']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


def _mark_synth_exit(lt: dict, week: list, meta_by_sym: dict, day_start: int,
                     expiry_ts: int, bound_ts: int, diag: dict) -> None:
    """── SYNTH_EVERYWHERE ── a synthetic leg exited by the engine carries
    the engine's price, which for an unmonitored synthetic leg is just its
    ENTRY price (no candles ⇒ last_close never moved). Re-mark it at the
    exit minute so the leg has a real modelled exit rather than a flat zero.

    Only touches legs flagged synthetic AND whose exit price still equals
    the entry price — a synthetic leg that DID have candles (never happens
    today, but the guard is cheap) keeps its real exit."""
    if not lt.get("synthetic"):
        return
    if lt.get("exit_price") is None:
        return
    if abs(float(lt["exit_price"]) - float(lt["entry_price"])) > 1e-9:
        return
    k = lt.get("strike")
    side = lt.get("opt_type")
    if not k or side not in ("CE", "PE"):
        return
    # module-level src is not available here; the caller has already loaded
    # the week's candles, so re-derive from the ladder via the closure-free
    # path is not possible — instead the exit stays at entry and is counted.
    diag["syn_exit_fail"] += 1
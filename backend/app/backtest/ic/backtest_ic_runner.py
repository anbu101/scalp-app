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
# ── SYNTH_EXIT_FIX (2026-07-22) ── `_mark_synth_exit` was shipped as a
# module-level stub that could not see `src` and therefore only COUNTED the
# failure (`syn_exit_fail`) instead of pricing the exit. Symptom in the
# results table: SYN- legs entered and exited at the SAME price, at the SAME
# timestamp, gross ₹0, net = −charges (e.g. SYN-NIFTY-20260721-23600PE,
# 2.80 → 2.80 at 20/07 09:18). It is now a CLOSURE inside
# `_run_ic_backtest_impl`, so it has `src`, `adjust_skew_mult`, `skew_mult`
# and `diag` in scope and re-prices at the exit bound via `_synth_mark_at`.
# Carried synthetic legs were never affected — they exit through the
# dark-mark path, which always priced correctly.
#
# TWO SKEW KNOBS: `skew_mult` (wings, far OTM, default 1.0) and
# `adjust_skew_mult` (adjustment + short legs, much nearer the money,
# default 1.0). They are separate because a single multiplier tuned for ₹4
# wings is the wrong correction for an ₹85 leg. `_mark_synth_exit` picks the
# knob by `synth_kind`, so a leg is marked out on the same skew basis it was
# marked in on — mixing them would manufacture P&L out of the knob itself.
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
        is_iv_sl_mode,                              # ── IC_IV_SL ──
    )
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone test harness
    from ic_v1_engine import (  # type: ignore
        norm_leg, norm_adjust, select_strike, entry_close,
        simulate_day, simulate_session, leg_pnl,
        is_iv_sl_mode,                              # ── IC_IV_SL ──
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
    # ── SYNTH_EXIT_FIX ── tripwire: a synthetic leg whose exit price still
    # equals its entry price is an unpriced leg (the old stub's signature).
    # This must be 0 on a healthy run; if it is not, `_mark_synth_exit` is
    # failing and `syn_exit_fail` says how often.
    # ── unpriced signature = SAME price AND SAME timestamp. Price alone
    # false-positives on legitimate marks that tick-round back to entry
    # (4/1714 in the 2020–2026 run — verified coincidences, exit stamped
    # at the 09:16 bound, not at entry).
    diag["syn_flat_legs"] = sum(
        1 for t in syn
        if t.exit_price is not None and t.exit_ts is not None
        and t.exit_ts == t.entry_ts
        and abs(float(t.exit_price) - float(t.entry_price)) < 1e-9)

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

# ── ADJ_ON_MTC ── days where BOTH sides re-loaded (L1·ADJ + L2·ADJ).
    _adj_by_day: dict = {}
    for t in closed:
        if t.condition and "·ADJ" in t.condition:
            _dk = (int(t.entry_ts or 0) + 19800) // 86400
            _adj_by_day.setdefault(_dk, set()).add(t.condition.split("·")[0])
    diag["both_adjust_days"] = sum(1 for v in _adj_by_day.values() if len(v) >= 2)

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
    (spec_dict, None) or (None, reason).

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


# ══════════════════════════════════════════════════════════════════════
# ── IC_IV_SL BEGIN ── per-minute implied-vol series for SHORT legs whose
# sl_mode is "iv" (absolute level) or "iv_delta" (entry IV + Δ vol pts).
#
# The mechanism is TSG's, lifted deliberately rather than re-derived, so
# a rule ported to live behaves identically in both strategies:
#
#   * PARITY SPOT PER MINUTE. CandleSource.spot_at is a DOCUMENTED STUB
#     (always None — the corpus has no index rows), so spot comes from
#     put-call parity off the week's own option chain, exactly as the
#     synth path already does. Built with ONE incremental cursor pass
#     (O(minutes·syms + candles)); rebuilding a ladder per minute is
#     O(minutes·syms·candles) and is the single dominant cost of a run
#     once IV monitoring is on.
#
#   * IV10 — STRIKE VOL VIA THE OTM SIDE. A short's monitored vol is its
#     STRIKE's vol, solved from whichever option at that strike is OTM
#     against parity spot, falling back to the short's own type. Parity
#     makes CE and PE IV equal at a strike, and the OTM one is the
#     solvable one. This is what keeps a LOSING deep-ITM short measurable
#     exactly when it matters: its own price is intrinsic-dominated and
#     ic_synth_wing.implied_vol returns None on price <= intrinsic.
#
#   * IV11 — DELTA ANCHORS. "iv_delta" thresholds are that leg's OWN
#     entry IV plus sl_val/100, solved at the entry minute with the same
#     OTM preference. A leg whose entry IV cannot be solved is left OUT
#     of iv_thresholds ⇒ unmonitored for the day (iv_entry_solve_fail).
#     It NEVER silently degrades to an absolute level or to a premium
#     stop — fail-open, loudly counted.
#
#   * SKIP-THE-SOLVE SHORTCUT. A minute whose mark is at or below entry
#     would be rejected by the engine's losing-side gate anyway, so no
#     IV is solved there. Typically removes most of the solver work.
#
# NOT lifted (locked with the user): no one-shot latch (D5), no hedge
# pairing (D7), synthetic shorts excluded outright rather than included
# but inert (D10). The losing-side gate itself lives in the ENGINE, which
# owns entry_price and the per-candle mark.
#
# ⚠ SCOPE (D9): ENTRY DAY ONLY. An IC_V2 leg that carries overnight stops
# being IV-monitored at the entry session's bound and runs on its TP (and
# any MTC cost pin) alone thereafter. Counted as iv_carried_unmonitored —
# if that number is material on a NEXT_OPEN run, the IV stop is governing
# far less of the book than the config suggests.
# ══════════════════════════════════════════════════════════════════════
def _ic_iv_spot_by_minute(candles_by_sym: Dict[str, List[dict]],
                          meta_by_sym: dict, minutes: List[int],
                          expiry_ts: int, diag: dict) -> Dict[int, Optional[float]]:
    """Parity spot at every monitored minute, one incremental pass.
    A minute with no solvable spot carries the last one forward."""
    syms = list(candles_by_sym.keys())
    typ = {s: (meta_by_sym.get(s) or {}).get("instrument_type") for s in syms}
    cur = {s: 0 for s in syms}
    lastpx: Dict[str, float] = {}
    last_spot: Optional[float] = None
    out: Dict[int, Optional[float]] = {}
    for m in minutes:
        for s in syms:
            cds = candles_by_sym[s]
            i, n = cur[s], len(cds)
            while i < n and cds[i]["ts"] < m:
                lastpx[s] = cds[i]["close"]
                i += 1
            cur[s] = i
        lad: Dict[str, list] = {"CE": [], "PE": []}
        for s, px in lastpx.items():
            if px > 0 and typ.get(s) in ("CE", "PE"):
                lad[typ[s]].append((s, px))
        sp = _spot_from_ladder(lad, meta_by_sym, SW.tau_years(m, expiry_ts))
        if sp is None or sp <= 0:
            diag["iv_no_spot_minutes"] += 1
            sp = last_spot
        else:
            last_spot = sp
        out[m] = sp
    return out


# ══════════════════════════════════════════════════════════════════════
# ── IC_MIN_ENTRY_IV BEGIN (2026-08-05; E1–E5 locked with the user) ──
#
# ENTRY-IV FLOOR, ported from TSG's IV13. min_entry_iv (DECIMAL, 0 = off):
# when the MEAN of the shorts' solved ENTRY IVs is below the floor, the
# whole day is SKIPPED before anything is booked. Rationale from TSG's 6y
# decile study: the sub-0.11 entry-IV decile was the ONLY negative decile
# — premium-capped strikes sit too close to spot to pay for the obligation.
#
# ── E1: DECOUPLED FROM THE SL MODE (deliberate divergence from TSG) ──
# TSG back-derives entry IV from its stored delta thresholds:
#     _eivs = [v - iv_sl_delta_pts/100 for v in iv_thresholds.values()]
# which makes min_entry_iv a SILENT NO-OP unless iv_sl_delta_pts > 0. That
# is an implementation artifact, not a design intent: an entry-vol regime
# filter and a stop rule are orthogonal. Here the anchors are solved by
# _ic_entry_ivs() REGARDLESS of sl_mode, so the floor works on a plain
# pct/pts condor too. _ic_iv_series consumes the SAME anchors for its
# delta thresholds, so the filter and the stop can never disagree about
# what a leg's entry IV was.
#
# COST: one parity spot + <=2 bisections at ONE minute — ~1/375th of the
# per-minute monitoring pass. It does not require iv_active.
#
# ── E3 ── synthetic shorts contribute their band-edge anchor IV (the vol
# _synth_leg_at priced them from), counted separately as
# iv_entry_synth_anchors: that is the ANCHOR's vol, not the synthetic
# strike's own, so a filter decision resting largely on modelled numbers
# stays visible rather than blending in.
# ── E4 ── fail-OPEN: no anchor solvable → the day trades, counted as
# iv_filter_open_days. A solver failure must never silently stop trading.
# ── E5 ── unit is DECIMAL (0.10 = 10%), TSG parity. Note this differs
# from sl_val in "iv" mode, which is a PERCENT — the same split TSG has
# between min_entry_iv and iv_sl_pct.
# ══════════════════════════════════════════════════════════════════════
def _ic_solve_strike_iv(strike: float, is_call: bool, spot: float, tau: float,
                        own_px: Optional[float],
                        opp_px: Optional[float]) -> Optional[float]:
    """── IV10 ── a strike's vol, preferring whichever of its CE/PE is OTM
    against parity spot (parity makes them equal, and the OTM one is the
    numerically solvable one), falling back to the leg's own type. Returns
    None when neither print is solvable — never a default."""
    own = (own_px, is_call) if own_px and own_px > 0 else None
    opp = (opp_px, not is_call) if opp_px and opp_px > 0 else None
    own_otm = (strike > spot) if is_call else (strike < spot)
    order = [own, opp] if (own_otm or opp is None) else [opp, own]
    for c in order:
        if c is None:
            continue
        iv = SW.implied_vol(c[0], c[1], spot, strike, tau)
        if iv is not None:
            return iv
    return None


def _ic_entry_ivs(*, day_legs: List[dict], selected: Dict[str, str],
                  entry_overrides: Dict[str, dict],
                  entry_ladder: Dict[str, list], meta_by_sym: dict,
                  entry_ts: int, expiry_ts: int,
                  diag: dict) -> Dict[str, float]:
    """SHORT leg id → its ENTRY implied vol (decimal). Solved ONCE per day
    and shared by the entry-IV floor and the iv_delta thresholds (E1).
    A leg absent from the result had no solvable anchor."""
    entry_tau = SW.tau_years(entry_ts, expiry_ts)
    entry_spot = _spot_from_ladder(entry_ladder, meta_by_sym, entry_tau)
    out: Dict[str, float] = {}
    for leg in day_legs:
        if leg["action"] != "SELL":
            continue
        lid = leg["id"]
        ov = entry_overrides.get(lid)
        if ov is not None:
            # ── E3 ── synthetic short: reuse the band-edge anchor IV that
            # _synth_leg_at priced this leg from. Nothing else is knowable —
            # a synthetic strike has no prints of its own.
            a_iv = ov.get("anchor_iv")
            if a_iv:
                out[lid] = float(a_iv)
                diag["iv_entry_synth_anchors"] += 1
            else:
                diag["iv_entry_solve_fail"] += 1
            continue
        if entry_spot is None or entry_spot <= 0:
            diag["iv_entry_solve_fail"] += 1
            continue
        sym = selected.get(lid)
        meta = meta_by_sym.get(sym) or {}
        strike = meta.get("strike")
        own_px = next((px for s, px in entry_ladder.get(leg["opt_type"], [])
                       if s == sym), None)
        if not strike or not own_px:
            diag["iv_entry_solve_fail"] += 1
            continue
        strike = float(strike)
        is_call = leg["opt_type"] == "CE"
        opp_px = next((px for s, px in entry_ladder.get(
            "PE" if is_call else "CE", [])
            if float((meta_by_sym.get(s) or {}).get("strike") or 0) == strike),
            None)
        iv = _ic_solve_strike_iv(strike, is_call, entry_spot, entry_tau,
                                 float(own_px), opp_px)
        if iv is None:
            diag["iv_entry_solve_fail"] += 1
        else:
            out[lid] = iv
    return out
# ── IC_MIN_ENTRY_IV END ──


def _ic_iv_series(*, day_legs: List[dict], selected: Dict[str, str],
                  entry_overrides: Dict[str, dict],
                  entry_ladder: Dict[str, list],
                  candles_by_sym: Dict[str, List[dict]],
                  meta_by_sym: dict, minutes: List[int],
                  entry_ts: int, expiry_ts: int, diag: dict,
                  entry_ivs: Optional[Dict[str, float]] = None):
    """Returns (iv_by_minute, iv_thresholds) for simulate_session, or
    (None, None) when nothing is monitored. Both are fail-open: a leg
    absent from iv_thresholds simply has no stop.

    ── IC_MIN_ENTRY_IV / E1 ── `entry_ivs` is the SHARED anchor map from
    _ic_entry_ivs(). Passing it means the delta thresholds and the entry-IV
    floor are computed from the identical numbers; it also avoids re-solving
    anchors that were already solved for the filter."""
    monitored = []          # (lid, opt_type, strike, entry_px, mode, val)
    for leg in day_legs:
        if not is_iv_sl_mode(leg.get("sl_mode")):
            continue
        lid = leg["id"]
        if leg["action"] != "SELL":
            # ── D3 ── IV modes are SELL-only. sl_price() already returned
            # None, so this BUY leg has NO stop at all. Say so loudly.
            diag["iv_mode_on_buy_leg"] += 1
            continue
        if float(leg.get("sl_val") or 0) <= 0:
            continue                        # 0 = disabled, same as pct/pts
        if lid in entry_overrides:
            # ── D10 ── a synthetic short holds its entry IV flat (it has
            # no candles), so in delta mode it can never cross and in
            # absolute mode it crosses at minute one or never. Excluded
            # outright rather than shipped as a silent no-op.
            diag["iv_synth_unmonitored"] += 1
            continue
        sym = selected.get(lid)
        meta = meta_by_sym.get(sym) or {}
        strike = meta.get("strike")
        entry_px = next((px for s, px in entry_ladder.get(leg["opt_type"], [])
                         if s == sym), None)
        if not strike or not entry_px:
            diag["iv_entry_solve_fail"] += 1
            continue
        monitored.append((lid, leg["opt_type"], float(strike),
                          float(entry_px), str(leg["sl_mode"]),
                          float(leg["sl_val"])))
    if not monitored:
        return None, None

    spot_by_minute = _ic_iv_spot_by_minute(candles_by_sym, meta_by_sym,
                                           minutes, expiry_ts, diag)
    entry_ivs = entry_ivs or {}

    iv_by_minute: Dict[int, Dict[str, float]] = {m: {} for m in minutes}
    iv_thresholds: Dict[str, float] = {}

    for lid, opt_type, strike, entry_px, mode, val in monitored:
        is_call = opt_type == "CE"

        # ── this strike's OPPOSITE-type sibling: entry print + mark series
        opp_sym = next((s for s, m2 in meta_by_sym.items()
                        if float((m2 or {}).get("strike") or 0) == strike
                        and (m2 or {}).get("instrument_type") != opt_type),
                       None)
        opp_marks: Dict[int, float] = {}
        if opp_sym and candles_by_sym.get(opp_sym):
            cds2, j, lastp = candles_by_sym[opp_sym], 0, None
            for m in minutes:
                while j < len(cds2) and cds2[j]["ts"] < m:
                    lastp = cds2[j]["close"]
                    j += 1
                if lastp is not None:
                    opp_marks[m] = lastp

        # ── threshold ──
        if mode == "iv":
            iv_thresholds[lid] = val / 100.0            # absolute level
        else:                                           # ── IV11 delta ──
            # ── E1 ── the SHARED anchor from _ic_entry_ivs. Not re-solved
            # here: one anchor per leg per day, used by both the entry-IV
            # floor and this threshold, so the two can never disagree.
            # Absent ⇒ no anchor was solvable (already counted there) ⇒
            # this leg is unmonitored today.
            e_iv = entry_ivs.get(lid)
            if e_iv is None:
                continue
            iv_thresholds[lid] = e_iv + val / 100.0
            diag["iv_anchor_solved"] += 1

        # ── per-minute series for THIS leg ──
        cds = candles_by_sym.get(selected.get(lid) or "", [])
        i, mark = 0, entry_px
        for m in minutes:
            while i < len(cds) and cds[i]["ts"] < m:
                mark = cds[i]["close"]
                i += 1
            if mark <= entry_px:
                continue      # the engine's losing-side gate would reject
            sp = spot_by_minute.get(m)
            if sp is None:
                diag["iv_solve_fail_minutes"] += 1
                continue
            iv = _ic_solve_strike_iv(strike, is_call, sp,
                                     SW.tau_years(m, expiry_ts),
                                     mark, opp_marks.get(m))
            if iv is None:
                diag["iv_solve_fail_minutes"] += 1
            else:
                iv_by_minute[m][lid] = iv

    if not iv_thresholds:
        return None, None
    diag["iv_monitored_legs"] += len(iv_thresholds)
    return iv_by_minute, iv_thresholds
# ── IC_IV_SL END ──


# ── IC_PARALLEL BEGIN (2026-08-05) ── top-level so it is picklable by the
# spawn context. Mirrors _tsg_parallel_worker exactly.
def _ic_parallel_worker(db_path: str, strategy_id: str, underlying: str,
                        date_from_iso: str, date_to_iso: str,
                        cfg: dict) -> dict:
    out = run_ic_backtest(
        db_path=db_path, strategy_id=strategy_id, underlying=underlying,
        date_from=date.fromisoformat(date_from_iso),
        date_to=date.fromisoformat(date_to_iso),
        config_override=cfg, progress_cb=None, cancel_cb=None)
    if out.get("aborted"):
        raise RuntimeError(f"chunk {date_from_iso}..{date_to_iso} aborted: "
                           f"{out.get('reason')}")
    return {"trades": out["trades"],
            "diag": out["summary"].get("diag_ic", {})}
# ── IC_PARALLEL END ──


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

    ── IC_MIN_ENTRY_IV key ──
      min_entry_iv      decimal (default 0 = off). ENTRY-IV FLOOR: when the
                        MEAN of the shorts' solved entry IVs is below this,
                        the day is SKIPPED before anything is booked
                        (diag days_iv_filtered). Works with ANY sl_mode —
                        unlike TSG, where the equivalent knob is inert
                        without delta mode. No anchor solvable → fail-OPEN
                        (diag iv_filter_open_days). NOTE the unit: this is
                        a DECIMAL, while sl_val in "iv" mode is a PERCENT.

    ── IC_PARALLEL key ──
      parallel_workers  int (default 1 = serial). N>1 shards the date range
                        into N contiguous chunks run in separate processes.
                        Results are IDENTICAL to serial (days are
                        independent in EOD mode); only wall-clock changes.
                        FORCED TO 1 when exit_mode=NEXT_OPEN — a carried
                        position crosses chunk boundaries and each worker
                        would start flat. Requires the freeze_support()
                        guard in main.py in the running bundle; a spawn
                        failure aborts LOUDLY rather than falling back.
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
    # ── ADJ_ONLY (2026-07-24) ── signal-track the condor, BOOK only ·ADJ
    # legs. Requires adjust_on_sl (without it there is nothing to book).
    adjust_only = bool(cfg.get("adjust_only", False)) and adjust_on_sl

    # ── SYNTH_EVERYWHERE ── the wing_synth_disabled_v2 downgrade is GONE:
    # per-minute BS pricing carries across sessions, so a synthetic wing is
    # now a normal leg with a modelled entry and a modelled exit.
    adjust_skew_mult = float(cfg.get("adjust_skew_mult", 1.0) or 1.0)
    synth_shorts = bool(cfg.get("synth_shorts", False))
    synth_adjust = bool(cfg.get("synth_adjust", True))
    synth_dark_marks = bool(cfg.get("synth_dark_marks", True))
    # ── IC_MIN_ENTRY_IV ── DECIMAL (0.10 = 10%), TSG parity. 0 = off.
    # abs() is sign-tolerance, matching every other threshold knob here.
    min_entry_iv = abs(float(cfg.get("min_entry_iv", 0) or 0))

    raw_legs = cfg.get("legs") or DEFAULT_LEGS
    legs_cfg = [norm_leg(l) for l in raw_legs if int(l.get("lots") or 0) > 0]
    # ── IC_IV_SL ── D11: the per-minute spot + IV pass is the dominant cost
    # of a run, so it is built ONLY when a SELL leg actually asks for it.
    # With it off every path below is byte-identical to the previous build.
    iv_active = any(l["action"] == "SELL" and is_iv_sl_mode(l.get("sl_mode"))
                    and float(l.get("sl_val") or 0) > 0 for l in legs_cfg)
    # ── IC_PARALLEL ── N>1 shards the date range into N contiguous chunks in
    # separate processes. This is ONLY sound when days are independent, i.e.
    # EOD mode. In NEXT_OPEN mode a position crosses the chunk boundary and
    # each worker would start with an empty `carry` — silently dropping the
    # overnight leg and inventing a fresh condor on a blocked day. Forced to
    # 1 there rather than trusted to the caller.
    parallel_workers = int(cfg.get("parallel_workers", 1) or 1)
    if positional and parallel_workers > 1:
        parallel_workers = 1
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

    # ── IC_PARALLEL BEGIN ── shard the day range across processes. Each
    # worker re-enters THIS impl over a contiguous date slice with workers
    # forced to 1, so per-day logic is byte-identical to serial BY
    # CONSTRUCTION. Parent merges trades + integer diag counters and
    # re-summarizes. Cancel is honoured between chunk completions.
    #
    # EOD mode only (see the `positional` clamp above) — and the `>= n*2`
    # guard keeps short ranges serial, because each spawned worker pays a
    # few seconds of interpreter + import startup that a 20-day run would
    # never earn back.
    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        import math as _math
        import multiprocessing as _mp
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n = min(parallel_workers, 8)
        step = _math.ceil(len(sim_days) / n)
        chunks = [sim_days[i:i + step] for i in range(0, len(sim_days), step)]
        child_cfg = dict(cfg)
        child_cfg["parallel_workers"] = 1
        merged_trades: List[ICTrade] = []
        merged_diag: Dict[str, float] = {}
        days_done = 0
        try:
            with ProcessPoolExecutor(
                    max_workers=n,
                    mp_context=_mp.get_context("spawn")) as pool:
                futs = {pool.submit(
                    _ic_parallel_worker, db_path, strategy_id, underlying,
                    ch[0].isoformat(), ch[-1].isoformat(), child_cfg): ch
                    for ch in chunks}
                for fut in as_completed(futs):
                    if cancel_cb and cancel_cb():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    out = fut.result()
                    merged_trades.extend(out["trades"])
                    for k, v in out["diag"].items():
                        if isinstance(v, bool) or not isinstance(v, int):
                            continue          # params/floats/dicts: parent's own
                        merged_diag[k] = merged_diag.get(k, 0) + v
                    days_done += len(futs[fut])
                    if progress_cb:
                        progress_cb({"day": days_done,
                                     "total_days": len(sim_days),
                                     "date": futs[fut][-1].isoformat()})
        except Exception as exc:
            # spawn unavailable / worker crash → loud, not silent-serial: a
            # silent fallback would mask a missing freeze_support guard and
            # quietly cost the user the speedup they asked for.
            return {"run_id": None, "aborted": True,
                    "reason": f"{strategy_id} parallel execution failed: "
                              f"{exc!r} — rerun with parallel_workers=1",
                    "trades": [], "summary": _empty_summary(),
                    "config": cfg, "strategy_id": strategy_id}
        merged_trades.sort(key=lambda t: (t.entry_ts or 0, t.condition))
        base_diag = {
            "days_total": len(sim_days), "exit_mode": exit_mode,
            "parallel_workers": n, "iv_active": iv_active,
            "skew_mult": skew_mult, "adjust_skew_mult": adjust_skew_mult,
            "adjust_on_sl": adjust_on_sl, "adjust_only": adjust_only,
        }
        for k, v in merged_diag.items():
            if k != "days_total":
                base_diag[k] = v
        summary = _summarize(merged_trades, base_diag)
        write_audit_log(
            f"[BACKTEST][{strategy_id}][{exit_mode}] {underlying} "
            f"{date_from}→{date_to}: PARALLEL x{n}, "
            f"{base_diag.get('days_entered', 0)}/{len(sim_days)} days "
            f"entered, {len(merged_trades)} leg-trades, "
            f"net {summary['net_pnl']}")
        return {"run_id": str(uuid.uuid4()), "summary": summary,
                "config": cfg, "trades": merged_trades,
                "strategy_id": strategy_id}
    # ── IC_PARALLEL END ──

    diag = {
        "days_total": len(sim_days), "days_entered": 0,
        "days_uncovered": 0, "days_no_short_strike": 0,
        "days_no_entry_price": 0,
        "wing_fallback_days": 0, "wing_absent_days": 0, "wing_synth_days": 0,
        "double_sl_days": 0, "mtc_activations": 0,
        "ambiguous_fills": 0, "no_exit_data": 0,
        # ── IC_V2 ──
        "exit_mode": exit_mode, "adjust_on_sl": adjust_on_sl,
        "adjust_only": adjust_only, "core_legs_suppressed": 0,
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
        "syn_flat_legs": 0,           # ── SYNTH_EXIT_FIX ── must stay 0
        "adjust_synth_unmonitored": 0,
        "adjust_cap_breaches": 0,     # real picks that exceeded the cap
        # ── IC_IV_SL ── DIAG funnel for the vol stop
        "iv_active": iv_active,
        "iv_modes": {l["id"]: l["sl_mode"] for l in legs_cfg
                     if is_iv_sl_mode(l.get("sl_mode"))},
        "iv_monitored_legs": 0,       # leg-days with a live threshold
        "iv_anchor_solved": 0,        # delta-mode entry IVs solved
        "iv_entry_solve_fail": 0,     # no anchor → leg unmonitored that day
        "iv_synth_unmonitored": 0,    # D10: synthetic shorts excluded
        "iv_mode_on_buy_leg": 0,      # D3: misconfig — that leg has NO stop
        "iv_no_spot_minutes": 0,      # parity spot unsolvable, carried fwd
        "iv_solve_fail_minutes": 0,   # vol unsolvable at a monitored minute
        "iv_sl_exits": 0,             # legs stopped out on vol
        "iv_leg_mtc_pinned": 0,       # IV leg also pinned to cost by MTC
        "iv_carried_unmonitored": 0,  # D9: carried legs lose IV monitoring
        # ── IC_MIN_ENTRY_IV ──
        "min_entry_iv": min_entry_iv,
        "days_iv_filtered": 0,        # days skipped by the entry-IV floor
        "iv_filter_open_days": 0,     # E4 fail-open: no anchor, traded anyway
        "iv_entry_synth_anchors": 0,  # E3 synthetic contributions to the mean
        "iv_entry_mean_sum": 0.0,     # running sum of day means (for avg)
        "iv_entry_mean_days": 0,      # days a mean was computable
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
        # ── ADJ_ONLY ── the condor is fully SIMULATED (SLs fire, MTC
        # re-pins, adjustments arm on the identical timeline) but only ·ADJ
        # legs are booked. Counted so a run's suppressed-core volume is
        # never invisible.
        if adjust_only and not lt.get("is_adjust"):
            diag["core_legs_suppressed"] += 1
            return
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
        # ── IC_IV_SL ──
        diag["iv_sl_exits"] += f.get("iv_sl_exits", 0)
        diag["iv_leg_mtc_pinned"] += f.get("iv_leg_mtc_pinned", 0)

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

    # ── SYNTH_EXIT_FIX (2026-07-22) BEGIN ──
    def _mark_synth_exit(lt: dict, week: list, meta_by_sym: dict,
                         day_start: int, expiry_ts: int,
                         bound_ts: int) -> None:
        """Re-price a SAME-SESSION synthetic leg at its exit bound.

        A synthetic leg has no corpus candles, so the engine never advances
        its `last_close` — every unmonitored synthetic leg comes back from
        simulate_session with exit_price == entry_price and exit_ts ==
        entry_ts. Booked as-is that is gross ₹0 and net = −charges, which is
        exactly the SYN-…-23600PE row (2.80 → 2.80 @ 20/07 09:18) in the
        first run. This closure re-marks the leg via `_synth_mark_at` at the
        session bound.

        SCOPE — deliberately narrow, three guards:
          * `synthetic` only. Real legs are never touched.
          * exit_price must still EQUAL entry_price. A synthetic leg that
            somehow did get a real exit (or was already re-marked by the
            dark-mark path on a carry day) keeps it — reality and the
            earlier mark both outrank this fallback.
          * strike + opt_type must be present, else there is nothing to
            price.

        SKEW BASIS: the knob is chosen by `synth_kind`, so a leg is marked
        OUT on the same basis it was marked IN. Marking a wing in at
        skew_mult and out at adjust_skew_mult would fabricate P&L from the
        difference between two config values.

        Mutates `lt` in place (exit_price / exit_ts) before `_emit` reads
        it; counts syn_exit_marks on success, syn_exit_fail otherwise. A
        failure leaves the flat ₹0 leg, which `syn_flat_legs` then reports
        in the summary rather than hiding."""
        if not lt.get("synthetic"):
            return
        if lt.get("exit_price") is None or lt.get("entry_price") is None:
            return
        if abs(float(lt["exit_price"]) - float(lt["entry_price"])) > 1e-9:
            return
        k = lt.get("strike")
        side = lt.get("opt_type")
        if not k or side not in ("CE", "PE"):
            diag["syn_exit_fail"] += 1
            return
        kind = lt.get("synth_kind")
        sk = adjust_skew_mult if kind in ("short", "adjust", "dark") else skew_mult
        px = _synth_mark_at(src=src, week=week, meta_by_sym=meta_by_sym,
                            day_start=day_start, ts=bound_ts,
                            expiry_ts=expiry_ts, opt_type=side,
                            strike=float(k), skew_mult=sk)
        if px is None:
            diag["syn_exit_fail"] += 1
            return
        lt["exit_price"] = round(px, 2)
        lt["exit_ts"] = bound_ts
        diag["syn_exit_marks"] += 1
    # ── SYNTH_EXIT_FIX END ──

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
        # ── PRELOAD_SCOPED ── expected_expiry_for_day is a PURE CALENDAR
        # function — it needs no corpus — so the expiry is knowable BEFORE the
        # universe is touched. Passing it scopes the day's preload to the one
        # week IC actually trades. On a 4-expiry corpus that is 168 → 42
        # symbols AND swaps the plan onto (underlying, expiry, ts), which
        # removes the temp b-tree: 214 → 14 ms/day measured.
        #
        # The carry path below deliberately does NOT re-scope: a leg carried
        # from a previous expiry is out of today's scope, and CandleSource
        # falls back to SQL per-symbol for exactly those (a handful of legs),
        # rather than reporting them as having no candles.
        want_expiry = expected_expiry_for_day(d).isoformat()
        universe_today = src.contracts_active_on_day(underlying, day_start,
                                                     expiry=want_expiry)
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
            # ── MORNING_SQUARE_OFF (2026-07-22) ── carried legs ALWAYS
            # close at next_open_time (09:16) — on the contract's own
            # expiry day and on the last range day too. The expiry-day
            # EVENING square-off applies ONLY to legs ENTERED that day
            # (the entry path's hard close). The previous build hard-closed
            # a carried basket at 15:28 on expiry day, which (a) let it
            # ride a second near-full session and (b) let a fresh 09:18
            # condor open while it was still live — observed 06/07→07/07:
            # L2·MTC rode to 07/07 15:27 EOD_MTC alongside 07/07's new
            # basket. hard_ts stays None here unconditionally; the engine
            # then closes every carried leg at next_open_ts.
            hard_ts, hard_reason = None, "EOD"

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
                                  synth_kind=(st.get("synth_kind") or "dark"))
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
        # ── PRELOAD_SCOPED ── want_expiry is already computed above (it drives
        # the scoped preload). The filter below is now a no-op on a scoped
        # universe, but is KEPT: it is the fail-closed expected-expiry gate,
        # and it must still hold if this ever runs against an unscoped
        # universe. Removing it would make coverage depend on a cache setting.
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not week:
            diag["days_uncovered"] += 1
            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "
                            f"{want_expiry} not in corpus — day skipped")
            continue

        # entry-candle close per candidate, per side
        cand: Dict[str, List] = {"CE": [], "PE": []}
        meta_by_sym: Dict[str, dict] = {}
        # ── IC_IV_SL ── the whole week's close series, kept ONLY when a leg
        # asks for vol monitoring. The day is already in CandleSource's cache
        # (contracts_active_on_day preloaded it above), so this loop costs no
        # extra SQL — just the retained lists.
        iv_candles_by_sym: Dict[str, List[dict]] = {}
        for c in week:
            sym = c["tradingsymbol"]
            meta_by_sym[sym] = c
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            closes = [{"ts": x.ts, "close": float(x.close)} for x in cds]
            if iv_active:
                iv_candles_by_sym[sym] = closes
            ec = entry_close(closes, entry_ts)
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
                    # ── IC_MIN_ENTRY_IV / E3 ── the band-edge vol this leg
                    # was priced from. The engine ignores unknown override
                    # keys, so this rides along purely for the floor.
                    "anchor_iv": spec.get("iv"),
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

        # ── IC_MIN_ENTRY_IV BEGIN ── evaluated HERE: after selection (the
        # anchors need the chosen strikes) but BEFORE the wing/synth day
        # counters and before anything is booked, so a filtered day never
        # pollutes wing_fallback_days / syn_*_days with a day that did not
        # trade. (TSG increments those first; clean funnel attribution is
        # worth the small divergence — it is diag-only, never P&L.)
        #
        # Anchors are solved whenever the floor OR a delta-mode leg needs
        # them, and the SAME map feeds both (E1).
        ic_entry_ivs: Dict[str, float] = {}
        if min_entry_iv > 0 or iv_active:
            ic_entry_ivs = _ic_entry_ivs(
                day_legs=day_legs, selected=selected,
                entry_overrides=entry_overrides, entry_ladder=cand,
                meta_by_sym=meta_by_sym, entry_ts=entry_ts,
                expiry_ts=expiry_ts, diag=diag)
        if ic_entry_ivs:
            _mean_iv = sum(ic_entry_ivs.values()) / len(ic_entry_ivs)
            diag["iv_entry_mean_sum"] += _mean_iv
            diag["iv_entry_mean_days"] += 1
        if min_entry_iv > 0:
            if not ic_entry_ivs:
                # ── E4 ── no anchor at all → FAIL OPEN. A solver failure
                # must never silently stop the strategy trading.
                diag["iv_filter_open_days"] += 1
            elif _mean_iv < min_entry_iv:      # ── E2 ── mean, TSG parity
                diag["days_iv_filtered"] += 1
                continue
        # ── IC_MIN_ENTRY_IV END ──

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

        # ── IC_IV_SL BEGIN ── vol series for this session's monitored
        # shorts. Bound is the ENTRY session's EOD minute in BOTH modes
        # (D9: entry day only) — an IC_V2 leg that carries is counted as
        # unmonitored from here on rather than silently half-covered.
        ic_iv_by_min = ic_iv_thr = None
        if iv_active:
            iv_minutes = list(range(entry_ts + 60, eod_ts + 1, 60))
            if iv_minutes:
                ic_iv_by_min, ic_iv_thr = _ic_iv_series(
                    day_legs=day_legs, selected=selected,
                    entry_overrides=entry_overrides, entry_ladder=cand,
                    candles_by_sym=iv_candles_by_sym,
                    meta_by_sym=meta_by_sym, minutes=iv_minutes,
                    entry_ts=entry_ts, expiry_ts=expiry_ts, diag=diag,
                    entry_ivs=ic_entry_ivs)   # ── E1 shared anchors ──
        # ── IC_IV_SL END ──

        # ── SYNTH_EVERYWHERE ── adjustment picks are resolved at the FILL
        # MINUTE, which is only knowable after the session has been run.
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
                entry_overrides=entry_overrides,
                # ── IC_IV_SL ── the probe MUST see the same vol series, or
                # an IV_SL'd short would be invisible to pass 1 and its
                # adjustment leg would never be priced (D6).
                iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr)
            # ── ADJ_ON_MTC ── MTC_COST minutes are discovered too. The
            # probe runs with adjust_on_sl=False, which is still valid:
            # adjustment BUY legs never touch the shorts' SL/TP/MTC state,
            # so the shorts' exit minutes are identical with or without
            # adjustments in play.
            # ── IC_IV_SL ── D6: IV_SL is a stop, so it re-loads the side.
            sl_minutes = {t["leg"]: int(t["exit_ts"]) + adjust_delay_s
                          for t in probe["trades"]
                          if t.get("exit_reason") in ("SL", "MTC_COST", "IV_SL")
                          and t["action"] == "SELL"
                          and not t.get("is_adjust")}
            engine_picks = _picks_for(sl_minutes)

        if not positional:
            # ── IC_V1 PATH ── unchanged when no synth is in play: the
            # legacy wrapper, EOD close. `entry_overrides` is empty on an
            # IC_V1 run (synth_shorts off, wing_mode real_fallback), so this
            # is the original call.
            # ── IC_IV_SL ── `ic_iv_thr` joins the condition because the
            # legacy simulate_day wrapper takes no vol series; routing an
            # IV run through it would silently drop the stop.
            if entry_overrides or engine_picks or ic_iv_thr:
                res = simulate_session(
                    day_legs, candles_by_leg, selected, entry_ts, eod_ts,
                    exit_mode="EOD",
                    adjust_on_sl=adjust_on_sl, adjust_cfg=adjust_cfg,
                    adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,
                    entry_overrides=entry_overrides,
                    iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr)
                res["trades"].sort(key=lambda t: t["leg"])
            else:
                res = simulate_day(day_legs, candles_by_leg, selected,
                                   entry_ts, eod_ts)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                # ── SYNTH_EXIT_FIX ── bound = the EOD square-off minute.
                _mark_synth_exit(lt, week, meta_by_sym, day_start,
                                 expiry_ts, eod_ts)
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
                entry_overrides=entry_overrides,
                iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                # ── SYNTH_EXIT_FIX ── only legs the engine CLOSED today
                # reach here (carried ones are in carry_out, not trades), so
                # the bound is the hard close when there is one, else the
                # session's own EOD minute.
                _mark_synth_exit(lt, week, meta_by_sym, day_start,
                                 expiry_ts, (hard_ts or eod_ts))
                _emit(lt, meta_by_sym)
                if lt["exit_reason"] == "EOD" and hard_ts is not None:
                    diag["expiry_closes"] += 1
                elif lt["exit_reason"] == "EOR":
                    diag["eor_closes"] += 1
            carry = res["carry_out"]
            # ── IC_IV_SL ── D9 scope boundary made visible: these legs were
            # IV-monitored today and will NOT be tomorrow. They run on TP
            # (and any MTC cost pin) alone from here. A large number on a
            # NEXT_OPEN run means the vol stop governs far less of the book
            # than the config reads like it does.
            if ic_iv_thr:
                diag["iv_carried_unmonitored"] += sum(
                    1 for _l in res["carry_out"] if _l in ic_iv_thr)
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
        f"SYN exitMark {diag['syn_exit_marks']} "
        f"(fail {diag['syn_exit_fail']}, flat {diag['syn_flat_legs']}), "
        f"SYN net {diag['syn_pnl_net']} of {summary['net_pnl']} "
        f"({diag['syn_pnl_share_pct']}% by |P&L|), "
        + (f"IVfloor {min_entry_iv} skipped {diag['days_iv_filtered']}d "
           f"(open {diag['iv_filter_open_days']}, avg entry IV "
           f"{round(diag['iv_entry_mean_sum'] / diag['iv_entry_mean_days'], 4) if diag['iv_entry_mean_days'] else 'n/a'}), "
           if min_entry_iv > 0 else "")
        + (f"IV_SL {diag['iv_sl_exits']} exits over "
           f"{diag['iv_monitored_legs']} monitored leg-days "
           f"(anchorFail {diag['iv_entry_solve_fail']}, "
           f"synthSkip {diag['iv_synth_unmonitored']}, "
           f"noSpot {diag['iv_no_spot_minutes']}, "
           f"solveFail {diag['iv_solve_fail_minutes']}, "
           f"mtcPin {diag['iv_leg_mtc_pinned']}, "
           f"carriedUnmon {diag['iv_carried_unmonitored']}), "
           if iv_active else "") +
        f"skips: uncovered {diag['days_uncovered']} / "
        f"noShort {diag['days_no_short_strike']} / "
        f"noEntryPx {diag['days_no_entry_price']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}
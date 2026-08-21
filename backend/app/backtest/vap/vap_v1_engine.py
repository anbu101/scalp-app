# backend/app/backtest/vap/vap_v1_engine.py
#
# ── VAP_V1 ENGINE ── Anchored VWAP on the OPTION PREMIUM series (NIFTY
# weekly CE/PE), 5m signals, intraday only. Option BUY or SELL mode.
#
# LOCKED SPEC (2026-08-20, decisions D1-D17 + N1-N6 confirmed):
#   * ONE strategy id (VAP_V1) with a top-level mode param, BUY | SELL
#     (D1/N6 — GC D11 / TMA_V2 pattern verbatim, so the SweepBuilder mode
#     axis copies over unchanged). `side_mode` is deliberately NOT used:
#     PST already owns that key with different semantics.
#   * SIGNAL is anchored VWAP on the OPTION premium (D2). One CE contract
#     and one PE contract are chosen ONCE per day (D4) and held — VWAP is
#     anchored to 09:15 of a SPECIFIC contract, so re-picking the strike
#     mid-day would restart the anchor and silently reset the arm/disarm
#     state machine against a series it never saw. Selection is therefore
#     a day-scoped decision, not a per-entry one.
#   * VWAP accumulates on 1m bars (D3): typical price (H+L+C)/3 weighted
#     by volume, evaluated at the close of each COMPLETED 5m bar. Bars
#     with volume 0 contribute nothing to either sum (~12.5% of the
#     option corpus) — this is automatic, not a special case. While
#     cumulative volume is still 0 the VWAP is UNDEFINED and no decision
#     is taken (N5, blocked_warmup) — same doctrine as TMA_V2's unwarmed
#     EMA: undecidable → no entry, and no arming either.
#   * ENTRY (BUY mode): a completed 5m bar of the CE (or PE) contract
#     closes ABOVE its own VWAP → BUY that contract.
#     ENTRY (SELL mode, D7/N2): the SAME signal, but the position is on
#     the OPPOSITE side — CE closes above its VWAP → SELL PE + BUY a
#     deeper-OTM PE hedge (TMA_V2 spread mechanics verbatim). Note the
#     asymmetry, it is intentional: the arm/disarm state machine tracks
#     the SIGNAL series (CE) while SL/TP live on the TRADED premium (PE).
#   * RE-ENTRY (D13): after any exit the leg is DISARMED and only re-arms
#     when a completed bar of the SIGNAL series closes BELOW its VWAP;
#     the next close above then enters. require_arm_first=False (default)
#     lets the FIRST entry of the day fire without a prior below-close,
#     which is the literal reading of the spec; True demands the leg arm
#     first. max_trades_per_day caps entries PER LEG.
#   * CONFLICT (N1): with allow_both_sides=False the two legs share ONE
#     slot, and a bar on which BOTH legs would enter takes NEITHER
#     (skipped_conflict). A simultaneous two-sided VWAP break is the
#     definition of the chop case; picking a winner by distance would be
#     inventing information.
#   * SL (D9/N4): sl_mode PCT (% of entry premium) or ATR (Wilder ATR of
#     the TRADED leg's 5m series × atr_mult, expressed in POINTS). ATR is
#     on the TRADED leg because the stop lives on the traded premium —
#     in SELL mode that is the PE series while the signal is VWAP(CE).
#     max_sl_pct clamps either mode so an ATR spike can never mint an
#     absurd stop (sl_clamped).
#   * TP (D10): tp_mode RR (multiple of the SL DISTANCE) or PCT (% of
#     entry premium). RR with SL disabled is a config error, not a
#     silent no-target — the runner aborts loudly.
#   * Levels are built by tma_v2_engine.sl_tp_levels — ATR feeds in as
#     PTS, so the locked SL/TP/clamp math is REUSED, not re-derived.
#   * Intraday only (D14). No positional carry, no cross-day warmup: the
#     VWAP anchor and the ATR seed are both intraday by construction.
#
# ── SL GRACE WINDOW (2026-08-20, sl_grace_min) ─────────────────────────
#   The 6-year run showed 74% of trades exiting at SL with a MEDIAN
#   time-to-stop of 21 minutes and the fastest quartile stopped inside 8
#   minutes — noise, not signal invalidation. sl_grace_min suspends the
#   SL for the first N minutes after entry. TP stays ARMED throughout
#   (the point is to stop discarding winners, not to delay them), and
#   the EOD bound always applies.
#
#   Implemented in the RUNNER as two sequential calls to an UNMODIFIED
#   monitor_position_day, never by editing that function: it is the
#   parity reference that test_tma_live_core / test_tma2_live_core
#   assert the LIVE TMA V1 and V2 engines against, so a signature or
#   behaviour change there is a live-path change wearing a backtest
#   disguise.
#
#   At grace expiry a premium already beyond the SL level is caught by
#   the EXISTING gap-fill branch and exits at that candle's OPEN — a
#   market fill, not a free rewind to the untouched SL level. That
#   convention falls out of the reuse for free, which is most of the
#   reason for doing it this way.
#
#   sl_grace_disaster_pct (default 0 = OFF) arms a WIDER stop during the
#   grace window. A short with no stop at all for N minutes has unbounded
#   loss, and "it is only a backtest" is exactly how an unbounded number
#   ends up in a results table being read as an edge.
#
# ── vwap_buffer_pct (D8, optional, default 0 = OFF) ─────────────────────
#   Requires the close to clear VWAP by a % margin before the break
#   counts. This is a DEVIATION buffer, not a theta model. Option premium
#   decays all session, so price sits under its own anchored VWAP more
#   and more as the day ages purely from decay — the BUY variant starves
#   after ~13:00 and the SELL variant fires almost continuously into the
#   close. The buffer damps marginal breaks; it does NOT neutralise that
#   drift, and no claim is made here that it does. Honest theta
#   adjustment is deferred to V2 rather than half-shipped as a knob that
#   looks like it solved the problem.
#
# ── WHAT LIVES WHERE ────────────────────────────────────────────────────
#   Pure module: consumes candle dicts, returns dicts/lists. No app
#   imports on the hot path, no I/O, no DB. The runner owns corpus reads,
#   selection, hedging, charges and persistence. monitor_position_day and
#   sl_tp_levels are REUSED from the TMA engines — single source of truth
#   for the fill/monitor conventions, exactly as TMA_V2 reuses V1.

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    from app.backtest.tma.tma_v1_engine import (   # noqa: F401  (re-export)
        ema_series, monitor_position_day,
    )
    from app.backtest.tma.tma_v2_engine import (   # noqa: F401  (re-export)
        sl_tp_levels,
    )
except ImportError:  # standalone tests
    from tma_v1_engine import ema_series, monitor_position_day  # type: ignore  # noqa: F401
    from tma_v2_engine import sl_tp_levels  # type: ignore  # noqa: F401

TF_S_DEFAULT = 300          # 5m
ATR_PERIOD_DEFAULT = 6      # ready by ~09:50 with a 09:15 anchor
ATR_PERIOD_MIN, ATR_PERIOD_MAX = 2, 60
EMA_PERIOD_DEFAULT = 20     # on 1m closes -> warm ~09:35 (see ENTRY FILTERS)
EMA_PERIOD_MAX = 400
VOL_LOOKBACK_DEFAULT = 12   # 1 hour of 5m bars
VOL_MIN_SAMPLE = 3          # fewer prior bars than this -> undecidable


# ──────────────────────────────────────────────────────────────────────
# ANCHORED VWAP over the 1m option series
# ──────────────────────────────────────────────────────────────────────
def vwap_by_minute(bars1m: List[dict]) -> Dict[int, Optional[float]]:
    """Running session-anchored VWAP keyed by 1m bar ts (bar START).

    Typical price (H+L+C)/3 weighted by volume, accumulated from the
    first bar given — the caller passes ONE session, so the 09:15 anchor
    is implicit in the slice rather than re-derived here.

    volume<=0 bars add nothing to either sum, so an illiquid strike
    simply carries the previous VWAP forward. Until cumulative volume is
    strictly positive the VWAP is None (undefined, NOT zero) — callers
    must treat None as undecidable, never as a level.
    """
    out: Dict[int, Optional[float]] = {}
    cum_pv = 0.0
    cum_v = 0.0
    for b in sorted(bars1m, key=lambda x: int(x["ts"])):
        try:
            v = float(b.get("volume") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
            cum_pv += tp * v
            cum_v += v
        out[int(b["ts"])] = (cum_pv / cum_v) if cum_v > 0 else None
    return out


# ──────────────────────────────────────────────────────────────────────
# WILDER ATR on the 5m series (SMA-seeded, house convention)
# ──────────────────────────────────────────────────────────────────────
def atr_wilder(bars5: List[dict], period: int) -> List[Optional[float]]:
    """None until warm; index `period` carries the SMA seed of the first
    `period` true ranges, then standard Wilder smoothing. Aligned 1:1
    with bars5.

    TR needs a previous close, so index 0 has none — with period=6 the
    first ATR lands on the 7th 5m bar (~09:50 off a 09:15 anchor). That
    is a real entry blackout for sl_mode=ATR and it is reported as
    blocked_atr_warmup rather than being papered over with a %-SL
    fallback: silently switching stop semantics mid-session would make
    two different strategies share one results row.
    """
    n = len(bars5)
    out: List[Optional[float]] = [None] * n
    p = int(period)
    if p < 1 or n < p + 1:
        return out
    trs: List[float] = [0.0]
    for i in range(1, n):
        pc = float(bars5[i - 1]["close"])
        hi = float(bars5[i]["high"])
        lo = float(bars5[i]["low"])
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    atr = sum(trs[1:p + 1]) / p
    out[p] = atr
    for i in range(p + 1, n):
        atr = ((atr * (p - 1)) + trs[i]) / p
        out[i] = atr
    return out


# ──────────────────────────────────────────────────────────────────────
# ENTRY FILTERS (2026-08-20) — both on the SIGNAL leg, both ENTRY-ONLY
# ──────────────────────────────────────────────────────────────────────
# Context: the 6-year run stopped out 74% of trades with a 21-minute
# median time-to-stop, and an SL grace window made every metric WORSE
# (net down, DD roughly doubled, hit rate flat). That killed the "the
# stops are noise" hypothesis: the stop was doing real work and the
# entries were simply wrong. These two filters attack entry QUALITY,
# which is the half that has never been tuned.
#
# CRITICAL — both filters gate ENTRY ONLY. Neither may block ARMING. A
# leg re-arms on a close BELOW VWAP, and that is a fact about the VWAP
# relationship alone; letting an unwarmed EMA or a thin-volume bar
# suppress it would silently disarm the leg for the rest of the day.
#
# EMA BASIS — this is the trap worth naming. ema_series is SMA-seeded and
# warm at index period-1. On 5m bars anchored at 09:15, EMA20 is not warm
# until 10:55; against a 09:30-11:00 entry window that leaves ONE usable
# bar per day, and the filter would read as "kills the strategy" when it
# had in fact never run. Default basis is therefore 1m closes (EMA20 = 20
# minutes, warm ~09:35), read at each 5m bar's close — the same 1m-
# accumulate / 5m-evaluate split VWAP already uses. The runner aborts
# loudly when period x basis leaves no room inside the entry window.


def ema_at_bar_ends(bars1m: List[dict], bars5: List[dict], *, tf_s: int,
                    period: int, basis_minutes: int) -> Dict[int, Optional[float]]:
    """EMA of the option's own premium, keyed by 5m bar COMPLETION ts.

    basis_minutes=1 runs the EMA over 1m closes and samples it at each
    bucket's final minute; basis_minutes=tf runs it over the 5m closes
    themselves. Same SMA-seeded ema_series either way — the house
    convention, reused rather than re-derived.
    """
    out: Dict[int, Optional[float]] = {}
    if period <= 0:
        return out
    if basis_minutes <= 1:
        rows = sorted(bars1m, key=lambda x: int(x["ts"]))
        vals = ema_series([float(b["close"]) for b in rows], period)
        by_min = {int(b["ts"]): v for b, v in zip(rows, vals)}
        for b in bars5:
            ts0 = int(b["ts"])
            out[ts0 + tf_s] = by_min.get(ts0 + tf_s - 60)
        return out
    vals = ema_series([float(b["close"]) for b in bars5], period)
    for b, v in zip(bars5, vals):
        out[int(b["ts"]) + tf_s] = v
    return out


def bucket_volumes(bars1m: List[dict], bars5: List[dict],
                   *, tf_s: int) -> Dict[int, float]:
    """Total traded volume per 5m bucket, keyed by bar COMPLETION ts.

    aggregate() does not carry volume through, so it is summed here from
    the 1m rows rather than inferred. Missing minutes simply contribute
    nothing — the same treatment VWAP gives them.
    """
    out: Dict[int, float] = {}
    if not bars5:
        return out
    edges = sorted(int(b["ts"]) for b in bars5)
    for b in bars1m:
        ts = int(b["ts"])
        base = ts - ((ts - edges[0]) % tf_s) if ts >= edges[0] else None
        if base is None:
            continue
        try:
            v = float(b.get("volume") or 0)
        except (TypeError, ValueError):
            v = 0.0
        out[base + tf_s] = out.get(base + tf_s, 0.0) + max(0.0, v)
    return {k: out.get(k, 0.0) for k in (int(b["ts"]) + tf_s for b in bars5)}


def volume_ok(vols: List[float], current: float, mult: float) -> Optional[bool]:
    """Is the break bar's volume at least `mult` x the mean of the prior
    bars? None when there is not enough history to judge.

    A ROLLING prior window, deliberately not the session mean: the 09:15
    open carries a volume spike that would drag a cumulative average up
    all morning and make every later break look thin.
    """
    if mult <= 0:
        return True
    usable = [v for v in vols if v > 0]
    if len(usable) < VOL_MIN_SAMPLE:
        return None
    avg = sum(usable) / len(usable)
    if avg <= 0:
        return None
    return current >= avg * mult


# ──────────────────────────────────────────────────────────────────────
# PER-LEG BAR FACTS (pure; one entry per COMPLETED 5m bar)
# ──────────────────────────────────────────────────────────────────────
def leg_bar_facts(bars5: List[dict], vwap_min: Dict[int, Optional[float]],
                  *, tf_s: int = TF_S_DEFAULT,
                  buffer_pct: float = 0.0,
                  ema_at: Optional[Dict[int, Optional[float]]] = None,
                  vol_at: Optional[Dict[int, float]] = None,
                  vol_mult: float = 0.0,
                  vol_lookback: int = VOL_LOOKBACK_DEFAULT) -> List[dict]:
    """[{ts_end, close, vwap, above, below}] for each completed 5m bar.

    ts_end = bar.ts + tf_s = the bar's COMPLETION time. That is also the
    ts of the 1m candle the runner fills on, so there is no lookahead:
    the decision uses only bars that finished strictly before the fill.

    VWAP is read at the bucket's FINAL minute (bar.ts + tf_s - 60), which
    aggregate() guarantees is present for a bar it marked complete. A
    bucket missing its final minute has a stale close and is dropped
    upstream, so it never reaches here.

    above/below are None when VWAP is undefined (no volume yet) — the
    caller must not arm or enter on that bar.
    `above` carries the vwap_buffer_pct margin; `below` never does, so a
    buffer widens the entry test WITHOUT making the leg harder to re-arm.
    """
    buf = max(0.0, float(buffer_pct or 0.0)) / 100.0
    out: List[dict] = []
    prior_vols: List[float] = []
    for b in bars5:
        if not b.get("complete"):
            continue
        ts0 = int(b["ts"])
        te = ts0 + tf_s
        vw = vwap_min.get(te - 60)
        cl = float(b["close"])

        # ── ENTRY FILTERS ── ema_ok / vol_ok are True when the filter is
        # OFF, None when it cannot yet be judged, False when it rejects.
        # None and False both block ENTRY; neither blocks arming.
        ema_v = None if ema_at is None else ema_at.get(te)
        if ema_at is None:
            ema_ok: Optional[bool] = True
        elif ema_v is None:
            ema_ok = None                      # unwarmed
        else:
            ema_ok = cl > ema_v

        cur_v = 0.0 if vol_at is None else float(vol_at.get(te, 0.0))
        if vol_at is None or vol_mult <= 0:
            vol_ok: Optional[bool] = True
            vol_avg = None
        else:
            window = prior_vols[-int(vol_lookback):] if vol_lookback > 0 else prior_vols
            vol_ok = volume_ok(window, cur_v, vol_mult)
            usable = [v for v in window if v > 0]
            vol_avg = (sum(usable) / len(usable)) if usable else None
        prior_vols.append(cur_v)

        base = {"ts_end": te, "close": cl, "ema": ema_v, "ema_ok": ema_ok,
                "vol": cur_v, "vol_avg": vol_avg, "vol_ok": vol_ok}
        if vw is None or vw <= 0:
            base.update({"vwap": None, "above": None, "below": None})
        else:
            base.update({"vwap": vw, "above": cl > vw * (1.0 + buf),
                         "below": cl < vw})
        out.append(base)
    return out


# ──────────────────────────────────────────────────────────────────────
# PER-LEG DECISION (pure function — no hidden state, trivially testable)
# ──────────────────────────────────────────────────────────────────────
def decide_leg(*, above: Optional[bool], below: Optional[bool],
               armed: bool, busy: bool, entries: int,
               max_entries: int,
               ema_ok: Optional[bool] = True,
               vol_ok: Optional[bool] = True) -> Tuple[str, bool]:
    """Returns (action, armed_next).

    action ∈ {BUSY, WARMUP, ARM, NONE, CAP, ENTER}

    Order matters and encodes the doctrine:
      BUSY   — a position is open on this slot; the bar is not consumed
               for arming either. The "closes below" that re-arms a leg
               must happen AFTER the exit, not while the trade is live.
      WARMUP — VWAP undefined: no decision at all (never arms).
      ARM    — closed below VWAP: the leg becomes eligible again.
      NONE   — inside the buffer band, or above VWAP while disarmed.
      CAP    — would enter but max_trades_per_day is spent.
      EMA_*  — the option's own EMA filter is unwarmed, or the close is
               not above it. Entry only; the leg stays armed.
      VOL_*  — the break bar failed the rolling volume test, or there is
               not enough history to judge it. Entry only.
      ENTER  — armed, closed above VWAP (+ buffer), filters satisfied.
    """
    if busy:
        return "BUSY", armed
    if above is None or below is None:
        return "WARMUP", armed
    if below:
        return "ARM", True
    if not above:
        return "NONE", armed
    if not armed:
        return "NONE", armed
    if max_entries and entries >= max_entries:
        return "CAP", armed
    # ── ENTRY FILTERS ── last, and deliberately AFTER the arming branch:
    # by this point the leg is armed and the VWAP break is real, so a
    # rejection here is a filtered signal, not a missing one. The leg
    # stays ARMED and can take the next qualifying break.
    if ema_ok is None:
        return "EMA_WARMUP", armed
    if ema_ok is False:
        return "EMA_BLOCK", armed
    if vol_ok is None:
        return "VOL_WARMUP", armed
    if vol_ok is False:
        return "VOL_BLOCK", armed
    return "ENTER", armed


# ──────────────────────────────────────────────────────────────────────
# SL-GRACE DIAGNOSTICS
# ──────────────────────────────────────────────────────────────────────
def breached_during(candles: List[dict], level: Optional[float],
                    short: bool) -> bool:
    """Did the premium touch the SL level over these candles?

    Used ONLY for diagnostics — it answers "would this trade have been
    stopped out during the grace window?", which is the whole point of
    running a grace window at all. A grace period that never covers a
    breach is costing nothing and buying nothing, and the DIAG counters
    are what tell those two cases apart.

    Mirrors the trigger side of monitor_position_day exactly: a SHORT is
    stopped when the premium RISES to the level, a long when it FALLS.
    """
    if level is None:
        return False
    for c in candles:
        if short:
            if float(c["high"]) >= level:
                return True
        elif float(c["low"]) <= level:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────
# SL/TP SIZING — turns the mode pair into (value, unit) for sl_tp_levels
# ──────────────────────────────────────────────────────────────────────
def size_sl_tp(*, entry_price: float, sl_mode: str, sl_pct: float,
               atr_value: Optional[float], atr_mult: float,
               max_sl_pct: float, tp_mode: str, rr: float,
               tp_pct: float) -> Tuple[Optional[dict], Optional[str]]:
    """Returns ({sl_val, sl_unit, tp_val, tp_unit, sl_pts, clamped}, None)
    or (None, reason) when the trade cannot be sized.

    Everything is reduced to a SL DISTANCE in points first, because RR
    targets are defined against that distance and the ₹-clamp is a
    distance too. The result is then handed to sl_tp_levels in PTS so the
    locked level math (side inversion, 0.05 floors, wrong-side clamps)
    is reused verbatim rather than reimplemented per mode.

    max_sl_pct clamps BOTH modes: an ATR spike on an illiquid strike can
    otherwise produce a stop wider than the premium itself, and a fat
    finger in the %-field deserves the same guard.
    """
    ep = float(entry_price)
    if ep <= 0:
        return None, "entry price <= 0"

    mode = str(sl_mode or "PCT").upper()
    clamped = False

    if mode == "ATR":
        if atr_value is None:
            return None, "atr_warmup"
        sl_pts = float(atr_value) * float(atr_mult or 0)
    else:
        sl_pts = ep * float(sl_pct or 0) / 100.0

    if sl_pts <= 0:
        # SL disabled. Legal on its own; fatal for an RR target.
        if str(tp_mode or "RR").upper() == "RR":
            return None, "rr_without_sl"
        sl_pts = 0.0
    else:
        cap = ep * float(max_sl_pct or 0) / 100.0
        if cap > 0 and sl_pts > cap:
            sl_pts = cap
            clamped = True

    if str(tp_mode or "RR").upper() == "RR":
        tp_val = sl_pts * float(rr or 0)
        tp_unit = "PTS"
    else:
        tp_val = float(tp_pct or 0)
        tp_unit = "PCT"

    return ({"sl_val": sl_pts, "sl_unit": "PTS",
             "tp_val": tp_val, "tp_unit": tp_unit,
             "sl_pts": sl_pts, "clamped": clamped}, None)
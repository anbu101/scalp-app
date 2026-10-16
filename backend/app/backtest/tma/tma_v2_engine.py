# backend/app/backtest/tma/tma_v2_engine.py
#
# ── TMA_V2 ENGINE ── Four-EMA (13/55/89/144) STACK strategy on NIFTY SPOT
# 5m; execution on weekly option premium, BUY or SELL mode (GC D11 pattern).
#
# LOCKED SPEC (2026-08-16, decisions D1-D8 confirmed):
#   * Signal TF 5m (aggregated from 1m spot, session-aligned 09:15),
#     evaluated on COMPLETED bars only. Entry executes on the NEXT 1m option
#     candle after the signal bar closes (PST fill conventions, verbatim —
#     same as TMA_V1).
#   * E1 (bearish stack): fires when EMA13 < EMA55 < EMA89 < EMA144 (strict)
#     transitions False→True on a completed bar. Trend side = PE.
#   * E2 (bullish stack): EMA13 > EMA55 > EMA89 > EMA144 (strict) False→True.
#     Trend side = CE.
#     No same-session staleness rule (unlike V1's C1): the TRANSITION itself
#     is the event — a stack already standing at the open emits nothing.
#     Re-entry on a fresh transition the same day is allowed (D1).
#   * ONE open position at a time, either direction (D2) — a single slot,
#     unlike V1's per-condition independence.
#   * Optional crossover exit (D3/D4, xover_exit_enabled, default ON):
#       E1 position exits when EMA13 >= EMA89 (INCLUSIVE);
#       E2 position exits when EMA13 <= EMA89 (INCLUSIVE).
#     EMA55 and EMA144 play NO role in exits (D4, confirmed intentional).
#     Toggle OFF → SL / TP / EOD only (EOR/expiry in positional).
#     No instant-exit hazard: the entry stack implies e13 > e89 (E2) or
#     e13 < e89 (E1) STRICTLY on the signal bar, so the inclusive exit
#     condition is always False at entry.
#   * MODE (GC D11): BUY → E1 BUY PE, E2 BUY CE (single leg, no hedge).
#     SELL → E1 SELL CE + BUY deeper-OTM CE hedge; E2 SELL PE + PE hedge —
#     TMA_V1 spread mechanics verbatim (runner concern).
#   * SL/TP levels via sl_tp_levels() below — V1's short semantics
#     reproduced exactly for SELL, mirrored for BUY (long) mode. Units
#     PCT | PTS | ABS per field; 0 = disabled; nonsense ABS levels (wrong
#     side of entry) clamp OFF (None), never fire instantly; long-side PTS
#     that would push a level to/below zero clamp OFF the same way.
#   * EMA periods HARDCODED at 13/55/89/144 (D7) — compute kwargs exist for
#     unit tests only; the runner never overrides them.
#
# ── 2026-CHOP KNOBS (2026-08-16, post-mortem of the 2026 drawdown) ──────
#   Backtest CSV analysis showed the 2026 loss mechanism: entries fire at
#   stack COMPLETION (13-89 gap at its widest), so the 13/89 exit is
#   maximally far away and the premium SL always wins the race in chop
#   (XOVER share collapsed 16-23% → 5%; SL share 80%; median 24min to SL).
#   Three OPTIONAL knobs, all default-OFF (byte-identical to the original
#   V2 semantics when unset):
#   * xover_exit_ref (ANY period; 89 and 55 are the studied presets):
#     exit reference EMA. 55 puts the exit line
#     a fraction of the 13-89 gap away — a genuine reversal exits BEFORE
#     the SL, while in a real trend EMA13 holds above EMA55 and winners
#     run unchanged (structurally why V1 survived 2026: its entry fires
#     when its exit gap is near zero). A CUSTOM period (e.g. 70) is
#     computed on demand into state["eref"] — it interpolates between
#     the two presets, so expect behaviour between the ref55 and ref89
#     curves. Ref periods below EXIT_REF_MIN or above EXIT_REF_MAX are
#     rejected by the runner (see the constants below), because
#     ref<=13 is degenerate (e13 vs itself → instant exit) and very
#     long refs cannot warm up inside WARMUP_DAYS.
#   * min_extension_pct (0 = off) / max_extension_pct (0 = off): the
#     entry-extension BAND. The four EMAs form a fan that opens as a
#     trend runs, so the 13-89 gap (as % of spot) measures how far the
#     move has already travelled when the stack completes:
#       too NARROW → the "stack" is four near-coincident lines ordered
#         by noise for one bar; no trend has been demonstrated and a
#         single wiggle re-orders them (blocked_extension_min);
#       too WIDE   → the move is spent; these produced the 24-minute
#         stop-outs (blocked_extension).
#     min is applied FIRST and counted separately so the two failure
#     modes never share a funnel number. Both default 0 = off. The floor
#     overlaps in intent with slope_gate (a flat EMA144 also implies a
#     degenerate stack), so expect their blocks to correlate — sweep
#     them together rather than reading either in isolation.
#   * max_extension_pct (0 = off): entry-freshness gate — a stack that
#     completes with |EMA13-EMA89| already > this % of spot is an
#     exhaustion entry (the 24-minute SLs); the transition is skipped and
#     DIAG-counted (blocked_extension).
#   * slope_gate (bool): EMA144 slope filter — E2 requires EMA144 rising
#     over the last SLOPE_BARS bars, E1 falling. Blocks stacks assembled
#     by sideways drift rather than trend (blocked_slope). Lookback fixed
#     at SLOPE_BARS=6 (30min @5m): one-bar EMA144 deltas are noise-level;
#     30min is the shortest window with a stable sign.
#
# ── CROSS-DAY WARMUP ────────────────────────────────────────────────────
#   EMA144 on 5m bars needs 144 bars just for the SMA seed (~2 sessions);
#   V1's 3 warmup sessions would leave the seed barely converged on range
#   day 1. The V2 runner therefore feeds FIVE prior sessions (D5,
#   WARMUP_DAYS=5 — ~375 bars: seed + ~230 bars of convergence, residual
#   pre-history weight on EMA144 down to a few percent). Assembly is the
#   PST_XDAY_WARMUP pattern via tma_v1_engine.warmup_bars, verbatim.
#
# Pure module: consumes candle dicts, returns dicts. Runner does corpus,
# selection, charges, persistence. No app imports on the hot path.
# monitor_position_day and ema_series are REUSED from tma_v1_engine —
# single source of truth for the fill/monitor conventions.

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    from app.backtest.tma.tma_v1_engine import (   # noqa: F401  (re-exports)
        ema_series, monitor_position_day, warmup_bars,
    )
except ImportError:  # standalone tests
    from tma_v1_engine import (  # type: ignore  # noqa: F401
        ema_series, monitor_position_day, warmup_bars,
    )

TF_S_DEFAULT = 300  # 5m

# ── D7 ── hardcoded stack periods (kwargs below exist for tests only)
P1, P2, P3, P4 = 13, 55, 89, 144

# ── 2026-CHOP ── EMA144 slope lookback (bars) for the slope gate
SLOPE_BARS = 6

# ── EXIT_REF_CUSTOM ── crossover-exit reference bounds. 14 is the floor
# because ref <= 13 compares EMA13 with itself (or a faster line) and the
# INCLUSIVE test would fire on the entry bar; 250 is the ceiling because
# WARMUP_DAYS=5 gives ~375 bars and a longer seed cannot converge.
EXIT_REF_MIN, EXIT_REF_MAX = 14, 250
# Periods already present in state (no extra series needed)
REF_KEYS = {13: "e13", 55: "e55", 89: "e89", 144: "e144"}


# ──────────────────────────────────────────────────────────────────────
# indicator state over the continuous (warmup + today) 5m stream
# ──────────────────────────────────────────────────────────────────────
def compute_state_v2(bars5: List[dict], *, p1: int = P1, p2: int = P2,
                     p3: int = P3, p4: int = P4,
                     ref_period: Optional[int] = None) -> Dict:
    """stack_up[i] / stack_dn[i]: Optional[bool] — None while ANY of the
    four EMAs is unwarmed (drives blocked_warmup, same convention as V1's
    b_up/b_dn). e13/e89 are also returned by NAME for the crossover exit
    (they are the p1/p3 series — the test-only kwargs do not rename them)."""
    closes = [b["close"] for b in bars5]
    e1 = ema_series(closes, p1)
    e2 = ema_series(closes, p2)
    e3 = ema_series(closes, p3)
    e4 = ema_series(closes, p4)
    n = len(bars5)
    stack_up: List[Optional[bool]] = [None] * n
    stack_dn: List[Optional[bool]] = [None] * n
    for i in range(n):
        if e1[i] is None or e2[i] is None or e3[i] is None or e4[i] is None:
            continue
        stack_up[i] = e1[i] > e2[i] > e3[i] > e4[i]
        stack_dn[i] = e1[i] < e2[i] < e3[i] < e4[i]
    out = {"e13": e1, "e55": e2, "e89": e3, "e144": e4,
           "stack_up": stack_up, "stack_dn": stack_dn}
    # ── EXIT_REF_CUSTOM ── one extra series only when the requested exit
    # reference is not already one of the stack EMAs (55/89 cost nothing)
    if ref_period is not None and int(ref_period) not in REF_KEYS:
        out["eref"] = ema_series(closes, int(ref_period))
        out["ref_period"] = int(ref_period)
    return out


# ──────────────────────────────────────────────────────────────────────
# signal generation (spot only)
# ──────────────────────────────────────────────────────────────────────
def build_signals_v2(bars5: List[dict], warmup_count: int, session0: int,
                     entry_start_ts: int, entry_end_ts: int,
                     *, tf_s: int = TF_S_DEFAULT,
                     state: Optional[Dict] = None,
                     min_extension_pct: float = 0.0,
                     max_extension_pct: float = 0.0,
                     slope_gate: bool = False,
                     p1: int = P1, p2: int = P2,
                     p3: int = P3, p4: int = P4) -> Dict:
    """bars5 = warmup(completed) + today(completed), chronological;
    warmup_count = index of today's first bar. Returns
    {"signals": [{ts, side, cond, spot}], "state", "diag"}.
    `ts` is the SIGNAL BAR COMPLETION time (bar.ts + tf_s) — the runner
    enters on the 1m option candle STAMPED ts. Entry window
    [entry_start_ts, entry_end_ts) gates emission; exits are NOT gated.
    side is the TREND side: CE = bullish stack (E2), PE = bearish (E1).
    cond is "E1" | "E2" (diag identity; the runner's slot is single).
    ── EXT_BAND ── min_extension_pct > 0 skips transitions whose 13-89
    gap is BELOW that % of spot (undeveloped trend,
    blocked_extension_min).
    ── 2026-CHOP ── max_extension_pct > 0 skips transitions where
    |e13-e89| exceeds that % of the signal bar's close (exhaustion gate,
    blocked_extension). slope_gate=True requires EMA144 moving WITH the
    stack over SLOPE_BARS bars (blocked_slope); an unwarmed lookback
    blocks conservatively into blocked_warmup — same doctrine as an
    undecidable stack: no decision, no entry."""
    st = state or compute_state_v2(bars5, p1=p1, p2=p2, p3=p3, p4=p4)
    stack_up, stack_dn = st["stack_up"], st["stack_dn"]

    signals: List[dict] = []
    diag = {"bars5_today": max(0, len(bars5) - warmup_count),
            "warmup_bars5": warmup_count,
            "e1_events": 0, "e2_events": 0,
            "blocked_warmup": 0, "blocked_session": 0,
            "blocked_extension": 0, "blocked_slope": 0,   # ── 2026-CHOP ──
            "blocked_extension_min": 0}   # ── EXT_BAND ──
    e13s, e89s, e144s = st["e13"], st["e89"], st["e144"]

    start_i = max(1, warmup_count)
    for i in range(start_i, len(bars5)):
        if bars5[i]["ts"] < session0:
            continue  # defensive: never emit on a warmup-session bar
        ts_end = bars5[i]["ts"] + tf_s
        emitted_here: List[dict] = []

        # ── E1 / E2: stack boolean False→True transition ──
        for arr, side, cond, key in ((stack_dn, "PE", "E1", "e1_events"),
                                     (stack_up, "CE", "E2", "e2_events")):
            cur, prev = arr[i], arr[i - 1]
            if cur is not True:
                continue
            if prev is None:
                diag["blocked_warmup"] += 1
                continue
            if prev is True:
                continue
            diag[key] += 1
            # ── EXT_BAND / 2026-CHOP ── entry-extension band on the
            # 13-89 fan width at stack completion: too narrow = trend not
            # yet demonstrated, too wide = exhaustion entry. Gap computed
            # once; floor checked before ceiling so the two funnels stay
            # disjoint (a signal is only ever counted in one of them).
            # NOTE: always 13-vs-89 regardless of xover_exit_ref — the
            # widest span is the trend-extension yardstick, while the
            # exit reference is a separate (deliberately decoupled) choice.
            if min_extension_pct > 0 or max_extension_pct > 0:
                spot_px = float(bars5[i]["close"]) or 0.0
                if spot_px > 0:
                    ext_pct = abs(e13s[i] - e89s[i]) / spot_px * 100.0
                    if min_extension_pct > 0 and ext_pct < min_extension_pct:
                        diag["blocked_extension_min"] += 1
                        continue
                    if max_extension_pct > 0 and ext_pct > max_extension_pct:
                        diag["blocked_extension"] += 1
                        continue
            # ── 2026-CHOP ── EMA144 slope gate: stack must form WITH the
            # long-run average moving its way, not by sideways drift
            if slope_gate:
                j = i - SLOPE_BARS
                if j < 0 or e144s[j] is None:
                    diag["blocked_warmup"] += 1   # undecidable → no entry
                    continue
                rising = e144s[i] > e144s[j]
                if (side == "CE" and not rising) or \
                        (side == "PE" and not (e144s[i] < e144s[j])):
                    diag["blocked_slope"] += 1
                    continue
            emitted_here.append({"ts": ts_end, "side": side, "cond": cond,
                                 "spot": bars5[i]["close"]})

        for s in emitted_here:
            if not (entry_start_ts <= s["ts"] < entry_end_ts):
                diag["blocked_session"] += 1
                continue
            signals.append(s)

    diag["signals"] = len(signals)
    return {"signals": signals, "state": st, "diag": diag}


# ──────────────────────────────────────────────────────────────────────
# crossover exit schedule (13 vs 89 ONLY — D4, inclusive — spec verbatim)
# ──────────────────────────────────────────────────────────────────────
def xover_exit_ts_v2(bars5: List[dict], state: Dict, side: str,
                     after_ts: int, *, tf_s: int = TF_S_DEFAULT,
                     exit_ref=89) -> Optional[int]:
    """First 5m bar COMPLETION time strictly greater than after_ts at which
    the crossover exit for the position's TREND side holds on that
    completed bar:
      CE position (from E2 bullish): e13 <= REF  (inclusive)
      PE position (from E1 bearish): e13 >= REF  (inclusive)
    ── 2026-CHOP / EXIT_REF_CUSTOM ── REF defaults to EMA89 (original D4
    semantics). exit_ref accepts a PERIOD NUMBER (55, 70, 89, ...): 55
    and 89 read the stack series directly, any other period reads
    state["eref"] (which compute_state_v2 must have been asked to build
    with the SAME ref_period — the runner guarantees this). The legacy
    string form ("e55"/"e89") is still accepted. A closer line is
    reached by a genuine reversal BEFORE the premium SL, while a healthy
    trend keeps EMA13 on its side of the line. Anything unresolvable
    falls back to EMA89 (fail-safe to the documented default).
    Bars with either EMA unwarmed are skipped (cannot decide → no exit).
    Returns None if no such bar exists in the stream (EOD handles it).
    The caller applies the xover_exit_enabled toggle — this function is
    always the enabled semantics."""
    e13 = state["e13"]
    ref = None
    if isinstance(exit_ref, str) and exit_ref.startswith("e"):
        ref = state.get(exit_ref)          # legacy "e55"/"e89" form
    else:
        try:
            p = int(exit_ref)
        except (TypeError, ValueError):
            p = None
        if p is not None:
            key = REF_KEYS.get(p)
            ref = state.get(key) if key else state.get("eref")
    if ref is None:
        ref = state["e89"]   # fail-safe: unresolvable ref → default
    for i in range(len(bars5)):
        ts_end = bars5[i]["ts"] + tf_s
        if ts_end <= after_ts:
            continue
        a, b = e13[i], ref[i]
        if a is None or b is None:
            continue
        if (a <= b) if side == "CE" else (a >= b):
            return ts_end
    return None


# ──────────────────────────────────────────────────────────────────────
# SL/TP level construction — SELL reproduces TMA_V1's SLTP_UNITS math
# EXACTLY; BUY is the long-side mirror. Pure + unit-tested.
# ──────────────────────────────────────────────────────────────────────
def sl_tp_levels(entry_price: float, action: str,
                 sl_val: float, tp_val: float,
                 sl_unit: str = "PCT", tp_unit: str = "PCT"
                 ) -> Tuple[Optional[float], Optional[float]]:
    """Returns (sl_level, tp_level); None = disabled. 0 input = off.
    SELL (short premium): SL ABOVE entry, TP BELOW, TP floored at 0.05.
      PCT: SL = ep*(1+v/100), TP = max(0.05, ep*(1-v/100))
      PTS: SL = ep+v,          TP = max(0.05, ep-v)
      ABS: SL = v iff v > ep else OFF; TP = max(0.05, v) iff v < ep else OFF
    BUY (long premium): SL BELOW entry, TP ABOVE, SL floored at 0.05.
      PCT: SL = max(0.05, ep*(1-v/100)), TP = ep*(1+v/100)
      PTS: SL = ep-v (OFF when <= 0),     TP = ep+v
      ABS: SL = v iff 0 < v < ep else OFF; TP = v iff v > ep else OFF
    Wrong-side ABS levels would trigger instantly → clamped OFF, fail-loud
    nonsense per the V1 doctrine. Long PTS SL pushing the level to/below
    zero clamps OFF the same way (an unhittable stop recorded as a level
    would just be dishonest)."""
    ep = float(entry_price)
    short = str(action).upper() == "SELL"
    su, tu = str(sl_unit or "PCT").upper(), str(tp_unit or "PCT").upper()
    slv, tpv = float(sl_val or 0), float(tp_val or 0)

    sl_level: Optional[float] = None
    tp_level: Optional[float] = None

    if slv > 0:
        if short:
            if su == "ABS":
                sl_level = slv if slv > ep else None
            elif su == "PTS":
                sl_level = ep + slv
            else:
                sl_level = ep * (1 + slv / 100.0)
        else:
            if su == "ABS":
                sl_level = slv if 0 < slv < ep else None
            elif su == "PTS":
                sl_level = (ep - slv) if (ep - slv) > 0 else None
            else:
                sl_level = max(0.05, ep * (1 - slv / 100.0))

    if tpv > 0:
        if short:
            if tu == "ABS":
                tp_level = max(0.05, tpv) if tpv < ep else None
            elif tu == "PTS":
                tp_level = max(0.05, ep - tpv)
            else:
                tp_level = max(0.05, ep * (1 - tpv / 100.0))
        else:
            if tu == "ABS":
                tp_level = tpv if tpv > ep else None
            elif tu == "PTS":
                tp_level = ep + tpv
            else:
                tp_level = ep * (1 + tpv / 100.0)

    return sl_level, tp_level
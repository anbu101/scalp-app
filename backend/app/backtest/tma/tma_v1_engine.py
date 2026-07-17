# backend/app/backtest/tma/tma_v1_engine.py
#
# ── TMA_V1 ENGINE ── Triple-EMA (5/13/89) spot-signal option-BUYING strategy.
# Signals computed on NIFTY SPOT 5m; execution on weekly option premium.
#
# LOCKED SPEC (2026-07-16, decisions D1–D10 confirmed):
#   * Signal TF 5m (aggregated from 1m spot, session-aligned 09:15), evaluated
#     on COMPLETED bars only. Entry executes on the NEXT 1m option candle after
#     the signal bar closes (PST fill conventions, verbatim).
#   * C1 (bigger trends): fires when BOTH EMA5 and EMA13 are above (below)
#     EMA89 and the previous bar did NOT satisfy that — i.e. the boolean
#     B_up = (e5>e89 AND e13>e89) transitions False→True (B_dn mirrored).
#     The two fast EMAs may cross EMA89 on DIFFERENT bars (staggered crosses);
#     the transition definition handles any gap natively — but BOTH crosses
#     must land WITHIN THE SAME SESSION (D1): the current "at-least-one-fast-
#     EMA-across" streak must have STARTED today, else the setup is stale.
#     Re-entry on a fresh False→True transition the same day is allowed (D2).
#     C1 exit: FIRST BREACH (D3) — either fast EMA closing back across EMA89.
#   * C2 (smaller trends): EMA5 crosses EMA13 while BOTH are already beyond
#     EMA89 on the signal bar (above for CE, below for PE).
#     C2 exit: EMA5 crosses back across EMA13.
#   * C2 DIFF FILTER (2026-07-16, chop guard — C2 ONLY, C1 untouched): the
#     SIGNED separation (e5−e13 for CE, e13−e5 for PE) must reach
#     c2_min_diff points before entry. If the cross bar N is short of the
#     threshold, the setup stays ARMED through N+1 and N+2 (c2_diff_bars=3
#     completed bars incl. N) and fires on the FIRST bar where the diff
#     qualifies with the full C2 state still valid (cross direction intact
#     AND both EMAs beyond EMA89 on that bar). A direction flip inside the
#     window CANCELS the pending setup (a re-cross arms a fresh window — no
#     double-fire); a bar merely failing the EMA89 precondition neither
#     fires nor cancels. c2_min_diff = 0 disables the filter (legacy
#     fire-at-cross behavior). Diag: c2_diff_armed / c2_diff_fired_late /
#     c2_diff_expired / c2_diff_flip_cancel.
#   * C1 and C2 are fully INDEPENDENT (D4/D5): both may fire on the same bar,
#     both may hold positions simultaneously; max ONE open position per
#     condition; each condition has its own premium cap / lots / daily cap /
#     SL% / TP%.
#   * SL/TP are % of OPTION ENTRY PREMIUM (D6), 0 = disabled. SL triggers on
#     option 1m LOW, fills AT the SL level; TP triggers on option 1m HIGH,
#     fills AT the TP level; both in one minute → SL wins + ambiguous flag
#     (house convention).
#   * Crossover exits are evaluated on COMPLETED 5m spot bars (D7); the option
#     leg exits at the close of the 1m option candle STAMPED at the 5m bar's
#     completion time (first tradable price after the exit is known — mirrors
#     the entry-fill convention; no lookahead).
#   * EOD square-off at exit_time (close of the last option candle strictly
#     before it). Session start/end bound NEW ENTRIES only; crossover/SL/TP
#     exits stay live until EOD.
#
# ── CROSS-DAY WARMUP ────────────────────────────────────────────────────
#   EMA89 on 5m bars can NEVER warm up inside one session (~75 bars/day <
#   89-bar SMA seed). The runner supplies the prior 3 completed sessions'
#   1m spot via `warmup_sessions` (PST_XDAY_WARMUP pattern): each session is
#   aggregated independently against its OWN day_start (no overnight straddle
#   bar), completed bars concatenated chronologically, EMAs run over the
#   continuous stream. Signal EMISSION is restricted to current-session bars.
#   Consequence (documented, honest): day 1 of a range has zero valid EMA89
#   bars, day 2 warms mid-session, day 3+ is fully warm from 09:15. The diag
#   (blocked_warmup) reports it.
#
# ── POSITIONAL MODE (2026-07-16) ───────────────────────────────────────
#   trade_mode INTRADAY (default) keeps EVERYTHING above byte-identical:
#   every open trade is squared off daily at exit_time via simulate_position.
#   trade_mode POSITIONAL removes the daily square-off: a position carries
#   overnight and closes only on (a) SL/TP, monitored every session on the
#   option 1m candles, (b) its crossover-reversal exit, evaluated on every
#   carried day's completed 5m spot bars, (c) exit_time on the CONTRACT'S OWN
#   EXPIRY DAY (reason EOD), or (d) exit_time on the final day of the
#   backtest range (reason EOR — a position cannot outlive the simulation).
#   Carried-day monitoring runs the FULL session (exit_time is ignored on
#   non-expiry days, per spec). monitor_position_day() below advances one
#   open position through one day; the runner owns the cross-day carry state.
#   GAP FILLS (positional only): overnight gaps are real — if a candle OPENS
#   already through a level, the fill is at the OPEN, not at the level
#   (SL: open <= sl -> fill at open; TP: open >= tp -> fill at open). The
#   intraday path keeps the legacy at-level convention untouched.
#
# Pure module: consumes candle dicts, returns dicts. Runner does corpus,
# selection, charges, persistence. No app imports on the hot path.

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.backtest.pst.pst_indicators import aggregate
except ImportError:  # standalone tests
    from pst_indicators import aggregate  # type: ignore

TF_S_DEFAULT = 300  # 5m


# ──────────────────────────────────────────────────────────────────────
# EMA (SMA-seeded, TradingView convention)
# ──────────────────────────────────────────────────────────────────────
def ema_series(closes: List[float], period: int) -> List[Optional[float]]:
    """None for the first period-1 values; index period-1 = SMA seed; then
    the standard recursive EMA. SMA seeding (vs first-value seeding) matches
    TradingView and converges fastest — with 3 prior warmup sessions the
    residual dependence on pre-history is <1% of weight for EMA89."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


# ──────────────────────────────────────────────────────────────────────
# warmup assembly (PST_XDAY_WARMUP pattern, verbatim semantics)
# ──────────────────────────────────────────────────────────────────────
def warmup_bars(warmup_sessions: List[Tuple[List[dict], int]],
                tf_minutes: int) -> List[dict]:
    """Aggregate each prior session independently against its OWN day_start,
    keep only completed bars, return concatenated oldest-first."""
    out: List[dict] = []
    for spot_1m, ds in warmup_sessions:
        if not spot_1m:
            continue
        out.extend(b for b in aggregate(spot_1m, tf_minutes, ds) if b["complete"])
    out.sort(key=lambda b: b["ts"])
    return out


# ──────────────────────────────────────────────────────────────────────
# indicator state over the continuous (warmup + today) 5m stream
# ──────────────────────────────────────────────────────────────────────
def compute_state(bars5: List[dict], *, fast: int = 5, mid: int = 13,
                  slow: int = 89) -> Dict:
    closes = [b["close"] for b in bars5]
    e5 = ema_series(closes, fast)
    e13 = ema_series(closes, mid)
    e89 = ema_series(closes, slow)
    n = len(bars5)
    b_up: List[Optional[bool]] = [None] * n
    b_dn: List[Optional[bool]] = [None] * n
    a5_up: List[bool] = [False] * n   # e5 strictly above e89 (valid bars only)
    a13_up: List[bool] = [False] * n
    a5_dn: List[bool] = [False] * n   # e5 strictly below e89
    a13_dn: List[bool] = [False] * n
    for i in range(n):
        if e5[i] is None or e13[i] is None or e89[i] is None:
            continue
        a5_up[i] = e5[i] > e89[i]
        a13_up[i] = e13[i] > e89[i]
        a5_dn[i] = e5[i] < e89[i]
        a13_dn[i] = e13[i] < e89[i]
        b_up[i] = a5_up[i] and a13_up[i]
        b_dn[i] = a5_dn[i] and a13_dn[i]
    return {"e5": e5, "e13": e13, "e89": e89,
            "b_up": b_up, "b_dn": b_dn,
            "a5_up": a5_up, "a13_up": a13_up,
            "a5_dn": a5_dn, "a13_dn": a13_dn}


def _streak_start_idx(i: int, a5: List[bool], a13: List[bool]) -> int:
    """Index of the FIRST bar of the current at-least-one-EMA-across streak
    ending at i. Walking back stops at the last bar where NEITHER fast EMA
    was across (or history runs out — caller treats index 0 with the streak
    still alive as 'start unknown/stale')."""
    j = i
    while j - 1 >= 0 and (a5[j - 1] or a13[j - 1]):
        j -= 1
    return j


# ──────────────────────────────────────────────────────────────────────
# signal generation (spot only)
# ──────────────────────────────────────────────────────────────────────
def build_signals(bars5: List[dict], warmup_count: int, session0: int,
                  entry_start_ts: int, entry_end_ts: int,
                  *, tf_s: int = TF_S_DEFAULT, state: Optional[Dict] = None,
                  fast: int = 5, mid: int = 13, slow: int = 89) -> Dict:
    """bars5 = warmup(completed) + today(completed), chronological;
    warmup_count = number of warmup bars (index of today's first bar).
    Returns {"signals": [{ts, side, cond, spot}], "state", "diag"}.
    `ts` is the SIGNAL BAR COMPLETION time (bar.ts + tf_s) — the runner
    enters on the 1m option candle STAMPED ts. Entry window [entry_start_ts,
    entry_end_ts) gates emission; exits are NOT gated here."""
    st = state or compute_state(bars5, fast=fast, mid=mid, slow=slow)
    b_up, b_dn = st["b_up"], st["b_dn"]
    e5, e13, e89 = st["e5"], st["e13"], st["e89"]
    a5_up, a13_up = st["a5_up"], st["a13_up"]
    a5_dn, a13_dn = st["a5_dn"], st["a13_dn"]

    signals: List[dict] = []
    diag = {"bars5_today": max(0, len(bars5) - warmup_count),
            "warmup_bars5": warmup_count,
            "c1_events": 0,
            "c1_stale": 0, "blocked_warmup": 0, "blocked_session": 0}

    start_i = max(1, warmup_count)
    for i in range(start_i, len(bars5)):
        if bars5[i]["ts"] < session0:
            continue  # defensive: never emit on a warmup-session bar
        ts_end = bars5[i]["ts"] + tf_s
        emitted_here: List[dict] = []

        # ── C1: B False→True transition, streak started this session ──
        for b_arr, a5x, a13x, side in ((b_up, a5_up, a13_up, "CE"),
                                       (b_dn, a5_dn, a13_dn, "PE")):
            cur, prev = b_arr[i], b_arr[i - 1]
            if cur is not True:
                continue
            if prev is None:
                diag["blocked_warmup"] += 1
                continue
            if prev is True:
                continue
            j = _streak_start_idx(i, a5x, a13x)
            # Stale iff the streak's first bar predates today's session. A
            # streak alive at index 0 with bars5[0] BEFORE session0 means it
            # started on (or beyond) a prior day → stale; if bars5[0] IS
            # today's open bar there is nothing earlier, so it started today.
            if bars5[j]["ts"] < session0:
                diag["c1_stale"] += 1     # first cross was on a prior day
                continue
            diag["c1_events"] += 1
            emitted_here.append({"ts": ts_end, "side": side, "cond": "C1",
                                 "spot": bars5[i]["close"]})

        for s in emitted_here:
            if not (entry_start_ts <= s["ts"] < entry_end_ts):
                diag["blocked_session"] += 1
                continue
            signals.append(s)

    diag["signals"] = len(signals)
    return {"signals": signals, "state": st, "diag": diag}


# ──────────────────────────────────────────────────────────────────────
# crossover-reversal exit schedule
# ──────────────────────────────────────────────────────────────────────
def xover_exit_ts(bars5: List[dict], state: Dict, cond: str, side: str,
                  after_ts: int, *, tf_s: int = TF_S_DEFAULT) -> Optional[int]:
    """First 5m bar COMPLETION time strictly greater than after_ts at which
    the crossover-reversal exit for (cond, side) holds on that completed bar.
      C1 CE: first breach — NOT (e5>e89 AND e13>e89)   (D3)
      C1 PE: first breach — NOT (e5<e89 AND e13<e89)
    `side` is the TREND side the signal was emitted with (CE = bullish),
    NOT the sold option type — the v2 runner maps trend→sold side itself.
    Returns None if no such bar exists in the stream (EOD handles it)."""
    b_up, b_dn = state["b_up"], state["b_dn"]
    for i in range(len(bars5)):
        ts_end = bars5[i]["ts"] + tf_s
        if ts_end <= after_ts:
            continue
        b = b_up[i] if side == "CE" else b_dn[i]
        if b is False:
            return ts_end
    return None


# ──────────────────────────────────────────────────────────────────────
# POSITIONAL: advance one open position through ONE day's candles
# ──────────────────────────────────────────────────────────────────────
def monitor_position_day(pos: Dict, opt_candles: List[dict],
                         xover_ts: Optional[int],
                         hard_close_ts: Optional[int],
                         hard_close_reason: str = "EOD",
                         mtm_cut_ts: Optional[int] = None) -> Optional[Dict]:
    # pos: {side, entry_ts, entry_price, sl_price, tp_price, watch_from,
    # last_close, last_ts} - sl/tp None when disabled; watch_from is
    # entry_ts+60 on the entry day and 0 on carried days; last_close/last_ts
    # persist across days for data-gap closes (this function mutates them).
    #
    # Per 1m candle chronologically (ts >= watch_from; ts < hard_close_ts
    # when a bound is set):
    #   GAP/level SL: open <= sl -> fill at OPEN; else low <= sl -> AT sl.
    #   GAP/level TP: open >= tp -> fill at OPEN; else high >= tp -> AT tp.
    #   SL + TP in one candle -> SL wins + ambiguous (house convention; a
    #   gap open through BOTH fills at open, reason SL).
    #   XOVER: candle ts >= xover_ts and still open -> fill at that
    #   candle's CLOSE (same convention as intraday).
    #   MTM_CUT (── NEG_MTM_EOD_CUT ──, positional-only, opt-in): when
    #   mtm_cut_ts is set, at the FIRST candle with ts >= mtm_cut_ts the
    #   position's mark (last 1m close STRICTLY BEFORE the cut time, i.e.
    #   last_close) is compared to entry_price, GROSS of charges. Strictly
    #   negative -> close at that mark (exit at last_ts/last_close, reason
    #   MTM_CUT). Zero/positive -> the check disarms and monitoring
    #   continues normally (the position may still SL/TP/XOVER later that
    #   day or carry overnight). If the day's data ends BEFORE the cut time
    #   the check runs post-loop on the best-available mark — cutting a
    #   negative position on stale data honors the risk intent better than
    #   silently carrying it. The runner passes mtm_cut_ts only when no
    #   hard close applies today (expiry/EOR days close everything anyway).
    # If hard_close_ts is set (expiry day / end of range) and the position
    # is still open after the loop -> close at the last candle strictly
    # before the bound (fallback: carried last_close/last_ts on a data-gap
    # day) with hard_close_reason. Returns exit dict or None (survives).
    sl_price, tp_price = pos.get("sl_price"), pos.get("tp_price")
    # ── SPREAD_V2 ── action BUY (legacy long) or SELL (short premium):
    # SELL inverts every trigger — SL when premium RISES, TP when it FALLS,
    # gaps through a level fill at the open accordingly, and negative MTM
    # means mark ABOVE entry.
    short = str(pos.get("action", "BUY")).upper() == "SELL"
    watch_from = int(pos.get("watch_from") or 0)
    exit_ts = exit_price = None
    reason = None
    ambiguous = False
    mtm_pending = mtm_cut_ts is not None   # ── NEG_MTM_EOD_CUT ──

    for c in sorted(opt_candles, key=lambda x: x["ts"]):
        ts = int(c["ts"])
        if ts < watch_from:
            continue
        if hard_close_ts is not None and ts >= hard_close_ts:
            break
        # ── NEG_MTM_EOD_CUT BEGIN ── decision at the cut instant uses the
        # mark strictly BEFORE it (last_close is the prior candle's close —
        # this candle has not been folded in yet).
        if mtm_pending and ts >= mtm_cut_ts:
            mtm_pending = False
            mark = pos.get("last_close", pos["entry_price"])
            if (mark > pos["entry_price"]) if short else (mark < pos["entry_price"]):
                exit_ts = pos.get("last_ts", pos["entry_ts"])
                exit_price = pos.get("last_close", pos["entry_price"])
                reason = "MTM_CUT"
                break
        # ── NEG_MTM_EOD_CUT END ──
        o, h, l, cl = (float(c["open"]), float(c["high"]),
                       float(c["low"]), float(c["close"]))
        pos["last_close"], pos["last_ts"] = cl, ts
        if short:
            gap_sl = sl_price is not None and o >= sl_price
            gap_tp = tp_price is not None and o <= tp_price
            hit_sl = sl_price is not None and h >= sl_price
            hit_tp = tp_price is not None and l <= tp_price
        else:
            gap_sl = sl_price is not None and o <= sl_price
            gap_tp = tp_price is not None and o >= tp_price
            hit_sl = sl_price is not None and l <= sl_price
            hit_tp = tp_price is not None and h >= tp_price
        if hit_sl:
            exit_ts, exit_price = ts, (o if gap_sl else sl_price)
            reason, ambiguous = "SL", bool(hit_tp)
            break
        if hit_tp:
            exit_ts, exit_price = ts, (o if gap_tp else tp_price)
            reason = "TP"
            break
        if xover_ts is not None and ts >= xover_ts:
            exit_ts, exit_price, reason = ts, cl, "XOVER"
            break

    if exit_ts is None:
        if hard_close_ts is None:
            # ── NEG_MTM_EOD_CUT ── data ended before the cut time: apply
            # the check on the best-available mark rather than carrying a
            # negative position on a data gap.
            _mk = pos.get("last_close", pos["entry_price"])
            if mtm_pending and \
                    ((_mk > pos["entry_price"]) if short else (_mk < pos["entry_price"])):
                return {"side": pos["side"], "entry_ts": pos["entry_ts"],
                        "entry_price": pos["entry_price"],
                        "sl_price": sl_price, "tp_price": tp_price,
                        "exit_ts": pos.get("last_ts", pos["entry_ts"]),
                        "exit_price": pos.get("last_close", pos["entry_price"]),
                        "exit_reason": "MTM_CUT", "ambiguous_fill": False}
            return None                      # survives to the next session
        exit_ts = pos.get("last_ts", pos["entry_ts"])
        exit_price = pos.get("last_close", pos["entry_price"])
        reason = hard_close_reason

    return {"side": pos["side"], "action": ("SELL" if short else "BUY"),
            "entry_ts": pos["entry_ts"],
            "entry_price": pos["entry_price"],
            "sl_price": sl_price, "tp_price": tp_price,
            "exit_ts": exit_ts, "exit_price": exit_price,
            "exit_reason": reason, "ambiguous_fill": ambiguous}
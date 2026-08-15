# backend/app/backtest/gc/gc_v1_engine.py
#
# ── GC_V1_ENGINE ── first-candle breakout-retest with SL-flip re-entry
# chain, on NIFTY SPOT candles at a selectable timeframe (1/3/5/10/15m).
#
# PURE MODULE by design (IC/TMA doctrine): no app imports, no DB, no I/O.
# The runner (backtest_gc_runner) feeds it resampled spot candles + config
# and maps the returned SPOT-DECISION chain onto option fills — so every
# branch of the state machine is unit-tested against synthetic candles with
# hand-computed expectations (test_gc_engine.py), per house rule.
#
# LOCKED DECISIONS (D1–D11, confirmed 2026-08-14):
#   D1=a  FIRST-entry SL lookback = the `sl_lookback` tf-candles BEFORE C1,
#         i.e. the PREVIOUS SESSION's tail (runner supplies prev_tail).
#         RE-entry lookback = the candles before the re-entry candle
#         (same-day sequence; may reach into prev_tail when early).
#   D2    F1/G1 = the MOST RECENT qualifying candle in the window (nearest
#         first, scanning backwards), not the extreme.
#   D3    The breakout/arming candle can NEVER be its own retrace — the
#         retrace candle must be STRICTLY AFTER the arming candle. Same for
#         a flip: the SL-exit candle never triggers its own flip re-entry.
#   D4    signal_mode "latest" (default): while armed and un-entered, an
#         opposite-side breakout close re-arms the other side (arm index
#         moves). "first": the first arm is sticky for the initial entry.
#         Flip arms are ALWAYS fixed-side (the spec dictates the side).
#   D5    All decisions on CANDLE CLOSE; entry/exit map to the close of the
#         decision candle (runner fills options at that minute's 1m close).
#   D7    Flip chain is UNLIMITED until max_trades_per_day is reached.
#   D10   If the entry candle's own close already breaches the freshly
#         built SL, the trade exits ON THAT SAME CANDLE (entry_idx ==
#         exit_idx, spot-flat, P&L ≈ -charges). Counted, flagged, and it
#         STILL arms the next flip.
#
# LEVEL SEMANTICS (exact, asymmetric on purpose — per spec):
#   * Breakout ARM     : close STRICTLY beyond the level (> H1 / < L1).
#   * Retrace TRIGGER  : range TOUCH of the level (low <= H1 / high >= L1).
#   * SL HIT           : close STRICTLY beyond the SL level
#                        (CE trade: close < sl · PE trade: close > sl).
#     A wick through the SL that closes back inside does NOT exit.
#   * CE entries (initial or flip) always trigger on a touch of H1;
#     PE entries always trigger on a touch of L1. The flip after a PE SL
#     therefore waits for price to come back DOWN to H1 (spec: "touches
#     back the H1"), and vice versa.
#
# SL CONSTRUCTION (per entry, D1/D2):
#   CE entry → scan the `sl_lookback` candles before the reference point,
#     most-recent-first, for a candle with close < L1 → SL = that candle's
#     LOW. None found → SL = L1 (fallback, flagged).
#   PE entry → close > H1 → SL = that candle's HIGH. None → SL = H1.
#   Reference point: first entry → C1 (window = prev_tail tail, D1=a);
#   flip entry → the entry candle itself (window = preceding candles in the
#   combined prev_tail+today sequence).
#
# The engine knows NOTHING about options, premiums, lots or MTM day caps —
# those are runner concerns (the runner may truncate the returned chain
# when a max-profit/loss day cap trips; the spot chain itself is invariant).

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TFCandle:
    """One resampled spot candle. `last1m_ts` = the ts of the LAST 1m bar
    that formed this candle — the minute whose option close the runner uses
    for any fill decided at this candle's close (D5)."""
    ts: int          # bucket start, epoch seconds (IST grid)
    open: float
    high: float
    low: float
    close: float
    last1m_ts: int


@dataclass
class GCSpotTrade:
    """One spot-decision trade in the day's chain. All ts values are the
    DECISION MINUTE (last1m_ts of the decision candle)."""
    signal_side: str            # "CE" | "PE" (the spot signal; runner maps
                                # to the option side per BUY/SELL mode)
    flip_seq: int               # 0 = initial entry, 1 = first flip, ...
    entry_idx: int              # index into today's tf candle list
    entry_ts: int
    entry_spot: float           # decision candle close
    sl_level: float
    sl_fallback: bool           # True = no qualifying lookback candle,
                                # SL fell back to H1/L1
    exit_idx: Optional[int] = None
    exit_ts: Optional[int] = None
    exit_spot: Optional[float] = None
    exit_reason: Optional[str] = None   # "SL" | "EOD"
    same_candle_sl: bool = False        # D10 one-candle trade
    sl_capped: bool = False             # GC_SL_CAP rejected a prev-day anchor


def resample_spot(candles_1m: List[dict], tf_min: int,
                  session_start_epoch: int) -> List[TFCandle]:
    """Session-anchored resample: buckets start at session_start + k*tf.
    1m bars BEFORE the session start (there are none on NSE, but the corpus
    has carried odd prints before) are dropped rather than folded into a
    phantom pre-session bucket. tf_min == 1 passes bars through 1:1 on the
    same dataclass so every consumer sees one shape."""
    tf_s = int(tf_min) * 60
    out: List[TFCandle] = []
    cur: Optional[TFCandle] = None
    cur_bucket = None
    for r in candles_1m:
        ts = int(r["ts"])
        if ts < session_start_epoch:
            continue
        bucket = session_start_epoch + ((ts - session_start_epoch) // tf_s) * tf_s
        if bucket != cur_bucket:
            if cur is not None:
                out.append(cur)
            cur_bucket = bucket
            cur = TFCandle(ts=bucket, open=float(r["open"]),
                           high=float(r["high"]), low=float(r["low"]),
                           close=float(r["close"]), last1m_ts=ts)
        else:
            cur.high = max(cur.high, float(r["high"]))
            cur.low = min(cur.low, float(r["low"]))
            cur.close = float(r["close"])
            cur.last1m_ts = ts
    if cur is not None:
        out.append(cur)
    return out


def _build_sl(window: List[TFCandle], n_prev: int, side: str,
              h1: float, l1: float, entry_spot: float,
              cap_pts: Optional[float]) -> tuple[float, bool, bool]:
    """D1/D2: scan `window` most-recent-first. CE → first candle with
    close < L1 gives SL = its low, fallback L1. PE → close > H1 gives
    SL = its high, fallback H1.

    ── GC_SL_CAP (D12/D13) ── window[:n_prev] are PREV-DAY candles. When
    the D2 winner is one of them and |entry_spot − anchor| > cap_pts, the
    anchor is rejected → level fallback, capped-flag set. Today-donated
    anchors and the fallback itself are never capped.

    Returns (sl_level, fallback_used, cap_rejected)."""
    fallback = float(l1) if side == "CE" else float(h1)
    for j in range(len(window) - 1, -1, -1):
        c = window[j]
        hit = (c.close < l1) if side == "CE" else (c.close > h1)
        if not hit:
            continue
        anchor = float(c.low) if side == "CE" else float(c.high)
        if (cap_pts is not None and j < n_prev
                and abs(entry_spot - anchor) > cap_pts):
            return fallback, True, True
        return anchor, False, False
    return fallback, True, False


def simulate_gc_day(today: List[TFCandle], prev_tail: List[TFCandle],
                    cfg: dict) -> Dict:
    """Run the GC state machine over one session.

    cfg keys (all required, runner normalizes):
      exit_epoch        int — epoch second of the EOD square-off boundary;
                        only candles whose END (ts + tf*60) <= exit_epoch
                        participate; an open trade exits at the LAST such
                        candle's close (reason EOD).
      tf_s              int — timeframe seconds (for the END computation).
      max_trades        int — day cap on entries (0 = unlimited, per D7 the
                        UI default is 4).
      entry_cutoff_epoch int | None — GC_ENTRY_CUTOFF: no NEW entries
                        (initial or flip) whose decision candle closes
                        AFTER this epoch (<= passes). A blocked touch
                        HALTs further entries (time only advances); an
                        already-open trade still runs to SL/EOD. None =
                        no cutoff.
      signal_mode       "latest" | "first"  (D4).
      sl_lookback       int — window size (default 10).
      c1_range_max_pct  float percent (0 = off) — C1 VOLATILITY GATE: skip
                        the whole day when (H1 - L1) is STRICTLY GREATER
                        than pct% of `prev_close`. FAIL-CLOSED: gate on but
                        prev_close missing → day skipped (a risk filter
                        that cannot compute its reference must not pass).
      prev_close        float | None — previous session's last spot close
                        (the gate's reference; runner supplies it).
      max_sl_pct        float percent (0 = off) — GC_SL_CAP (D12/D13,
                        confirmed 2026-08-14): gap-day protection. When the
                        SL anchor is donated by a PREV-DAY candle and its
                        distance from the ENTRY SPOT exceeds pct% of
                        prev_close, the anchor is REJECTED and the SL falls
                        back to L1/H1 (today's structure). Today-donated
                        anchors are never capped; the level fallback is
                        never capped (terminal, and bounded by the C1 gate
                        when that is on). No deeper scan past a rejected
                        prev-day anchor — older prev-day candles are
                        staler, not safer.

    Returns {"trades": [GCSpotTrade...], "diag": {...}}. Trades are in
    chronological order; the chain is exactly as the spec's flip rule
    produces it. The runner may truncate it on an MTM day cap."""
    tf_s = int(cfg["tf_s"])
    exit_epoch = int(cfg["exit_epoch"])
    max_trades = int(cfg.get("max_trades") or 0)
    cutoff_epoch = cfg.get("entry_cutoff_epoch")   # ── GC_ENTRY_CUTOFF ──
    signal_mode = str(cfg.get("signal_mode") or "latest").lower()
    lookback = int(cfg.get("sl_lookback") or 10)
    c1_range_pct = float(cfg.get("c1_range_max_pct") or 0)
    max_sl_pct = float(cfg.get("max_sl_pct") or 0)
    prev_close = cfg.get("prev_close")

    diag = {"session_candles": 0, "no_c1": 0, "no_breakout": 0,
            "c1_range_skip": 0, "c1_range_no_ref": 0,
            "c1_range_pts": None,
            "armed_no_retrace": 0, "rearm_switches": 0,
            "entries": 0, "flip_entries": 0, "same_candle_sl": 0,
            "sl_fallback_entries": 0, "sl_cap_fallbacks": 0,
            "cap_blocked_flips": 0, "cutoff_blocked_entries": 0,
            "prev_tail_len": len(prev_tail)}

    # Session scope: candles fully closed by exit_epoch. The EOD exit lands
    # on the LAST in-scope candle's close.
    sess = [c for c in today if (c.ts + tf_s) <= exit_epoch]
    diag["session_candles"] = len(sess)
    if not sess:
        diag["no_c1"] = 1
        return {"trades": [], "diag": diag}

    c1 = sess[0]
    h1, l1 = float(c1.high), float(c1.low)
    # ── GC_C1_RANGE_GATE BEGIN ── skip the day when C1's range exceeds
    # pct% of the previous session's close (strict >, per spec). 0 = off.
    if c1_range_pct > 0:
        diag["c1_range_pts"] = round(h1 - l1, 2)
        if prev_close is None or float(prev_close) <= 0:
            diag["c1_range_no_ref"] = 1          # fail-closed
            return {"trades": [], "diag": diag}
        if (h1 - l1) > float(prev_close) * c1_range_pct / 100.0:
            diag["c1_range_skip"] = 1
            return {"trades": [], "diag": diag}
    # ── GC_C1_RANGE_GATE END ──
    seq: List[TFCandle] = list(prev_tail) + sess
    base = len(prev_tail)               # index of C1 in seq
    last_i = len(sess) - 1
    # ── GC_SL_CAP ── cap in points; needs prev_close (no prev_close ⇒ no
    # prev_tail ⇒ no prev-day anchors exist ⇒ vacuously off).
    cap_pts = (float(prev_close) * max_sl_pct / 100.0
               if max_sl_pct > 0 and prev_close else None)

    trades: List[GCSpotTrade] = []
    state = "IDLE"                      # IDLE|ARMED|FLIP_ARMED|IN_TRADE|HALTED
    armed_side: Optional[str] = None
    arm_i = -1                          # retrace must satisfy i > arm_i (D3)
    cur: Optional[GCSpotTrade] = None

    def _enter(side: str, i: int, flip_seq: int) -> GCSpotTrade:
        c = sess[i]
        if flip_seq == 0:
            # D1=a — window is the tail BEFORE C1 (previous session)
            lo_g = max(0, base - lookback)
            window = seq[lo_g:base]
        else:
            g = base + i
            lo_g = max(0, g - lookback)
            window = seq[lo_g:g]
        n_prev = max(0, base - lo_g)     # prev-day candles at window head
        sl, fb, capped = _build_sl(window, n_prev, side, h1, l1,
                                   float(c.close), cap_pts)
        return GCSpotTrade(signal_side=side, flip_seq=flip_seq,
                           entry_idx=i, entry_ts=c.last1m_ts,
                           entry_spot=float(c.close),
                           sl_level=sl, sl_fallback=fb, sl_capped=capped)

    def _close(t: GCSpotTrade, i: int, reason: str,
               same_candle: bool = False) -> None:
        c = sess[i]
        t.exit_idx = i
        t.exit_ts = c.last1m_ts
        t.exit_spot = float(c.close)
        t.exit_reason = reason
        t.same_candle_sl = same_candle

    def _sl_hit(t: GCSpotTrade, close: float) -> bool:
        return (close < t.sl_level) if t.signal_side == "CE" \
            else (close > t.sl_level)

    def _arm_flip(exited: GCSpotTrade, i: int) -> None:
        """Post-SL: arm the opposite side. Level is implied by the side
        (CE→H1 touch, PE→L1 touch). Cap check happens at ARM time so a
        capped day shows the block in DIAG rather than a silent idle."""
        nonlocal state, armed_side, arm_i
        if max_trades and len(trades) >= max_trades:
            state = "HALTED"
            diag["cap_blocked_flips"] += 1
            return
        armed_side = "CE" if exited.signal_side == "PE" else "PE"
        arm_i = i
        state = "FLIP_ARMED"

    i = 0
    while i <= last_i:
        c = sess[i]
        close = float(c.close)

        if state == "IN_TRADE":
            if _sl_hit(cur, close):
                _close(cur, i, "SL")
                exited = cur
                cur = None
                _arm_flip(exited, i)
            elif i == last_i:
                _close(cur, i, "EOD")
                cur = None
                state = "HALTED"
            i += 1
            continue

        entered_this_candle = False
        if state in ("ARMED", "FLIP_ARMED") and i > arm_i:
            touched = (c.low <= h1) if armed_side == "CE" else (c.high >= l1)
            if touched and cutoff_epoch is not None \
                    and (c.ts + tf_s) > int(cutoff_epoch):
                # ── GC_ENTRY_CUTOFF ── decision candle closes after the
                # cutoff → entry blocked; nothing later can enter either.
                diag["cutoff_blocked_entries"] += 1
                state = "HALTED"
                touched = False
            if touched:
                flip_seq = 0 if state == "ARMED" else \
                    (trades[-1].flip_seq + 1 if trades else 1)
                t = _enter(armed_side, i, flip_seq)
                trades.append(t)
                diag["entries"] += 1
                if flip_seq > 0:
                    diag["flip_entries"] += 1
                if t.sl_fallback:
                    diag["sl_fallback_entries"] += 1
                if t.sl_capped:
                    diag["sl_cap_fallbacks"] += 1   # ── GC_SL_CAP ──
                entered_this_candle = True
                armed_side = None
                if _sl_hit(t, close):
                    # D10 — entry candle's own close breaches the fresh SL
                    _close(t, i, "SL", same_candle=True)
                    diag["same_candle_sl"] += 1
                    _arm_flip(t, i)
                elif i == last_i:
                    _close(t, i, "EOD")
                    state = "HALTED"
                else:
                    cur = t
                    state = "IN_TRADE"

        if not entered_this_candle and state in ("IDLE", "ARMED") and i >= 1:
            # Initial arming (breakout close). D4: "latest" lets an opposite
            # breakout re-arm; "first" makes the first arm sticky. A repeat
            # SAME-side breakout never moves arm_i (any later toucher is
            # after both candles, so the earlier anchor is the permissive
            # and correct one).
            if close > h1 and armed_side != "CE":
                if state == "IDLE" or signal_mode == "latest":
                    if state == "ARMED":
                        diag["rearm_switches"] += 1
                    armed_side, arm_i, state = "CE", i, "ARMED"
            elif close < l1 and armed_side != "PE":
                if state == "IDLE" or signal_mode == "latest":
                    if state == "ARMED":
                        diag["rearm_switches"] += 1
                    armed_side, arm_i, state = "PE", i, "ARMED"
        i += 1

    if not trades:
        if state in ("ARMED",):
            diag["armed_no_retrace"] = 1
        elif state == "IDLE":
            diag["no_breakout"] = 1
    elif state in ("ARMED", "FLIP_ARMED"):
        # chain ended armed with no further retrace before EOD — informative
        diag["armed_no_retrace"] = 1

    return {"trades": trades, "diag": diag}
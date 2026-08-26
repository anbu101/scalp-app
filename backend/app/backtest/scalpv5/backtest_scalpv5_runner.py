# backend/app/backtest/scalpv5/backtest_scalpv5_runner.py
#
# SCALP_V5 backtest runner. Option BUYING on NIFTY weekly options, 3-minute
# candles, indicators on the OPTION contract itself (NOT futures).
#
# FAITHFUL REPLAY — drives the SAME classes the live engine uses:
#   * IndicatorEnginePineV19  (real EMA8 / EMA20_low / EMA20_high / RSI)
#   * ScalpV5Engine           (real cross-up entry + EMA_EXIT)
# so the backtest signal == the live signal, candle for candle.
#
# ============================================================================
# SELECTION + ARBITRATION  — now matches the PROVEN V3 / V1 live behaviour,
# reusing the same backtest_selector.py the V1/V3 runner uses.
#
#   MOMENT 1 — SELECTION (every 120s, the live :30 grid). The live selection
#   loop calls OptionSelector(premium band, ATM±800, nearest-2-per-side). The
#   backtest replays this with build_selection_timeline / active_snapshot_for_ts
#   from backtest_selector.py: median-ATM (no spot data needed), ATM±800, the
#   per-side nearest-2 strikes. A BUY may only enter on a contract that is in
#   the selection snapshot active at the signal candle's close — exactly the
#   live _is_selected_signal gate (CE/PE_NOT_SELECTED).
#
#   MOMENT 2 — SAME-CANDLE ARBITRATION. When >1 SELECTED contract fires a BUY on
#   the SAME 3m candle, live V3 elects the HIGHEST entry premium (symbol string
#   as the deterministic tie-break) — ScalpV3TickEngine._arbitrate_after_window:
#   max(candidates, key=(entry_price, symbol)). Only that ONE winner enters, and
#   only if the global single-trade slot is free. V5 must match V3, so the same
#   election runs here.
#
# This REPLACES the previous "scan every strike, premium-band post-filter"
# selection, which fired BUYs on far-OTM/far-ITM contracts that live V5 never
# watches (the 2024 4-month-clustering bug). NO spot backfill is needed: ATM is
# inferred as the median of the band-surviving strikes, exactly as the live
# OptionSelector._infer_atm does.
# ============================================================================
#
# STRATEGY (matches scalpv5_engine.py doc):
#   ENTRY (completed 3m candle, per contract):
#     green ∧ EMA8 crosses above EMA20_HIGH (transition only) ∧ close>EMA20_HIGH
#     → BUY at candle.close. sl=entry-sl_points (None if 0); tp=entry+tp_points.
#   EXIT (first of):
#     EMA_EXIT : a completed 3m candle of the HELD contract closes < ema20_high
#     SL       : option 1m low  <= sl_price   (intrabar, pessimistic)
#     TP       : option 1m high >= tp_price
#     MAX_LOSS / MAX_PROFIT : session MTM cap (gross, like the live latch)
#     EOD      : session_end square-off
#   NO time-based exit. Ambiguous SL+TP in the same 1m → pessimistic SL-first.
#
# Read-only on the corpus. P&L LONG = (exit - entry) * qty, qty = lots * 65.
# Charges via charges_for_long_trade (STT on the exit/sell leg).

from __future__ import annotations

# Safer form — anchors for PyInstaller, but tolerant if a dep is unavailable
# at module-import time (the real import still happens lazily in the function).
try:
    import app.backtest.data.candle_source  # noqa: F401
    import app.backtest.engine.backtest_selector  # noqa: F401
except Exception:
    pass

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE = 65            # NIFTY
STRIKE_STEP = 50         # NIFTY
WARMUP_CANDLES = 200     # 3m bars fed for indicator convergence (engine uses 500 live;
                         # 200 is plenty for EMA8/EMA20+SMA9 + RSI(5) to latch ready)


@dataclass
class V5Trade:
    side: str                  # CE | PE
    symbol: str
    strike: float
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    qty: int
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    ambiguous: bool = False
    # ── fields persist_run (non-hedge branch) reads as attributes ──
    instrument_type: str = "CE"     # CE | PE (mirrors side)
    expiry: str = ""                # ISO date of the contract
    direction: str = "LONG"         # V5 is always LONG
    max_adverse: float = 0.0        # not tracked in V5 backtest → 0
    max_favorable: float = 0.0      # not tracked in V5 backtest → 0

    # persist_run reads t.pnl / t.net_pnl / t.ambiguous_fill — expose as
    # read-only aliases so the SAME object serves both the repo and the UI dict.
    @property
    def pnl(self) -> Optional[float]:
        return self.gross

    @property
    def net_pnl(self) -> Optional[float]:
        return self.net

    @property
    def ambiguous_fill(self) -> bool:
        return self.ambiguous


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _ist_day(ep: int) -> date:
    return datetime.fromtimestamp(ep, IST).date()


def _day_bounds(d: date) -> Tuple[int, int]:
    lo = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _hm(ep: int) -> str:
    dt = datetime.fromtimestamp(ep, IST)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _in_session(ep: int, start_hm: str, end_hm: str) -> bool:
    hm = _hm(ep)
    return start_hm <= hm <= end_hm


def _batch_warmup(conn, day_map: Dict[str, list], limit: int) -> Dict[str, list]:
    """── SCALP_V5_PARITY_PERF_20260825 ── ONE warmup query for the whole day.

    EQUIVALENCE with N x CandleSource.warmup_candles_before: for each symbol
    that method returns the most recent `limit` candles strictly BEFORE that
    symbol's own first candle of the day. Here one query covers a generous
    window across every watched symbol; a symbol whose slice holds >= limit
    candles yields exactly the same tail (the last `limit` of a superset that
    ends at the same cutoff ARE the globally most recent `limit`). A symbol
    whose slice holds FEWER is omitted from the result and refetched
    individually by the caller — the window may have clipped older history,
    and an approximation there would silently change EMA seeds. So: faster
    on the common path, never different.

    Returns {symbol: [{ts, open, high, low, close}, ...]} ascending.
    """
    if not day_map or limit <= 0:
        return {}
    cutoffs = {s: int(dc[0].ts) for s, dc in day_map.items() if dc}
    if not cutoffs:
        return {}
    hi = max(cutoffs.values())
    # A session holds ~375 1m candles; 3x that many sessions plus a week of
    # slack covers holidays and thin contracts without unbounded scanning.
    span_days = int(limit // 375) * 3 + 7
    lo_w = min(cutoffs.values()) - span_days * 86400
    syms = sorted(cutoffs)
    acc: Dict[str, list] = {}
    CHUNK = 400          # SQLite's variable ceiling is 999 — stay well under
    for i in range(0, len(syms), CHUNK):
        part = syms[i:i + CHUNK]
        q = ("SELECT tradingsymbol, ts, open, high, low, close "
             "FROM backtest_candles_1m "
             f"WHERE tradingsymbol IN ({','.join('?' * len(part))}) "
             "AND ts >= ? AND ts < ? "
             "ORDER BY tradingsymbol, ts")
        for r in conn.execute(q, (*part, lo_w, hi)):
            s = r[0]
            cut = cutoffs.get(s)
            ts = int(r[1])
            if cut is None or ts >= cut:
                continue
            acc.setdefault(s, []).append(
                {"ts": ts, "open": float(r[2]), "high": float(r[3]),
                 "low": float(r[4]), "close": float(r[5])})
    return {s: v[-limit:] for s, v in acc.items() if len(v) >= limit}


def _empty_summary() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
        "max_drawdown": 0.0, "ambiguous_fills": 0,
    }


# A lightweight Candle shim with the attributes the engine/indicator read
# (open/high/low/close/start_ts/end_ts). Matches the live Candle dataclass's
# duck-typed surface so we can drive the real classes without importing the
# app's Candle (which may pull marketdata deps not present in the runner env).
@dataclass
class _Candle:
    start_ts: int
    end_ts: int
    open: float
    high: float
    low: float
    close: float
    source: str = "BACKTEST"


# ── V5_TIMEFRAME BEGIN ── generalized aggregator (was fixed 180s / 3m).
def _aggregate_1m_to_tf(bars_1m: List[dict], tf_minutes: int) -> List[dict]:
    """floor(ts/tf) grid, EXACT match to the live CandleBuilder / BB aggregator.
    tf_minutes ∈ {1,3,5,10,15,30}. bars_1m: ascending dicts with ts/OHLC.
    Returns TF dicts with start_ts/end_ts/open/high/low/close."""
    TF = int(tf_minutes) * 60
    out: List[dict] = []
    cur = None
    o = h = l = c = None
    for b in bars_1m:
        bucket = (b["ts"] // TF) * TF
        if cur is None:
            cur = bucket
            o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        elif bucket == cur:
            h = max(h, b["high"]); l = min(l, b["low"]); c = b["close"]
        else:
            out.append({"start_ts": cur, "end_ts": cur + TF, "open": o, "high": h, "low": l, "close": c})
            cur = bucket
            o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    if cur is not None:
        out.append({"start_ts": cur, "end_ts": cur + TF, "open": o, "high": h, "low": l, "close": c})
    return out


def _aggregate_1m_to_3m(bars_1m: List[dict]) -> List[dict]:
    """Back-compat alias — any caller still expecting 3m keeps working."""
    return _aggregate_1m_to_tf(bars_1m, 3)
# ── V5_TIMEFRAME END ──


def _snapshot_symbols(snap: List[dict], side: Optional[str] = None) -> set:
    """Set of tradingsymbols in a selection snapshot, optionally filtered to a
    side. Mirrors the live _is_selected_signal membership check (own side)."""
    out = set()
    for o in snap or []:
        if side is not None and o.get("type") != side:
            continue
        sym = o.get("tradingsymbol") or o.get("symbol")
        if sym:
            out.add(sym)
    return out


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_scalpv5_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "SCALP_V5"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Run a SCALP_V5 backtest over the corpus.

    config keys (all optional, sane defaults):
      option_premium: {min, max}   selection band (live OptionSelector band)
      sl_points, tp_points         absolute points; 0 = disabled
      session: {primary:{start,end}}  IST HH:MM strings
      quantity: {lots}
      trade_side_mode              "BOTH" | "CE" | "PE"
      max_loss, max_profit         session MTM caps (gross, ₹); 0 = disabled

    SELECTION + ARBITRATION are replayed exactly as live V3/V1 (see header):
    per-day 120s selection timeline via backtest_selector.py, membership gate on
    the active snapshot, and same-3m-candle highest-premium election.
    """
    from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
    from app.engine.scalpv5.scalpv5_engine import ScalpV5Engine
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts,
    )
    charges_for_long_trade = _resolve_charges_fn()

    cfg = config_override or {}
    prem = cfg.get("option_premium", {}) or {}
    prem_min = float(prem.get("min", 0) or 0)
    prem_max = float(prem.get("max", 1e9) or 1e9)
    sl_points = float(cfg.get("sl_points", 0) or 0)
    tp_points = float(cfg.get("tp_points", 0) or 0)
    # ── V5_TIMEFRAME ── signal candle TF (was hardcoded 3m). Fills stay 1m.
    tf_minutes = int(float(cfg.get("timeframe_minutes", 3) or 3))
    if tf_minutes not in (1, 3, 5, 10, 15, 30):
        tf_minutes = 3
    tf_sec = tf_minutes * 60
    lots = int((cfg.get("quantity", {}) or {}).get("lots", 1) or 1)
    qty = lots * LOT_SIZE
    sess = ((cfg.get("session", {}) or {}).get("primary", {}) or {})
    sess_start = sess.get("start", "09:30")
    sess_end = sess.get("end", "15:20")
    # ── SCALP_V5_PARITY_PERF_20260825 ── EOD square-off parity (D14.1).
    # "" / absent = LEGACY: square off on the day's LAST candle (stamps
    # 15:30/15:31 — later than live trades). "HH:MM" = PARITY: the day stops
    # at that boundary and the leftover position closes on the last 1m bar
    # closing at or before it. Set this to the live cron time.
    _eod_hm = str(cfg.get("eod_squareoff_time", "") or "").strip()
    eod_sod = None            # seconds from IST midnight, or None = legacy
    if _eod_hm:
        try:
            _eh, _em = _eod_hm.split(":")
            _eh, _em = int(_eh), int(_em)
            if 0 <= _eh <= 23 and 0 <= _em <= 59:
                eod_sod = _eh * 3600 + _em * 60
        except (ValueError, AttributeError):
            eod_sod = None
    side_mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
    max_loss = abs(float(cfg.get("max_loss", 0) or 0))
    max_profit = abs(float(cfg.get("max_profit", 0) or 0))

    # Selection reads the premium band + trade_side_mode from cfg. The live V5
    # selection loop ALWAYS selects BOTH sides (so either side can signal) and
    # lets trade_side_mode gate the traded side in the engine — we mirror that by
    # forcing the selector to BOTH and applying side_mode at entry below.
    sel_cfg = {
        "option_premium": {"min": prem_min, "max": prem_max},
        "trade_side_mode": "BOTH",
    }

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # A CandleSource over the SAME db for the selector's premium/contract reads.
    src = CandleSource(db_path)

    # Sim days that have NIFTY option data in range.
    lo_all, hi_all = _day_bounds(date_from)[0], _day_bounds(date_to)[1]
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
        return {"run_id": None, "aborted": True,
                "reason": f"no {underlying} option data in range",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    trades: List[V5Trade] = []
    realised_running = 0.0          # session-cumulative gross (for MTM cap)
    total_days = len(sim_days)

    # ── Diagnostics: counts WHY candidate entries were rejected, so a sparse
    #    result can be explained. Now selection-aware: rej_not_selected is the
    #    membership gate that REPLACES the old premium-band post-filter.
    _diag = {
        "sim_days": total_days, "days_with_data": 0, "days_no_expiry": 0,
        "days_no_candles": 0, "days_uncovered": 0, "contracts_seen": 0,
        "signals": 0, "accepted": 0, "arb_contests": 0, "arb_dropped": 0,
        "rej_single_gate": 0, "rej_session": 0, "rej_side_mode": 0,
        "rej_not_selected": 0, "rej_mtm_block": 0,
        "prem_seen_min": None, "prem_seen_max": None,
    }

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break

        lo, hi = _day_bounds(d)

        # ── Build the per-day 120s SELECTION TIMELINE (reuses V1/V3 selector). ──
        # premium band → expected weekly expiry (no farther fallback) → median-ATM
        # → ATM±800 → nearest-2-per-side, re-selected every 120s on the :30 grid.
        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
            # ── SCALP_V5_PARITY_PERF_20260825 (P1) ── additive flag, proven by
            # the HA runner: the boundary selector filters candidates to
            # want_expiry regardless, so scoping cannot change WHICH contracts
            # are selected — it only stops materialising rows destined for the
            # discard pile, and flips preload onto idx_bt1m_under_exp_ts.
            scope_to_expected_expiry=True,
        )
        if not timeline.get("covered"):
            _diag["days_uncovered"] += 1
            continue

        # WATCH ONLY THE SELECTED UNION (this is the fix — matches V3 exactly).
        # The V3 runner runs signals ONLY on timeline["all_symbols"] (the union of
        # contracts the selector picks across all 120s boundaries that day), NOT on
        # every contract of the expiry. Running signals on every strike (the old
        # V5 behaviour) made ~70% of signals fire on non-selected contracts →
        # rejected as not_selected → ~41 trades/yr vs V3's ~1303. Watching only the
        # selected union aligns the SIGNAL set with the SELECTION set.
        watched = timeline.get("all_symbols") or set()
        if not watched:
            continue
        _diag["days_with_data"] += 1
        current_expiry = timeline.get("expected_expiry")

        # Per-symbol meta from the day's universe (strike / side / expiry).
        # ── SCALP_V5_PARITY_PERF_20260825 (P2) ── read the universe SCOPED to
        # the same expiry the timeline just preloaded. Unscoped here would be
        # a cache MISS and would force a second, full-day preload, undoing P1.
        # Every watched symbol belongs to this expiry by construction.
        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(underlying, lo, expiry=current_expiry)
        }

        # Build per-watched-symbol: 1m candles for the day, 3m aggregation, a real
        # ScalpV5Engine + IndicatorEnginePineV19 warmed on prior-day 3m bars.
        engines: Dict[str, object] = {}
        indicators: Dict[str, object] = {}
        bars3m_today: Dict[str, List[dict]] = {}
        one_min_index: Dict[str, Dict[int, list]] = {}
        meta: Dict[str, dict] = {}

        # ── SCALP_V5_PARITY_PERF_20260825 (P3) ── pre-pass over the watched
        # set: day candles are cache-served (free after the preload above),
        # then ONE batched warmup query replaces one query per contract.
        _warm_limit = WARMUP_CANDLES * tf_minutes
        _day_map: Dict[str, list] = {}
        for _s in sorted(watched):
            _dc = src.candles_1m_for_symbol_day(_s, lo)
            if _dc:
                _day_map[_s] = _dc
        _warm_batch = _batch_warmup(conn, _day_map, _warm_limit)

        for sym in sorted(watched):
            day_candles = _day_map.get(sym)
            if not day_candles:
                continue
            m = meta_map.get(sym)
            if m is None:
                continue
            meta[sym] = m

            bars_1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in day_candles]

            ind = IndicatorEnginePineV19()
            eng = ScalpV5Engine(strategy_id=strategy_id, slot_name=sym, symbol=sym)
            # ── BACKTEST EXPIRY-GATE NEUTRALIZATION ──
            # The live engine's _is_current_week_expiry() derives the current
            # weekly from date.today() (real wall-clock) and checks the 2-digit
            # YEAR appears in the symbol. In a 2024 backtest run during 2026 that
            # is ALWAYS False → on_candle bails before any signal (signals=0).
            # The backtest already enforces expiry CORRECTLY upstream: the
            # selection timeline resolves expected_expiry_for_day and the runner
            # only watches that expiry's contracts. So the engine's date.today()
            # gate is redundant here and must be bypassed. Patch THIS instance
            # only (live code untouched).
            eng._is_current_week_expiry = (lambda: True)  # type: ignore[method-assign]

            # warmup: prior-day TF bars for this contract (mirrors live warmup).
            # ── V5_TIMEFRAME ── depth scales with TF so higher TFs still build
            # WARMUP_CANDLES bars (200×30 ≈ 15 sessions of 1m history).
            # ── SCALP_V5_PARITY_PERF_20260825 (P3) ── batched warmup, with an
            # EXACT per-symbol fallback whenever the batch could not guarantee
            # the full depth (see _batch_warmup).
            w1m = _warm_batch.get(sym)
            if w1m is None:
                _wc = src.warmup_candles_before(sym, day_candles[0].ts, _warm_limit)
                w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in _wc]
            if w1m:
                w3m = _aggregate_1m_to_tf(w1m, tf_minutes)
                warm_candles = [
                    _Candle(b["start_ts"], b["end_ts"], b["open"], b["high"], b["low"], b["close"], "WARMUP")
                    for b in w3m
                ]
                try:
                    ind.warmup(warm_candles, use_history=True)
                except Exception:
                    for c in warm_candles:
                        ind.update(c)

            today3m = _aggregate_1m_to_tf(bars_1m, tf_minutes)
            bars3m_today[sym] = today3m

            idx: Dict[int, list] = {}
            for b in bars_1m:
                bkt = (b["ts"] // tf_sec) * tf_sec   # ── V5_TIMEFRAME ── was 180
                idx.setdefault(bkt, []).append(b)
            one_min_index[sym] = idx

            engines[sym] = eng
            indicators[sym] = ind

        if not engines:
            continue
        _diag["contracts_seen"] += len(engines)

        # Group this day's 3m candles across watched contracts by bucket_start, so
        # same-3m-candle arbitration runs per bar.
        by_bucket: Dict[int, List[Tuple[str, dict]]] = {}
        for sym in engines:
            for b in bars3m_today[sym]:
                by_bucket.setdefault(b["start_ts"], []).append((sym, b))
        ordered_buckets = sorted(by_bucket.keys())

        open_trade: Optional[V5Trade] = None

        for bucket_start in ordered_buckets:
            # ── SCALP_V5_PARITY_PERF_20260825 (D14.1) ── with a square-off
            # time configured the day STOPS there: no entry and no exit is
            # evaluated on candles the live engine would never trade.
            if eod_sod is not None and bucket_start >= lo + eod_sod:
                break
            if cancel_cb and cancel_cb():
                break

            items = sorted(by_bucket[bucket_start], key=lambda t: t[0])

            # Selection snapshot in effect at this TF candle's CLOSE (end_ts).
            snap_end_ts = bucket_start + tf_sec   # ── V5_TIMEFRAME ── was 180
            snap = active_snapshot_for_ts(timeline, snap_end_ts)
            sel_ce = _snapshot_symbols(snap, "CE")
            sel_pe = _snapshot_symbols(snap, "PE")
            # Lock carve-out: the held contract stays "selected" even if its
            # premium drifts out of band (live preserves locked_ce/locked_pe).
            locked_sym = open_trade.symbol if open_trade is not None else None

            entry_candidates: List[Tuple[float, str, dict]] = []

            for sym, b3 in items:
                ind = indicators[sym]
                eng = engines[sym]
                candle = _Candle(b3["start_ts"], b3["end_ts"], b3["open"], b3["high"], b3["low"], b3["close"])

                ind.update(candle)
                ready = ind.is_ready()

                # held contract → intrabar SL/TP, then EMA_EXIT at the 3m close
                if open_trade is not None and open_trade.symbol == sym:
                    exited = _try_intrabar_exit(open_trade, one_min_index[sym].get(bucket_start, []))
                    if not exited and ready:
                        if eng.should_exit_on_candle(candle, ind):
                            _close_trade(open_trade, exit_ts=b3["end_ts"], exit_price=b3["close"],
                                         reason="EMA_EXIT", charges_fn=charges_for_long_trade)
                            exited = True
                    if exited:
                        realised_running += (open_trade.gross or 0.0)
                        trades.append(open_trade)
                        open_trade = None
                        locked_sym = None
                        if _mtm_breached(realised_running, max_loss, max_profit):
                            break

                if not ready:
                    continue
                signal = eng.on_candle(candle, ind, sl_points, tp_points)
                if not signal.is_buy:
                    continue

                _diag["signals"] += 1
                entry_price = float(signal.entry_price)
                if _diag["prem_seen_min"] is None or entry_price < _diag["prem_seen_min"]:
                    _diag["prem_seen_min"] = round(entry_price, 2)
                if _diag["prem_seen_max"] is None or entry_price > _diag["prem_seen_max"]:
                    _diag["prem_seen_max"] = round(entry_price, 2)

                if open_trade is not None:
                    _diag["rej_single_gate"] += 1
                    continue

                if not _in_session(b3["end_ts"], sess_start, sess_end):
                    _diag["rej_session"] += 1
                    continue

                side = meta[sym]["side"]
                if side_mode in ("CE", "PE") and side_mode != side:
                    _diag["rej_side_mode"] += 1
                    continue

                # membership gate on the active snapshot (own side) + lock carve-out
                in_selected = (sym in sel_ce) if side == "CE" else (sym in sel_pe)
                if not in_selected and sym != locked_sym:
                    _diag["rej_not_selected"] += 1
                    continue

                if _mtm_breached(realised_running, max_loss, max_profit):
                    _diag["rej_mtm_block"] += 1
                    continue

                entry_candidates.append((entry_price, sym, {
                    "side": side, "strike": meta[sym]["strike"],
                    "entry_ts": b3["end_ts"], "entry_price": entry_price,
                    "sl": signal.sl, "tp": signal.tp,
                }))

            # same-candle arbitration: highest entry premium, symbol tie-break
            if open_trade is None and entry_candidates:
                if len(entry_candidates) > 1:
                    _diag["arb_contests"] += 1
                    _diag["arb_dropped"] += (len(entry_candidates) - 1)
                winner = max(entry_candidates, key=lambda c: (c[0], c[1]))
                _ep, _sym, ctx = winner
                _diag["accepted"] += 1
                open_trade = V5Trade(
                    side=ctx["side"], symbol=_sym, strike=ctx["strike"],
                    entry_ts=ctx["entry_ts"], entry_price=ctx["entry_price"],
                    sl=ctx["sl"], tp=ctx["tp"], qty=qty,
                    instrument_type=ctx["side"], expiry=current_expiry, direction="LONG",
                )

            if open_trade is None and _mtm_breached(realised_running, max_loss, max_profit):
                break

        # EOD square-off the still-open trade at the held contract's last close.
        if open_trade is not None:
            day_bars = None
            for sym in engines:
                if sym == open_trade.symbol:
                    day_bars = src.candles_1m_for_symbol_day(sym, lo)
                    break
            # ── SCALP_V5_PARITY_PERF_20260825 (D14.1) ── close on the last 1m
            # bar CLOSING at or before the boundary (bar ts + 60 <= cutoff).
            # Legacy (eod_sod None) keeps the day's last bar — 15:30/15:31.
            if day_bars and eod_sod is not None:
                _cut = lo + eod_sod
                day_bars = [b for b in day_bars if int(b.ts) + 60 <= _cut]
            if day_bars:
                last = day_bars[-1]
                _close_trade(open_trade, exit_ts=int(last.ts) + 60, exit_price=float(last.close),
                             reason="EOD", charges_fn=charges_for_long_trade)
                realised_running += (open_trade.gross or 0.0)
                trades.append(open_trade)
            open_trade = None

        if progress_cb:
            progress_cb({"day": di, "total_days": total_days, "date": d.isoformat(),
                         "trades": len(trades)})

    try:
        src.close()
    except Exception:
        pass
    conn.close()

    summary = _summarize(trades)
    summary["diagnostics"] = _diag
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][V5][DIAG] "
            f"days={_diag['sim_days']} with_data={_diag['days_with_data']} "
            f"uncovered={_diag['days_uncovered']} no_candles={_diag['days_no_candles']} "
            f"contracts={_diag['contracts_seen']} signals={_diag['signals']} "
            f"accepted={_diag['accepted']} arb_contests={_diag['arb_contests']} "
            f"arb_dropped={_diag['arb_dropped']} | rejected: "
            f"single_gate={_diag['rej_single_gate']} session={_diag['rej_session']} "
            f"side_mode={_diag['rej_side_mode']} not_selected={_diag['rej_not_selected']} "
            f"mtm={_diag['rej_mtm_block']} | signal_premium_seen="
            f"{_diag['prem_seen_min']}..{_diag['prem_seen_max']}"
        )
    except Exception:
        pass

    import uuid as _uuid
    return {
        "run_id": str(_uuid.uuid4()),
        "strategy_id": strategy_id,
        "config": cfg,
        "summary": summary,
        # Return the trade OBJECTS (not dicts): persist_run reads them as
        # attributes (t.symbol, t.entry_ts, t.pnl, t.ambiguous_fill, …).
        "trades": trades,
    }


# ----------------------------------------------------------------------
# Exit helpers
# ----------------------------------------------------------------------
def _try_intrabar_exit(trade: V5Trade, one_min_bars: List[dict]) -> bool:
    """Check SL/TP across the held 3m candle's underlying 1m bars, in order.
    Pessimistic: if both SL and TP fall inside the same 1m bar, take SL first
    and flag ambiguous. Returns True if an exit fired (mutates trade)."""
    if trade.sl is None and trade.tp is None:
        return False
    for b in sorted(one_min_bars, key=lambda x: x["ts"]):
        hi = b["high"]; lo = b["low"]
        hit_sl = trade.sl is not None and lo <= float(trade.sl)
        hit_tp = trade.tp is not None and hi >= float(trade.tp)
        if hit_sl and hit_tp:
            trade.ambiguous = True
            _close_trade(trade, exit_ts=b["ts"] + 60, exit_price=float(trade.sl),
                         reason="SL", charges_fn=_charges_fn())
            return True
        if hit_sl:
            _close_trade(trade, exit_ts=b["ts"] + 60, exit_price=float(trade.sl),
                         reason="SL", charges_fn=_charges_fn())
            return True
        if hit_tp:
            _close_trade(trade, exit_ts=b["ts"] + 60, exit_price=float(trade.tp),
                         reason="TP", charges_fn=_charges_fn())
            return True
    return False


# Known locations charges_for_long_trade has lived at across the tree. We try
# each and LOG which one resolved. If NONE resolve we warn loudly (once) rather
# than silently zeroing charges — a wrong path here would otherwise make every
# trade's charges ₹0 and quietly overstate net P&L.
_CHARGES_PATHS = (
    "app.backtest.charges.charges_model",
    "app.backtest.data.charges_model",
    "app.backtest.engine.charges_model",
    "app.backtest.charges_model",
)
_CHARGES_FN = None
_CHARGES_RESOLVED = False


def _resolve_charges_fn():
    """Resolve charges_for_long_trade from whichever module path exists. Caches
    the result. Logs the resolved path, or a loud warning if none are found."""
    global _CHARGES_FN, _CHARGES_RESOLVED
    if _CHARGES_RESOLVED:
        return _CHARGES_FN
    _CHARGES_RESOLVED = True
    import importlib
    for path in _CHARGES_PATHS:
        try:
            mod = importlib.import_module(path)
            fn = getattr(mod, "charges_for_long_trade", None)
            if fn is not None:
                _CHARGES_FN = fn
                try:
                    from app.event_bus.audit_logger import write_audit_log
                    write_audit_log(f"[BACKTEST][V5][CHARGES] using {path}.charges_for_long_trade")
                except Exception:
                    pass
                return _CHARGES_FN
        except Exception:
            continue
    # Nothing resolved — warn loudly. Charges will be ₹0; net == gross.
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][V5][CHARGES][WARN] charges_for_long_trade NOT FOUND in any "
            f"of {_CHARGES_PATHS} — charges will be ZERO and net P&L will equal "
            "gross. Fix the import path in backtest_scalpv5_runner.py."
        )
    except Exception:
        pass
    _CHARGES_FN = None
    return _CHARGES_FN


def _charges_fn():
    return _resolve_charges_fn()


def _close_trade(trade: V5Trade, *, exit_ts: int, exit_price: float, reason: str, charges_fn) -> None:
    trade.exit_ts = exit_ts
    trade.exit_price = float(exit_price)
    trade.exit_reason = reason
    gross = (trade.exit_price - trade.entry_price) * trade.qty
    charges = 0.0
    if charges_fn is not None:
        try:
            res = charges_fn(entry_price=trade.entry_price, exit_price=trade.exit_price, qty=trade.qty)
            charges = float(getattr(res, "total_charges", 0.0))
            gross = float(getattr(res, "gross_pnl", gross))
        except Exception:
            charges = 0.0
    trade.gross = round(gross, 2)
    trade.charges = round(charges, 2)
    trade.net = round(gross - charges, 2)


def _mtm_breached(realised_running: float, max_loss: float, max_profit: float) -> bool:
    if max_loss > 0 and realised_running <= -max_loss:
        return True
    if max_profit > 0 and realised_running >= max_profit:
        return True
    return False


# ----------------------------------------------------------------------
# Summary + serialization
# ----------------------------------------------------------------------
def _trade_to_dict(t: V5Trade) -> dict:
    return {
        "tradingsymbol": t.symbol,
        "side": t.side,
        "strike": t.strike,
        "entry_ts": t.entry_ts,
        "entry_price": t.entry_price,
        "sl": t.sl,
        "tp": t.tp,
        "qty": t.qty,
        "exit_ts": t.exit_ts,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "pnl": t.gross,
        "charges": t.charges,
        "net_pnl": t.net,
        "ambiguous_fill": t.ambiguous,
    }


def _summarize(trades: List[V5Trade]) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return _empty_summary()
    nets = [t.net or 0.0 for t in closed]
    gross = sum(t.gross or 0.0 for t in closed)
    charges = sum(t.charges or 0.0 for t in closed)
    net = sum(nets)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    amb = sum(1 for t in closed if t.ambiguous)

    eq = 0.0; peak = 0.0; mdd = 0.0
    for t in sorted(closed, key=lambda x: x.entry_ts or 0):
        eq += (t.net or 0.0)
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    return {
        "total_trades": len(closed),
        "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2) if closed else 0.0,
        "gross_pnl": round(gross, 2),
        "total_charges": round(charges, 2),
        "net_pnl": round(net, 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": amb,
    }
# backend/app/backtest/wick/backtest_wick_runner.py
#
# WICK_V1 backtest runner. LONG option-BUYING on NIFTY weekly options — a
# rejection-wick + midpoint pivot-reclaim reversal, computed on the OPTION
# contract's OWN premium candles (not the underlying).
#
# THESIS: two consecutive RED candles = premium falling. The FIRST (older) red
# candle's TOP WICK (high - open) is a rejection. If that wick is big enough, its
# MIDPOINT (open+high)/2 becomes a PIVOT. If price later reclaims that pivot, the
# down-move is failing → BUY, expecting premium to rise to TP.
#
# MULTI-TIMEFRAME (the distinguishing feature):
#   * SIGNAL DETECTION runs on the USER timeframe TF ∈ {1,3,5,10,15}m, aggregated
#     from the 1m corpus (O=first, H=max, L=min, C=last close).
#   * ENTRY and EXIT run on 1-MINUTE resolution, ALWAYS, regardless of TF. So TF
#     changes ONLY which setups fire, never how fills are modelled — the honest
#     way to compare timeframes.
#
# NO-LOOKAHEAD ARMING:
#   A pivot is armed at the CLOSE of the 2nd red candle of the pair (the first
#   moment the pair is confirmed AND the 1st red's wick is final). Entry scanning
#   begins on the next 1m bar AFTER that close — never inside the signal candles.
#
# PIVOT LIFECYCLE:
#   * Sliding pair: each new red-after-red forms a fresh pair; the pivot RESETS to
#     the new pair's 1st (older) red. Green candles do NOT cancel a pivot.
#   * A pivot stays armed until: reclaimed (touched) OR replaced by a new red pair
#     OR end of day. Reclaim CONSUMES the pivot whether or not a trade results.
#
# THREE CLOCKS:
#   1. SIGNAL detection — ALL DAY (from the first candle, ignores session_start).
#   2. ENTRY gate — only reclaims within [session_start, session_end] enter. A
#      reclaim OUTSIDE the window VOIDS the pivot (a touch is a touch; no trade).
#   3. EOD square-off — any open trade force-closed at EOD_SQUAREOFF (15:25),
#      distinct from session_end (<=15:20 gates ENTRIES only).
#
# ENTRY/EXIT (1m resolution, book AT the level, no slippage):
#   ENTRY  → 1m high >= pivot (in window)  → BUY at pivot.
#   SL     → 1m low  <= entry - sl_points  → exit @ sl.
#   TP     → 1m high >= entry + tp_points  → exit @ tp.
#   Same-1m-bar SL & TP → pessimistic SL-first, flagged ambiguous.
#   EOD    → square off at the last in-range 1m close.
#
# SINGLE GLOBAL trade across the whole selected universe (freeze scanning while
# in a trade). Daily reset — no pivots carry across days.
#
# P&L LONG = (exit - entry) * qty. Charges via charges_for_long_trade.
# ============================================================================

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Callable
import uuid as _uuid

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE = 65            # NIFTY
TIMEFRAME_SEC = 60       # fills always 1-minute
EOD_SQUAREOFF_HM = "15:25"   # hard square-off (live EOD job); distinct from session_end


# ----------------------------------------------------------------------
# Trade object — exposes the EXACT attribute surface persist_run reads
# (mirrors the HA_SELL HATrade contract: pnl / net_pnl / ambiguous_fill).
# ----------------------------------------------------------------------
@dataclass
class WickTrade:
    side: str                           # CE | PE
    symbol: str
    strike: float
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    qty: int
    condition: Optional[str] = None     # pivot/wick context (signal reason)
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    ambiguous: bool = False
    # pivot context (WICK-specific, informational)
    pivot: Optional[float] = None
    wick: Optional[float] = None
    timeframe: Optional[int] = None
    # ── fields persist_run (non-hedge branch) reads as attributes ──
    instrument_type: str = "CE"
    expiry: str = ""
    direction: str = "LONG"             # WICK_V1 is always LONG (option buying)
    max_adverse: float = 0.0
    max_favorable: float = 0.0

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
# Small helpers (shared shape with the HA/V5 runners)
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


def _empty_summary() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
        "max_drawdown": 0.0, "ambiguous_fills": 0,
    }


# ----------------------------------------------------------------------
# Signal core — TF aggregation, red/wick/pivot, sliding-pair scan.
# ----------------------------------------------------------------------
def _aggregate_1m_to_tf(bars_1m: List[dict], tf_minutes: int) -> List[dict]:
    """Roll 1m OHLC into TF candles. Each TF candle records close_ts (= last sub
    bar ts + 60, the moment it's CONFIRMED — no lookahead)."""
    if not bars_1m:
        return []
    if tf_minutes <= 1:
        return [{"ts": b["ts"], "open": b["open"], "high": b["high"],
                 "low": b["low"], "close": b["close"], "close_ts": b["ts"] + 60}
                for b in bars_1m]
    tf_sec = tf_minutes * 60
    day_start = bars_1m[0]["ts"]
    buckets: Dict[int, List[dict]] = {}
    for b in bars_1m:
        buckets.setdefault((b["ts"] - day_start) // tf_sec, []).append(b)
    out = []
    for k in sorted(buckets):
        grp = sorted(buckets[k], key=lambda x: x["ts"])
        out.append({
            "ts": grp[0]["ts"], "open": grp[0]["open"],
            "high": max(x["high"] for x in grp), "low": min(x["low"] for x in grp),
            "close": grp[-1]["close"], "close_ts": grp[-1]["ts"] + 60,
        })
    return out


def _is_red(c: dict) -> bool:
    return c["close"] < c["open"]


def _top_wick(c: dict) -> float:
    return c["high"] - max(c["open"], c["close"])


def _pivot_of(c: dict) -> float:
    return (max(c["open"], c["close"]) + c["high"]) / 2.0


def _scan_pivots(tf_candles: List[dict], wick_min: float) -> List[dict]:
    """Sliding pair of consecutive reds → pivot from the 1st red's wick, armed at
    the 2nd red's close_ts. Returns chronological pivot list."""
    pivots = []
    for i in range(1, len(tf_candles)):
        first, second = tf_candles[i - 1], tf_candles[i]
        if _is_red(first) and _is_red(second):
            w = _top_wick(first)
            if w >= wick_min:
                pivots.append({
                    "armed_ts": second["close_ts"],
                    "pivot": round(round(_pivot_of(first) / 0.05) * 0.05, 2),
                    "wick": w,
                })
    return pivots


def _tick(x: float) -> float:
    return round(round(x / 0.05) * 0.05, 2)


# ----------------------------------------------------------------------
# Public entry — mutes audit logging, delegates to the impl.
# ----------------------------------------------------------------------
def run_wick_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "WICK_V1"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    from app.event_bus.audit_logger import audit_muted
    with audit_muted():
        return _run_wick_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )


def _run_wick_backtest_impl(
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
    """WICK_V1 backtest. config keys (all optional, sane defaults):
      timeframe_minutes            signal candle TF ∈ {1,3,5,10,15}  (default 3)
      top_wick_min                 min top-wick of the 1st red (pts) (default 1.5)
      option_premium: {min, max}   selection band                    (default 150-200)
      sl_points                    SL distance below entry (pts)     (default 10)
      tp_points                    TP distance above entry (pts)     (default 16)
      session: {primary:{start,end}}  IST HH:MM — gates ENTRIES only (default 09:30-15:20)
      quantity: {lots}
      trade_side_mode              "BOTH" | "CE" | "PE"
      max_trades_per_side          daily per-side entry cap          (default 50)
      max_loss, max_profit         PER-DAY MTM caps (NET ₹); 0 = disabled
    """
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts,
    )
    charges_for_long_trade = _resolve_charges_fn()

    cfg = config_override or {}
    tf_minutes = int(float(cfg.get("timeframe_minutes", 3) or 3))
    if tf_minutes not in (1, 3, 5, 10, 15):
        tf_minutes = 3
    wick_min = abs(float(cfg.get("top_wick_min", 1.5) or 1.5))
    prem = cfg.get("option_premium", {}) or {}
    prem_min = float(prem.get("min", 150) or 150)
    prem_max = float(prem.get("max", 200) or 200)
    sl_points = abs(float(cfg.get("sl_points", 10) or 10))
    tp_points = abs(float(cfg.get("tp_points", 16) or 16))
    lots = int((cfg.get("quantity", {}) or {}).get("lots", 1) or 1)
    qty = lots * LOT_SIZE
    sess = ((cfg.get("session", {}) or {}).get("primary", {}) or {})
    sess_start = sess.get("start", "09:30")
    sess_end = sess.get("end", "15:20")
    side_mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
    max_trades_per_side = int(cfg.get("max_trades_per_side", 50) or 50)
    # ── DUAL_SIDE_MODE ── False (default) = SINGLE GLOBAL trade (one position at a
    # time across the whole universe). True = PER-SIDE gate: at most 1 CE AND 1 PE
    # open at once (never 2 CE, never 2 PE). Freeze/discard/arbitration key on side.
    dual_side_mode = bool(cfg.get("dual_side_mode", False))
    max_loss = abs(float(cfg.get("max_loss", 0) or 0))
    max_profit = abs(float(cfg.get("max_profit", 0) or 0))

    src = CandleSource(db_path)
    sel_cfg = {"option_premium": {"min": prem_min, "max": prem_max}}

    # Simulated day list.
    sim_days: List[date] = []
    d = date_from
    while d <= date_to:
        sim_days.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    _diag = {
        "sim_days": len(sim_days), "days_with_data": 0, "days_uncovered": 0,
        "contracts_seen": 0, "pairs_seen": 0, "wicks_qualified": 0,
        "pivots_armed": 0, "pivots_replaced": 0, "pivots_voided_offwindow": 0,
        "pivots_expired_eod": 0, "pivots_discarded_intrade": 0, "entries": 0, "rej_side_mode": 0,
        "rej_per_side_cap": 0, "day_mtm_blocked": 0,
        "exit_tp": 0, "exit_sl": 0, "exit_eod": 0, "ambiguous": 0,
        "prem_seen_min": 1e9, "prem_seen_max": 0.0,
    }
    trades: List[WickTrade] = []

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break
        lo, hi = _day_bounds(d)
        realised_running = 0.0
        day_blocked = False
        per_side_count = {"CE": 0, "PE": 0}

        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
        )
        if not timeline.get("covered"):
            _diag["days_uncovered"] += 1
            continue
        watched = timeline.get("all_symbols") or set()
        if not watched:
            continue
        _diag["days_with_data"] += 1
        current_expiry = timeline.get("expected_expiry")

        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(underlying, lo)
        }

        # Load 1m bars per watched symbol; build TF candles + pivots per symbol.
        one_min_by_sym: Dict[str, List[dict]] = {}
        pivots_by_sym: Dict[str, List[dict]] = {}
        for sym in sorted(watched):
            if sym not in meta_map:
                continue
            day_candles = src.candles_1m_for_symbol_day(sym, lo)
            if not day_candles:
                continue
            bars_1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in day_candles]
            one_min_by_sym[sym] = bars_1m
            tf_candles = _aggregate_1m_to_tf(bars_1m, tf_minutes)
            # count pairs seen (any two consecutive reds) for diagnostics
            for i in range(1, len(tf_candles)):
                if _is_red(tf_candles[i-1]) and _is_red(tf_candles[i]):
                    _diag["pairs_seen"] += 1
            piv = _scan_pivots(tf_candles, wick_min)
            _diag["wicks_qualified"] += len(piv)
            pivots_by_sym[sym] = piv
        if not one_min_by_sym:
            continue
        _diag["contracts_seen"] += len(one_min_by_sym)

        # Global 1m timeline across all watched symbols, bucketed by ts so the
        # SINGLE global trade + arbitration run per 1-minute bucket.
        by_bucket: Dict[int, List[Tuple[str, dict]]] = {}
        for sym, bars in one_min_by_sym.items():
            for b in bars:
                by_bucket.setdefault(b["ts"], []).append((sym, b))
        ordered_buckets = sorted(by_bucket.keys())

        # Per-symbol pivot activation cursor + active pivot (sliding replace).
        piv_cursor: Dict[str, int] = {s: 0 for s in pivots_by_sym}
        active_piv: Dict[str, Optional[dict]] = {s: None for s in pivots_by_sym}

        # PER-SIDE trade slots. In GLOBAL mode we still use this dict but permit
        # only ONE non-None slot at a time (a global lock). In DUAL mode each side
        # is independent: 1 CE + 1 PE may be open together, never 2 of a side.
        open_by_side: Dict[str, Optional[WickTrade]] = {"CE": None, "PE": None}
        # Per-side exit_ts of the most recent trade on that side; a pivot on a side
        # may only arm STRICTLY AFTER that side's last exit (the (B) discard rule,
        # applied per-side). In GLOBAL mode both sides share the max (any trade
        # freezes everything), enforced below.
        last_exit_ts: Dict[str, int] = {"CE": 0, "PE": 0}

        def _any_open() -> bool:
            return open_by_side["CE"] is not None or open_by_side["PE"] is not None

        def _side_frozen(side: str) -> bool:
            # GLOBAL mode: frozen if ANY trade is open. DUAL mode: frozen only if
            # THIS side already has an open trade.
            if dual_side_mode:
                return open_by_side.get(side) is not None
            return _any_open()

        for bucket_start in ordered_buckets:
            if cancel_cb and cancel_cb():
                break
            snap_end_ts = bucket_start + TIMEFRAME_SEC
            snap = active_snapshot_for_ts(timeline, snap_end_ts)
            sel_syms = _snapshot_symbols(snap)
            items = sorted(by_bucket[bucket_start], key=lambda t: t[0])

            # ── EXIT CHECK FIRST (both sides): may free a side up THIS bar. ──
            for side in ("CE", "PE"):
                ot = open_by_side[side]
                if ot is None:
                    continue
                b1 = None
                for sym, bar in items:
                    if sym == ot.symbol:
                        b1 = bar
                        break
                if b1 is not None:
                    _maybe_exit(ot, b1, charges_for_long_trade, _diag)
                    if ot.exit_reason is not None:
                        realised_running += (ot.net or 0.0)
                        last_exit_ts[side] = int(ot.exit_ts or 0)
                        trades.append(ot)
                        open_by_side[side] = None

            # ── PIVOT ACTIVATION with per-side (B) freeze/discard ──
            # For each symbol, its side determines whether it's frozen. While a
            # side is frozen, pivots on that side are DISCARDED (cursor advances,
            # no activation). When free, a pivot activates only if it armed
            # STRICTLY AFTER that side's last exit (armed_ts > last_exit_ts[side]).
            for sym in pivots_by_sym:
                side = meta_map.get(sym, {}).get("side", "CE")
                piv = pivots_by_sym[sym]
                cur = piv_cursor[sym]
                frozen = _side_frozen(side)
                le = last_exit_ts[side] if not dual_side_mode else last_exit_ts[side]
                # In GLOBAL mode the effective "last exit" that gates a pivot is
                # the most recent exit on EITHER side (any trade froze everything).
                if not dual_side_mode:
                    le = max(last_exit_ts["CE"], last_exit_ts["PE"])
                while cur < len(piv) and piv[cur]["armed_ts"] <= bucket_start:
                    if frozen:
                        _diag["pivots_discarded_intrade"] = _diag.get("pivots_discarded_intrade", 0) + 1
                        cur += 1
                        continue
                    if piv[cur]["armed_ts"] <= le:
                        _diag["pivots_discarded_intrade"] = _diag.get("pivots_discarded_intrade", 0) + 1
                        cur += 1
                        continue
                    if active_piv[sym] is not None:
                        _diag["pivots_replaced"] += 1
                    active_piv[sym] = piv[cur]
                    _diag["pivots_armed"] += 1
                    cur += 1
                piv_cursor[sym] = cur
                # drop any stale active pivot on a frozen side
                if frozen:
                    active_piv[sym] = None

            # ── day MTM cap gate (entries only) ──
            if day_blocked or _day_cap_hit(realised_running, max_loss, max_profit):
                day_blocked = True
                continue

            # ── ENTRY SCAN — collect candidates PER SIDE ──
            cand_by_side: Dict[str, List[Tuple[float, str, dict]]] = {"CE": [], "PE": []}
            for sym, b1 in items:
                ap = active_piv.get(sym)
                if ap is None:
                    continue
                if b1["high"] < ap["pivot"]:
                    continue
                # pivot RECLAIMED — consume it regardless of tradeability.
                in_window = _in_session(bucket_start, sess_start, sess_end)
                active_piv[sym] = None
                if not in_window:
                    _diag["pivots_voided_offwindow"] += 1
                    continue
                side = meta_map.get(sym, {}).get("side", "CE")
                if _side_frozen(side):
                    # side already occupied (or global lock) — cannot enter
                    continue
                if side_mode in ("CE", "PE") and side != side_mode:
                    _diag["rej_side_mode"] += 1
                    continue
                if per_side_count.get(side, 0) >= max_trades_per_side:
                    _diag["rej_per_side_cap"] += 1
                    continue
                if sym not in sel_syms:
                    continue
                prem_px = ap["pivot"]
                _diag["prem_seen_min"] = min(_diag["prem_seen_min"], prem_px)
                _diag["prem_seen_max"] = max(_diag["prem_seen_max"], prem_px)
                cand_by_side[side].append((prem_px, sym, {
                    "side": side, "strike": meta_map[sym]["strike"],
                    "entry_ts": bucket_start, "pivot": ap["pivot"], "wick": ap["wick"],
                }))

            # ── ARBITRATION per side; in GLOBAL mode only ONE side may fill ──
            sides_to_fill = ["CE", "PE"]
            if not dual_side_mode:
                # global: pick the single best candidate across BOTH sides
                allc = cand_by_side["CE"] + cand_by_side["PE"]
                if not allc:
                    continue
                allc.sort(key=lambda t: -t[0])
                _px, _sym, ctx = allc[0]
                _open_trade_from_ctx(open_by_side, per_side_count, _diag,
                                     ctx, _sym, sl_points, tp_points, qty,
                                     tf_minutes, current_expiry)
                continue

            # dual mode: best CE and best PE may BOTH enter (if side free)
            for side in sides_to_fill:
                if open_by_side[side] is not None:
                    continue
                cands = cand_by_side[side]
                if not cands:
                    continue
                cands.sort(key=lambda t: -t[0])
                _px, _sym, ctx = cands[0]
                _open_trade_from_ctx(open_by_side, per_side_count, _diag,
                                     ctx, _sym, sl_points, tp_points, qty,
                                     tf_minutes, current_expiry)

        # ── EOD square-off at EOD_SQUAREOFF (15:25) — BOTH sides ──
        for side in ("CE", "PE"):
            ot = open_by_side[side]
            if ot is None:
                continue
            day_bars = one_min_by_sym.get(ot.symbol) or []
            eod_bar = None
            for b in day_bars:
                if _hm(b["ts"]) <= EOD_SQUAREOFF_HM:
                    eod_bar = b
            if eod_bar is not None:
                _close_at(ot, exit_ts=int(eod_bar["ts"]) + TIMEFRAME_SEC,
                          exit_price=float(eod_bar["close"]), reason="EOD",
                          charges_fn=charges_for_long_trade)
                _diag["exit_eod"] += 1
                realised_running += (ot.net or 0.0)
                trades.append(ot)
            open_by_side[side] = None

        if progress_cb:
            progress_cb({"day": di, "total_days": len(sim_days),
                         "trades": len(trades)})

    summary = _summarize(trades)
    summary["diagnostics"] = _diag
    return {
        "run_id": str(_uuid.uuid4()),
        "strategy_id": strategy_id,
        "config": cfg,
        "summary": summary,
        "trades": trades,
    }


# ----------------------------------------------------------------------
# Exit + persistence helpers
# ----------------------------------------------------------------------
def _open_trade_from_ctx(open_by_side, per_side_count, diag, ctx, sym,
                         sl_points, tp_points, qty, tf_minutes, expiry):
    """Create a WickTrade from an entry ctx and register it in the per-side slot."""
    entry = ctx["pivot"]
    side = ctx["side"]
    t = WickTrade(
        side=side, symbol=sym, strike=ctx["strike"],
        entry_ts=ctx["entry_ts"], entry_price=entry,
        sl=_tick(entry - sl_points), tp=_tick(entry + tp_points),
        qty=qty, pivot=ctx["pivot"], wick=ctx["wick"], timeframe=tf_minutes,
        condition=f"WICK{ctx['wick']:.1f}@{tf_minutes}m",
        instrument_type=side, expiry=expiry, direction="LONG",
    )
    open_by_side[side] = t
    per_side_count[side] = per_side_count.get(side, 0) + 1
    diag["entries"] = diag.get("entries", 0) + 1


def _close_at(trade: WickTrade, *, exit_ts: int, exit_price: float, reason: str,
              charges_fn) -> None:
    """Close a LONG trade AT an exact price, no slippage. P&L = (exit-entry)*qty.
    Uses charges_for_long_trade for the round-trip charges + signed gross."""
    exit_price = _tick(exit_price)
    trade.exit_ts = exit_ts
    trade.exit_price = exit_price
    trade.exit_reason = reason
    gross = (exit_price - trade.entry_price) * trade.qty
    charges = 0.0
    if charges_fn is not None:
        try:
            res = charges_fn(entry_price=trade.entry_price, exit_price=exit_price,
                             qty=trade.qty)
            charges = float(getattr(res, "total_charges", 0.0))
            gross = float(getattr(res, "gross_pnl", gross))
        except Exception:
            charges = 0.0
    trade.gross = round(gross, 2)
    trade.charges = round(charges, 2)
    trade.net = round(gross - charges, 2)


def _maybe_exit(trade: WickTrade, bar_1m: dict, charges_fn, diag: dict) -> None:
    """Check SL/TP on ONE 1m bar for the open LONG trade. Pure level-touch:
      SL → low  <= sl → exit @ sl.   TP → high >= tp → exit @ tp.
      Same bar both → pessimistic SL-first, flagged. Book AT level, no slippage."""
    hi = float(bar_1m["high"])
    lo = float(bar_1m["low"])
    bar_ts = int(bar_1m["ts"])
    hit_sl = trade.sl is not None and lo <= float(trade.sl)
    hit_tp = trade.tp is not None and hi >= float(trade.tp)
    if hit_sl and hit_tp:
        trade.ambiguous = True
        diag["ambiguous"] = diag.get("ambiguous", 0) + 1
        _close_at(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.sl),
                  reason="SL", charges_fn=charges_fn)
        diag["exit_sl"] = diag.get("exit_sl", 0) + 1
        return
    if hit_sl:
        _close_at(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.sl),
                  reason="SL", charges_fn=charges_fn)
        diag["exit_sl"] = diag.get("exit_sl", 0) + 1
        return
    if hit_tp:
        _close_at(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.tp),
                  reason="TP", charges_fn=charges_fn)
        diag["exit_tp"] = diag.get("exit_tp", 0) + 1
        return


def _snapshot_symbols(snap, side: Optional[str] = None) -> set:
    """Set of tradingsymbols in a selection snapshot (optionally one side)."""
    out = set()
    if not snap:
        return out
    for row in snap:
        sym = row.get("tradingsymbol") if isinstance(row, dict) else getattr(row, "tradingsymbol", None)
        if sym is None:
            continue
        if side is not None:
            s = row.get("instrument_type") if isinstance(row, dict) else getattr(row, "instrument_type", None)
            if s != side:
                continue
        out.add(sym)
    return out


def _day_cap_hit(realised_net_today: float, max_loss: float, max_profit: float) -> bool:
    if max_loss > 0 and realised_net_today <= -max_loss:
        return True
    if max_profit > 0 and realised_net_today >= max_profit:
        return True
    return False


_CHARGES_PATHS = (
    "app.backtest.charges.charges_model",
    "app.backtest.data.charges_model",
    "app.backtest.engine.charges_model",
    "app.backtest.charges_model",
)
_CHARGES_FN = None
_CHARGES_RESOLVED = False


def _resolve_charges_fn():
    global _CHARGES_FN, _CHARGES_RESOLVED
    if _CHARGES_RESOLVED:
        return _CHARGES_FN
    import importlib
    for path in _CHARGES_PATHS:
        try:
            mod = importlib.import_module(path)
        except Exception:
            continue
        fn = getattr(mod, "charges_for_long_trade", None)
        if fn is not None:
            _CHARGES_FN = fn
            _CHARGES_RESOLVED = True
            return fn
    _CHARGES_FN = None
    _CHARGES_RESOLVED = True
    return None


def _summarize(trades: List[WickTrade]) -> dict:
    if not trades:
        return _empty_summary()
    wins = [t for t in trades if (t.net or 0) > 0]
    losses = [t for t in trades if (t.net or 0) < 0]
    gross = sum(float(t.gross or 0) for t in trades)
    charges = sum(float(t.charges or 0) for t in trades)
    net = sum(float(t.net or 0) for t in trades)
    # max drawdown on the running NET equity curve
    eq = 0.0; peak = 0.0; mdd = 0.0
    for t in trades:
        eq += float(t.net or 0)
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {
        "total_trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / len(trades), 2) if trades else 0.0,
        "gross_pnl": round(gross, 2), "total_charges": round(charges, 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in trades if t.ambiguous),
    }
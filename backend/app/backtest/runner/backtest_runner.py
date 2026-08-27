# backend/app/backtest/runner/backtest_runner.py
#
# SCALP_V1 backtest over a date range for ONE underlying.
#
# FAITHFUL TO LIVE — the two gates that previously diverged are now exact:
#
#  A. 120s ROLLING SELECTION (the fix). Live re-selects every 120s on the :30
#     grid; SignalRouter._common_gates DROPS any signal whose symbol is not in
#     the CURRENT selection (CE_NOT_SELECTED / PE_NOT_SELECTED). We build a
#     per-day selection timeline and gate each candle's entry on the snapshot
#     active at that candle's ts (most recent :30 boundary <= ts), PLUS a
#     lock carve-out: the contract holding the open trade stays "selected".
#
#  B. SINGLE STRATEGY-WIDE SLOT. Live's _any_slot_busy is strategy-wide (one
#     open trade blocks all CE/PE slots). We replay all watched contracts
#     INTERLEAVED by timestamp sharing ONE slot via the VirtualBook.
#
# FIDELITY CONTRACT (live indicator/condition engine traps — each handled):
#  1. warmup(use_history=True) over prior history; test candles fed via
#     update() (flag OFF) so _last_red_low tracks as live (drives SL/TP).
#  2. never warmup(use_history=False) (it date-filters to real today()).
#  3. is_trading_time from SimClock via is_within_session (not hardcoded True).
#
# Signal/exit DECISIONS are the REAL StrategyEngine + ConditionEngine +
# IndicatorEnginePineV19. All V1 config keys come from load_strategy_config
# (premium band, SL params, RR, session, lots, side mode) — same as live.

from __future__ import annotations

import json   # ── SCALP_V1_DIAG_20260823 ── entry snapshot serializer
import uuid
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import (
    load_strategy_config, set_backtest_config_override,
    clear_backtest_config_override)
from app.utils.session_utils import is_within_session

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.condition_engine_v1_9 import ConditionEngineV19
from app.marketdata.candle import Candle, CandleSource as MDCandleSource

from app.backtest.data.candle_source import CandleSource, BTCandle
from app.backtest.sim.sim_clock import SimClock
from app.backtest.sim.virtual_book import VirtualBook, VirtualPosition
from app.backtest.sim.fill_model import resolve_exit_on_candle
from app.backtest.engine.backtest_strategy_engine import BacktestStrategyEngine
from app.backtest.engine.backtest_selector import (
    build_selection_timeline, active_snapshot_for_ts,
)

WARMUP_CANDLES = 500
IST = 5 * 3600 + 30 * 60


class _NoopDebugLogger:
    def log(self, *a, **k):
        pass


def _bt_to_md_candle(c: BTCandle) -> Candle:
    return Candle(start_ts=c.ts, end_ts=c.ts + 60,
                  open=c.open, high=c.high, low=c.low, close=c.close,
                  source=MDCandleSource.WARMUP)


def _ist_midnight_epoch(d: date) -> int:
    secs = int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds())
    return secs - IST


def _trading_days(date_from: date, date_to: date) -> List[date]:
    out, d = [], date_from
    while d <= date_to:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ── SCALP_V1_BT_FILTERS_20260823 BEGIN: helpers ──
# D4: live EOD square-off cron fires 15:15 IST (CAS freeze rebaseline
# 2026-08-03 — NIFTY index freezes 15:15, scheduler cron is the only exit
# path). The backtest must square off at the same wall-clock instant, NOT at
# the day's last candle (~15:29) as before. Seconds after IST midnight:
EOD_SQUARE_OFF_IST_SECS = 15 * 3600 + 15 * 60   # 15:15:00 IST


def _in_blackout(stamp_dt, start_hhmm: str, end_hhmm: str) -> bool:
    """True if stamp_dt.time() falls in the half-open window [start, end).

    Half-open matches the validated analysis: an entry stamped exactly at the
    blackout END boundary (e.g. 14:00) is ALLOWED; one stamped exactly at the
    START (e.g. 12:00) is blocked. The stamp is the entry decision time =
    candle close (ts + 60), the same timestamp the trade is recorded with."""
    st = datetime.strptime(start_hhmm, "%H:%M").time()
    en = datetime.strptime(end_hhmm, "%H:%M").time()
    return st <= stamp_dt.time() < en
# ── SCALP_V1_BT_FILTERS_20260823 END: helpers ──


# ── SCALP_V1_PARALLEL_20260823 BEGIN: parallel-days machinery ──
# IC_PARALLEL pattern: module-level worker (spawn-picklable) that recursively
# runs its contiguous chunk SERIALLY with audit muted, returning picklable
# ClosedTrade dataclasses + the chunk's coverage dict.
def _scalp_parallel_worker(strategy_id: str, underlying: str,
                           date_from_iso: str, date_to_iso: str,
                           cfg: dict) -> dict:
    child_cfg = dict(cfg)
    child_cfg["parallel_workers"] = 1          # child MUST run serial
    try:
        from app.event_bus.audit_logger import audit_muted
        _mute = audit_muted()
    except Exception:                          # audit_muted unavailable → run unmuted
        import contextlib
        _mute = contextlib.nullcontext()
    with _mute:
        out = run_backtest(
            strategy_id=strategy_id, underlying=underlying,
            date_from=date.fromisoformat(date_from_iso),
            date_to=date.fromisoformat(date_to_iso),
            config_override=child_cfg, progress_cb=None)
    return {"trades": out["trades"],
            "coverage": out["summary"].get("coverage", {})}
# ── SCALP_V1_PARALLEL_20260823 END: parallel-days machinery ──


class _Ctx:
    """Per-contract replay state for the interleaved single-slot loop."""
    def __init__(self, contract, candles, clock, book, strategy_id):
        self.contract = contract
        self.symbol = contract["tradingsymbol"]
        self.candles = candles
        self.clock = clock
        self.engine = BacktestStrategyEngine(
            strategy_id=strategy_id, slot_name=self.symbol, symbol=self.symbol,
            clock=clock, book=book)
        self.engine.debug_logger = _NoopDebugLogger()
        # ── SCALP_V1_EMA_GATE_20260824 ── gate params from the run's
        # merged cfg (the BT_CONFIG_OVERRIDE token is installed before ctxs
        # are built, so load_strategy_config returns this run's overrides).
        from app.config.strategy_loader import load_strategy_config as _lsc
        _eg = (_lsc(self.engine.strategy_id) or {}).get("ema_gate") or {}
        self.indicator = IndicatorEnginePineV19(
            gate_ema_period=(int(_eg.get("period", 144) or 144)
                             if _eg.get("enabled") else None),
            gate_slope_lookback=int(_eg.get("slope_lookback", 30) or 30))
        self.conditions_engine = ConditionEngineV19()


def run_backtest(
    *,
    strategy_id: str,
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    assert strategy_id == "SCALP_V1", "Phase 1 supports SCALP_V1 only"
    started = time.time()
    run_id = str(uuid.uuid4())

    cfg = load_strategy_config(strategy_id)
    if config_override:
        cfg = _deep_merge(cfg, config_override)

    # ── SCALP_V1_PARALLEL_20260823 BEGIN: shard days across processes ──
    try:
        _n_workers = int(cfg.get("parallel_workers", 1) or 1)
    except (TypeError, ValueError):
        _n_workers = 1
    if _n_workers > 1:
        _all_days = _trading_days(date_from, date_to)
        if len(_all_days) > _n_workers:
            import math as _math
            from concurrent.futures import ProcessPoolExecutor, as_completed
            from multiprocessing import get_context
            _step = _math.ceil(len(_all_days) / _n_workers)
            _chunks = [_all_days[i:i + _step]
                       for i in range(0, len(_all_days), _step)]
            write_audit_log(
                f"[BACKTEST] START run={run_id} {strategy_id}/{underlying} "
                f"{date_from}..{date_to} days={len(_all_days)} "
                f"PARALLEL workers={_n_workers} chunks={len(_chunks)}")
            _merged: list = []
            _cov_m = {"days_total": len(_all_days), "days_covered": 0,
                      "days_skipped": 0, "skipped": []}
            _days_done = 0
            try:
                with ProcessPoolExecutor(
                        max_workers=len(_chunks),
                        mp_context=get_context("spawn")) as _pool:
                    _futs = {_pool.submit(
                        _scalp_parallel_worker, strategy_id, underlying,
                        ch[0].isoformat(), ch[-1].isoformat(), cfg): ch
                        for ch in _chunks}
                    for _fut in as_completed(_futs):
                        _out = _fut.result()
                        _merged.extend(_out["trades"])
                        _c = _out.get("coverage") or {}
                        _cov_m["days_covered"] += _c.get("days_covered", 0)
                        _cov_m["days_skipped"] += _c.get("days_skipped", 0)
                        _cov_m["skipped"].extend(_c.get("skipped", []))
                        _days_done += len(_futs[_fut])
                        if progress_cb:
                            progress_cb({"day": _days_done,
                                         "total_days": len(_all_days),
                                         "date": _futs[_fut][-1].isoformat(),
                                         "watched": 0})
            except Exception as _exc:
                # LOUD, not silent-serial: a quiet fallback would mask a
                # missing freeze_support guard and silently cost the user
                # the speedup they configured (IC_PARALLEL precedent).
                raise RuntimeError(
                    f"{strategy_id} parallel execution failed: {_exc!r} — "
                    f"rerun with parallel_workers=1") from _exc
            _merged.sort(key=lambda t: (t.entry_ts, t.symbol))
            _cov_m["skipped"].sort(key=lambda s: s.get("date", ""))
            summary = _summarize(_merged, started)
            write_audit_log(
                f"[BACKTEST] DONE run={run_id} trades={len(_merged)} "
                f"gross={summary['summary']['gross_pnl']:.2f} "
                f"charges={summary['summary']['total_charges']:.2f} "
                f"net={summary['summary']['net_pnl']:.2f} "
                f"win_rate={summary['summary']['win_rate']:.1f}% "
                f"workers={_n_workers} "
                f"elapsed={summary['summary']['elapsed_s']}s")
            summary["run_id"] = run_id
            summary["trades"] = _merged
            summary["config"] = cfg
            summary["summary"]["coverage"] = _cov_m
            write_audit_log(
                f"[BACKTEST][COVERAGE] days_total={_cov_m['days_total']} "
                f"covered={_cov_m['days_covered']} "
                f"skipped={_cov_m['days_skipped']}")
            return summary
    # ── SCALP_V1_PARALLEL_20260823 END (serial path continues below) ──

    # ── BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)
    # INLINE; install this run's merged cfg so the engine's SL/RR gates use the
    # Backtest page params, not the on-disk Settings file. Cleared before return. ──
    _bt_ov_token = set_backtest_config_override({strategy_id: cfg})

    session_cfg = cfg.get("session", {}).get("primary", {})
    sess_start = session_cfg.get("start", "09:30")
    sess_end = session_cfg.get("end", "15:20")
    lot_size = cfg.get("quantity", {}).get("lot_size", 65)
    lots = cfg.get("quantity", {}).get("lots", 1)
    qty = lots * lot_size
    side_mode = cfg.get("trade_side_mode", "BOTH").upper()

    # ── SCALP_V1_BT_FILTERS_20260823 BEGIN: config (D1, D2) ──
    _bo = cfg.get("entry_blackout") or {}
    bo_enabled = bool(_bo.get("enabled", False))
    bo_start = str(_bo.get("start", "12:00"))
    bo_end = str(_bo.get("end", "14:00"))
    try:
        # D2 fail-closed contract: None here means "unparseable" and blocks
        # every entry for the whole run (audited once below). 0 means OFF.
        max_trades_day = int(cfg.get("max_trades_per_day", 0) or 0)
        if max_trades_day < 0:
            max_trades_day = None
    except (TypeError, ValueError):
        max_trades_day = None
    if max_trades_day is None:
        write_audit_log(
            f"[BACKTEST][{strategy_id}] max_trades_per_day UNPARSEABLE "
            f"(value={cfg.get('max_trades_per_day')!r}) -> FAIL-CLOSED: "
            f"ALL entries blocked for this run")
    # ── SCALP_V1_BT_FILTERS_20260823 END: config ──

    # ── SCALP_V1_ENTRY_SIZING_20260823 BEGIN: config (D8.2, D8.3) ──
    _rs = cfg.get("risk_sizing") or {}
    rs_enabled = bool(_rs.get("enabled", False))
    try:
        rs_rupee = float(_rs.get("rupee_risk", 13000) or 13000)
        if rs_rupee <= 0:
            raise ValueError(rs_rupee)
    except (TypeError, ValueError):
        if rs_enabled:
            write_audit_log(
                f"[BACKTEST][{strategy_id}] risk_sizing.rupee_risk UNPARSEABLE "
                f"(value={_rs.get('rupee_risk')!r}) -> sizing DISABLED, "
                f"fixed lots={lots} (fail-safe)")
        rs_enabled = False
        rs_rupee = 0.0
    try:
        max_spread_pts = float(cfg.get("entry_max_spread_points", 0) or 0)
    except (TypeError, ValueError):
        max_spread_pts = 0.0
    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──

    # ── SCALP_V1_ATM_SKEW_20260826: config (D15) ──
    _sk = cfg.get("atm_skew_filter") or {}
    skew_on = bool(_sk.get("enabled", False))
    try:
        skew_min = float(_sk.get("min_diff_pts", 0.0) or 0.0)
    except (TypeError, ValueError):
        skew_min = 0.0
    # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── False = original ("sell the side
    # the ATM pair prices CHEAPER"), True = inverted. Default False keeps the
    # as-specified rule; the inverted branch is the post-hoc hypothesis.
    skew_invert = bool(_sk.get("invert", False))
    # ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── use the parity residual
    # (sk + sd + carry) instead of raw sk. carry_pts is MEASURED, not fitted:
    # carry == -(sk + sd) on every trade; the corpus mean is 6.57.
    skew_parity = bool(_sk.get("parity_adjust", False))
    try:
        _cp = _sk.get("carry_pts", 6.5)
        skew_carry = float(6.5 if _cp is None else _cp)
    except (TypeError, ValueError):
        skew_carry = 6.5

    # ── SCALP_V1_MTM_STOP_20260824: daily max MTM loss (rupees; 0 = off) ──
    try:
        mtm_limit = float(cfg.get("daily_max_mtm_loss", 0) or 0)
        if mtm_limit < 0:
            mtm_limit = abs(mtm_limit)   # tolerate "-50000" style input
    except (TypeError, ValueError):
        mtm_limit = 0.0

    # ── SCALP_V1_HEDGE_LEG_20260824: config (D11) ──
    _hl = cfg.get("hedge_leg") or {}
    hedge_on = bool(_hl.get("enabled", False))
    try:
        hedge_max_prem = float(_hl.get("max_premium", 8.0) or 8.0)
        if hedge_max_prem <= 0:
            hedge_on = False
    except (TypeError, ValueError):
        hedge_on = False
        hedge_max_prem = 8.0

    src = CandleSource()
    book = VirtualBook()
    days = _trading_days(date_from, date_to)
    total_days = len(days)

    write_audit_log(
        f"[BACKTEST] START run={run_id} {strategy_id}/{underlying} "
        f"{date_from}..{date_to} days={total_days} qty={qty} single_slot=TRUE "
        f"rolling_selection=120s premium={cfg.get('option_premium')} "
        f"session=[{sess_start},{sess_end}] side={side_mode}"
    )

    _cov = {"days_total": total_days, "days_covered": 0,
            "days_skipped": 0, "skipped": []}

    for di, day in enumerate(days, start=1):
        day_start_epoch = _ist_midnight_epoch(day)
        # ── SCALP_V1_BT_FILTERS_20260823: per-day state (D2, D4) ──
        day_entries = 0
        day_realized = 0.0        # ── SCALP_V1_MTM_STOP_20260824 ── gross of today's closed trades
        day_mtm_halted = False    #    breach latch: no further entries today
        # ── SCALP_V1_HEDGE_LEG_20260824: per-day hedge state + helpers ──
        open_hedges = {}      # main_sym -> (hedge_sym, hedge_entry_px, qty)
        _h_cache = {}         # hedge_sym -> {minute_ts: close}
        _h_universe = None    # lazy: contracts active this day
        # ── SCALP_V1_ATM_SKEW_20260826: per-day ATM state (D15) ──
        _sk_grid: Dict[str, tuple] = {}   # expiry -> (sorted strikes, {k: {CE,PE}})
        _sk_cache: Dict[tuple, object] = {}   # (minute_ts, expiry) -> (sk, sd) | None

        def _sk_build(expiry):
            """Strike grid for ONE expiry: only strikes carrying BOTH legs, so
            the ATM pair is always complete. Read from the day's universe —
            no hardcoded strike step."""
            if expiry not in _sk_grid:
                legs: Dict[float, dict] = {}
                for _m in meta_map.values():
                    if _m.get("expiry") != expiry:
                        continue
                    try:
                        _k = float(_m.get("strike") or 0)
                    except (TypeError, ValueError):
                        continue
                    if _k <= 0:
                        continue
                    legs.setdefault(_k, {})[_m.get("instrument_type")] = \
                        _m.get("tradingsymbol")
                pairs = {k: v for k, v in legs.items() if "CE" in v and "PE" in v}
                _sk_grid[expiry] = (sorted(pairs), pairs)
            return _sk_grid[expiry]

        def _sk_at(sig_ts, expiry):
            """(ATM_PE - ATM_CE, spot - ATM_strike) at this minute, or None when
            it cannot be measured (spot gap, no complete ATM pair, missing
            print). None BLOCKS the entry at the call site — fail-closed."""
            key = (sig_ts, expiry)
            if key in _sk_cache:
                return _sk_cache[key]
            out = None
            # +60: the decision is taken at the candle CLOSE, and spot_at's
            # own no-lookahead rule returns the bar stamped at-or-before
            # (arg - 60) — so this is the freshest LEGAL spot close.
            spot = src.spot_at(underlying, int(sig_ts) + 60)
            if spot is not None:
                strikes, pairs = _sk_build(expiry)
                if strikes:
                    k = min(strikes, key=lambda s: (abs(s - spot), s))
                    ce_px = src.option_premium_at(pairs[k]["CE"], sig_ts)
                    pe_px = src.option_premium_at(pairs[k]["PE"], sig_ts)
                    if ce_px is not None and pe_px is not None:
                        out = (round(float(pe_px) - float(ce_px), 2),
                               round(float(spot) - float(k), 2))
            _sk_cache[key] = out
            return out

        def _h_prices(hsym):
            if hsym not in _h_cache:
                _h_cache[hsym] = {c.ts: c.close for c in
                                  src.candles_1m_for_symbol_day(hsym, day_start_epoch)}
            return _h_cache[hsym]

        def _pick_hedge(main_sym, sig_ts, m_qty):
            """Highest-premium same-type/same-expiry contract <= max_premium at
            the signal candle (TSG semantics). None -> run unhedged (audited)."""
            nonlocal _h_universe
            if _h_universe is None:
                _h_universe = src.contracts_active_on_day(underlying, day_start_epoch)
            opt_type = "CE" if main_sym.endswith("CE") else "PE"
            m_meta = next((c for c in _h_universe
                           if c.get("tradingsymbol") == main_sym), None)
            m_exp = m_meta.get("expiry") if m_meta else None
            best = None
            for c in _h_universe:
                hsym = c.get("tradingsymbol")
                if (not hsym or hsym == main_sym
                        or not hsym.endswith(opt_type)
                        or (m_exp and c.get("expiry") != m_exp)):
                    continue
                px = _h_prices(hsym).get(sig_ts)
                if px is None or px > hedge_max_prem:
                    continue
                if best is None or px > best[1]:
                    best = (hsym, px)
            if best is None:
                write_audit_log(
                    f"[BACKTEST][{strategy_id}][HEDGE] no contract <= "
                    f"{hedge_max_prem} at entry for {main_sym} — UNHEDGED")
                return None
            return (best[0], best[1], m_qty)

        def _settle_hedge(ct, sig_ts):
            """Sell the hedge at the exit candle; FOLD pnl+charges into ct."""
            h = open_hedges.pop(ct.symbol, None)
            if h is None:
                return
            hsym, h_in, h_qty = h
            prices = _h_prices(hsym)
            h_out = prices.get(sig_ts)
            if h_out is None:
                past = [t for t in prices if t <= sig_ts]
                if past:
                    h_out = prices[max(past)]
                else:
                    h_out = h_in   # scratch at cost — audited
                    write_audit_log(
                        f"[BACKTEST][{strategy_id}][HEDGE] no exit price for "
                        f"{hsym} — scratched at cost (fail-visible)")
            h_pnl = (h_out - h_in) * h_qty          # LONG leg
            try:
                # exact-purpose model: LONG hedge trade, STT on the EXIT leg
                from app.backtest.charges.charges_model import charges_for_long_trade
                h_chg = float(charges_for_long_trade(
                    entry_price=h_in, exit_price=h_out, qty=h_qty).total_charges)
            except Exception:
                h_chg = 0.0
                write_audit_log(
                    f"[BACKTEST][{strategy_id}][HEDGE] charges model unavailable "
                    f"for {hsym} — hedge charges recorded as 0 (fail-visible)")
            ct.pnl += h_pnl
            ct.charges += h_chg
            ct.net_pnl = ct.pnl - ct.charges
            write_audit_log(
                f"[BACKTEST][{strategy_id}][HEDGE] {ct.symbol} hedged by {hsym} "
                f"in={h_in} out={h_out} pnl={h_pnl:.0f} chg={h_chg:.0f} "
                f"(folded into trade)")
        eod_close_ts = day_start_epoch + EOD_SQUARE_OFF_IST_SECS

        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=day_start_epoch,
            cfg=cfg, strategy_id=strategy_id)

        # Fix 2: skip days whose expected weekly expiry isn't in the corpus.
        if not timeline.get("covered", True):
            _cov["days_skipped"] += 1
            _cov["skipped"].append({
                "date": day.isoformat(),
                "expected_expiry": timeline.get("expected_expiry"),
                "reason": timeline.get("skip_reason"),
            })
            if progress_cb:
                progress_cb({"day": di, "total_days": total_days,
                             "date": day.isoformat(), "watched": 0,
                             "skipped": True})
            continue

        # ── SCALP_V1_DETERMINISM_20260823 ── all_symbols is a SET; raw
        # iteration order is hash-randomized per process and used to drive
        # per-candle processing order. Sort it: identical run -> identical
        # context order -> identical results.
        watched = sorted(timeline["all_symbols"])
        if not watched:
            if progress_cb:
                progress_cb({"day": di, "total_days": total_days,
                             "date": day.isoformat(), "watched": 0})
            continue
        _cov["days_covered"] += 1

        # One DB query for the day's universe → meta map (avoid per-symbol re-query)
        meta_map = {c["tradingsymbol"]: {
            "tradingsymbol": c["tradingsymbol"], "strike": c["strike"],
            "instrument_type": c["instrument_type"], "expiry": c["expiry"],
            "type": c["instrument_type"]}
            for c in src.contracts_active_on_day(underlying, day_start_epoch)}

        # Build a context (engine+indicator) per watched symbol; warm each up.
        ctxs: Dict[str, _Ctx] = {}
        for sym in watched:
            candles = src.candles_1m_for_symbol_day(sym, day_start_epoch)
            if not candles:
                continue
            meta = meta_map.get(sym)
            if meta is None:
                continue
            clock = SimClock(candles[0].ts)
            ctx = _Ctx(meta, candles, clock, book, strategy_id)
            warm = src.warmup_candles_before(sym, candles[0].ts, WARMUP_CANDLES)
            if warm:
                ctx.indicator.warmup([_bt_to_md_candle(c) for c in warm],
                                     use_history=True, history_lookback=WARMUP_CANDLES)
            ctxs[sym] = ctx

        if not ctxs:
            if progress_cb:
                progress_cb({"day": di, "total_days": total_days,
                             "date": day.isoformat(), "watched": 0})
            continue

        # Group all watched contracts' candles BY candle ts (minute), so we can
        # replicate live same-candle arbitration: when >1 selected contract fires
        # a SELL on the SAME candle, live elects the HIGHEST entry premium
        # (SignalRouter._arbitrate_sell_after_window). We process each ts in
        # order: exits first, then collect SELL candidates, then elect 1 winner.
        from collections import defaultdict
        by_ts = defaultdict(list)
        for sym, ctx in ctxs.items():
            for c in ctx.candles:
                by_ts[c.ts].append((sym, c))

        _all_ts = sorted(by_ts.keys())
        _n_ts = len(_all_ts)
        if progress_cb and _n_ts:
            progress_cb({"day": di, "total_days": total_days,
                         "date": day.isoformat(), "minute": 0, "minutes_total": _n_ts})
        for _mi, ts in enumerate(_all_ts, start=1):
            if progress_cb and (_mi % 30 == 0 or _mi == _n_ts):
                progress_cb({"day": di, "total_days": total_days,
                             "date": day.isoformat(), "minute": _mi, "minutes_total": _n_ts})
            entry_candidates = []   # (entry_price, symbol, ctx, c, signal)

            # ── SCALP_V1_DETERMINISM_20260823 BEGIN: PASS 1 — EXITS ──
            # All exits for this candle resolve BEFORE any signal evaluates.
            # Live-faithful: an SL/TP touch is a tick DURING the minute, so at
            # candle close (when signals fire) the slot is already free. This
            # also removes the same-candle exit/entry race that made results
            # depend on hash-randomized symbol order.
            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                ctx.clock.advance_to(ts)

                # ── EXIT (only the contract holding the slot) ──
                open_pos = book.get_open_for_symbol(sym)
                if open_pos is not None:
                    # ── SCALP_V1_BT_FILTERS_20260823 BEGIN: EOD @15:15 (D4) ──
                    if ts >= eod_close_ts:
                        # Candle STARTS at/after 15:15 — live already squared
                        # off at 15:15:00. This branch only fires when the
                        # 15:14 candle was missing from the corpus; fill at
                        # this candle's OPEN as the closest proxy for the
                        # 15:15:00 market price. No SL/TP resolution: that
                        # price action post-dates the live square-off.
                        _ct = book.close_position(sym, exit_ts=eod_close_ts,
                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──
                        continue
                    # ── SCALP_V1_BT_FILTERS_20260823 END (SL/TP path below) ──
                    book.update_extremes(sym, c.close)
                    # ── SCALP_V1_PARALLEL_20260823 ── the 1s series is only
                    # READ by resolve_exit_on_candle in the BOTH-TOUCHED case
                    # (high>=sl AND low<=tp); probing the 1s table on every
                    # in-trade candle was hundreds of thousands of needless
                    # SQLite queries per full run. Gate the probe on the same
                    # predicate — identical fills by construction.
                    seconds = None
                    if c.high >= open_pos.sl and c.low <= open_pos.tp:
                        minute_start = (ts // 60) * 60
                        seconds = (src.seconds_for_minute(sym, minute_start)
                                   if src.has_1s_for_minute(sym, minute_start) else None)
                    fr = resolve_exit_on_candle(candle=c, sl=open_pos.sl,
                                                tp=open_pos.tp, seconds=seconds)
                    if fr.exited:
                        # stamp exit at candle CLOSE (ts+60) to match live labelling
                        _ct = book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=fr.exit_price,
                                            exit_reason=fr.exit_reason,
                                            ambiguous_fill=fr.ambiguous)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──
                    # ── SCALP_V1_BT_FILTERS_20260823: the 15:14 candle closes
                    # at exactly 15:15:00 — if SL/TP didn't fire inside it,
                    # live's 15:15:00 EOD job exits here. Fill at candle close.
                    elif ts + 60 >= eod_close_ts:
                        _ct = book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──
                    # ── SCALP_V1_MTM_STOP_20260824: MTM check AFTER this
                    # candle's SL/TP resolution (intra-candle exits fire first
                    # at their levels, like live ticks). Breach → force-close
                    # at candle close, reason MTM, and halt the day's entries.
                    if mtm_limit > 0 and not day_mtm_halted:
                        _op = book.get_open_for_symbol(sym)
                        if _op is not None:
                            _unreal = (_op.entry_price - c.close) * _op.qty
                            if day_realized + _unreal <= -mtm_limit:
                                _ct = book.close_position(sym, exit_ts=ts + 60,
                                                    exit_price=c.close,
                                                    exit_reason="MTM",
                                                    ambiguous_fill=False)
                                _settle_hedge(_ct, ts)   # ── SCALP_V1_HEDGE_LEG_20260824 ──
                                day_realized += _ct.pnl
                                day_mtm_halted = True
                        elif day_realized <= -mtm_limit:
                            day_mtm_halted = True

            # ── SCALP_V1_DETERMINISM_20260823: PASS 2 — INDICATORS + SIGNALS ──
            # Slot state is now post-exit for every symbol uniformly. NOTE:
            # candles at/after the 15:15 EOD close now also feed the indicator
            # (pass 1's `continue` no longer skips it) — harmless and uniform:
            # the session gate blocks any entry there, and per-day contexts
            # are rebuilt from DB warmup, so no state crosses days.
            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                md = _bt_to_md_candle(c)

                # feed indicator (live-mode → tracks _last_red_low)
                ind_vals = ctx.indicator.update(md)
                if not ctx.indicator.is_ready() or ind_vals is None:
                    continue

                now_ist = ctx.clock.now_ist()
                is_trading_time = is_within_session(now_ist, sess_start, sess_end)
                no_open_trade = not book.any_open()

                conds = ctx.conditions_engine.evaluate(
                    candle=md, indicators=ind_vals,
                    is_trading_time=is_trading_time, no_open_trade=no_open_trade)

                signal = ctx.engine.on_candle(md, ctx.indicator, conds)
                if not signal.is_sell:
                    continue

                # ── 120s SELECTION GATE (live CE_NOT_SELECTED / PE_NOT_SELECTED) ──
                snap = active_snapshot_for_ts(timeline, ts)
                snap_syms = {o["tradingsymbol"] for o in snap}
                locked_sym = None
                if book.any_open():
                    op_syms = book.open_symbols()
                    locked_sym = op_syms[0] if op_syms else None
                if sym not in snap_syms and sym != locked_sym:
                    continue  # NOT SELECTED → drop, exactly like live

                # side-mode gate (matches _resolve_slot)
                if side_mode == "CE" and sym.endswith("PE"):
                    continue
                if side_mode == "PE" and sym.endswith("CE"):
                    continue

                # ── SCALP_V1_BT_FILTERS_20260823 BEGIN: entry gates ──
                # D4: an entry stamped at/after the 15:15 square-off would be
                # killed instantly by the live EOD job — don't create it.
                if ts + 60 >= eod_close_ts:
                    continue
                # D1: blackout on the entry decision stamp (candle close).
                if bo_enabled and _in_blackout(
                        ctx.clock.now_ist() + timedelta(seconds=60),
                        bo_start, bo_end):
                    continue
                # ── SCALP_V1_BT_FILTERS_20260823 END: entry gates ──

                # ── SCALP_V1_DIAG_20260823 BEGIN ── entry snapshot. Built at
                # CANDIDATE time because ind_vals/conds are per-symbol loop
                # locals: by election time they'd hold the LAST iterated
                # symbol's values, not the winner's. Diagnostics only —
                # nothing downstream reads this for any trading decision.
                _e8 = ind_vals.get("ema8")
                _e20l = ind_vals.get("ema20_low")
                _e20h = ind_vals.get("ema20_high")
                # ── SCALP_V1_ENTRY_SIZING_20260823: D8.3 overextension gate.
                # Skip entries where the band spread (EMA8 - EMA20_low) shows
                # an overextended move — the one entry feature negative in
                # both 2020-24 and 2025-26. Warmup-None values -> gate
                # inactive for this candle (can't measure -> don't block).
                if (max_spread_pts > 0 and _e8 is not None
                        and _e20l is not None
                        and (_e8 - _e20l) > max_spread_pts):
                    continue
                # ── SCALP_V1_ATM_SKEW_20260826: D15 ATM skew gate. Sell the
                # side the ATM pair prices as the cheaper one: a CE sell needs
                # ATM PE dearer than ATM CE (and vice-versa) by >= min_diff.
                # Unmeasurable -> BLOCK (fail-closed, as with the EMA/VWAP
                # gates). Recorded either way as "sk"/"sd" for analysis.
                _skv = _sk_at(ts, (meta_map.get(sym) or {}).get("expiry")) \
                    if (skew_on or True) else None
                if skew_on:
                    if _skv is None:
                        continue
                    # ── SCALP_V1_ATM_SKEW_PARITY_20260826 ── parity-adjusted
                    # value: sk + sd == -carry EXACTLY under put-call parity,
                    # so (sk + sd + carry) is the residual richness with the
                    # strike-grid geometry removed, centred at ~0. OFF keeps
                    # raw sk, so a paired run differs ONLY in this choice.
                    _sv = ((_skv[0] + _skv[1] + skew_carry)
                           if skew_parity else _skv[0])
                    _diff = _sv if sym.endswith("CE") else -_sv
                    # ── SCALP_V1_ATM_SKEW_FLIP_20260826 ── one sign flip is the
                    # whole difference between the two rules; the threshold,
                    # the fail-closed path and the diagnostics are shared, so
                    # a paired comparison differs ONLY in direction.
                    if skew_invert:
                        _diff = -_diff
                    if _diff <= skew_min:
                        continue
                _r2 = lambda v: round(v, 2)
                diag = json.dumps({
                    "b": _r2(c.close - c.open),
                    "r": _r2(c.high - c.low),
                    "e8": _r2(c.close - _e8) if _e8 is not None else None,
                    "e20": _r2(c.close - _e20l) if _e20l is not None else None,
                    "sp": _r2(_e8 - _e20l) if (_e8 is not None and _e20l is not None) else None,
                    "e20h": _r2(_e20h - c.close) if _e20h is not None else None,
                    "rk": _r2(signal.sl - signal.entry_price),
                    # ── SCALP_V1_EMA_GATE_20260824 ── gate slope at entry so
                    # the next ceiling analysis can slice on regime state.
                    "gs": (_r2(ind_vals.get("gate_ema_slope"))
                           if ind_vals.get("gate_ema_slope") is not None else None),
                    # ── SCALP_V1_VWAP_20260825 ── close-minus-VWAP at entry
                    "vw": (_r2(c.close - ind_vals.get("vwap"))
                           if ind_vals.get("vwap") is not None else None),
                    # ── SCALP_V1_ATM_SKEW_20260826 ── sk = ATM PE - ATM CE
                    # (what the filter tests); sd = spot - ATM strike (the
                    # put-call-parity component). Recorded even when the
                    # filter is OFF so separation can be tested BEFORE tuning.
                    "sk": (_skv[0] if _skv is not None else None),
                    "sd": (_skv[1] if _skv is not None else None),
                }, separators=(",", ":"))
                entry_candidates.append((signal.entry_price, sym, ctx, c, signal, diag))
                # ── SCALP_V1_DIAG_20260823 END ──

            # ── SAME-CANDLE ARBITRATION: elect HIGHEST entry premium ──
            # Matches SignalRouter: max by (entry_price, symbol). Only ONE winner
            # enters, and only if the strategy-wide slot is free.
            # ── SCALP_V1_BT_FILTERS_20260823: D2 daily cap at ELECTION ──
            # (strategy-wide, like the single slot: cap counts entries, and
            #  None = fail-closed parse failure -> block everything)
            # ── SCALP_V1_MTM_STOP_20260824 ── halted day: no entries
            _mtm_blocked = mtm_limit > 0 and (day_mtm_halted or
                                              day_realized <= -mtm_limit)
            _cap_blocked = _mtm_blocked or (max_trades_day is None or
                            (max_trades_day > 0 and day_entries >= max_trades_day))
            if entry_candidates and not book.any_open() and not _cap_blocked:
                entry_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                ep, sym, ctx, c, signal, diag = entry_candidates[0]   # ── SCALP_V1_DIAG_20260823 ──
                # ── SCALP_V1_ENTRY_SIZING_20260823: D8.2 risk-normalized
                # sizing. Constant rupee risk per trade: wider stop -> fewer
                # lots, tighter stop -> more (never above configured lots,
                # never below 1). risk_pts is the ACTUAL final stop distance,
                # so this composes correctly with any min/max SL clamp config.
                _trade_qty = qty
                if rs_enabled:
                    # ── SCALP_V1_SIZING_FLOATFIX_20260824 ── prices are
                    # paise-quantized; quantize the distance before the
                    # floor division so 20.000000000000004 sizes as 20.0.
                    _risk_pts = round(float(signal.sl) - float(signal.entry_price), 2)
                    if _risk_pts > 0:
                        _lots_dyn = int(rs_rupee // (_risk_pts * lot_size))
                        _lots_dyn = max(1, min(lots, _lots_dyn))
                        _trade_qty = _lots_dyn * lot_size
                book.open_position(VirtualPosition(
                    symbol=sym,
                    strike=float(ctx.contract.get("strike", 0.0)),
                    instrument_type=ctx.contract.get("instrument_type", "CE"),
                    expiry=ctx.contract.get("expiry", ""),
                    direction="SHORT",
                    entry_ts=ts + 60, entry_price=signal.entry_price,
                    sl=signal.sl, tp=signal.tp, qty=_trade_qty,   # ── SCALP_V1_ENTRY_SIZING_20260823 ──
                    condition=diag))   # ── SCALP_V1_DIAG_20260823 ──
                # ── SCALP_V1_HEDGE_LEG_20260824: buy protection (fail-open) ──
                if hedge_on:
                    _h = _pick_hedge(sym, ts, _trade_qty)
                    if _h is not None:
                        open_hedges[sym] = _h
                day_entries += 1   # SCALP_V1_BT_FILTERS_20260823 (D2)

        # EOD: close whatever holds the slot
        for sym, ctx in ctxs.items():
            if book.has_open_for_symbol(sym):
                last = ctx.candles[-1]
                book.close_position(sym, exit_ts=last.ts + 60, exit_price=last.close,
                                    exit_reason="EOD", ambiguous_fill=False)

        if progress_cb:
            progress_cb({"day": di, "total_days": total_days,
                         "date": day.isoformat(), "watched": len(ctxs)})

    trades = book.closed_trades()
    summary = _summarize(trades, started)
    write_audit_log(
        f"[BACKTEST] DONE run={run_id} trades={len(trades)} "
        f"gross={summary['summary']['gross_pnl']:.2f} "
        f"charges={summary['summary']['total_charges']:.2f} "
        f"net={summary['summary']['net_pnl']:.2f} "
        f"win_rate={summary['summary']['win_rate']:.1f}% "
        f"ambiguous={summary['summary']['ambiguous_fills']} "
        f"elapsed={summary['summary']['elapsed_s']}s")
    summary["run_id"] = run_id
    summary["trades"] = trades
    summary["config"] = cfg
    summary["summary"]["coverage"] = _cov
    write_audit_log(
        f"[BACKTEST][COVERAGE] days_total={_cov['days_total']} "
        f"covered={_cov['days_covered']} skipped={_cov['days_skipped']}"
    )
    # ── BT_CONFIG_OVERRIDE: restore — override must not outlive the run. ──
    clear_backtest_config_override(_bt_ov_token)
    return summary


def _deep_merge(base, over):
    import copy
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _summarize(trades, started):
    n = len(trades)
    gross = sum(t.pnl for t in trades)
    total_charges = sum(getattr(t, "charges", 0.0) for t in trades)
    net = sum(getattr(t, "net_pnl", t.pnl) for t in trades)
    # Win-rate on GROSS pnl, matching the paper UI (pnl_value > 0).
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = (100.0 * len(wins) / n) if n else 0.0
    ambiguous = sum(1 for t in trades if t.ambiguous_fill)
    # Max drawdown on the NET equity curve (chronological by exit).
    eq = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        eq += getattr(t, "net_pnl", t.pnl)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {"summary": {
        "total_trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": gross,            # GROSS (kept key name for compatibility)
        "gross_pnl": gross,
        "total_charges": total_charges,
        "net_pnl": net,
        "avg_pnl": (gross / n) if n else 0.0,
        "avg_net_pnl": (net / n) if n else 0.0,
        "max_drawdown": max_dd,        # on NET equity
        "ambiguous_fills": ambiguous,
        "elapsed_s": round(time.time() - started)}}
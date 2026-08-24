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
        self.indicator = IndicatorEnginePineV19()
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
                        book.close_position(sym, exit_ts=eod_close_ts,
                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
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
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=fr.exit_price,
                                            exit_reason=fr.exit_reason,
                                            ambiguous_fill=fr.ambiguous)
                    # ── SCALP_V1_BT_FILTERS_20260823: the 15:14 candle closes
                    # at exactly 15:15:00 — if SL/TP didn't fire inside it,
                    # live's 15:15:00 EOD job exits here. Fill at candle close.
                    elif ts + 60 >= eod_close_ts:
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)

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
                _r2 = lambda v: round(v, 2)
                diag = json.dumps({
                    "b": _r2(c.close - c.open),
                    "r": _r2(c.high - c.low),
                    "e8": _r2(c.close - _e8) if _e8 is not None else None,
                    "e20": _r2(c.close - _e20l) if _e20l is not None else None,
                    "sp": _r2(_e8 - _e20l) if (_e8 is not None and _e20l is not None) else None,
                    "e20h": _r2(_e20h - c.close) if _e20h is not None else None,
                    "rk": _r2(signal.sl - signal.entry_price),
                }, separators=(",", ":"))
                entry_candidates.append((signal.entry_price, sym, ctx, c, signal, diag))
                # ── SCALP_V1_DIAG_20260823 END ──

            # ── SAME-CANDLE ARBITRATION: elect HIGHEST entry premium ──
            # Matches SignalRouter: max by (entry_price, symbol). Only ONE winner
            # enters, and only if the strategy-wide slot is free.
            # ── SCALP_V1_BT_FILTERS_20260823: D2 daily cap at ELECTION ──
            # (strategy-wide, like the single slot: cap counts entries, and
            #  None = fail-closed parse failure -> block everything)
            _cap_blocked = (max_trades_day is None or
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
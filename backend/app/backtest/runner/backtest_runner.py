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

        watched = timeline["all_symbols"]
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

            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                ctx.clock.advance_to(ts)
                md = _bt_to_md_candle(c)

                # ── EXIT (only the contract holding the slot) ──
                open_pos = book.get_open_for_symbol(sym)
                if open_pos is not None:
                    book.update_extremes(sym, c.close)
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

                entry_candidates.append((signal.entry_price, sym, ctx, c, signal))

            # ── SAME-CANDLE ARBITRATION: elect HIGHEST entry premium ──
            # Matches SignalRouter: max by (entry_price, symbol). Only ONE winner
            # enters, and only if the strategy-wide slot is free.
            if entry_candidates and not book.any_open():
                entry_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                ep, sym, ctx, c, signal = entry_candidates[0]
                book.open_position(VirtualPosition(
                    symbol=sym,
                    strike=float(ctx.contract.get("strike", 0.0)),
                    instrument_type=ctx.contract.get("instrument_type", "CE"),
                    expiry=ctx.contract.get("expiry", ""),
                    direction="SHORT",
                    entry_ts=ts + 60, entry_price=signal.entry_price,
                    sl=signal.sl, tp=signal.tp, qty=qty))

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
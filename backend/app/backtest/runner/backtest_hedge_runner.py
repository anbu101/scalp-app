# backend/app/backtest/runner/backtest_hedge_runner.py
#
# SCALP_V3 / SCALP_V4 backtest — option-BUYING HEDGE strategies.
#
# Reuses the V1 backtest machinery (candle source, selection timeline, real
# StrategyEngine/Indicator/Condition pipeline, warmup) but replicates the hedge
# trade model from scalp_v3_tick_engine / scalp_v3_manager:
#
#   • Signal fires on contract S (identical V1 pipeline). S is TRACKED, not traded.
#   • V4 ONLY: post-signal veto — if ema8 > ema20_high, the SELL is dropped
#     (scalp_v4_tick_engine SCALP_V4_EXTRA_GATE; strict '>').
#   • Same-candle arbitration: highest signal premium wins (entry_price, symbol).
#   • Hedge = highest-premium OPPOSITE-side selected contract (_pick_hedge).
#   • Hedge entry = hedge close on the signal candle (stamped ts+60).
#     hedge_sl = hedge_entry - hedge_sl_points (decoupled; fallback max_sl→20).
#   • Exit (dual-trigger, each candle): signal high>=signal_sl → SIG_SL;
#     signal low<=signal_tp → SIG_TP; hedge low<=hedge_sl → HEDGE_SL.
#     Pessimistic for a LONG hedge on same-candle ambiguity (loss-side wins).
#   • P&L LONG on the hedge; charges direction="LONG" (STT on exit).
#
# Single global trade at a time (the DB single-trade gate) → one open per book.

from __future__ import annotations

import uuid
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import (
    set_backtest_config_override, clear_backtest_config_override)
from app.config.strategy_loader import load_strategy_config
from app.utils.session_utils import is_within_session

from app.marketdata.candle import Candle, CandleSource as MDCandleSource

from app.backtest.data.candle_source import CandleSource, BTCandle
from app.backtest.sim.sim_clock import SimClock
from app.backtest.sim.hedge_virtual_book import (
    HedgeVirtualBook, HedgePosition,
)
from app.backtest.sim.hedge_fill_model import resolve_hedge_exit_on_candle
from app.backtest.engine.backtest_strategy_engine import BacktestStrategyEngine
from app.backtest.engine.backtest_selector import (
    build_selection_timeline, active_snapshot_for_ts,
)

WARMUP_CANDLES = 500
IST = 5 * 3600 + 30 * 60


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


class _EmptyBook:
    """Always-empty book. V3/V4 live feed no_open_trade=True to the condition
    engine ALWAYS — their single-trade gate is DB-backed in the manager, NOT
    StrategyEngine.in_trade. So the engine must always see 'no open trade' for
    THIS symbol. An always-empty book makes _refresh_in_trade a no-op, exactly
    replicating that. (The actual single-trade gate is HedgeVirtualBook in the
    runner loop.)"""
    def get_open_for_symbol(self, symbol):
        return None


class _Ctx:
    """Per-symbol engine context (mirrors the V1 runner's _Ctx)."""
    def __init__(self, contract, candles, clock, strategy_id):
        from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
        from app.engine.condition_engine_v1_9 import ConditionEngineV19
        self.contract = contract
        self.symbol = contract["tradingsymbol"]
        self.candles = candles
        self.clock = clock
        self.indicator = IndicatorEnginePineV19()
        self.conditions_engine = ConditionEngineV19()
        self.engine = BacktestStrategyEngine(
            strategy_id=strategy_id, slot_name=self.symbol,
            symbol=self.symbol, clock=clock, book=_EmptyBook(),
        )
        # candle lookup by ts for hedge-price + exit OHLC resolution
        self.by_ts = {c.ts: c for c in candles}


def _hedge_sl_points(cfg: dict) -> float:
    """Decoupled hedge SL distance: hedge_sl_points → max_sl_points → 20."""
    return float(cfg.get("hedge_sl_points", cfg.get("max_sl_points", 20)) or 20)


def run_hedge_backtest(
    *,
    strategy_id: str,           # "SCALP_V3" | "SCALP_V4"
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    assert strategy_id in ("SCALP_V3", "SCALP_V4"), "hedge runner is V3/V4 only"
    is_v4 = (strategy_id == "SCALP_V4")
    started = time.time()
    run_id = str(uuid.uuid4())

    cfg = load_strategy_config(strategy_id)
    if config_override:
        cfg = _deep_merge(cfg, config_override)

    # ── BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)
    # INLINE (the on-disk Settings file). Install this run's merged cfg as a
    # context override so the engine's SL/RR gates use the Backtest page params,
    # not Settings. Cleared in the finally that wraps the run body. ──
    _bt_ov_token = set_backtest_config_override({strategy_id: cfg})

    session_cfg = cfg.get("session", {}).get("primary", {})
    sess_start = session_cfg.get("start", "09:30")
    sess_end = session_cfg.get("end", "15:20")
    lot_size = cfg.get("quantity", {}).get("lot_size", 65)
    lots = cfg.get("quantity", {}).get("lots", 1)
    qty = lots * lot_size
    side_mode = cfg.get("trade_side_mode", "BOTH").upper()
    hedge_sl_pts = _hedge_sl_points(cfg)

    # ── V3_RISK_LIMITS BEGIN ── daily/monthly ₹ P&L guards (0/absent = off).
    # Config-driven: only SCALP_V3 configs carry these keys today, so V4 runs
    # are untouched. Basis: realized NET (post-charge) of the period's closed
    # trades + open-trade gross MTM; clamped INTRABAR at the exact threshold
    # price; IST calendar-day / calendar-month buckets; a block persists for
    # the remainder of its period once reached.
    _rl_dml = max(0.0, float(cfg.get("daily_max_loss") or 0))
    _rl_dmp = max(0.0, float(cfg.get("daily_max_profit") or 0))
    _rl_mml = max(0.0, float(cfg.get("monthly_max_loss") or 0))
    _rl_mmp = max(0.0, float(cfg.get("monthly_max_profit") or 0))
    _rl_enabled = any(v > 0 for v in (_rl_dml, _rl_dmp, _rl_mml, _rl_mmp))
    _day_realized = 0.0
    _day_blocked = False
    _month_key = ""
    _month_realized: Dict[str, float] = {}
    _month_blocked: set = set()
    _rl_stats = {"risk_exits": 0, "days_blocked": 0, "months_blocked": []}

    def _rl_on_close():
        """Accumulate the JUST-closed trade (book.closed[-1]) into the day and
        month buckets and refresh block flags. MUST be called after EVERY
        book.close_position (risk, normal, EOD) so realized stays exact."""
        nonlocal _day_realized, _day_blocked
        t = book.closed[-1]
        net = float(getattr(t, "net_pnl", t.pnl))
        _day_realized += net
        _month_realized[_month_key] = _month_realized.get(_month_key, 0.0) + net
        if not _rl_enabled:
            return
        m = _month_realized[_month_key]
        if (_rl_dml and _day_realized <= -_rl_dml) or (_rl_dmp and _day_realized >= _rl_dmp):
            _day_blocked = True
        if (_rl_mml and m <= -_rl_mml) or (_rl_mmp and m >= _rl_mmp):
            _month_blocked.add(_month_key)
    # ── V3_RISK_LIMITS END ──

    # ── V3_TRADE_COUNT_LIMITS BEGIN ── per-IST-day trade-COUNT guards
    # (0/absent = off). Config-driven and V3-only by the same design as
    # V3_RISK_LIMITS: only SCALP_V3 configs carry these keys, so V4 runs are
    # untouched. Counting basis: a trade consumes quota at ENTRY (open_position)
    # regardless of how it later exits. Side = the TRADED (hedge/bought) side —
    # in V3 the hedge side is always the strict opposite of the signal side, so
    # per-side caps are equivalent under either labeling; we count the option
    # actually bought. Both caps are independent AND gates with V3_RISK_LIMITS.
    _tc_max_day = max(0, int(cfg.get("max_trades_per_day") or 0))
    _tc_max_side = max(0, int(cfg.get("max_trades_per_side_per_day") or 0))
    _tc_day_total = 0
    _tc_day_side = {"CE": 0, "PE": 0}
    _tc_stats = {"entries_blocked_day_cap": 0, "entries_blocked_side_cap": 0}
    # ── V3_TRADE_COUNT_LIMITS END ──

    src = CandleSource()
    book = HedgeVirtualBook()
    days = _trading_days(date_from, date_to)
    total_days = len(days)

    # Coverage report (Fix 2): days where the EXPECTED weekly expiry isn't in the
    # corpus are SKIPPED honestly rather than run against a wrong farther expiry.
    _cov = {"days_total": total_days, "days_covered": 0,
            "days_skipped": 0, "skipped": []}

    write_audit_log(
        f"[BACKTEST_HEDGE] START run={run_id} {strategy_id}/{underlying} "
        f"{date_from}..{date_to} days={total_days} qty={qty} hedge_sl_pts={hedge_sl_pts} "
        f"premium={cfg.get('option_premium')} session=[{sess_start},{sess_end}] "
        f"side={side_mode} v4_veto={is_v4}"
    )

    for di, day in enumerate(days, start=1):
        day_start_epoch = _ist_midnight_epoch(day)
        # ── V3_RISK_LIMITS ── new IST day: reset the day bucket; the month
        # bucket is keyed by calendar month so it carries across days.
        _day_realized = 0.0
        _day_blocked = False
        _month_key = day.strftime("%Y-%m")
        _month_realized.setdefault(_month_key, 0.0)
        # ── V3_TRADE_COUNT_LIMITS ── new IST day: reset trade counters.
        _tc_day_total = 0
        _tc_day_side = {"CE": 0, "PE": 0}

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

        meta_map = {c["tradingsymbol"]: {
            "tradingsymbol": c["tradingsymbol"], "strike": c["strike"],
            "instrument_type": c["instrument_type"], "expiry": c["expiry"],
            "type": c["instrument_type"]}
            for c in src.contracts_active_on_day(underlying, day_start_epoch)}

        ctxs: Dict[str, _Ctx] = {}
        for sym in watched:
            candles = src.candles_1m_for_symbol_day(sym, day_start_epoch)
            if not candles:
                continue
            meta = meta_map.get(sym)
            if meta is None:
                continue
            clock = SimClock(candles[0].ts)
            ctx = _Ctx(meta, candles, clock, strategy_id)
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

        # Group candles by ts (minute) for ordered replay.
        from collections import defaultdict
        by_ts = defaultdict(list)
        for sym, ctx in ctxs.items():
            for c in ctx.candles:
                by_ts[c.ts].append((sym, c))

        _all_ts = sorted(by_ts.keys())
        _n_ts = len(_all_ts)
        # Emit a progress tick at the start of the day's replay so a single-day
        # run shows movement immediately rather than sitting on "starting…".
        if progress_cb and _n_ts:
            progress_cb({"day": di, "total_days": total_days,
                         "date": day.isoformat(), "watched": len(ctxs),
                         "minute": 0, "minutes_total": _n_ts})
        for _mi, ts in enumerate(_all_ts, start=1):
            # Intra-day progress every ~30 minutes of replay (and on the last
            # minute) so the bar animates within a single day and the cancel
            # flag is observed promptly.
            if progress_cb and (_mi % 30 == 0 or _mi == _n_ts):
                progress_cb({"day": di, "total_days": total_days,
                             "date": day.isoformat(), "watched": len(ctxs),
                             "minute": _mi, "minutes_total": _n_ts})
            # ── 1) EXIT first if a hedge trade is open ──
            if book.any_open():
                pos = book.get_open()
                sig_ctx = ctxs.get(pos.signal_symbol)
                hed_ctx = ctxs.get(pos.hedge_symbol)
                sig_c = sig_ctx.by_ts.get(ts) if sig_ctx else None
                hed_c = hed_ctx.by_ts.get(ts) if hed_ctx else None

                # Only evaluate exits on/after the candle AFTER entry (entry
                # candle already consumed at entry close). ts here is candle
                # start; entry was stamped ts_entry+60, so a candle with
                # ts > signal_candle_ts is a later minute.
                if ts > pos.signal_candle_ts and (sig_c is not None or hed_c is not None):
                    if hed_c is not None:
                        book.update_extremes_hedge(hed_c.high, hed_c.low)
                    # ── V3_RISK_LIMITS BEGIN ── intrabar clamp, checked BEFORE
                    # the normal dual-trigger exit. cum(period) = realized net
                    # + (px − entry)·qty on the open hedge; solve for the px
                    # where cum == ±limit and see whether this hedge candle
                    # traded through it. LOSS checks first (pessimistic); if a
                    # loss AND a profit threshold sit inside the same candle,
                    # loss wins and the fill is flagged ambiguous. Gap-throughs
                    # fill at the candle open (a market exit can't fill at a
                    # price that never traded).
                    _risk_exit = None  # (exit_px, reason, ambiguous)
                    if _rl_enabled and hed_c is not None:
                        _q = float(pos.qty)
                        _he = float(pos.hedge_entry_price)
                        _mreal = _month_realized.get(_month_key, 0.0)
                        _loss = []
                        if _rl_dml:
                            _loss.append((_he + (-_rl_dml - _day_realized) / _q, "DAILY_MAX_LOSS"))
                        if _rl_mml:
                            _loss.append((_he + (-_rl_mml - _mreal) / _q, "MONTHLY_MAX_LOSS"))
                        _prof = []
                        if _rl_dmp:
                            _prof.append((_he + (_rl_dmp - _day_realized) / _q, "DAILY_MAX_PROFIT"))
                        if _rl_mmp:
                            _prof.append((_he + (_rl_mmp - _mreal) / _q, "MONTHLY_MAX_PROFIT"))
                        _lpx, _lreason = max(_loss, key=lambda x: x[0]) if _loss else (None, None)
                        _ppx, _preason = min(_prof, key=lambda x: x[0]) if _prof else (None, None)
                        _l_hit = _lpx is not None and hed_c.low <= _lpx
                        _p_hit = _ppx is not None and hed_c.high >= _ppx
                        if _l_hit:
                            _px = _lpx if hed_c.open > _lpx else hed_c.open
                            _risk_exit = (_px, _lreason, bool(_p_hit))
                        elif _p_hit:
                            _px = _ppx if hed_c.open < _ppx else hed_c.open
                            _risk_exit = (_px, _preason, False)
                    if _risk_exit is not None:
                        _px, _reason, _amb = _risk_exit
                        hmeta = meta_map.get(pos.hedge_symbol, {})
                        book.close_position(
                            exit_ts=ts + 60, exit_price=round(_px, 2),
                            exit_reason=_reason, ambiguous_fill=_amb,
                            strike=float(hmeta.get("strike", 0.0)),
                            expiry=hmeta.get("expiry", ""))
                        _rl_on_close()
                        # Force the block from the EXIT EVENT itself: exit
                        # charges can leave realized net a hair short of a
                        # PROFIT limit, and "limit reached" must still halt
                        # the period.
                        if _reason.startswith("DAILY"):
                            _day_blocked = True
                        else:
                            _month_blocked.add(_month_key)
                        _rl_stats["risk_exits"] += 1
                    # ── V3_RISK_LIMITS END ── (normal exits only if no clamp)
                    else:
                        # Resolve dual-trigger exit. Missing a contract's candle for
                        # this minute → treat its triggers as not-hit (no data).
                        s_hi = sig_c.high if sig_c else float("-inf")
                        s_lo = sig_c.low if sig_c else float("inf")
                        h_lo = hed_c.low if hed_c else float("inf")
                        minute_start = (ts // 60) * 60
                        sig_secs = (src.seconds_for_minute(pos.signal_symbol, minute_start)
                                    if src.has_1s_for_minute(pos.signal_symbol, minute_start) else None)
                        hed_secs = (src.seconds_for_minute(pos.hedge_symbol, minute_start)
                                    if src.has_1s_for_minute(pos.hedge_symbol, minute_start) else None)
                        fr = resolve_hedge_exit_on_candle(
                            signal_high=s_hi, signal_low=s_lo, hedge_low=h_lo,
                            signal_sl=pos.signal_sl, signal_tp=pos.signal_tp,
                            hedge_sl=pos.hedge_sl,
                            signal_seconds=sig_secs, hedge_seconds=hed_secs,
                        )
                        if fr.exited:
                            # Exit price = hedge close on exit candle (paper parity).
                            exit_px = hed_c.close if hed_c is not None else pos.hedge_entry_price
                            hmeta = meta_map.get(pos.hedge_symbol, {})
                            book.close_position(
                                exit_ts=ts + 60, exit_price=exit_px,
                                exit_reason=fr.exit_reason, ambiguous_fill=fr.ambiguous,
                                strike=float(hmeta.get("strike", 0.0)),
                                expiry=hmeta.get("expiry", ""))
                            _rl_on_close()   # ── V3_RISK_LIMITS ── accumulate

            # ── 2) Collect SELL candidates this minute (if no open trade) ──
            # ── V3_RISK_LIMITS ── hard entry gate: once a period limit is
            # reached, no further entries that IST day / calendar month.
            # Skipping the whole candidate scan is safe: exits use raw candles
            # (not indicators), a daily block never lifts intraday, and ctxs
            # are rebuilt with fresh warmup every day — no state divergence.
            if _day_blocked or (_month_key in _month_blocked):
                continue
            entry_candidates = []  # (entry_price, signal_symbol, ctx, c, signal)
            for sym, c in by_ts[ts]:
                ctx = ctxs[sym]
                ctx.clock.advance_to(ts)
                md = _bt_to_md_candle(c)
                ind_vals = ctx.indicator.update(md)
                if not ctx.indicator.is_ready() or ind_vals is None:
                    continue
                now_ist = ctx.clock.now_ist()
                is_trading_time = is_within_session(now_ist, sess_start, sess_end)
                conds = ctx.conditions_engine.evaluate(
                    candle=md, indicators=ind_vals,
                    is_trading_time=is_trading_time, no_open_trade=True)
                signal = ctx.engine.on_candle(md, ctx.indicator, conds)
                if not signal.is_sell:
                    continue

                # ── V4 VETO: ema8 > ema20_high drops the SELL (strict '>') ──
                if is_v4:
                    e8 = ind_vals.get("ema8")
                    e20h = ind_vals.get("ema20_high")
                    if e8 is not None and e20h is not None and e8 > e20h:
                        continue

                # signal-side selection-membership gate (CE/PE_NOT_SELECTED)
                snap = active_snapshot_for_ts(timeline, ts)
                snap_syms = {o["tradingsymbol"] for o in snap}
                if sym not in snap_syms:
                    continue
                # side-mode gates the SIGNAL side
                if side_mode == "CE" and sym.endswith("PE"):
                    continue
                if side_mode == "PE" and sym.endswith("CE"):
                    continue

                # ── V3_TRADE_COUNT_LIMITS BEGIN ── per-day count caps gate
                # CANDIDACY (not election) so a capped side can never outbid an
                # uncapped one. Placed AFTER indicator/engine evaluation and all
                # other gates: indicator + engine state stays bit-identical to
                # an unlimited run — a limited run merely IGNORES entries.
                # Side is the TRADED (hedge) side = opposite of signal symbol.
                if _tc_max_day and _tc_day_total >= _tc_max_day:
                    _tc_stats["entries_blocked_day_cap"] += 1
                    continue
                if _tc_max_side:
                    _traded_side = "PE" if sym.endswith("CE") else "CE"
                    if _tc_day_side[_traded_side] >= _tc_max_side:
                        _tc_stats["entries_blocked_side_cap"] += 1
                        continue
                # ── V3_TRADE_COUNT_LIMITS END ──

                entry_candidates.append((signal.entry_price, sym, ctx, c, signal))

            # ── 3) Elect highest signal premium, pair hedge, enter ──
            if entry_candidates and not book.any_open():
                entry_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                ep, sig_sym, ctx, c, signal = entry_candidates[0]
                signal_side = "CE" if sig_sym.endswith("CE") else "PE"

                hedge = _pick_hedge(
                    opposite_of=signal_side, ts=ts, ctxs=ctxs,
                    snap=active_snapshot_for_ts(timeline, ts))
                if hedge is None:
                    continue  # no hedge available → skip (per spec)

                hedge_entry = round(hedge["close"], 2)
                hedge_sl = round(hedge_entry - hedge_sl_pts, 2)
                book.open_position(HedgePosition(
                    signal_symbol=sig_sym,
                    signal_token=0, signal_side=signal_side,
                    signal_entry_price=signal.entry_price,
                    signal_sl=signal.sl, signal_tp=signal.tp,
                    signal_candle_ts=ts,
                    hedge_symbol=hedge["symbol"], hedge_token=0,
                    hedge_side=hedge["side"],
                    hedge_entry_ts=ts + 60, hedge_entry_price=hedge_entry,
                    hedge_sl=hedge_sl, qty=qty))
                # ── V3_TRADE_COUNT_LIMITS ── quota consumed at ENTRY, keyed by
                # the TRADED (hedge) side. Counted unconditionally (harmless
                # when caps are off) so stats stay meaningful in control runs.
                _tc_day_total += 1
                _tc_day_side[hedge["side"]] += 1

        # EOD square-off any still-open trade at last hedge candle close.
        if book.any_open():
            pos = book.get_open()
            hed_ctx = ctxs.get(pos.hedge_symbol)
            if hed_ctx and hed_ctx.candles:
                last = hed_ctx.candles[-1]
                hmeta = meta_map.get(pos.hedge_symbol, {})
                book.close_position(
                    exit_ts=last.ts + 60, exit_price=last.close,
                    exit_reason="EOD", ambiguous_fill=False,
                    strike=float(hmeta.get("strike", 0.0)),
                    expiry=hmeta.get("expiry", ""))
                _rl_on_close()   # ── V3_RISK_LIMITS ── accumulate EOD close
            # ── V3_RISK_LIMITS ── STALE_FORCE_CLOSE invariant: a position must
            # NEVER survive a day boundary. If the hedge ctx is missing (expiry
            # passed / no candles), force-close at last known entry-side price
            # so the book can never wedge open silently again.
            if book.any_open():
                pos2 = book.get_open()
                hmeta2 = meta_map.get(pos2.hedge_symbol, {})
                book.close_position(
                    exit_ts=day_start_epoch + (15 * 3600 + 30 * 60),
                    exit_price=pos2.hedge_entry_price,
                    exit_reason="STALE_FORCE_CLOSE", ambiguous_fill=True,
                    strike=float(hmeta2.get("strike", 0.0)),
                    expiry=hmeta2.get("expiry", ""))
                _rl_on_close()
                write_audit_log(
                    f"[BACKTEST_HEDGE][STALE_FORCE_CLOSE] {pos2.hedge_symbol} "
                    f"had no EOD candle on {day.isoformat()} — forced flat. "
                    f"This indicates a data gap or close-path bug; investigate.")

        # ── V3_RISK_LIMITS ── per-day observability
        if _day_blocked or (_month_key in _month_blocked):
            _rl_stats["days_blocked"] += 1
        if progress_cb:
            progress_cb({"day": di, "total_days": total_days,
                         "date": day.isoformat(), "watched": len(ctxs)})

    summary = _summarize(book.closed, started)
    summary["run_id"] = run_id
    write_audit_log(
        f"[BACKTEST_HEDGE] DONE run={run_id} trades={len(book.closed)} "
        f"gross={summary['summary']['gross_pnl']:.2f} "
        f"charges={summary['summary']['total_charges']:.2f} "
        f"net={summary['summary']['net_pnl']:.2f} "
        f"win_rate={summary['summary']['win_rate']:.1f}% "
        f"ambiguous={summary['summary']['ambiguous_fills']} "
        f"elapsed={summary['summary']['elapsed_s']}s")
    # ── BT_CONFIG_OVERRIDE: restore — the override must not outlive the run. ──
    clear_backtest_config_override(_bt_ov_token)
    summary["summary"]["coverage"] = _cov
    # ── V3_RISK_LIMITS ── surface guard activity in the persisted summary
    _rl_stats["months_blocked"] = sorted(_month_blocked)
    summary["summary"]["risk_limits"] = _rl_stats
    # ── V3_TRADE_COUNT_LIMITS ── surface count-guard config + activity
    summary["summary"]["trade_count_limits"] = {
        "max_trades_per_day": _tc_max_day,
        "max_trades_per_side_per_day": _tc_max_side,
        **_tc_stats,
    }
    write_audit_log(
        f"[BACKTEST_HEDGE][COVERAGE] days_total={_cov['days_total']} "
        f"covered={_cov['days_covered']} skipped={_cov['days_skipped']} "
        f"(skipped days had no in-corpus weekly expiry — not faithfully testable)"
    )
    return {"run_id": run_id, "summary": summary["summary"],
            "trades": book.closed, "config": cfg, "coverage": _cov}


def _pick_hedge(*, opposite_of, ts, ctxs, snap):
    """Highest-premium opposite-side SELECTED contract, priced at this minute's
    close. Mirrors scalp_v3 _pick_hedge: among the selected hedge-side contracts,
    take the highest premium. Backtest premium = the contract's candle close at
    ts (the WS-tick analogue right after the signal candle)."""
    hedge_side = "PE" if opposite_of == "CE" else "CE"
    snap_syms = {o["tradingsymbol"] for o in snap
                 if o["tradingsymbol"].endswith(hedge_side)}
    best = None
    for sym in snap_syms:
        ctx = ctxs.get(sym)
        if ctx is None:
            continue
        c = ctx.by_ts.get(ts)
        if c is None or c.close <= 0:
            continue
        cand = {"symbol": sym, "side": hedge_side, "close": float(c.close)}
        if best is None or cand["close"] > best["close"]:
            best = cand
    return best


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
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
    wins = [t for t in trades if t.pnl > 0]      # win on GROSS hedge P&L
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = (100.0 * len(wins) / n) if n else 0.0
    ambiguous = sum(1 for t in trades if t.ambiguous_fill)
    eq = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        eq += getattr(t, "net_pnl", t.pnl)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {"summary": {
        "total_trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "total_pnl": gross, "gross_pnl": gross,
        "total_charges": total_charges, "net_pnl": net,
        "avg_pnl": (gross / n) if n else 0.0,
        "avg_net_pnl": (net / n) if n else 0.0,
        "max_drawdown": max_dd, "ambiguous_fills": ambiguous,
        "elapsed_s": round(time.time() - started)}}
# backend/app/engine/vet/vet_selection_loop.py
#
# ── VET_V1 SELECTION LOOP + MINUTE COORDINATOR ── (tma2_selection_loop donor)
# ============================================================================
# Standalone async loop, launched from api_server (enabled-flag + license
# gate, NOT via StrategyRuntimeManager). One WebSocket, one signal engine,
# one manager, ONE position at a time.
#
# REUSE, NOT REINVENTION: the tick plumbing is TMA_V2's TMA2TickEngine,
# imported verbatim — its ±30-strike band (±1500 pts) was sized for hedge
# reach, which is exactly the ₹-cheap wing VET needs, and its CandleBuilder /
# ChainStore are strategy-agnostic. Cross-package reuse follows the house
# precedent of VET's backtest importing IC's synthetic-wing primitives: one
# convention, maintained once. If TMA_V2 is ever deleted, move the classes
# into a shared module rather than forking them here.
#
# ORDERING per completed minute ts (coordinator-owned):
#   1. exits/boundaries first — expiry exit, EOD square (belt under the
#      api_server cron), then the engine decision's exit half
#   2. signal engine folds the spot candle (5m decisions come out of it)
#   3. entries last, behind the entry-cutoff gate
#
# ENTRY CUTOFF SEMANTICS (backtest parity): after entry_cutoff (15:00) new
# entries are blocked but exits still fire — a FLIP after cutoff degrades to
# exit-only, leaving the book flat, which is exactly what the backtest's
# cutoff_blocked_entries counter records.
#
# CARRIED-POSITION SUBSCRIPTION: a positional carry can hold a strike that a
# big gap has pushed outside today's ±1500 band. After resume, the carried
# legs' tokens are added to the universe explicitly — otherwise their exits
# fall back to last-known prices and the fill quality silently rots.
#
# BOOT: 10-session spot warmup via TMA2's fetch_warmup_sessions(days=10)
# (fail closed — short warmup BLOCKS trading rather than diverging from the
# backtest), mid-session 1m backfill on restart, boot reconciliation
# (intraday-mode rows from a previous day → STALE; positional carry adopted).
# WATCHDOG: no spot candle for 3+ minutes inside session hours → Telegram
# system alert (zombie-WS doctrine).
# CRASH-PROOF SHELL: every death is loud + alerted, day-cycle rearm.
# ============================================================================

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Dict, List, Optional

from app.engine.tma2.tma2_live_warmup import (fetch_today_spot,
                                              fetch_warmup_sessions)
from app.engine.tma2.tma2_tick_engine import TMA2TickEngine
from app.engine.vet.vet_common import VetRepo
from app.engine.vet.vet_live_core import ENTER, FLIP, HOLD
from app.engine.vet.vet_live_signal_engine import VetLiveSignalEngine
from app.engine.vet.vet_manager import VetManager
from app.event_bus.audit_logger import write_audit_log
from app.utils.day_cycle import wait_for_arm_window, wait_for_teardown

IST = 5 * 3600 + 30 * 60
STRATEGY_ID = "VET_V1"
QUOTE_LOOKBACK_MIN = 15          # last-print window, in MINUTES (the
                                 # ChainStore API takes lookback_min)

# module-level runtime registry (V3/PST get_manager pattern) — the EOD job,
# the kill switch and the state routes all reach the SAME manager instance.
_manager: Optional[VetManager] = None
_engine: Optional[VetLiveSignalEngine] = None


def get_manager() -> Optional[VetManager]:
    return _manager


def get_engine() -> Optional[VetLiveSignalEngine]:
    return _engine


def _hm_min(s: str, default_min: int) -> int:
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _ist_min(ts: int) -> int:
    return ((int(ts) + IST) % 86400) // 60


def _ist_day(ts: int) -> date:
    return datetime.utcfromtimestamp(int(ts) + IST).date()


async def vet_selection_loop(zerodha_manager):
    """Crash-proof day-cycle shell (TMA2 wrapper, verbatim shape)."""
    last_run_day: Optional[date] = None
    while True:
        armed_day: Optional[date] = None
        try:
            armed_day = await wait_for_arm_window(STRATEGY_ID, last_run_day)
            await _vet_selection_loop_inner(zerodha_manager)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            write_audit_log(f"[VET] selection loop DIED: {e!r} — will rearm "
                            f"next session")
            try:
                from app.api.telegram_api import notify_system_alert
                notify_system_alert({"message": f"VET_V1 loop died: {e!r}",
                                     "severity": "error"})
            except Exception:
                pass
            if armed_day is None:
                armed_day = datetime.now().date()
        try:
            # NOTE: wait_for_teardown takes NO tag — verified signature.
            await wait_for_teardown()
        except Exception:
            await asyncio.sleep(600)
        last_run_day = armed_day


async def _vet_selection_loop_inner(zerodha_manager):
    global _manager, _engine
    from app.config.strategy_loader import load_strategy_config
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    notify = None
    try:
        from app.api.telegram_api import notify_system_alert as _raw_alert

        def _notify_impl(msg, severity="warning"):
            _raw_alert({"message": str(msg), "severity": severity})
        notify = _notify_impl
    except Exception:
        pass

    write_audit_log("[VET] selection loop starting")

    # ── wait for the Zerodha session INDEFINITELY (2026-07-16 doctrine) ──
    kite = None
    _waited = 0
    while kite is None:
        try:
            kite = zerodha_manager.get_kite()
        except Exception:
            kite = None
        if kite is None:
            if _waited and _waited % 300 == 0:
                write_audit_log(f"[VET] waiting for Zerodha session "
                                f"({_waited // 60} min)")
            await asyncio.sleep(5)
            _waited += 5

    cfg = load_strategy_config(STRATEGY_ID) or {}
    mode = str(cfg.get("trade_execution_mode", "PAPER")).upper()
    eod_square = bool(cfg.get("eod_square", True))
    entry_cutoff_min = _hm_min(cfg.get("entry_cutoff", "15:00"), 15 * 60)
    exit_min = _hm_min(cfg.get("exit_time", "15:15"), 15 * 60 + 15)
    expiry_exit_min = min(exit_min, 15 * 60 + 20)
    warm_need = int(cfg.get("warmup_sessions") or 10)

    # ── BOOT RETRY until 15:00 IST (instruments/warmup fail transiently) ──
    instruments_df = None
    warm = None
    while True:
        try:
            from app.fetcher.zerodha_instruments import load_instruments_df
            instruments_df = load_instruments_df()
            warm = fetch_warmup_sessions(kite, instruments_df=instruments_df,
                                         days=warm_need)
        except Exception as e:
            write_audit_log(f"[VET] boot fetch failed: {e!r}")
            warm = None
        if warm:
            break
        if _ist_min(int(time.time())) >= 15 * 60:
            write_audit_log("[VET] no warmup by 15:00 — giving up today "
                            "(fail closed)")
            if notify:
                notify("VET_V1: no warmup data by 15:00 — not trading today",
                       "error")
            return
        await asyncio.sleep(60)

    warm_flat: List[Dict] = [c for spot_1m, _ds in warm for c in spot_1m]
    engine = VetLiveSignalEngine(cfg, warmup_1m=warm_flat)
    _engine = engine
    if not engine.warmup_ok():
        # honest degradation is for the ENGINE to refuse, loudly, not trade
        write_audit_log(f"[VET] warmup short: {engine.warmup_session_count()}"
                        f"/{warm_need} sessions — decisions will be BLOCKED")
        if notify:
            notify(f"VET_V1 warmup short ({engine.warmup_session_count()}"
                   f"/{warm_need}) — blocked for the day", "error")

    repo = VetRepo()
    repo.ensure_schema()

    # ── universe + websocket ──
    today = datetime.now().date()
    expiry_iso = expected_expiry_for_day(today).isoformat()
    try:
        spot_ltp = float(kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]
                         ["last_price"])
    except Exception as e:
        write_audit_log(f"[VET] spot LTP failed: {e!r} — not trading "
                        f"(fail closed)")
        return

    state = {"cutoff_blocked": 0, "last_decision": None}

    def chain_list(side: str, ts: int) -> List[Dict]:
        """The manager's chain_fn: live ladder with last-print LTPs, the
        same at-or-before convention (bounded staleness) the backtest uses."""
        out = []
        for sym in tick.chain.symbols(side):
            meta = tick.chain.meta(sym) if hasattr(tick.chain, "meta") else {}
            px = tick.chain.last_close_at_or_before(sym, ts - ts % 60,
                                                    QUOTE_LOOKBACK_MIN)
            if px is None:
                continue
            out.append({"tradingsymbol": sym,
                        "token": meta.get("token"),
                        "strike": meta.get("strike"),
                        "expiry": meta.get("expiry") or expiry_iso,
                        "instrument_type": side, "ltp": float(px)})
        return out

    def quote(sym: str) -> Optional[float]:
        # ── QUOTE FIX 2026-09-01 ── candles are keyed at minute STARTS and the
        # store probes in exact 60s steps from the ts given; an unaligned
        # wall-clock ts (12:00:01) misses every key and every exit fell back
        # to the entry price ("gross 0"). Align to the minute first.
        now = int(time.time())
        return tick.chain.last_close_at_or_before(sym, now - now % 60,
                                                  QUOTE_LOOKBACK_MIN)

    executor = None
    if mode == "LIVE":
        from app.execution.executor_factory import get_executor_for_strategy
        executor = get_executor_for_strategy(STRATEGY_ID)

    manager = VetManager(cfg, repo=repo, chain_fn=chain_list,
                         quote_fn=quote, executor=executor, mode=mode)
    _manager = manager

    # ── boot reconciliation (checklist Part 5) ──
    g = repo.open_group(mode)
    if g and g.get("main"):
        row_day = _ist_day(int(g["main"]["entry_ts"]))
        if eod_square and row_day != today:
            # intraday mode must not adopt yesterday's row — it should not
            # exist (crash before its own EOD); investigate, don't trade it.
            for leg in (g.get("main"), g.get("wing")):
                if leg:
                    repo.mark_stale(leg["id"], "intraday row from prior day")
            write_audit_log("[VET] prior-day intraday row(s) marked STALE")
        else:
            manager.resume_from_db()
            write_audit_log(f"[VET] adopted carried position "
                            f"{g['group_id']} ({row_day})")

    # ── the per-minute coordinator ──
    def on_minute(ts: int, spot_candle: Optional[dict], chain_store) -> None:
        try:
            _on_minute_inner(ts, spot_candle)
        except Exception as e:
            write_audit_log(f"[VET] on_minute FAILED at {ts}: {e!r}")

    def _on_minute_inner(ts: int, spot_candle: Optional[dict]) -> None:
        now_min = _ist_min(ts)

        # 1 ── boundaries before anything else
        pos = manager.pos
        if pos is not None:
            exp = (pos["main"] or {}).get("expiry")
            if exp and str(exp)[:10] == today.isoformat() \
                    and now_min >= expiry_exit_min:
                manager.expiry_exit(ts)
                return
        if eod_square and now_min >= exit_min and manager.pos is not None:
            manager.eod_square_off(ts)
            return

        if spot_candle is None:
            return
        manager.cfg["_spot"] = float(spot_candle["close"])

        # 2 ── fold the candle; a decision only exists at 5m completion
        sig = engine.on_minute(spot_candle)
        if sig is None:
            return
        decision = engine.decide(manager.pos["side"] if manager.pos else None)
        state["last_decision"] = decision
        if decision.get("blocked"):
            return
        action = decision.get("action", HOLD)
        if action == HOLD:
            return

        # 3 ── entry-cutoff gate (exits always run; entries degrade away)
        if now_min >= entry_cutoff_min:
            if action == ENTER:
                state["cutoff_blocked"] += 1
                write_audit_log(f"[VET] entry blocked by cutoff "
                                f"({cfg.get('entry_cutoff')})")
                return
            if action == FLIP:
                state["cutoff_blocked"] += 1
                manager.close_position(decision.get("reason") or "FLIP",
                                       ts=ts)
                write_audit_log("[VET] FLIP after cutoff degraded to "
                                "exit-only (backtest parity)")
                return
        manager.on_decision(decision, ts=ts)

    tick = TMA2TickEngine(zerodha_manager, instruments_df, on_minute,
                          capture_dir=None)
    n = tick.resolve_universe(spot_ltp, expiry_iso)
    if n <= 0:
        write_audit_log("[VET] empty option universe — not trading "
                        "(fail closed)")
        if notify:
            notify("VET_V1: empty option universe at boot", "error")
        return

    # carried strikes may sit outside today's band after a gap — subscribe
    # them explicitly so their exits price off live ticks, not stale memory.
    if manager.pos is not None:
        from app.engine.tma2.tma2_tick_engine import CandleBuilder
        for leg in (manager.pos.get("main"), manager.pos.get("wing")):
            if not leg or not leg.get("token"):
                continue
            tok = int(leg["token"])
            if tok not in tick._builders:
                tick._builders[tok] = CandleBuilder()
                tick._tok2sym[tok] = leg["tradingsymbol"]
                tick.chain.put_meta(leg["tradingsymbol"],
                                    float(leg.get("strike") or 0),
                                    str(leg.get("expiry") or expiry_iso),
                                    leg.get("instrument_type") or "",
                                    tok)
                write_audit_log(f"[VET] carried leg {leg['tradingsymbol']} "
                                f"added to universe explicitly")

    # ── mid-session restart backfill: today's completed minutes ──
    if _ist_min(int(time.time())) > 9 * 60 + 20:
        try:
            bf = fetch_today_spot(kite, instruments_df=instruments_df)
            fed = 0
            for c in (bf or []):
                if engine.on_minute(c) is not None:
                    fed += 1
            write_audit_log(f"[VET] backfilled {len(bf or [])} minutes "
                            f"({fed} completed 5m bars) — decisions from "
                            f"backfill are DISCARDED, live starts now")
        except Exception as e:
            write_audit_log(f"[VET] backfill failed: {e!r} — engine will "
                            f"warm from live ticks (later first signal)")

    tick.start()
    write_audit_log(f"[VET] armed: mode={mode} leg={cfg.get('leg_action')} "
                    f"eod_square={eod_square} universe={n} expiry={expiry_iso}")

    # ── watchdog + session lifetime ──
    silent_alerted = False
    while True:
        await asyncio.sleep(30)
        nowm = _ist_min(int(time.time()))
        if nowm >= 15 * 60 + 29:
            break
        armed = 9 * 60 + 16 <= nowm <= 15 * 60 + 29
        wd = datetime.now().weekday() < 5
        if armed and wd and tick.last_spot_candle_ts:
            gap = int(time.time()) - tick.last_spot_candle_ts
            if gap > 180 and not silent_alerted:
                silent_alerted = True
                write_audit_log(f"[VET] WATCHDOG: no spot candle for "
                                f"{gap}s — zombie WS?")
                if notify:
                    notify(f"VET_V1: no spot candle for {gap}s", "error")
            elif gap <= 90:
                silent_alerted = False
    try:
        tick.stop()
    except Exception:
        pass
    write_audit_log("[VET] session over — loop returning to day-cycle")
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
# FAR-WING SEARCH (VET_WING_FAR_20261101): the ±band is fixed at boot, so a
# Rs-cheap wing can sit outside it all day (28 Oct: every PE <= 3 did). The
# manager's wing_search_fn below looks further out on this expiry's
# instrument list (one REST quote), and subscribes the pick into this engine.
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
from app.engine.vet.vet_manager import VetManager, _fresh_vet_cfg   # ── VET_SLTP_LIVE_20261015 ──
from app.event_bus.audit_logger import write_audit_log
from app.risk import execution_modes as _xm   # ── FLEET_MODES_20260923 ──
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


# ── VET_NEAR_OBS_20261106 BEGIN ── observability: per-bar log, freeze / feed / restart alerts,
# live spot candles kept for parity. Nothing here changes a trading decision.
NEAR_PTS = 3.0              # a bar this close to either trend test is flagged NEAR
CONN_GRACE_S = 60           # connection down this long in the session → alert
FIRST_CANDLE_GRACE_S = 150  # after start, before "no spot candle yet" may fire
CAPTURE_KEEP_DAYS = 45


def _vet_capture_dir() -> Optional[str]:
    """~/.scalp-app/vet_capture — the live NIFTY 1m candles Velvet decided on
    (one <day>.jsonl per session). Old files are pruned. Never raises."""
    try:
        try:
            from app.utils.app_paths import APP_HOME
            base = APP_HOME / "vet_capture"
        except Exception:
            from pathlib import Path
            base = Path.home() / ".scalp-app" / "vet_capture"
        base.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - CAPTURE_KEEP_DAYS * 86400
        for f in base.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass
        return str(base)
    except Exception as e:
        write_audit_log(f"[VET][CAPTURE] disabled — {e!r}")
        return None
# ── VET_NEAR_OBS_20261106 END ──


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

    # ── VET_NEAR_OBS_20261106 ── new alerts follow the fleet rule (CRITICAL_ALERT_ROUTING_20260909):
    # always the in-app bell; Telegram only when Velvet trades LIVE.
    _alert_mode = {"mode": None}

    def _alert(msg, severity="warning"):
        try:
            from app.api.telegram_api import notify_critical
            payload = {"severity": severity, "message": str(msg), "strategy_id": STRATEGY_ID}
            if _alert_mode["mode"]:
                payload["mode"] = _alert_mode["mode"]
            notify_critical(payload)
        except Exception as _ae:
            write_audit_log(f"[VET][ALERT] could not raise alert ({_ae!r}): {msg}")

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
    mode = _xm.book(cfg.get("trade_execution_mode", "PAPER"))   # ── FLEET_MODES_20260923 ── PAPER_LIVE → LIVE engine (twin via VetRepo)
    _alert_mode["mode"] = str(mode or "PAPER").upper()           # ── VET_NEAR_OBS_20261106 ──
    eod_square = bool(cfg.get("eod_square", True))
    entry_cutoff_min = _hm_min(cfg.get("entry_cutoff", "15:00"), 15 * 60)
    exit_min = _hm_min(cfg.get("exit_time", "15:15"), 15 * 60 + 15)
    expiry_exit_min = min(exit_min, 15 * 60 + 20)
    warm_need = int(cfg.get("warmup_sessions") or 10)

    # ── BOOT RETRY until 15:00 IST (instruments/warmup fail transiently) ──
    instruments_df = None
    warm = None
    # ── FAIL-LOUD 2026-09-01 ── on 2026-09-01 this loop retried silently
    # from 08:30 to 15:00 (an EMPTY warmup return logs nothing on the
    # exception path). Every attempt now records its reason, rate-limited,
    # and a single Telegram alert fires if still unwarmed at 09:20 — the
    # operator must learn BEFORE the session that the strategy is blind.
    attempts, last_reason, last_log, alerted = 0, "", 0, False
    while True:
        attempts += 1
        try:
            from app.fetcher.zerodha_instruments import load_instruments_df
            instruments_df = load_instruments_df()
            warm = fetch_warmup_sessions(kite, instruments_df=instruments_df,
                                         days=warm_need)
            last_reason = ("warmup returned EMPTY (historical API gave no "
                           "sessions — token stale? network?)"
                           if not warm else "")
        except Exception as e:
            last_reason = f"exception {e!r}"
            warm = None
        if warm:
            if attempts > 1:
                write_audit_log(f"[VET] warmup OK after {attempts} attempts")
            break
        now = int(time.time())
        if attempts == 1 or now - last_log >= 300:
            write_audit_log(f"[VET] warmup attempt {attempts} failed: "
                            f"{last_reason} — retrying every 60s")
            last_log = now
        if not alerted and _ist_min(now) >= 9 * 60 + 20:
            alerted = True
            if notify:
                notify(f"VET_V1 still has NO warmup at 09:20 after "
                       f"{attempts} attempts: {last_reason}", "error")
        if _ist_min(now) >= 15 * 60:
            write_audit_log(f"[VET] no warmup by 15:00 after {attempts} "
                            f"attempts (last: {last_reason}) — giving up "
                            f"today (fail closed)")
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

    state = {"cutoff_blocked": 0, "last_decision": None,
             "sltp_blocked": 0}   # ── VET_SLTP_LIVE_20261015 ──

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
                         quote_fn=quote, executor=executor, mode=mode,
                         cfg_reader=_fresh_vet_cfg)   # ── VET_SLTP_LIVE_20261015 ── SL/TP % from Settings at each entry
    _manager = manager
    manager.entries_gate = lambda: _xm.entries_allowed(_xm.strategy_mode(STRATEGY_ID))   # ── FLEET_MODES_20260923 ──
    # ── VET_DTE_LIVE_20261005 ── today's lots / skip from dte_lot_mult (read
    # once per session, like the mode); carried positions keep their own qty.
    manager.apply_day_lots(today)
    # ── VET_HEDGE_FIX_20261012 ── a SELL config that DISPLAYS a wing cap but has
    # hedge_enabled false sells NAKED shorts. Say so every session; never flip
    # the flag here (a risk change needs the owner's Save in Settings).
    try:
        _cap = float(cfg.get("hedge_max_premium") or 0)
        if manager.is_sell and not manager.wants_wing and _cap > 0:
            _msg = (f"VET SELL is trading NAKED: hedge_enabled is false although Hedge Wing Max shows "
                    f"₹{_cap:g}. Settings → VET V1 → Save to switch the wing on (or set it to 0 to "
                    f"sell naked on purpose).")
            write_audit_log(f"[VET][HEDGE_OFF] {_msg}")
            from app.event_bus.inapp_events import record_alert
            record_alert("VET_HEDGE_OFF", _msg, severity="warning", strategy_id=STRATEGY_ID,
                         mode=("live" if str(mode).upper() == "LIVE" else "paper"))
    except Exception as _e:
        write_audit_log(f"[VET][HEDGE_OFF][CHECK_FAIL] {_e!r}")

    # ── boot reconciliation (checklist Part 5) ──
    # ── EXPIRED_CARRY_SETTLE_20261021 ── settle legs whose contract
    # expired while the app was off, BEFORE open_group adopts them.
    try:
        from app.trading.expired_settle import settle_vet
        settle_vet(repo, repo.open_legs(mode), kite=kite, notify=notify)
    except Exception as _xe:
        write_audit_log(f"[VET][EXPIRED_SETTLE] failed: {_xe!r} — "
                        f"adoption continues unchanged")
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

    # ── VET_SLTP_LIVE_20261015 ── a same-day SL/TP exit keeps blocking its
    # side across a restart (the runner's block_target outlives the bar loop)
    try:
        manager.restore_sltp_block()
    except Exception as _e:
        write_audit_log(f"[VET][SLTP] restore block failed: {_e!r}")

    # ── FEED_RECOVERY_20261103 ── rebuild today's indicators from Kite after a feed stall
    from app.engine.feed_recovery import FeedRecovery

    def _kite_now():
        try:
            return zerodha_manager.get_kite() or kite
        except Exception:
            return kite
    recovery = FeedRecovery(tag="VET", engine=engine, notify=notify,
                            fetch_fn=lambda: fetch_today_spot(_kite_now(), instruments_df=instruments_df))

    def _recover_before(c) -> bool:
        try:
            return recovery.before_candle(int(c["ts"]))
        except Exception as _e:
            write_audit_log(f"[VET][RECOVER] check failed: {_e!r}")
            return False

    # ── VET_NEAR_OBS_20261106 BEGIN ── per-bar log line, freeze alert, restart check
    state["freeze_alerted"] = False

    def _held() -> str:
        p = manager.pos
        if not p:
            return "flat"
        return f"holding {p.get('side')} {(p.get('main') or {}).get('tradingsymbol')}"

    def _bar_log(sig: Dict, decision: Optional[Dict], note: Optional[str] = None) -> None:
        try:
            hhmm = datetime.utcfromtimestamp(int(sig["bar_ts"]) + IST).strftime("%H:%M")
            if note:
                what = note
            elif decision is None:
                what = "no decision"
            elif decision.get("blocked"):
                what = f"BLOCKED ({decision['blocked']})"
            else:
                a = decision.get("action", HOLD)
                what = a + (f" {decision.get('side')}" if a != HOLD and decision.get("side") else "")
            up, dn = float(sig.get("to_up", 0.0)), float(sig.get("to_down", 0.0))
            near = " NEAR" if min(abs(up), abs(dn)) < NEAR_PTS else ""
            write_audit_log(
                f"[VET][BAR] {hhmm} O {float(sig.get('open', sig['spot'])):.2f} C {float(sig.get('close', sig['spot'])):.2f} "
                f"channel {float(sig.get('ch_bot', 0)):.2f}/{float(sig.get('ch_top', 0)):.2f} "
                f"ema{engine.ema_fast1}-{engine.ema_fast2} {float(sig.get('ema_gap', 0)):+.2f} "
                f"cond {int(sig.get('prev_condition', 0)):+d}→{int(sig['condition']):+d} "
                f"to_up {up:+.2f} to_down {dn:+.2f}{near} → {what} ({_held()})")
        except Exception as _be:
            write_audit_log(f"[VET][BAR] log failed: {_be!r}")

    def _check_frozen() -> None:
        if engine.guard.frozen and not state["freeze_alerted"]:
            state["freeze_alerted"] = True
            msg = (f"VET_V1 signal engine FROZE: {engine.guard.reason}. No further decisions today; "
                   f"the app is {_held()} and that position still exits by the expiry / EOD rules.")
            write_audit_log(f"[VET][FROZEN] {msg}")
            _alert(msg, "error")

    def _restart_check(fed: int) -> None:
        """After a mid-session backfill: do today's rebuilt candles want a
        different side from the position held? (Velvet acts on the next
        completed 5m bar; this only makes the gap visible.)"""
        if not fed:
            return
        try:
            d = engine.decide(manager.pos["side"] if manager.pos else None)
            sig = engine.latest_signal()
            if sig:
                _bar_log(sig, d, note="last rebuilt bar (restart backfill)")
            if d.get("blocked") or d.get("action", HOLD) == HOLD:
                write_audit_log("[VET][RESTART_CHECK] rebuilt candles agree with the position held")
                return
            hhmm = (datetime.utcfromtimestamp(int(d["bar_ts"]) + IST).strftime("%H:%M")
                    if d.get("bar_ts") else "?")
            msg = (f"VET_V1 restarted mid-session: today's rebuilt candles say {d.get('action')} "
                   f"{d.get('side') or ''} (last bar {hhmm}, condition {int(d.get('condition') or 0):+d}) "
                   f"while the app is {_held()}. Nothing was traded during the restart (backfill decisions "
                   f"are discarded by design); Velvet acts on the next completed 5-minute bar.")
            write_audit_log(f"[VET][RESTART_CHECK] {msg}")
            _alert(msg, "warning")
        except Exception as _re:
            write_audit_log(f"[VET][RESTART_CHECK] failed: {_re!r}")
    # ── VET_NEAR_OBS_20261106 END ──

    # ── the per-minute coordinator ──
    def on_minute(ts: int, spot_candle: Optional[dict], chain_store) -> None:
        try:
            _on_minute_inner(ts, spot_candle)
        except Exception as e:
            write_audit_log(f"[VET] on_minute FAILED at {ts}: {e!r}")

    def _on_minute_inner(ts: int, spot_candle: Optional[dict]) -> None:
        now_min = _ist_min(ts)

        # 0 ── VET_LIVE_CONFIRM_20261026 ── a LIVE exit that could not be
        # confirmed is re-driven every minute (nothing new is decided while it
        # is pending: open_position refuses while a position exists). The
        # candle is still folded below so the signal engine stays in parity.
        if manager.exit_pending:
            manager.retry_exit(ts)
            if manager.exit_pending:          # still stuck: fold, decide nothing
                if spot_candle is not None:
                    manager.cfg["_spot"] = float(spot_candle["close"])
                    _recover_before(spot_candle)      # ── FEED_RECOVERY_20261103 ──
                    _s = engine.on_minute(spot_candle)
                    _check_frozen()                   # ── VET_NEAR_OBS_20261106 ──
                    if _s is not None:
                        _bar_log(_s, None, note="exit pending — nothing decided")
                return

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
        _suppress = _recover_before(spot_candle)      # ── FEED_RECOVERY_20261103 ──
        sig = engine.on_minute(spot_candle)
        _check_frozen()                               # ── VET_NEAR_OBS_20261106 ──
        if sig is None:
            return
        if _suppress:     # the stream is still distorted (rebuild pending): no decision
            write_audit_log("[VET][RECOVER] decision suppressed — spot-feed rebuild pending")
            _bar_log(sig, None, note="decision suppressed — spot-feed rebuild pending")   # ── VET_NEAR_OBS_20261106 ──
            return
        # ── VET_SLTP_LIVE_20261015 ── runner step 1c: SL / TP at this 5m
        # close, BEFORE the signal (a stop that fires keeps its own reason)
        _sltp_hit = manager.check_sltp(ts)
        decision = engine.decide(manager.pos["side"] if manager.pos else None)
        state["last_decision"] = decision
        _bar_log(sig, decision)                       # ── VET_NEAR_OBS_20261106 ──
        if decision.get("blocked"):
            return
        # runner block_target: no re-entry while the trend is unchanged
        if not manager.sltp_gate(decision, _sltp_hit):
            state["sltp_blocked"] += 1
            write_audit_log(f"[VET][SLTP] re-entry on {decision.get('side')} "
                            f"blocked — trend unchanged since the SL/TP exit")
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
                          capture_dir=_vet_capture_dir(),      # ── VET_NEAR_OBS_20261106 ── live spot candles kept
                          capture_spot_only=True,
                          notify=notify, log_tag="VET_TICK",   # ── WS_SELFHEAL_20261021 ──
                          alert_label="VET_V1")
    n = tick.resolve_universe(spot_ltp, expiry_iso)
    if n <= 0:
        write_audit_log("[VET] empty option universe — not trading "
                        "(fail closed)")
        if notify:
            notify(f"VET_V1: empty option universe at boot — "
                   f"{tick.expiry_hint(expiry_iso)}", "error")   # ── STRATEGY_INDEPENDENCE_20261015 ──
        return

    # ── VET_WING_FAR_20261101 BEGIN ── far-wing search wiring. Band edges are
    # recorded NOW — before carried legs or any far add widen the chain — so
    # the log's "N strikes beyond the universe edge" is measured from the
    # armed ±band.
    from app.engine.tma2.tma2_tick_engine import STRIKE_STEP as _STEP
    _wing_band = None
    try:
        _ks = [float((tick.chain.meta(s) or {}).get("strike") or 0)
               for _sd in ("CE", "PE") for s in tick.chain.symbols(_sd)]
        _ks = [k for k in _ks if k > 0]
        if _ks:
            _wing_band = (min(_ks), max(_ks))
    except Exception as _be:
        write_audit_log(f"[VET][WING_FAR] band edges unavailable: {_be!r}")

    def far_wing(side, ts, main, expiry, cap, max_strikes):
        from app.engine.vet.vet_wing_search import find_far_wing
        priced = {c["tradingsymbol"] for c in chain_list(side, ts)}
        try:
            qk = zerodha_manager.get_data_kite() or kite
        except Exception:
            qk = kite
        row, info = find_far_wing(
            instruments_df=instruments_df, quote_batch=qk.quote, side=side,
            main_strike=float(main.get("strike") or 0),
            main_symbol=main.get("tradingsymbol"),
            expiry_iso=str(expiry or expiry_iso)[:10], cap=cap,
            max_strikes=max_strikes, exclude=priced, band=_wing_band,
            step=_STEP)
        if row is None:
            write_audit_log(f"[VET][WING_FAR] {side}: {info.get('reason')}")
            return None
        if not tick.add_live_symbol(row["tradingsymbol"], row.get("token"),
                                    row.get("strike"), row.get("expiry"),
                                    side):
            write_audit_log(f"[VET][WING_FAR] {side}: {row['tradingsymbol']} "
                            f"could not be subscribed — not used (its exits "
                            f"would price off stale memory)")
            return None
        row["_far"]["subscribed"] = True
        return row

    manager.wing_search_fn = far_wing
    # ── VET_WING_FAR_20261101 END ──

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
            _restart_check(fed)                       # ── VET_NEAR_OBS_20261106 ──
        except Exception as e:
            write_audit_log(f"[VET] backfill failed: {e!r} — engine will "
                            f"warm from live ticks (later first signal)")

    tick.start()
    write_audit_log(f"[VET] armed: mode={mode} leg={cfg.get('leg_action')} "
                    f"eod_square={eod_square} universe={n} expiry={expiry_iso} "
                    f"dte={manager.day_dte} ({manager.day_dte_tag}, "
                    f"entry qty {manager.entry_qty}) "   # ── VET_DTE_LIVE_20261005 ──
                    f"wing_search={manager.wing_search_max_strikes if manager.wants_wing else 'off'} "
                    f"band={_wing_band}")   # ── VET_WING_FAR_20261101 ──

    # ── watchdog + session lifetime ──
    silent_alerted = False
    # ── VET_NEAR_OBS_20261106 BEGIN ── the two watchdogs only covered a socket that had
    # connected AND produced a candle; a socket that never comes up (or drops
    # and stays down) was silent. Checked every 30 s in the spot session.
    fw = {"start": time.time(), "down_since": None, "conn_alerted": False, "first_alerted": False,
          "ever_up": False}
    try:
        from app.utils.market_hours import is_nse_holiday
        _holiday = bool(is_nse_holiday(today))
    except Exception:
        _holiday = False

    def _feed_watch(now: float) -> None:
        day0 = ((int(now) + IST) // 86400) * 86400 - IST
        grace_from = max(fw["start"], day0 + (9 * 60 + 16) * 60)
        if bool(getattr(tick, "_ws_connected", False)):
            if fw["conn_alerted"]:
                write_audit_log("[VET][FEED] data connection back")
                _alert("VET_V1: data connection back — candles resume", "info")
            fw["conn_alerted"], fw["down_since"], fw["ever_up"] = False, None, True
        else:
            # never up since start → down since start; a later drop → since first seen
            fw["down_since"] = fw["down_since"] or (now if fw["ever_up"] else fw["start"])
            down_for = now - max(fw["down_since"], grace_from)
            if not fw["conn_alerted"] and down_for >= CONN_GRACE_S:
                fw["conn_alerted"] = True
                msg = (f"VET_V1: data connection is not up ({int(now - max(fw['down_since'], fw['start']))}s) — "
                       f"no candles, no decisions. It retries on its own; restart the app if this persists.")
                write_audit_log(f"[VET][FEED] {msg}")
                _alert(msg, "error")
        if (not fw["first_alerted"] and not tick.last_spot_candle_ts
                and now >= max(fw["start"] + FIRST_CANDLE_GRACE_S, day0 + (9 * 60 + 18) * 60)):
            fw["first_alerted"] = True
            msg = ("VET_V1: no NIFTY spot candle yet this session — Velvet cannot decide. "
                   "Check the data connection; restart the app if this persists.")
            write_audit_log(f"[VET][FEED] {msg}")
            _alert(msg, "error")
    # ── VET_NEAR_OBS_20261106 END ──
    while True:
        await asyncio.sleep(30)
        nowm = _ist_min(int(time.time()))
        if nowm >= 15 * 60 + 29:
            break
        armed = 9 * 60 + 16 <= nowm <= 15 * 60 + 29
        wd = datetime.now().weekday() < 5
        if armed and wd and not _holiday:             # ── VET_NEAR_OBS_20261106 ──
            try:
                _feed_watch(time.time())
            except Exception as _fe:
                write_audit_log(f"[VET][FEED] watch failed: {_fe!r}")
        if armed and wd and tick.last_spot_candle_ts:
            # ── WS_SELFHEAL_20261021 ── measure from the bar END, never
            # from before 09:15 (the 2026-10-21 alert counted 41 min of
            # pre-market as "silence")
            _now_i = int(time.time())
            _open = ((_now_i + IST) // 86400) * 86400 - IST + (9 * 60 + 15) * 60
            gap = _now_i - max(int(tick.last_spot_candle_ts) + 60, _open)
            if gap > 180 and not silent_alerted:
                silent_alerted = True
                write_audit_log(f"[VET] WATCHDOG: no spot candle for "
                                f"{gap}s — zombie WS?")
                if notify:
                    notify(f"VET_V1: no spot candle for {gap}s", "error")
            elif gap <= 90:
                if silent_alerted:   # ── WS_SELFHEAL_20261021 ── close the loop
                    write_audit_log("[VET] WATCHDOG: spot candles back — "
                                    "feed recovered")
                    if notify:
                        try:
                            notify("VET_V1: spot candles back — feed recovered",
                                   "info")
                        except Exception:
                            pass
                silent_alerted = False
    try:
        tick.stop()
    except Exception:
        pass
    write_audit_log("[VET] session over — loop returning to day-cycle")
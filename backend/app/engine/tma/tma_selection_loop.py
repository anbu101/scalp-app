# backend/app/engine/tma/tma_selection_loop.py
#
# ── TMA SELECTION LOOP + MINUTE COORDINATOR ── (pst_selection_loop pattern)
# ============================================================================
# Standalone async loop, launched from api_server (enabled-flag + license
# gate, NOT via StrategyRuntimeManager). One WebSocket, one signal engine,
# one trade manager.
#
# ORDERING (owned by the coordinator, PST-parity-proven ordering):
#   for each completed minute ts:
#     1. manager.on_minute(ts, spot_candle, chain)   — fills + exits first
#     2. signals = signal_engine.on_spot_candle(spot) — then new signals
#     3. manager.on_signal(sig, chain)               — then entries
#
# BOOT: 3-session EMA warmup via Kite historical (tma_live_warmup, fail
# closed), mid-session backfill on restart (stale signals discarded),
# universe from spot LTP + era-aware expected weekly expiry, boot
# reconciliation + adoption (INTRADAY prior-day rows → STALE; POSITIONAL
# carry adopted with broker cross-check for LIVE).
# WATCHDOG: no spot candle for 3+ minutes inside session hours (weekday,
# stream armed) → Telegram system alert (zombie-WS doctrine).
# EOD: manager.force_eod fires from the coordinator at exit_time as a
# belt-and-braces layer under the api_server cron job.
# CRASH-PROOF SHELL (2026-07-16 incident): every death is loud + alerted.
# ============================================================================

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Optional

from app.engine.tma.tma_common import (STRATEGY_ID, TMARepo, hm_to_min,
                                       ist_day_start)
from app.engine.tma.tma_live_signal_engine import TMALiveSignalEngine
from app.engine.tma.tma_live_warmup import (fetch_today_spot,
                                            fetch_warmup_sessions)
from app.engine.tma.tma_tick_engine import TMATickEngine
from app.engine.tma.tma_trade_manager import TMATradeManager
from app.engine.tma.tma_gtt_monitor import TMAGTTMonitor
from app.event_bus.audit_logger import write_audit_log
# ── DAY_CYCLE BEGIN (import) ──
from app.utils.day_cycle import wait_for_arm_window, wait_for_teardown
# ── DAY_CYCLE END (import) ──

IST = 5 * 3600 + 30 * 60

# module-level runtime registry (V3/PST get_manager pattern) — lets the EOD
# cron and the state API reach the SAME manager the loop trades with.
_RUNTIME = {"manager": None, "engine": None, "monitor": None}


def get_manager() -> Optional[TMATradeManager]:
    return _RUNTIME.get("manager")


def get_signal_engine() -> Optional[TMALiveSignalEngine]:
    return _RUNTIME.get("engine")


class TMAMinuteCoordinator:
    def __init__(self, signal_engine: TMALiveSignalEngine,
                 manager: TMATradeManager, exit_min: int, notify=None):
        self.sig_engine = signal_engine
        self.manager = manager
        self.exit_min = exit_min
        self.notify = notify
        self.last_spot_seen = 0.0
        self._spot_seen_count = 0     # watchdog arms only after a real stream
        self._eod_done_day: Optional[int] = None
        self._warned_quiet = False

    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        # chain reference for hedge exit pricing (before any close can run)
        self.manager.note_chain(chain)
        # 1) fills + exits first (xover from the SAME indicator stream)
        self.manager.on_minute(ts, spot_candle, chain,
                               xover_fn=self.sig_engine.xover_ts_for)
        # 2) signals from the completed spot candle
        if spot_candle is not None:
            self.last_spot_seen = time.time()
            self._spot_seen_count += 1
            self._warned_quiet = False
            for sig in self.sig_engine.on_spot_candle(spot_candle):
                # 3) entries
                self.manager.on_signal(sig, chain)
        # EOD belt-and-braces (manager.force_eod is trade_mode-aware:
        # positional non-expiry carry is a deliberate no-op)
        day = ist_day_start(ts)
        if ts >= day + self.exit_min * 60 and self._eod_done_day != day:
            self.manager.force_eod(ts)
            self._eod_done_day = day
        # zombie-WS watchdog (weekdays, armed stream only — PST guards)
        #
        # ── CAS_NOTE (2026-08-03) — DO NOT CHANGE 15:30 TO 15:40 ──────────
        # NFO now closes 15:40, but this watchdog alerts on "no SPOT candle for
        # 3+ minutes". NIFTY constituents stop continuous trading at 15:15 and
        # after CAS matching (~15:35) the index is expected to stop updating
        # while options still trade. Extending the bound below to (15*60+40)
        # would fire a false "possible zombie WebSocket" Telegram EVERY trading
        # day at ~15:38. Spot-derived staleness stays on the spot clock; only
        # option-LTP-driven logic moves to 15:40. See app/utils/market_hours.py
        # (is_spot_continuous_session vs is_market_open).
        # ── CAS_NOTE END ─────────────────────────────────────────────────
        mins = (ts - day) // 60
        _is_weekday = datetime.utcfromtimestamp(ts + IST).weekday() < 5
        in_session = _is_weekday and (9 * 60 + 15) <= mins <= (15 * 60 + 30)
        if in_session and self._spot_seen_count >= 5 and self.last_spot_seen \
                and time.time() - self.last_spot_seen > 180 \
                and not self._warned_quiet:
            self._warned_quiet = True
            write_audit_log("[TMA][WATCHDOG] no spot candle for 3+ minutes "
                            "during session — possible zombie WebSocket")
            if self.notify:
                try:
                    self.notify("TMA: no spot candle for 3+ minutes — "
                                "possible zombie WebSocket, check the app")
                except Exception:
                    pass


async def tma_selection_loop(zerodha_manager):
    """── DAY_CYCLE BEGIN (wrapper) ──
    Perpetual cycle: arm (next trading morning) → run ONE day → teardown →
    wait. Replaces the one-shot run (2026-08-13 incident: a hibernate-
    surviving backend left the old loop in yesterday's "market over" state
    for a full session). Crash-proof: every death is loud + Telegram-
    alerted and costs only the CURRENT day — the cycle re-arms next
    session; same-day recovery remains a manual app restart."""
    last_run_day = None
    while True:
        armed_day = await wait_for_arm_window("TMA", last_run_day)
        try:
            await _tma_selection_loop_inner(zerodha_manager)
        except Exception as e:
            import traceback
            write_audit_log(f"[TMA][CRITICAL] selection loop DIED: {e!r}\n"
                            f"{traceback.format_exc()}")
            try:
                from app.api.telegram_api import notify_system_alert
                notify_system_alert({"message": f"🚨 TMA loop DIED: {e!r} — "
                                                f"no TMA trading until "
                                                f"tomorrow's re-arm or an "
                                                f"app restart",
                                     "severity": "error"})
            except Exception:
                pass
        last_run_day = armed_day
    # ── DAY_CYCLE END (wrapper) ──


async def _tma_selection_loop_inner(zerodha_manager):
    from app.config.strategy_loader import load_strategy_config
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    notify = None
    try:
        from app.api.telegram_api import notify_system_alert as _raw_alert

        def notify(msg, severity="warning"):
            _raw_alert({"message": str(msg), "severity": severity})
    except Exception:
        pass

    try:
        from app.utils.app_paths import APP_HOME
        capture_dir = str(APP_HOME / "tma_capture")
    except Exception:
        import os
        capture_dir = os.path.expanduser("~/.scalp-app/tma_capture")

    write_audit_log("[TMA] selection loop starting")

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
                write_audit_log(f"[TMA] waiting for Zerodha session "
                                f"({_waited // 60} min)")
            await asyncio.sleep(5)
            _waited += 5

    # ── BOOT RETRY until 15:00 IST (instruments/warmup can fail transiently) ──
    instruments_df = None
    sig_engine = TMALiveSignalEngine()
    _boot_attempts = 0
    while True:
        _ist_min = (int(time.time()) + IST) % 86400 // 60
        if _ist_min >= 15 * 60:
            if _boot_attempts == 0:
                # DAY_CYCLE: normally unreachable (the arm gate blocks
                # post-cutoff entry); kept as belt-and-braces.
                write_audit_log("[TMA] launched after the boot cutoff (15:00 "
                                "IST) — market over; idle until next session "
                                "re-arm (DAY_CYCLE)")
            else:
                write_audit_log(f"[TMA] boot never succeeded before 15:00 IST "
                                f"({_boot_attempts} attempts) — giving up for "
                                f"today (fail closed)")
                if notify:
                    try:
                        notify(f"TMA: boot kept failing ({_boot_attempts} "
                               f"attempts, instruments/warmup) — not trading "
                               f"today", severity="error")
                    except Exception:
                        pass
            return
        _boot_attempts += 1
        try:
            from app.fetcher.zerodha_instruments import load_instruments_df
            instruments_df = load_instruments_df()
            warm = fetch_warmup_sessions(kite, instruments_df=instruments_df)
            if warm is None or not sig_engine.seed_warmup(warm):
                raise RuntimeError("warmup unavailable")
            break
        except Exception as e:
            write_audit_log(f"[TMA][BOOT] attempt failed ({e!r}) — retrying in 60s")
            await asyncio.sleep(60)

    cfg = load_strategy_config(STRATEGY_ID) or {}
    sess_start_min = hm_to_min(cfg.get("session_start", "09:15"), 9 * 60 + 15)
    sess_end_min = hm_to_min(cfg.get("session_end", "15:00"), 15 * 60)
    exit_min = hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
    # fail LOUD on a nonsense session window (the "V3 no entries" lesson)
    if not (sess_start_min < sess_end_min <= exit_min):
        write_audit_log(f"[TMA][ABORT] session window invalid: "
                        f"{cfg.get('session_start')} < {cfg.get('session_end')} "
                        f"<= {cfg.get('exit_time')} must hold — not trading")
        if notify:
            try:
                notify("TMA: invalid session window in Settings — not trading "
                       "(start < end <= exit_time must hold)", severity="error")
            except Exception:
                pass
        return

    today = datetime.now().date()
    day_start = int((datetime(today.year, today.month, today.day)
                     - datetime(1970, 1, 1)).total_seconds()) - IST
    sig_engine.start_day(day_start, sess_start_min=sess_start_min,
                         sess_end_min=sess_end_min)

    # ── MIDSESSION_BACKFILL ── restart during session: rebuild today's spot
    # prefix; emitted signals are stale-flagged and skipped by the manager.
    _now_ist_min = (int(time.time()) + IST) % 86400 // 60
    if _now_ist_min > (9 * 60 + 16):
        _bf = fetch_today_spot(kite, instruments_df=instruments_df)
        _fed = 0
        for _c in _bf:
            sig_engine.on_spot_candle(_c)      # returns stale signals — dropped
            _fed += 1
        if _fed:
            write_audit_log(f"[TMA][BACKFILL] mid-session start: {_fed} spot "
                            f"candles restored (last ts {_bf[-1]['ts']})")
        else:
            write_audit_log("[TMA][BACKFILL] mid-session start but no candles "
                            "returned — indicators run on a GAPPED prefix "
                            "today; treat signals with suspicion")

    # ── executor pre-built even for PAPER (house rule: a PAPER→LIVE flip
    # mid-session never needs a restart); build failure is loud, PAPER
    # unaffected, LIVE entries fail closed at the executor gate.
    executor = None
    try:
        from app.execution.zerodha_executor import ZerodhaOrderExecutor
        executor = ZerodhaOrderExecutor(zerodha_manager)
        write_audit_log("[TMA] executor pre-built")
    except Exception as e:
        write_audit_log(f"[TMA] executor build failed ({e!r}) — PAPER "
                        f"unaffected; LIVE entries will be skipped (fail closed)")

    repo = TMARepo()
    manager = TMATradeManager(cfg, repo, executor=executor, notify=notify)

    # ── BOOT RECONCILIATION + ADOPTION ──
    trade_mode = str(cfg.get("trade_mode", "INTRADAY")).upper()
    rows = repo.open_legs()
    prior = [r for r in rows if int(r["entry_ts"]) < day_start]
    todays = [r for r in rows if int(r["entry_ts"]) >= day_start]
    if trade_mode == "INTRADAY" and prior:
        for r in prior:
            repo.mark_stale(r["id"])
        write_audit_log(f"[TMA] {len(prior)} OPEN leg(s) from a previous "
                        f"session marked STALE (INTRADAY) — review manually")
        if notify:
            try:
                notify(f"TMA: {len(prior)} stale open leg(s) from a previous "
                       f"session — marked STALE, review")
            except Exception:
                pass
        adoptable = todays
    else:
        adoptable = prior + todays          # positional carry is legitimate
    if adoptable:
        manager.adopt_rows(adoptable, kite=kite)
    if manager.disabled:
        write_audit_log("[TMA] manager disabled by reconciliation — loop exiting")
        return

    _RUNTIME["manager"] = manager
    _RUNTIME["engine"] = sig_engine
    coord = TMAMinuteCoordinator(sig_engine, manager, exit_min, notify=notify)

    # ── universe + websocket ──
    engine = TMATickEngine(zerodha_manager, instruments_df,
                           coord.on_minute, capture_dir=capture_dir)
    try:
        spot_ltp = float(kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"])
    except Exception as e:
        write_audit_log(f"[TMA] spot LTP fetch failed ({e}) — not trading (fail closed)")
        return
    expiry_iso = expected_expiry_for_day(today).isoformat()
    n = engine.resolve_universe(spot_ltp, expiry_iso)
    if n == 0:
        write_audit_log("[TMA] empty option universe — not trading (fail closed)")
        if notify:
            try:
                notify("TMA: empty weekly option universe at boot — verify "
                       "instruments.csv freshness (load_instruments_df only "
                       "refreshes when the file is MISSING)", severity="error")
            except Exception:
                pass
        return
    engine.start()

    # GTT backstop (LIVE protection layer)
    if executor is not None:
        mon = TMAGTTMonitor(executor, manager)
        mon.start()
        _RUNTIME["monitor"] = mon

    write_audit_log(f"[TMA] LIVE loop up: {n} contracts, expiry {expiry_iso}, "
                    f"warmup {sig_engine.diag['warmup_sessions']} session(s), "
                    f"mode={cfg.get('trade_execution_mode')}, "
                    f"trade_mode={trade_mode}")

    # ── DAY_CYCLE BEGIN (teardown) ── day-run keep-alive: the tick engine
    # drives everything on its timer; past 15:45 IST (after the 15:25 EOD
    # cron and the 15:40 NFO close) stop the stream + GTT monitor, clear
    # the runtime registry and return — the wrapper re-arms next session.
    # Overnight zombie WebSockets die here by construction.
    await wait_for_teardown()
    try:
        engine.stop()
    except Exception as e:
        write_audit_log(f"[TMA][DAY_CYCLE] engine.stop failed: {e!r}")
    _mon = _RUNTIME.get("monitor")
    if _mon is not None:
        try:
            _mon.stop()
        except Exception as e:
            write_audit_log(f"[TMA][DAY_CYCLE] monitor.stop failed: {e!r}")
    _RUNTIME["manager"] = None
    _RUNTIME["engine"] = None
    _RUNTIME["monitor"] = None
    write_audit_log("[TMA][DAY_CYCLE] day complete — torn down; will re-arm "
                    "next session")
    # ── DAY_CYCLE END (teardown) ──
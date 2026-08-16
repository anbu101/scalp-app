# backend/app/engine/pst/pst_selection_loop.py
#
# ── PST SELECTION LOOP + MINUTE COORDINATOR ── (Phase 1, Delivery 3)
#
# Standalone async loop, launched from api_server EXACTLY like SCALP_V3
# (enabled-flag + license gate, NOT via StrategyRuntimeManager). One loop
# serves BOTH PST_SELL and PST_HEDGE — one WebSocket, one signal engine,
# two managers (D23).
#
# ORDERING (owned by the coordinator, parity-proven by the harness):
#   for each completed minute ts:
#     1. managers.on_minute(ts, spot_candle, chain)   — exits first
#     2. signals = signal_engine.on_spot_candle(spot) — then new signals
#     3. managers.on_signal(sig, chain)               — then entries
#
# BOOT: warmup via Kite historical (pst_live_warmup, fail closed), universe
# resolved from spot LTP + expected weekly expiry, engine day started.
# WATCHDOG: no spot candle for 3 minutes inside session hours → Telegram
# system alert (zombie-WS doctrine: connected ≠ ticking).
# EOD: managers.force_eod fires from the coordinator at exit_time as a
# belt-and-braces layer under the api_server cron job.

from __future__ import annotations

import asyncio
import time  # (backfill uses time.time for IST minutes)
from datetime import datetime, date
from typing import List, Optional

try:
    from app.engine.pst.pst_common import PSTRepo, hm_to_min, ist_day_start
    from app.engine.pst.pst_live_signal_engine import PSTLiveSignalEngine
    from app.engine.pst.pst_live_warmup import fetch_prev_session_spot, fetch_today_spot
    from app.engine.pst.pst_sell_paper_manager import PSTSellPaperManager
    from app.engine.pst.pst_hedge_paper_manager import PSTHedgePaperManager
    from app.engine.pst.pst_tick_engine import PSTTickEngine
except ImportError:  # standalone tests
    from pst_common import PSTRepo, hm_to_min, ist_day_start
    from pst_live_signal_engine import PSTLiveSignalEngine
    from pst_live_warmup import fetch_prev_session_spot, fetch_today_spot
    from pst_sell_paper_manager import PSTSellPaperManager
    from pst_hedge_paper_manager import PSTHedgePaperManager
    from pst_tick_engine import PSTTickEngine
try:
    from app.engine.pst.pst_order_executor import PaperExecutor, LiveExecutor
except ImportError:
    from pst_order_executor import PaperExecutor, LiveExecutor

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

# ── DAY_CYCLE BEGIN (import) ──
try:
    from app.utils.day_cycle import wait_for_arm_window, wait_for_teardown
except ImportError:  # standalone tests
    from day_cycle import wait_for_arm_window, wait_for_teardown
# ── DAY_CYCLE END (import) ──

IST = 5 * 3600 + 30 * 60

# module-level runtime registry (V3's get_manager pattern) — lets the EOD
# cron square off through the SAME managers/executors the loop trades with.
_RUNTIME = {"managers": []}


def get_managers():
    return list(_RUNTIME.get("managers") or [])


class PSTMinuteCoordinator:
    def __init__(self, signal_engine: PSTLiveSignalEngine,
                 managers: List, exit_min: int, notify=None):
        self.sig_engine = signal_engine
        self.managers = managers
        self.exit_min = exit_min
        self.notify = notify
        self.last_spot_seen = 0.0
        self._spot_seen_count = 0     # watchdog arms only after a real stream
        self._eod_done_day: Optional[int] = None
        self._warned_quiet = False

    # ── PST_EARLY_EXIT BEGIN ──
    def on_pre_boundary(self, bar_ts: int, spot_peek: Optional[dict],
                        chain) -> None:
        """T-1s: LIVE positions only. Managers self-gate on pos_mode, so a
        PAPER position is never touched by this path and paper<->backtest
        parity is preserved by construction."""
        for m in self.managers:
            fn = getattr(m, "on_pre_boundary", None)
            if fn is None:
                continue
            try:
                fn(bar_ts, spot_peek, chain)
            except Exception as e:
                write_audit_log(f"[PST][EARLY] manager pre-boundary failed: {e}")
    # ── PST_EARLY_EXIT END ──

    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        # 1) exits / monitoring first
        for m in self.managers:
            m.on_minute(ts, spot_candle, chain)
        # 2) signals from the completed spot candle
        if spot_candle is not None:
            self.last_spot_seen = time.time()
            self._spot_seen_count += 1
            self._warned_quiet = False
            for sig in self.sig_engine.on_spot_candle(spot_candle):
                # 3) entries
                for m in self.managers:
                    m.on_signal(sig, chain)
        # EOD belt-and-braces
        day = ist_day_start(ts)
        if ts >= day + self.exit_min * 60 and self._eod_done_day != day:
            for m in self.managers:
                m.force_eod(ts)
            self._eod_done_day = day
        # zombie-WS watchdog (session hours only)
        mins = (ts - day) // 60
        # 2026-07-18 (Saturday) false positive: Kite's WS sends ONE snapshot
        # tick on connect — a single candle armed the watchdog, then natural
        # weekend silence tripped it at 09:16. Two guards: weekdays only,
        # AND a real candle STREAM (≥5) before arming — the second also
        # covers mid-week exchange holidays, which no weekday check can.
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
        _is_weekday = datetime.utcfromtimestamp(ts + IST).weekday() < 5
        in_session = _is_weekday and (9 * 60 + 15) <= mins <= (15 * 60 + 30)
        if in_session and self._spot_seen_count >= 5 and self.last_spot_seen and \
                time.time() - self.last_spot_seen > 180 and not self._warned_quiet:
            self._warned_quiet = True
            write_audit_log("[PST][WATCHDOG] no spot candle for 3+ minutes "
                            "during session — possible zombie WebSocket")
            if self.notify:
                try:
                    self.notify("PST: no spot candle for 3+ minutes — "
                                "possible zombie WebSocket, check the app")
                except Exception:
                    pass


async def pst_selection_loop(zerodha_manager):
    """── DAY_CYCLE BEGIN (wrapper) ──
    Perpetual cycle: arm (next trading morning) → run ONE day → teardown →
    wait. Replaces the one-shot run (2026-08-13 incident: a hibernate-
    surviving backend left the old loop in yesterday's "market over" state
    for a full session). Crash-proof shell retained (2026-07-16 incident:
    silent asyncio-task death) — every death is loud + Telegram-alerted
    and costs only the CURRENT day; the cycle re-arms next session, and
    same-day recovery remains a manual app restart."""
    last_run_day = None
    while True:
        armed_day = await wait_for_arm_window("PST", last_run_day)
        try:
            await _pst_selection_loop_inner(zerodha_manager)
        except Exception as e:
            import traceback
            write_audit_log(f"[PST][CRITICAL] selection loop DIED: {e!r}\n"
                            f"{traceback.format_exc()}")
            try:
                from app.api.telegram_api import notify_system_alert
                notify_system_alert({"message": f"🚨 PST loop DIED: {e!r} — "
                                                f"no PST trading until "
                                                f"tomorrow's re-arm or an "
                                                f"app restart",
                                     "severity": "error"})
            except Exception:
                pass
        last_run_day = armed_day
    # ── DAY_CYCLE END (wrapper) ──


async def _pst_selection_loop_inner(zerodha_manager):
    """Launched from api_server when PST_SELL or PST_HEDGE is enabled."""
    from app.config.strategy_loader import load_strategy_config
    from app.strategy.strategy_registry import STRATEGIES
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day
    try:
        from app.engine.pst.pst_common import canonical_db_path
    except ImportError:
        from pst_common import canonical_db_path
    db_path = canonical_db_path()   # data/app.db — get_conn()'s file. NEVER hand-build
    try:
        from app.utils.app_paths import APP_HOME
        capture_dir = str(APP_HOME / "pst_capture")
    except Exception:
        import os
        capture_dir = os.path.expanduser("~/.scalp-app/pst_capture")
    notify = None
    try:
        from app.api.telegram_api import notify_system_alert as _raw_alert
        # notify_system_alert takes a DICT — all PST call sites pass strings,
        # so wrap once here (string → {"message", "severity"}).
        def notify(msg, severity="warning"):
            _raw_alert({"message": str(msg), "severity": severity})
    except Exception:
        pass

    write_audit_log("[PST] selection loop starting (paper phase)")

    # ── wait for the Zerodha session INDEFINITELY (2026-07-16 incident:
    # the old 20-minute give-up exited PERMANENTLY at 05:56 while the user's
    # login lands ~08:45+; app restarts were the only accidental rescue).
    kite = None
    _waited = 0
    while kite is None:
        try:
            kite = zerodha_manager.get_kite()
        except Exception:
            kite = None
        if kite is None:
            if _waited and _waited % 300 == 0:
                write_audit_log(f"[PST] waiting for Zerodha session "
                                f"({_waited // 60} min)")
            await asyncio.sleep(5)
            _waited += 5

    # ── BOOT RETRY (2026-07-16): instruments/warmup can fail transiently
    # (dump not downloaded yet, network). Retry every 60s until 15:00 IST
    # instead of exiting permanently; every attempt is logged.
    instruments_df = None
    warm = None
    sig_engine = PSTLiveSignalEngine()
    _boot_attempts = 0
    while True:
        _ist_min = (int(time.time()) + IST) % 86400 // 60
        if _ist_min >= 15 * 60:
            if _boot_attempts == 0:
                # 2026-07-16: evening launches (post-market rebuild tests)
                # tripped the failed-all-day alert on the FIRST check —
                # nothing failed; the market was simply over. Quiet line,
                # no Telegram.
                # DAY_CYCLE: normally unreachable (the arm gate blocks
                # post-cutoff entry); kept as belt-and-braces.
                write_audit_log("[PST] launched after the boot cutoff (15:00 "
                                "IST) — market over; idle until next session "
                                "re-arm (DAY_CYCLE)")
            else:
                write_audit_log(f"[PST] boot never succeeded before 15:00 IST "
                                f"({_boot_attempts} attempts) — giving up for "
                                f"today (fail closed)")
                if notify:
                    try:
                        notify(f"PST: boot kept failing ({_boot_attempts} "
                               f"attempts, instruments/warmup) — not trading "
                               f"today", severity="error")
                    except Exception:
                        pass
            return
        _boot_attempts += 1
        try:
            from app.fetcher.zerodha_instruments import load_instruments_df
            instruments_df = load_instruments_df()
            warm = fetch_prev_session_spot(kite, instruments_df=instruments_df)
            if warm is None or not sig_engine.seed_warmup(
                    warm["spot_1m"], warm["day_start"], warm["prev_hlc"]):
                raise RuntimeError("warmup unavailable")
            break
        except Exception as e:
            write_audit_log(f"[PST][BOOT] attempt failed ({e!r}) — retrying in 60s")
            await asyncio.sleep(60)
    today = datetime.now().date()
    sig_engine.start_day(int((datetime(today.year, today.month, today.day)
                              - datetime(1970, 1, 1)).total_seconds()) - IST)

    # ── MIDSESSION_BACKFILL BEGIN ── restart during session hours: rebuild
    # the day's spot prefix from Kite historical so replay indicators
    # (SMA9@5m, ST 10×2@3m) match a continuous run. Signals emitted during
    # backfill are DISCARDED — they are minutes old; positions that were
    # never opened stay unopened (conservative by design). Managers only
    # see signals from live candles onward.
    _now_ist_min = (int(time.time()) + IST) % 86400 // 60
    if _now_ist_min > (9 * 60 + 16):
        _bf = fetch_today_spot(kite, instruments_df=instruments_df)
        _fed = 0
        for _c in _bf:
            sig_engine.on_spot_candle(_c)      # returns old signals — dropped
            _fed += 1
        if _fed:
            write_audit_log(f"[PST][BACKFILL] mid-session start: {_fed} spot "
                            f"candles restored (last ts {_bf[-1]['ts']}); "
                            f"seam to live feed ≤1–2 min (historical lag)")
        else:
            write_audit_log("[PST][BACKFILL] mid-session start but no candles "
                            "returned — signal indicators run on a GAPPED "
                            "prefix today; treat signals with suspicion")
    # ── MIDSESSION_BACKFILL END ──

    # ── managers (paper-hardwired; each disabled unless its flag is on) ──
    repo = PSTRepo(db_path)

    # DYNAMIC MODE: every manager gets BOTH executors; the fresh entry-time
    # config read picks per position (no restart after a Settings flip).
    _live_exec = LiveExecutor(zerodha_manager, notify=notify)   # relay-routed (2026-07-15)

    managers = []
    tables = {}
    exit_min = 15 * 60 + 25
    # ── LICENSE_GATE_FIX ── per-sid entitlement check, evaluated HERE (not
    # just at api_server launch) so a mixed entitlement (only one of the
    # pair licensed) builds only the licensed manager.
    from app.license import license_state as _lic
    if (STRATEGIES.get("PST_SELL", {}).get("enabled", False)
            and _lic.license_allows_strategy("PST_SELL")):
        cfg = load_strategy_config("PST_SELL")
        m = PSTSellPaperManager(cfg, repo, live_executor=_live_exec)
        managers.append(m); tables[id(m)] = "pst_sell_trades"
        exit_min = hm_to_min(cfg.get("exit_time", "15:25"), exit_min)
    if (STRATEGIES.get("PST_HEDGE", {}).get("enabled", False)
            and _lic.license_allows_strategy("PST_HEDGE")):
        cfg = load_strategy_config("PST_HEDGE")
        m = PSTHedgePaperManager(cfg, repo, live_executor=_live_exec)
        managers.append(m); tables[id(m)] = "pst_hedge_trades"
        exit_min = max(exit_min, hm_to_min(cfg.get("exit_time", "15:25"), exit_min))
    managers = [m for m in managers if not getattr(m, "disabled", False)]

    # ── BOOT RECONCILIATION + ADOPTION (fail closed) ──
    today_start = int((datetime(today.year, today.month, today.day)
                       - datetime(1970, 1, 1)).total_seconds()) - IST
    for m in managers:
        table = tables[id(m)]
        rows = repo.open_legs(table)
        stale = [r for r in rows if int(r["entry_ts"]) < today_start]
        for r in stale:
            repo.mark_stale(table, r["id"])
        if stale:
            write_audit_log(f"[PST] {table}: {len(stale)} OPEN leg(s) from a "
                            f"previous session marked STALE — review manually")
            if notify:
                try:
                    notify(f"PST: {len(stale)} stale open leg(s) in {table} from a "
                           f"previous session — marked STALE, review")
                except Exception:
                    pass
        todays = [r for r in rows if int(r["entry_ts"]) >= today_start]
        if not todays:
            continue
        if str(todays[0].get("mode", "PAPER")).upper() == "LIVE":
            # broker cross-check: net position must match the rows exactly
            try:
                sym = todays[0]["tradingsymbol"]
                want = sum(int(r["qty"]) for r in todays)
                if isinstance(m, PSTSellPaperManager):
                    want = -want
                net = 0
                for p in kite.positions().get("net", []):
                    if p.get("tradingsymbol") == sym:
                        net = int(p.get("quantity") or 0)
                if net != want:
                    m.disabled = True
                    write_audit_log(f"[PST][RECON] {table}: broker net {net} != "
                                    f"expected {want} for {sym} — manager DISABLED "
                                    f"(fail closed), resolve manually")
                    if notify:
                        try:
                            notify(f"PST RECON MISMATCH {sym}: broker {net} vs "
                                   f"app {want} — {table} disabled, resolve manually")
                        except Exception:
                            pass
                    continue
                write_audit_log(f"[PST][RECON] {table}: broker matches app "
                                f"({net} {sym}) — adopting; TP falls back to "
                                f"app-monitored (resting order ids not persisted)")
            except Exception as e:
                m.disabled = True
                write_audit_log(f"[PST][RECON] {table}: positions check failed "
                                f"({e}) — manager DISABLED (fail closed)")
                continue
        m.adopt_rows(todays)
    managers = [m for m in managers if not getattr(m, "disabled", False)]
    if not managers:
        write_audit_log("[PST] no enabled paper managers — loop exiting")
        return

    _RUNTIME["managers"] = managers
    coord = PSTMinuteCoordinator(sig_engine, managers, exit_min, notify=notify)

    # ── universe + websocket ──
    # ── PST_EARLY_EXIT BEGIN ── opt-in; absent/false → callback is None and
    # the pre-boundary thread never starts (behaviour identical to today).
    _early = False
    try:
        _ecfg = load_strategy_config("PST_HEDGE") or {}
        _early = bool(_ecfg.get("early_exit_enabled", False))
    except Exception:
        _early = False
    write_audit_log(f"[PST] early-exit (T-1s, LIVE only): "
                    f"{'ENABLED' if _early else 'disabled'}")
    engine = PSTTickEngine(zerodha_manager, instruments_df,
                           coord.on_minute, capture_dir=capture_dir,
                           on_pre_boundary_cb=(coord.on_pre_boundary
                                               if _early else None))
    # ── PST_EARLY_EXIT END ──
    try:
        spot_ltp = float(kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"])
    except Exception as e:
        write_audit_log(f"[PST] spot LTP fetch failed ({e}) — not trading (fail closed)")
        return
    expiry_iso = expected_expiry_for_day(today).isoformat()
    n = engine.resolve_universe(spot_ltp, expiry_iso)
    if n == 0:
        write_audit_log("[PST] empty option universe — not trading (fail closed)")
        return
    engine.start()
    write_audit_log(f"[PST] LIVE (paper): {len(managers)} manager(s), "
                    f"{n} contracts, expiry {expiry_iso}")

    # ── DAY_CYCLE BEGIN (teardown) ── day-run keep-alive: the tick engine
    # drives everything on its timer; past 15:45 IST (after the 15:28 EOD
    # cron and the 15:40 NFO close) stop the stream, clear the runtime
    # registry and return — the wrapper re-arms next session. Overnight
    # zombie WebSockets die here by construction. NOTE: pst_live_eod_job's
    # manager path needs a populated registry — its 15:28 cron slot runs
    # BEFORE this teardown by design; its STALE-marking fallback still
    # covers rows if the job ever runs later.
    await wait_for_teardown()
    try:
        engine.stop()
    except Exception as e:
        write_audit_log(f"[PST][DAY_CYCLE] engine.stop failed: {e!r}")
    _RUNTIME["managers"] = []
    write_audit_log("[PST][DAY_CYCLE] day complete — torn down; will re-arm "
                    "next session")
    # ── DAY_CYCLE END (teardown) ──


async def pst_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[EOD][PST] non-trading day — no-op")
        return
    """api_server cron (15:28): REAL square-off, V3's get_manager pattern.
    Primary path: force_eod through the live manager objects (works even
    with a zombie WebSocket — paper closes at last known close; LIVE
    routes through the executor: cancel resting TP, market exit, actual
    fills booked). Managers unreachable (loop never started / crashed)
    with rows still OPEN → paper rows are marked STALE (no invented
    prices), LIVE rows raise a CRITICAL alert for manual square-off —
    the one scenario this job cannot safely automate is placing orders
    without the loop's confirmed broker session."""
    import time as _time
    closed_via_managers = 0
    for m in get_managers():
        try:
            if m.open_legs or m.pending:
                m.force_eod(int(_time.time()))
                closed_via_managers += 1
        except Exception as e:
            write_audit_log(f"[PST][EOD] manager force_eod failed: {e}")
    if closed_via_managers:
        write_audit_log(f"[PST][EOD] square-off via {closed_via_managers} manager(s)")
    try:
        from app.engine.pst.pst_common import canonical_db_path
    except ImportError:
        from pst_common import canonical_db_path
    repo = PSTRepo(canonical_db_path())
    critical = []
    for table in ("pst_sell_trades", "pst_hedge_trades"):
        for r in (repo.open_legs(table) or []):
            if str(r.get("mode", "PAPER")).upper() == "PAPER":
                repo.mark_stale(table, r["id"])   # honest: no invented price
                write_audit_log(f"[PST][EOD] paper leg {table}#{r['id']} "
                                f"({r['tradingsymbol']}) marked STALE — loop was down")
            else:
                critical.append((table, r))
    if critical:
        msg = "; ".join(f"{t}#{r['id']} {r['tradingsymbol']} qty {r['qty']}"
                        for t, r in critical)
        write_audit_log(f"[PST][EOD][CRITICAL] LIVE legs still OPEN and the "
                        f"loop is unreachable — SQUARE OFF MANUALLY: {msg}")
        try:
            from app.api.telegram_api import notify_system_alert
            notify_system_alert({"message": f"🚨 PST LIVE legs OPEN after EOD, "
                                            f"loop dead — square off manually NOW: {msg}",
                                 "severity": "error"})
        except Exception:
            pass
    else:
        write_audit_log("[PST][EOD] clean — no open PST legs")
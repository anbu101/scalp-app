from fastapi import FastAPI
import asyncio
import threading
import os

from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from app.strategy.strategy_runtime import StrategyRuntimeManager
from app.execution.executor_factory import get_executor_for_broker
from app.jobs.bb_live_eod_v2 import bb_live_eod_v2_job

# Eagerly import the backtest entry points so PyInstaller's static analysis
# traces their (otherwise lazy) submodule tree into the bundle — same as the
# live trading stack. Fixes Windows "ModuleNotFoundError: app.backtest.*".
# Import-only; the routes/runners are still invoked lazily as before.
import app.backtest.scalpv5.backtest_scalpv5_runner  # noqa: F401
import app.backtest.runner.backtest_runner            # noqa: F401
import app.backtest.runner.backtest_hedge_runner      # noqa: F401
import app.backtest.bb.backtest_bb_runner             # noqa: F401
import app.backtest.repo.backtest_repo                # noqa: F401
import app.backtest.repo.backtest_queue_repo          # noqa: F401
import app.backtest.queue_worker                      # noqa: F401
import app.backtest.dhan.dhan_backfill                # noqa: F401
import app.backtest.dhan.fut_backfill                 # noqa: F401
import app.backtest.dhan.bnf_options_backfill         # noqa: F401
import app.backtest.backfill.kite_backfill            # noqa: F401
import app.backtest.data.candle_source              # noqa: F401
# --------------------------------------------------
# RUNTIME ENV
# --------------------------------------------------

SCALP_ENV = os.environ.get("SCALP_ENV", "dev")
SCALP_PORT = int(os.environ.get("SCALP_PORT", "8000"))

# --------------------------------------------------
# LICENSE
# --------------------------------------------------
# PHASE 2: TEMP DEV BYPASS removed. license_client is the only writer of
# license_state; api_server only reads it (strategy gating below).

from app.license import license_state
from app.license import license_client
from app.event_bus.audit_logger import write_audit_log

from fastapi import Depends, HTTPException as _HTTPException

def _require_admin_ui():
    if license_state.ui_level() != "admin":
        raise _HTTPException(status_code=403, detail="admin license required")

# --------------------------------------------------
# PATHS
# --------------------------------------------------

from app.utils.app_paths import ensure_app_dirs, export_env, STATE_DIR

# --------------------------------------------------
# ROUTERS
# --------------------------------------------------

from app.api.health_routes import router as health_router
from app.api.selection_routes import router as selection_router
from app.api.strategy_routes import router as strategy_router
from app.api.zerodha_routes import router as zerodha_router
from app.api.acc2_routes import router as acc2_router  # ACC2
from app.api.status_routes import router as status_router
from app.api.trade_history_routes import router as trade_history_router
from app.api.positions_routes import router as positions_router
from app.api.trade_state_routes import router as trade_state_router
from app.api.signal_routes import router as signal_router
from app.api.log_routes import router as log_router
from app.routes.config_routes import router as config_router
from app.api.debug_routes import router as debug_router
from app.api.debug_ui_routes import router as debug_ui_router
from app.api.ltp_routes import router as ltp_router
from app.api.market_indices_routes import router as market_indices_router
from app.api.paper_trades_routes import router as paper_trades_router
from app.api.system_routes import router as system_router
from app.api.telegram_api import router as telegram_router
from app.indicators.pivot_cache import PivotCache
from app.api.relay_routes import router as relay_router
from app.api.scalp_v3_state_routes import router as scalp_v3_state_router
from app.api.scalpv5_state_routes import router as scalpv5_state_router
from app.api.ic_state_routes import router as ic_state_router   # ← IC_SPLIT (shared V1/V2)
from app.api.tsg_v1_state_routes import router as tsg_v1_state_router  # ← NEW (TSG_V1)
from app.api.gc_v1_state_routes import router as gc_v1_state_router  # ← NEW (GC_V1)
from app.api.tma_state_routes import router as tma_state_router       # ← NEW (TMA_V1)
from app.api.backtest_routes import router as backtest_router


# 🔔 TELEGRAM ALERT
from app.api.telegram_api import notify_system_alert

# 🔔 TELEGRAM SCHEDULER
from app.services.telegram_scheduler import TelegramScheduler

# with the other router imports (near line 59):
from app.api.app_settings_api import router as app_settings_router
from app.api.pst_state_api import router as pst_state_router

# --------------------------------------------------
# JOBS
# --------------------------------------------------

from app.jobs.paper_trade_eod import paper_trade_eod_job
from app.jobs.bb_live_eod import bb_live_eod_job
from app.jobs.ha_live_eod import ha_live_eod_job          # ← NEW
from app.jobs.scalp_v3_live_eod import scalp_v3_live_eod_job   # ← NEW (SCALP_V3)
from app.jobs.scalpv5_live_eod import scalpv5_live_eod_job     # ← NEW (SCALP_V5)
from app.jobs.ic_live_eod import ic_live_eod_job, ic_morning_job  # ← IC_SPLIT (shared V1/V2)
from app.jobs.tsg_live_eod import tsg_live_eod_job  # ← NEW (TSG_V1)
from app.jobs.gc_live_eod import gc_live_eod_job  # ← NEW (GC_V1)
from app.jobs.tma_live_eod import tma_live_eod_job             # ← NEW (TMA_V1)
from app.api.futures_candles_routes import router as futures_candles_router
from contextlib import contextmanager
import traceback

# --------------------------------------------------
# MARKET DATA
# --------------------------------------------------

from app.marketdata.load_index_prev_close import (
    load_index_prev_close_once,
    seed_index_ltp_once,
    index_prev_close_watchdog,   # ← INDEX_PREVCLOSE_ROLLOVER
)

# --------------------------------------------------
# CORE ENGINE
# --------------------------------------------------

from app.engine.broker_reconciliation import BrokerReconciliationJob

# --------------------------------------------------
# TRADING
# --------------------------------------------------

from app.trading.trade_state_manager import TradeStateManager
from app.trading.recovery import recover_trades_from_zerodha

# --------------------------------------------------
# BROKER
# --------------------------------------------------

from app.brokers.zerodha_broker import ZerodhaBroker
from app.brokers.zerodha_manager import ZerodhaManager

# --------------------------------------------------
# DB
# --------------------------------------------------

from app.db.sqlite import init_db
from app.db.migrations.runner import run_migrations
from app.db.housekeeping import run_housekeeping, housekeeping_loop

# --------------------------------------------------
# LOGGING
# --------------------------------------------------

from app.utils.housekeeping import run_housekeeping as run_log_housekeeping

# --------------------------------------------------
# INSTRUMENTS
# --------------------------------------------------

from app.fetcher.zerodha_instruments import ensure_instruments_dump
from app.fetcher.instruments_snapshot import (
    snapshot_instruments_for_today, snapshot_job_factory)

# --------------------------------------------------
# RELAY MONITOR
# --------------------------------------------------
 
from app.services.relay_deployer import start_relay_monitor
 
# ── DISK_GUARD BEGIN ──────────────────────────────
# Free-space watchdog (own daemon thread). Warns on low/critical disk before a
# full volume starts failing SQLite writes mid-trade. Pure observer — never
# blocks trading. Remove: delete this import + the start block below + the file
# app/services/disk_guard.py. grep "DISK_GUARD".
from app.services.disk_guard import start_disk_guard
# ── DISK_GUARD END ────────────────────────────────

# --------------------------------------------------
# SCALP_V3 (standalone async selection loop — NOT via StrategyRuntimeManager)
# Mirrors SCALP_V2's launch pattern. TEST option-BUYING hedge strategy.
# --------------------------------------------------

from app.engine.scalp_v3.scalp_v3_selection_loop import scalp_v3_selection_loop
from app.engine.scalpv5.scalpv5_selection_loop import scalpv5_selection_loop
# PST paper phase — one loop serves PST_SELL + PST_HEDGE (change-set B)
from app.engine.pst.pst_selection_loop import pst_selection_loop, pst_live_eod_job
from app.engine.ic.ic_runtime import ic_runtime, IC_STRATEGY_IDS  # ← IC_SPLIT (shared V1/V2)
from app.engine.tsg.tsg_runtime import tsg_v1_runtime          # ← NEW (TSG_V1)
from app.engine.gc.gc_runtime import gc_v1_runtime              # ← NEW (GC_V1)
from app.engine.tma.tma_selection_loop import tma_selection_loop  # ← NEW (TMA_V1)

# SCALP_V3 hedge-GTT reconcile loop — detects the hedge SL-only GTT firing in
# LIVE mode and closes the trade so the single-trade gate is freed. Launched as
# a standalone async task next to the V3 selection loop (same enabled+license
# gate). Self-contained: V1 / BB / HA / V2 untouched.
from app.jobs.scalp_v3_gtt_reconcile import scalp_v3_gtt_reconcile_loop

from app.backtest import queue_worker

# ── EXPIRY_ERA_STARTUP BEGIN ──────────────────────
# One-time, marker-gated repair of pre-Sep-2025 expiry labels in the backtest
# corpus (Thursday→Tuesday era change; see app/backtest/maintenance.py).
# Runs BEFORE the backtest worker resumes so no run sees a half-labeled
# corpus. Marker present → instant no-op. FAIL-SAFE: any error is logged and
# swallowed — the trading app must boot regardless; the repair retries next
# boot and unrepaired old years stay days_uncovered (fail closed) meanwhile.
# Remove: this block + app/backtest/maintenance.py. grep "EXPIRY_ERA_STARTUP".
try:
    from app.backtest.maintenance import ensure_expiry_era_labels
    _era = ensure_expiry_era_labels()
    if _era.get("status") not in ("already_done", "no_db"):
        print(f"[EXPIRY_ERA_STARTUP] {_era}")
except Exception as _e:                                    # noqa: BLE001
    print(f"[EXPIRY_ERA_STARTUP] repair failed (will retry next boot): {_e}")
# ── EXPIRY_ERA_STARTUP END ────────────────────────

queue_worker.resume_on_startup()

# --------------------------------------------------
# APP
# --------------------------------------------------

app = FastAPI(title="Scalp App Backend")

# --------------------------------------------------
# REGISTER ROUTERS
# --------------------------------------------------

app.include_router(system_router)
app.include_router(log_router)
app.include_router(config_router)
app.include_router(debug_router,    dependencies=[Depends(_require_admin_ui)])
app.include_router(debug_ui_router, dependencies=[Depends(_require_admin_ui)])
app.include_router(market_indices_router)
app.include_router(paper_trades_router)
# ── KILL_SWITCH BEGIN ── per-strategy kill: close all live exposure, verify
# flat, then flip mode → PAPER (app/execution/kill_switch.py owns doctrine)
from app.api.kill_routes import router as kill_router
app.include_router(kill_router)
# ── KILL_SWITCH END ──

app.include_router(status_router)
app.include_router(selection_router)
app.include_router(strategy_router)
app.include_router(zerodha_router)
app.include_router(acc2_router)  # ACC2
app.include_router(trade_state_router)
app.include_router(trade_history_router)
app.include_router(positions_router)
app.include_router(signal_router)
app.include_router(ltp_router)
app.include_router(health_router)
app.include_router(telegram_router)
app.include_router(futures_candles_router)
app.include_router(relay_router)
app.include_router(app_settings_router)
app.include_router(pst_state_router)
app.include_router(scalp_v3_state_router)
app.include_router(scalpv5_state_router)
app.include_router(ic_state_router)
app.include_router(tsg_v1_state_router)
app.include_router(gc_v1_state_router)  # ← NEW (GC_V1)
app.include_router(tma_state_router)
app.include_router(backtest_router, dependencies=[Depends(_require_admin_ui)])
# ── CRYPTO_LAB_OPEN BEGIN ── TEMPORARY gate toggle for the Crypto Lab.
# The crypto sub-router is mounted HERE (not inside backtest_router) so its
# gate is independent of the Backtest page's admin gate. Paths are unchanged:
# /api/backtest/crypto/*.
#   CRYPTO_LAB_OPEN_TO_ALL = True   -> any licensed user may use the lab
#   CRYPTO_LAB_OPEN_TO_ALL = False  -> admin-only (same gate as Backtest)
# REVERT: set False here AND in App.jsx (same flag name), rebuild.
# NOTE: while True, these routes are reachable WITHOUT admin on anything that
# can reach the port — keep the app OFF the public Funnel (same rule as the
# Backtest routes; see the auth-audit note in Backtest.jsx).
from app.api.crypto_lab_routes import crypto_router
CRYPTO_LAB_OPEN_TO_ALL = True
if CRYPTO_LAB_OPEN_TO_ALL:
    app.include_router(crypto_router, prefix="/api/backtest")
else:
    app.include_router(crypto_router, prefix="/api/backtest",
                       dependencies=[Depends(_require_admin_ui)])
# ── CRYPTO_LAB_OPEN END ──


# ── BOOT_ISOLATION_20260817 (route) BEGIN ────────────────────────────
# MUST stay ABOVE the SCALP_UI_SERVE mount below. Starlette matches routes
# in registration order and Mount("/") matches everything, so any explicit
# route registered after it is unreachable (returns StaticFiles' 404).
@app.get("/boot-status")
def boot_status():
    """
    D3: makes a partial boot answerable without log archaeology.
    degraded=True means the process is serving but at least one component
    did not start. failures[] names them.
    """
    return {
        "startup_complete": getattr(app.state, "startup_complete", False),
        "startup_phase": getattr(app.state, "startup_phase", "unknown"),
        "degraded": getattr(app.state, "startup_degraded", False),
        "failures": getattr(app.state, "startup_failures", []),
    }
# ── BOOT_ISOLATION_20260817 (route) END ──────────────────────────────


# ====================================================================
# >>> SCALP_UI_SERVE BEGIN <<<
# Feature: serve the built React UI from the backend so mobile can use a
# single always-on origin (:47321 / Tailscale Funnel) instead of the
# fragile :3000 dev server. PURELY ADDITIVE — registered AFTER all routers
# above, so it can only ever catch requests no API router claimed.
# To remove this feature entirely: delete this whole block (BEGIN..END),
# revert the tauri.conf.json resources line, and revert main.rs. Find all
# parts with:  grep -rn "SCALP_UI_SERVE" .
#
# Safety properties:
#   - Mount at "/" sits LAST in the route table; Starlette checks mounts
#     after explicit routes, so /system/*, /api/scalp_v3/state, etc. win.
#   - Confirmed `curl :47321/` returned 404 before adding this (root free).
#   - Fail-open: if the build dir is missing, we log and serve API only —
#     startup never crashes, behavior degrades to exactly today's.
#   - React uses hash routing (/#/connections), so the server only needs
#     to serve "/" + static assets; no aggressive catch-all required.
# ====================================================================
import sys as _UISERVE_sys
from pathlib import Path as _UISERVE_Path
from fastapi.staticfiles import StaticFiles as _UISERVE_StaticFiles

# Resolve where the React build lives. Two execution modes:
#
#  (1) FROZEN (PyInstaller binary inside Scalp.app): __file__ points into
#      PyInstaller's temp extraction dir (_MEIPASS), NOT the on-disk
#      Resources/backend. The reliable anchor is sys.executable, which IS
#      the real on-disk binary path: Resources/backend/scalp-backend.
#         sys.executable           -> Resources/backend/scalp-backend
#         .parent                  -> Resources/backend
#         .parent.parent           -> Resources
#         .parent.parent/frontend/build -> Resources/frontend/build  (bundled)
#
#  (2) DEV (loose .py via uvicorn): __file__ is the real source path, so the
#      __file__-anchored candidates find the source-tree frontend/build.
#
# We try frozen-style first, then dev-style, and pick the first that has an
# index.html. Order is harmless: only an existing index.html is selected.
_uiserve_exe = _UISERVE_Path(_UISERVE_sys.executable).resolve().parent
_uiserve_here = _UISERVE_Path(__file__).resolve().parent
_uiserve_candidates = [
    _uiserve_exe.parent / "frontend" / "build",           # frozen: Resources/frontend/build
    _uiserve_here.parent / "frontend" / "build",          # dev: one up from api_server.py
    _uiserve_here.parent.parent / "frontend" / "build",   # dev: two up (source-tree fallback)
]
_uiserve_build_dir = next(
    (p for p in _uiserve_candidates if (p / "index.html").is_file()),
    None,
)

if _uiserve_build_dir is not None:
    # html=True serves index.html at "/" (and as fallback) while still
    # serving real asset files (JS/CSS/png) directly.
    app.mount(
        "/",
        _UISERVE_StaticFiles(directory=str(_uiserve_build_dir), html=True),
        name="scalp_ui_serve",
    )
    write_audit_log(f"[SCALP_UI_SERVE] Serving React UI from {_uiserve_build_dir}")
else:
    write_audit_log(
        "[SCALP_UI_SERVE] React build not found — UI not served from backend "
        f"(API only). Looked in: {[str(p) for p in _uiserve_candidates]}"
    )
# ====================================================================
# >>> SCALP_UI_SERVE END <<<
# ====================================================================

# --------------------------------------------------
# CORS
# --------------------------------------------------

if SCALP_ENV == "desktop":
    allow_origins = [
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:47321",
        "http://127.0.0.1:47321",
        "*",
    ]
else:
    allow_origins = ["http://localhost:3000", "http://127.0.0.1:3000", "*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# CORE SINGLETONS
# --------------------------------------------------

zerodha_manager = ZerodhaManager()
broker = ZerodhaBroker(zerodha_manager)

# 🔔 TELEGRAM SCHEDULER INSTANCE
telegram_scheduler = TelegramScheduler()
app.state.telegram_scheduler = telegram_scheduler

# --------------------------------------------------
# BACKGROUND STARTUP STATE
# --------------------------------------------------
# Exposes background-init progress so /status (or a health route) can report
# whether heavy startup has finished. The HTTP server is live well before this
# flips to True.
app.state.startup_complete = False
app.state.startup_phase = "pending"


# --------------------------------------------------
# HEAVY STARTUP WORK (runs in background AFTER port is bound)
# --------------------------------------------------
# This is the exact same sequence as before, in the same order — only it now
# runs off the critical path so the HTTP port opens in a few seconds instead
# of waiting 80s+. Each block is timed so boot cost is visible in the log.
# ── BOOT_ISOLATION_20260817 BEGIN ────────────────────────────────────────
# Phase-isolated startup. Rationale in full at the top of
# apply_boot_isolation_20260817.py; short version: on 2026-08-16/17 a single
# NameError in the GC_V1 launch aborted _run_heavy_startup() and took every
# later launch AND all 13 EOD/morning crons down with it, silently, for a
# whole trading session.
#
# INVARIANT: no single strategy launch can prevent another launch, the
# scheduler, or startup completion. Failures are loud, not fatal.

def _boot_alert(label: str, err: BaseException) -> None:
    """
    Telegram CRITICAL for a startup phase failure. Infrastructure alert —
    deliberately NOT gated on any per-strategy notification toggle, mirroring
    services/disk_guard.py. Never raises: a Telegram outage must not turn a
    recoverable boot failure into a fatal one.
    """
    try:
        from app.api import telegram_api
        cfg = telegram_api.TELEGRAM_CONFIG or {}
        bot_token = (cfg.get("bot_token") or "").strip()
        if not bot_token:
            return
        msg = (f"\U0001F6A8 SCALP BOOT FAILURE\n\n"
               f"Phase: {label}\n"
               f"Error: {type(err).__name__}: {err}\n\n"
               f"That component did NOT start. Other components continued. "
               f"Check GET /boot-status.")
        for ch in (cfg.get("channels") or []):
            try:
                if not ch.get("enabled"):
                    continue
                chat_id = (ch.get("chat_id") or "").strip()
                if not chat_id:
                    continue
                telegram_api.send_telegram_message(bot_token, chat_id, msg)
            except Exception as e:
                write_audit_log(f"[BOOT_GUARD][TG_CH_ERR] {e}")
    except Exception as e:
        write_audit_log(f"[BOOT_GUARD][TG_ERR] {e}")


@contextmanager
def _boot_guard(label: str):
    """
    Isolate one startup phase. Logs + alerts + records on failure, then lets
    startup continue. app.state.startup_failures is the machine-readable
    record; /boot-status serves it.
    """
    try:
        yield
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        write_audit_log(f"[SYSTEM][BOOT_FAIL] {label} — {detail}")
        write_audit_log(f"[SYSTEM][BOOT_FAIL][TRACE] {label} — "
                        f"{traceback.format_exc()}")
        try:
            app.state.startup_failures.append({"phase": label,
                                               "error": detail})
        except Exception:
            pass
        _boot_alert(label, e)


async def _run_heavy_startup():
    import time
    _t = time.time()

    def lap(label):
        nonlocal _t
        now = time.time()
        write_audit_log(f"[BOOT-TIMING] {label}: {now - _t:.1f}s")
        _t = now

    app.state.startup_failures = []

    # --------------------------------------------------
    # HOUSEKEEPING
    # --------------------------------------------------
    app.state.startup_phase = "housekeeping"
    with _boot_guard("log_housekeeping"):
        run_log_housekeeping()
        write_audit_log("[SYSTEM] Log housekeeping completed")
        lap("log_housekeeping")

    with _boot_guard("db_housekeeping"):
        run_housekeeping()
        asyncio.create_task(housekeeping_loop())
        write_audit_log("[SYSTEM] DB housekeeping started")
        lap("db_housekeeping")

    with _boot_guard("state_dir"):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # --------------------------------------------------
    # STRATEGY INIT  (unchanged order/logic + PHASE 2 license gate)
    # --------------------------------------------------
    app.state.startup_phase = "strategies"
    from app.strategy.strategy_registry import STRATEGIES

    for strategy_id, cfg in STRATEGIES.items():

        if not cfg.get("enabled", False):
            write_audit_log(f"[SYSTEM] Strategy {strategy_id} disabled — skipping")
            continue

        # PHASE 2 LICENSE GATE: ADMIN entitlements are ["*"] so this is
        # always True for admin builds — provably identical behavior.
        if not license_state.license_allows_strategy(strategy_id):
            write_audit_log(
                f"[LICENSE] Strategy {strategy_id} not licensed — skipping"
            )
            continue

        if strategy_id == "SCALP_V3":
            write_audit_log(
                "[SYSTEM] SCALP_V3 deferred — launched via standalone selection loop"
            )
            continue

        if strategy_id == "SCALP_V5":
            write_audit_log(
                "[SYSTEM] SCALP_V5 deferred — launched via standalone selection loop"
            )
            continue

        if strategy_id == "GC_V1":
            write_audit_log(
                "[SYSTEM] GC_V1 deferred — launched via standalone runtime"
            )
            continue
        if strategy_id == "TSG_V1":
            write_audit_log(
                "[SYSTEM] TSG_V1 deferred — launched via standalone runtime"
            )
            continue
        if strategy_id in ("IC_V1", "IC_V2"):   # ── IC_SPLIT ──
            write_audit_log(
                f"[SYSTEM] {strategy_id} deferred — launched via "
                f"standalone runtime"
            )
            continue

        # D1: per-strategy isolation. A bad slot/executor for one strategy
        # no longer prevents the others from initialising.
        with _boot_guard(f"strategy_init {strategy_id}"):
            write_audit_log(f"[SYSTEM] Initializing strategy {strategy_id}")

            # ── ACC2_W3 ── executor follows the per-strategy account binding
            from app.execution.executor_factory import get_executor_for_strategy
            strategy_executor = get_executor_for_strategy(strategy_id)

            for slot_name in cfg.get("slots", []):
                TradeStateManager(
                    strategy_id=strategy_id,
                    name=slot_name,
                    executor=strategy_executor,
                    state_file=STATE_DIR / f"{strategy_id}_{slot_name}.json",
                    price_provider=None,
                )

            StrategyRuntimeManager.start(strategy_id, zerodha_manager)
            write_audit_log(f"[SYSTEM] Strategy {strategy_id} runtime started")
            lap(f"strategy {strategy_id}")

    # --------------------------------------------------
    # TRADE RECOVERY
    # --------------------------------------------------
    app.state.startup_phase = "recovery"
    with _boot_guard("recover_trades"):
        recover_trades_from_zerodha()
        lap("recover_trades")

    # --------------------------------------------------
    # ZERODHA INSTRUMENTS + INDEX STATE + PIVOTS
    # --------------------------------------------------
    with _boot_guard("instruments_and_index_state"):
        if zerodha_manager.is_trade_ready():
            kite = (
                zerodha_manager.get_data_kite()
                or zerodha_manager.get_trade_kite()
            )
            if kite:
                app.state.startup_phase = "instruments"
                ensure_instruments_dump(kite.api_key, kite.access_token)
                # Fix 1: capture a DATED snapshot of today's NFO master so future
                # backtests can reconstruct the correct per-day weekly expiry and
                # backfill expired weeklies (whose tokens Kite flushes at expiry).
                try:
                    snapshot_instruments_for_today(kite)
                except Exception as _e:
                    write_audit_log(f"[INSTR_SNAPSHOT][WARN] startup snapshot failed: {_e!r}")
                lap("instruments")

                load_index_prev_close_once(kite)
                seed_index_ltp_once(kite)
                lap("index_state")

                PivotCache.initialize(kite)
                write_audit_log("[PIVOT] PivotCache initialized")
                lap("pivot_cache")

                write_audit_log("[ZERODHA] Instruments + index state loaded")

    # ── INDEX_PREVCLOSE_ROLLOVER BEGIN (watchdog launch) ──
    # Started OUTSIDE the is_trade_ready() gate on purpose:
    #   (a) rolls prev_close over the midnight boundary so a backend
    #       left running overnight never serves a stale reference
    #       (2026-08-13 bug: BANKNIFTY change sign flipped on dash);
    #   (b) self-heals a startup where trade wasn't ready and the
    #       gated loader above never ran — watchdog populates
    #       prev_close once the morning login lands.
    with _boot_guard("index_prev_close_watchdog"):
        asyncio.create_task(index_prev_close_watchdog(zerodha_manager))
        write_audit_log("[SYSTEM] Index prev_close rollover watchdog launched")
    # ── INDEX_PREVCLOSE_ROLLOVER END (watchdog launch) ──

    # --------------------------------------------------
    # SCHEDULER
    # --------------------------------------------------
    # D2: MOVED AHEAD OF THE STANDALONE LAUNCHES (2026-08-17). These crons
    # are the last-resort EOD/morning safety net for every strategy. They
    # previously sat behind ~8 unguarded launch statements, so one typo in a
    # launch line silently deregistered all 13. Registration order has no
    # behavioural effect — cron triggers fire on wall clock — so registering
    # first is strictly safer.
    app.state.startup_phase = "scheduler"
    with _boot_guard("scheduler"):
        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

        scheduler.add_job(
            paper_trade_eod_job, trigger="cron", hour=15, minute=25,
            id="paper_trade_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            bb_live_eod_job, trigger="cron", hour=15, minute=25,
            id="bb_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            bb_live_eod_v2_job, trigger="cron", hour=15, minute=25,
            id="bb_v2_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            ha_live_eod_job, trigger="cron", hour=15, minute=25,
            id="ha_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            scalp_v3_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalp_v3_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            pst_live_eod_job, trigger="cron", hour=15, minute=28,
            id="pst_live_eod_check", replace_existing=True,
        )
        # ── SCALP_V5 BEGIN ──
        scheduler.add_job(
            scalpv5_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalpv5_live_eod_squareoff", replace_existing=True,
        )
        # ── SCALP_V5 END ──
        # ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
        # ONE EOD job serves BOTH IC instances: fires 15:25, iterates the
        # IC_REGISTRY and waits internally per instance to expiry_exit_time
        # (NEXT_OPEN mode: closes ONLY today-entered expiring legs, DA5) or
        # exit_time (legacy EOD mode: full square-off). Misfire acts
        # immediately. Second layer: each ICEngine's own continuous
        # session-end backstop.
        scheduler.add_job(
            ic_live_eod_job, trigger="cron", hour=15, minute=25,
            id="ic_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            tsg_live_eod_job, trigger="cron", hour=15, minute=26,
            id="tsg_v1_live_eod_squareoff", replace_existing=True,
        )
        # ── GC_V1 BEGIN ── EOD backstop cron, UNIQUE id (checklist scar:
        # a cloned id with replace_existing evicts the donor's job). 15:22
        # sits between the engine's ≤15:20 EOD and the 15:25 paper sweep.
        scheduler.add_job(
            gc_live_eod_job, trigger="cron", hour=15, minute=22,
            id="gc_v1_live_eod_squareoff", replace_existing=True,
        )
        # ── GC_V1 END ──
        # IC carry morning (ONE_NIGHT_MAX instances only): fires 09:08 IST —
        # pre-market GTT teardown (first-candle rule), waits to
        # next_open_time (09:16), then the morning square-off retry loop.
        # No-op with no carried legs on any instance (an EOD-mode IC_V1
        # never carries — structural no-op). Second layer: each ICEngine's
        # continuous carry-morning state machine.
        scheduler.add_job(
            ic_morning_job, trigger="cron", hour=9, minute=8,
            id="ic_morning_squareoff", replace_existing=True,
        )
        # ── IC END ──
        # ── TMA_V1 BEGIN ──
        # Layer-three safety net (candle path + coordinator are layers 1-2).
        # trade_mode-aware: INTRADAY/expiry-day → square off; positional
        # carry → no-op; loop dead → STALE paper rows / CRITICAL for live.
        scheduler.add_job(
            tma_live_eod_job, trigger="cron", hour=15, minute=25,
            id="tma_live_eod_squareoff", replace_existing=True,
        )
        # ── TMA_V1 END ──
        # Fix 1: daily dated NFO instrument snapshot (Mon–Fri, 09:05 IST). Builds
        # ~/.scalp-app/state/instruments_history/NFO_YYYY-MM-DD.csv so future
        # backtests can resolve expired weeklies' tokens. Idempotent per day.
        scheduler.add_job(
            snapshot_job_factory(zerodha_manager),
            trigger="cron", day_of_week="mon-fri", hour=9, minute=5,
            id="instruments_daily_snapshot", replace_existing=True,
        )

        scheduler.start()
        write_audit_log("[SYSTEM] All EOD schedulers started)")
        lap("schedulers")

    # --------------------------------------------------
    # STANDALONE STRATEGY LAUNCHES
    # --------------------------------------------------
    # D1: each launch is independently guarded. Gates and call arguments are
    # byte-for-byte unchanged.
    app.state.startup_phase = "launches"

    # --------------------------------------------------
    # PST STANDALONE LAUNCH (paper phase — SELL + HEDGE, one loop)
    # --------------------------------------------------
    # ── LICENSE_GATE_FIX (2026-08-07) ── PST was the ONLY launch site
    # without the Phase-2 license gate; a license without PST still
    # started this loop and took paper trades. Gate now mirrors the
    # sibling strategies. Per-sid enforcement (mixed entitlements +
    # entitlement shrink after boot) lives inside the loop itself.
    with _boot_guard("launch PST"):
        _pst_entitled = [sid for sid in ("PST_SELL", "PST_HEDGE")
                         if STRATEGIES.get(sid, {}).get("enabled", False)
                         and license_state.license_allows_strategy(sid)]
        if _pst_entitled:
            asyncio.create_task(pst_selection_loop(zerodha_manager))
            write_audit_log(f"[SYSTEM] PST standalone selection loop launched (paper) — entitled: {_pst_entitled}")
        elif (STRATEGIES.get("PST_SELL", {}).get("enabled", False)
                or STRATEGIES.get("PST_HEDGE", {}).get("enabled", False)):
            write_audit_log("[SYSTEM][LICENSE] PST enabled but not entitled — loop NOT launched")

    # --------------------------------------------------
    # SCALP_V3 STANDALONE LAUNCH  (mirrors SCALP_V2 + PHASE 2 license gate)
    # --------------------------------------------------
    with _boot_guard("launch SCALP_V3"):
        if STRATEGIES.get("SCALP_V3", {}).get("enabled", False) and \
                license_state.license_allows_strategy("SCALP_V3"):
            asyncio.create_task(scalp_v3_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] SCALP_V3 standalone selection loop launched")

            # Hedge-GTT reconcile loop: closes a live V3 trade when its hedge
            # SL-only GTT fires at the broker, freeing the single-trade gate.
            # Without this the row stays OPEN until the signal contract hits its
            # own SL/TP or EOD, blocking the next trade. (LIVE only; paper exits
            # via the tick engine's _watch_exit.)
            asyncio.create_task(scalp_v3_gtt_reconcile_loop())
            write_audit_log("[SYSTEM] SCALP_V3 hedge-GTT reconcile loop launched")

    # ── SCALP_V5 BEGIN ──
    # SCALP_V5 STANDALONE LAUNCH (mirrors SCALP_V3 + PHASE 2 license gate).
    # No GTT-reconcile loop: V5 has no hedge SL-only GTT to reconcile — its
    # SL/TP GTT (when present) is handled by the tick watcher's cancel→verify
    # exit path + the TIME exit, and a fired SL/TP OCO leg flattens the
    # position which the next close_trade()/EOD reconciles via ALREADY_FLAT.
    with _boot_guard("launch SCALP_V5"):
        if STRATEGIES.get("SCALP_V5", {}).get("enabled", False) and \
                license_state.license_allows_strategy("SCALP_V5"):
            asyncio.create_task(scalpv5_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] SCALP_V5 standalone selection loop launched")
    # ── SCALP_V5 END ──

    # ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
    # IC STANDALONE LAUNCH (mirrors SCALP_V5 + PHASE 2 license gate).
    # Time-entry iron condor: no selection loop, no candle pipeline. ONE
    # runtime PER STRATEGY (IC_V1 = legacy EOD condor, IC_V2 = NEXT_OPEN
    # / ONE_NIGHT_MAX + ADJ_ON_MTC); each builds its own group manager +
    # engine (entry scheduler + REST LTP watcher + continuous EOD
    # backstop) + GTT backstop monitor. Defaults ship
    # trade_execution_mode=OFF — launching a runtime with mode OFF places
    # no orders and enters no positions.
    # D1: guarded PER INSTANCE — IC_V1 failing must not strand IC_V2.
    for _ic_sid in IC_STRATEGY_IDS:
        with _boot_guard(f"launch {_ic_sid}"):
            if STRATEGIES.get(_ic_sid, {}).get("enabled", False) and \
                    license_state.license_allows_strategy(_ic_sid):
                asyncio.create_task(ic_runtime(zerodha_manager, _ic_sid))
                write_audit_log(f"[SYSTEM] {_ic_sid} standalone runtime launched")

    # ── TSG_V1 BEGIN ──
    # TSG_V1 STANDALONE LAUNCH (mirrors IC_V1; LD10 Phase 1).
    with _boot_guard("launch TSG_V1"):
        if STRATEGIES.get("TSG_V1", {}).get("enabled", False) and \
                license_state.license_allows_strategy("TSG_V1"):
            asyncio.create_task(tsg_v1_runtime(zerodha_manager))
            write_audit_log("[SYSTEM] TSG_V1 standalone runtime launched")
    # ── TSG_V1 END ──

    # ── GC_V1 BEGIN ──
    # GC_V1 STANDALONE LAUNCH (mirrors TSG_V1; LD5/LD15 PAPER phase).
    # 2026-08-17: this line passed `broker_manager`, gc_v1_runtime's own
    # parameter name, which does not exist in this module. The NameError
    # killed startup here. Guarded now, and pyflakes gates the class.
    with _boot_guard("launch GC_V1"):
        if STRATEGIES.get("GC_V1", {}).get("enabled", False) and \
                license_state.license_allows_strategy("GC_V1"):
            asyncio.create_task(gc_v1_runtime(zerodha_manager))
            write_audit_log("[SYSTEM] GC_V1 standalone runtime launched")
    # ── GC_V1 END ──
    # ── IC END ──

    # ── TMA_V1 BEGIN ──
    # TMA_V1 STANDALONE LAUNCH (mirrors PST + license gate). Triple-EMA
    # credit spread: 3-session EMA warmup, own KiteTicker, parity-by-
    # construction signals (backtest build_signals re-run per minute).
    # Ships mode=PAPER — launching starts paper trading; LIVE is a
    # Settings flip (dynamic mode, stamped per position).
    with _boot_guard("launch TMA_V1"):
        if STRATEGIES.get("TMA_V1", {}).get("enabled", False) and \
                license_state.license_allows_strategy("TMA_V1"):
            asyncio.create_task(tma_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] TMA_V1 standalone selection loop launched")
    # ── TMA_V1 END ──

    # --------------------------------------------------
    # BROKER RECONCILIATION
    # --------------------------------------------------
    app.state.startup_phase = "reconciliation"
    with _boot_guard("broker_reconciliation_thread"):
        threading.Thread(
            target=BrokerReconciliationJob(
                get_executor_for_broker("ZERODHA")
            ).run_forever,
            daemon=True,
        ).start()
        lap("broker_reconciliation_thread")

    # 🔔 TELEGRAM SCHEDULER START
    try:
        telegram_scheduler.start()
        write_audit_log("[TELEGRAM] Scheduler started")
    except Exception as e:
        write_audit_log(f"[TELEGRAM] Scheduler failed to start: {e}")

    # 🛡️ RELAY MONITOR START
    try:
        start_relay_monitor()
        write_audit_log("[RELAY_MONITOR] Started")
    except Exception as e:
        write_audit_log(f"[RELAY_MONITOR] Failed to start: {e}")

    # ── DISK_GUARD BEGIN ── free-space watchdog (own daemon thread)
    try:
        start_disk_guard()
    except Exception as e:
        write_audit_log(f"[DISK_GUARD] Failed to start: {e}")
    # ── DISK_GUARD END ──

    # --------------------------------------------------
    # COMPLETION
    # --------------------------------------------------
    # startup_complete flips True even with failures — the process IS up and
    # serving, and callers need to distinguish "still booting" from "booted,
    # degraded". startup_degraded carries the latter.
    _failures = list(getattr(app.state, "startup_failures", []))
    app.state.startup_complete = True
    app.state.startup_degraded = bool(_failures)
    if _failures:
        app.state.startup_phase = f"complete_with_failures ({len(_failures)})"
        write_audit_log(
            f"[SYSTEM][ERROR] Background startup completed WITH "
            f"{len(_failures)} FAILURE(S): "
            f"{', '.join(f['phase'] for f in _failures)}"
        )
    else:
        app.state.startup_phase = "complete"
        write_audit_log("[SYSTEM] Background startup complete")

# ── BOOT_ISOLATION_20260817 END ──────────────────────────────────────────


# --------------------------------------------------
# STARTUP  (fast path — only what must finish before serving)
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():

    write_audit_log("[SYSTEM] Backend startup initiated")

    # Fast, essential setup that later code depends on. Kept synchronous.
    ensure_app_dirs()
    export_env()
    from app.utils.version import write_version_file
    write_version_file()
    write_audit_log("[SYSTEM] App directories ensured")

    # ── BOOT_BANNER BEGIN ── one unmissable line answering "when did THIS
    # process actually start" (2026-08-13 incident: a hibernate-surviving
    # backend was mistaken for a fresh morning start; the diagnosis took
    # ps/grep archaeology that a boot banner reduces to a one-grep question).
    try:
        from datetime import datetime as _dt
        from app.utils.version import get_version as _gv
        _v = str((_gv() or {}).get("version", "unknown"))
        write_audit_log(f"[BOOT_BANNER] scalp-backend STARTED — version={_v} "
                        f"pid={os.getpid()} started_at="
                        f"{_dt.now().isoformat(timespec='seconds')} "
                        f"(any lines above this are from a PREVIOUS process)")
    except Exception as _e:
        write_audit_log(f"[BOOT_BANNER] banner failed ({_e!r}) — non-fatal")
    # ── BOOT_BANNER END ──

    # PHASE 2: real license check (local token verify; one short network
    # refresh ONLY if a stored token is stale). Never raises, never blocks
    # beyond a 6s cap in the stale-token case.
    license_client.initialize_license()

    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    # ── IC_SPLIT (2026-08-04) ── one-time IC_V1→IC_V2 FILE migration
    # (config json + latch/carry/session renames). Must run AFTER the DB
    # migration (023 retags rows) and BEFORE any strategy runtime launches
    # — an IC engine booting on unmigrated files would resurrect the
    # pre-split identity. Idempotent (marker file); never raises.
    from app.config.ic_split_migration import run_ic_split_migration
    run_ic_split_migration()

    # Everything heavy now runs in the background so the HTTP port binds
    # immediately and the UI's BackendBootGuard unblocks in a few seconds.
    asyncio.create_task(_run_heavy_startup())
    write_audit_log("[SYSTEM] Heavy startup dispatched to background task")

    # PHASE 2: license heartbeat loop (6h cadence, 30m retry on failure).
    asyncio.create_task(license_client.heartbeat_loop())
    write_audit_log("[LICENSE] Heartbeat loop launched")

    # Advisory app-version check (non-blocking, fail-open). Never gates
    # trading; only sets a flag the UI reads for a soft update banner.
    from app.license import version_check
    asyncio.create_task(asyncio.to_thread(version_check.check_for_update))
    write_audit_log("[VERSION] Advisory update check dispatched")
# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    write_audit_log(
        f"[SYSTEM] Starting backend (env={SCALP_ENV}, port={SCALP_PORT})"
    )

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=SCALP_PORT,
        log_level="info",
        access_log=False,
    )
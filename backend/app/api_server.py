from fastapi import FastAPI
import asyncio
import threading
import os

from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from app.strategy.strategy_runtime import StrategyRuntimeManager
from app.execution.executor_factory import get_executor_for_broker
from app.jobs.bb_live_eod_v2 import bb_live_eod_v2_job

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


# 🔔 TELEGRAM ALERT
from app.api.telegram_api import notify_system_alert

# 🔔 TELEGRAM SCHEDULER
from app.services.telegram_scheduler import TelegramScheduler

# with the other router imports (near line 59):
from app.api.scalp_v2_api import router as scalp_v2_router
from app.api.app_settings_api import router as app_settings_router

# --------------------------------------------------
# JOBS
# --------------------------------------------------

from app.jobs.paper_trade_eod import paper_trade_eod_job
from app.jobs.bb_live_eod import bb_live_eod_job
from app.jobs.ha_live_eod import ha_live_eod_job          # ← NEW
from app.jobs.scalp_v2_live_eod import scalp_v2_live_eod_job   # ← NEW (SCALP_V2)
from app.jobs.scalp_v3_live_eod import scalp_v3_live_eod_job   # ← NEW (SCALP_V3)
from app.api.futures_candles_routes import router as futures_candles_router

# --------------------------------------------------
# MARKET DATA
# --------------------------------------------------

from app.marketdata.load_index_prev_close import (
    load_index_prev_close_once,
    seed_index_ltp_once,
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

# --------------------------------------------------
# RELAY MONITOR
# --------------------------------------------------

from app.services.relay_deployer import start_relay_monitor

# --------------------------------------------------
# SCALP_V2 (standalone async selection loop — NOT via StrategyRuntimeManager)
# --------------------------------------------------

from app.engine.scalp_v2.scalp_v2_selection_loop import scalp_v2_selection_loop

# --------------------------------------------------
# SCALP_V3 (standalone async selection loop — NOT via StrategyRuntimeManager)
# Mirrors SCALP_V2's launch pattern. TEST option-BUYING hedge strategy.
# --------------------------------------------------

from app.engine.scalp_v3.scalp_v3_selection_loop import scalp_v3_selection_loop

# SCALP_V3 hedge-GTT reconcile loop — detects the hedge SL-only GTT firing in
# LIVE mode and closes the trade so the single-trade gate is freed. Launched as
# a standalone async task next to the V3 selection loop (same enabled+license
# gate). Self-contained: V1 / BB / HA / V2 untouched.
from app.jobs.scalp_v3_gtt_reconcile import scalp_v3_gtt_reconcile_loop

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

app.include_router(status_router)
app.include_router(selection_router)
app.include_router(strategy_router)
app.include_router(zerodha_router)
app.include_router(trade_state_router)
app.include_router(trade_history_router)
app.include_router(positions_router)
app.include_router(signal_router)
app.include_router(ltp_router)
app.include_router(health_router)
app.include_router(telegram_router)
app.include_router(futures_candles_router)
app.include_router(relay_router)
app.include_router(scalp_v2_router)
app.include_router(app_settings_router)
app.include_router(scalp_v3_state_router)

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
async def _run_heavy_startup():
    import time
    _t = time.time()

    def lap(label):
        nonlocal _t
        now = time.time()
        write_audit_log(f"[BOOT-TIMING] {label}: {now - _t:.1f}s")
        _t = now

    try:
        app.state.startup_phase = "housekeeping"
        run_log_housekeeping()
        write_audit_log("[SYSTEM] Log housekeeping completed")
        lap("log_housekeeping")

        run_housekeeping()
        asyncio.create_task(housekeeping_loop())
        write_audit_log("[SYSTEM] DB housekeeping started")
        lap("db_housekeeping")

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

            if strategy_id == "SCALP_V2":
                write_audit_log(
                    "[SYSTEM] SCALP_V2 deferred — launched via standalone selection loop"
                )
                continue

            if strategy_id == "SCALP_V3":
                write_audit_log(
                    "[SYSTEM] SCALP_V3 deferred — launched via standalone selection loop"
                )
                continue

            write_audit_log(f"[SYSTEM] Initializing strategy {strategy_id}")

            strategy_executor = get_executor_for_broker(cfg["broker"])

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
        recover_trades_from_zerodha()
        lap("recover_trades")

        # --------------------------------------------------
        # ZERODHA INSTRUMENTS + INDEX STATE + PIVOTS
        # --------------------------------------------------
        if zerodha_manager.is_trade_ready():
            kite = (
                zerodha_manager.get_data_kite()
                or zerodha_manager.get_trade_kite()
            )
            if kite:
                app.state.startup_phase = "instruments"
                ensure_instruments_dump(kite.api_key, kite.access_token)
                lap("instruments")

                load_index_prev_close_once(kite)
                seed_index_ltp_once(kite)
                lap("index_state")

                PivotCache.initialize(kite)
                write_audit_log("[PIVOT] PivotCache initialized")
                lap("pivot_cache")

                write_audit_log("[ZERODHA] Instruments + index state loaded")

        # --------------------------------------------------
        # SCALP_V2 STANDALONE LAUNCH  (unchanged + PHASE 2 license gate)
        # --------------------------------------------------
        if STRATEGIES.get("SCALP_V2", {}).get("enabled", False) and \
                license_state.license_allows_strategy("SCALP_V2"):
            asyncio.create_task(scalp_v2_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] SCALP_V2 standalone selection loop launched")

        # --------------------------------------------------
        # SCALP_V3 STANDALONE LAUNCH  (mirrors SCALP_V2 + PHASE 2 license gate)
        # --------------------------------------------------
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

        # --------------------------------------------------
        # BROKER RECONCILIATION  (unchanged)
        # --------------------------------------------------
        threading.Thread(
            target=BrokerReconciliationJob(
                get_executor_for_broker("ZERODHA")
            ).run_forever,
            daemon=True,
        ).start()
        lap("broker_reconciliation_thread")

        # --------------------------------------------------
        # SCHEDULER  (unchanged)
        # --------------------------------------------------
        app.state.startup_phase = "scheduler"
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
            scalp_v2_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalp_v2_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            scalp_v3_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalp_v3_live_eod_squareoff", replace_existing=True,
        )

        scheduler.start()
        write_audit_log("[SYSTEM] All EOD schedulers started (paper + BB + HA + SCALP_V2 + SCALP_V3)")
        lap("schedulers")

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

        # 🔔 TELEGRAM STARTUP NOTIFICATION
        try:
            notify_system_alert({
                "severity": "info",
                "message": "🚀 Scalp Terminal backend started successfully!"
            })
            write_audit_log("[TELEGRAM] Startup notification sent")
        except Exception as e:
            write_audit_log(f"[TELEGRAM] Startup notification failed: {e}")

        app.state.startup_phase = "complete"
        app.state.startup_complete = True
        write_audit_log("[SYSTEM] Background startup complete")

    except Exception as e:
        app.state.startup_phase = f"error: {e}"
        write_audit_log(f"[SYSTEM][ERROR] Background startup failed: {e}")
        raise


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

    # PHASE 2: real license check (local token verify; one short network
    # refresh ONLY if a stored token is stale). Never raises, never blocks
    # beyond a 6s cap in the stale-token case.
    license_client.initialize_license()

    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

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
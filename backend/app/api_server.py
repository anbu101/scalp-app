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

from app.license.machine_id import get_machine_id
from app.license.license_validator import validate_license
from app.license.license_state import LicenseStatus
from app.license import license_state
from app.event_bus.audit_logger import write_audit_log

# TEMP DEV BYPASS
license_state.LICENSE_STATUS = LicenseStatus.VALID
print("[LICENSE] License check BYPASSED - all checks will pass")

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

# 🔔 TELEGRAM ALERT
from app.api.telegram_api import notify_system_alert

# 🔔 TELEGRAM SCHEDULER
from app.services.telegram_scheduler import TelegramScheduler

# --------------------------------------------------
# JOBS
# --------------------------------------------------

from app.jobs.paper_trade_eod import paper_trade_eod_job
from app.jobs.bb_live_eod import bb_live_eod_job
from app.jobs.ha_live_eod import ha_live_eod_job          # ← NEW
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
# APP
# --------------------------------------------------

app = FastAPI(title="Scalp App Backend")

# --------------------------------------------------
# REGISTER ROUTERS
# --------------------------------------------------

app.include_router(system_router)
app.include_router(log_router)
app.include_router(config_router)
app.include_router(debug_router)
app.include_router(debug_ui_router)
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
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():

    write_audit_log("[SYSTEM] Backend startup initiated")

    ensure_app_dirs()
    export_env()
    write_audit_log("[SYSTEM] App directories ensured")

    get_machine_id()
    validate_license()
    license_state.LICENSE_STATUS = LicenseStatus.VALID
    write_audit_log(f"[LICENSE] Startup status = {license_state.LICENSE_STATUS}")

    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    run_housekeeping()
    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping started")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # --------------------------------------------------
    # STRATEGY INIT
    # --------------------------------------------------

    from app.strategy.strategy_registry import STRATEGIES

    for strategy_id, cfg in STRATEGIES.items():

        if not cfg.get("enabled", False):
            write_audit_log(f"[SYSTEM] Strategy {strategy_id} disabled — skipping")
            continue

        write_audit_log(f"[SYSTEM] Initializing strategy {strategy_id}")

        strategy_executor = get_executor_for_broker(cfg["broker"])

        # HA_V1 has no TradeStateManager slots — it manages state
        # internally via HATradeManager, exactly like BB_V1.
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

    recover_trades_from_zerodha()

    if zerodha_manager.is_trade_ready():

        kite = (
            zerodha_manager.get_data_kite()
            or zerodha_manager.get_trade_kite()
        )

        if kite:
            ensure_instruments_dump(kite.api_key, kite.access_token)
            load_index_prev_close_once(kite)
            seed_index_ltp_once(kite)

            PivotCache.initialize(kite)
            write_audit_log("[PIVOT] PivotCache initialized")

            write_audit_log("[ZERODHA] Instruments + index state loaded")

    threading.Thread(
        target=BrokerReconciliationJob(
            get_executor_for_broker("ZERODHA")
        ).run_forever,
        daemon=True,
    ).start()

    # --------------------------------------------------
    # SCHEDULER  (paper EOD + BB live EOD + HA live EOD)
    # --------------------------------------------------

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(
        paper_trade_eod_job,
        trigger="cron",
        hour=15,
        minute=25,
        id="paper_trade_eod_squareoff",
        replace_existing=True,
    )

    scheduler.add_job(
        bb_live_eod_job,
        trigger="cron",
        hour=15,
        minute=25,
        id="bb_live_eod_squareoff",
        replace_existing=True,
    )
    scheduler.add_job(
        bb_live_eod_v2_job,
        trigger="cron",
        hour=15,
        minute=25,
        id="bb_v2_live_eod_squareoff",
        replace_existing=True,
    )
    # ← NEW: HA live EOD square-off at 15:25 IST
    scheduler.add_job(
        ha_live_eod_job,
        trigger="cron",
        hour=15,
        minute=25,
        id="ha_live_eod_squareoff",
        replace_existing=True,
    )

    scheduler.start()

    write_audit_log("[SYSTEM] All EOD schedulers started (paper + BB + HA)")

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

    # --------------------------------------------------
    # 🔔 TELEGRAM STARTUP NOTIFICATION
    # --------------------------------------------------

    try:
        notify_system_alert({
            "severity": "info",
            "message": "🚀 Scalp Terminal backend started successfully!"
        })
        write_audit_log("[TELEGRAM] Startup notification sent")
    except Exception as e:
        write_audit_log(f"[TELEGRAM] Startup notification failed: {e}")


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
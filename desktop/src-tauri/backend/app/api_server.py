from fastapi import FastAPI
import asyncio
import threading
import os

from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from app.strategy.strategy_runtime import StrategyRuntimeManager
from app.execution.executor_factory import get_executor_for_broker

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
from app.indicators.pivot_cache import PivotCache


# --------------------------------------------------
# JOBS
# --------------------------------------------------

from app.jobs.paper_trade_eod import paper_trade_eod_job

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

from app.engine.exit_boot import start_exit_engine
from app.engine.startup_reconciliation import StartupReconciliation
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
    ]
else:
    allow_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

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

# --------------------------------------------------
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():

    write_audit_log("[SYSTEM] Backend startup initiated")


    # 0️⃣ App dirs
    ensure_app_dirs()
    export_env()
    write_audit_log("[SYSTEM] App directories ensured")

    # 🔑 License (dev bypass)
    get_machine_id()
    validate_license()
    license_state.LICENSE_STATUS = LicenseStatus.VALID
    write_audit_log(f"[LICENSE] Startup status = {license_state.LICENSE_STATUS}")

    # 1️⃣ DB
    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    # 2️⃣ Log housekeeping
    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    # 3️⃣ DB housekeeping
    run_housekeeping()
    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping started")

    # 4️⃣ State dir
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # 5️⃣ Startup reconciliation
    StartupReconciliation(broker).run()

    # --------------------------------------------------
    # 6️⃣ STRATEGY INITIALIZATION
    # --------------------------------------------------

    from app.strategy.strategy_registry import STRATEGIES

    for strategy_id, cfg in STRATEGIES.items():

        if not cfg.get("enabled", False):
            write_audit_log(
                f"[SYSTEM] Strategy {strategy_id} disabled — skipping"
            )
            continue

        write_audit_log(f"[SYSTEM] Initializing strategy {strategy_id}")

        # Broker executor via factory
        strategy_executor = get_executor_for_broker(cfg["broker"])

        # Create trade slots
        for slot_name in cfg["slots"]:
            TradeStateManager(
                strategy_id=strategy_id,
                name=slot_name,
                executor=strategy_executor,
                state_file=STATE_DIR / f"{strategy_id}_{slot_name}.json",
                price_provider=None,
            )

        # Start runtime (selection + recon)
        StrategyRuntimeManager.start(strategy_id, zerodha_manager)

        write_audit_log(
            f"[SYSTEM] Strategy {strategy_id} runtime started"
        )

    # 7️⃣ Recovery
    recover_trades_from_zerodha()

    # 8️⃣ Exit engine
    start_exit_engine(broker)

    # 9️⃣ Zerodha bootstrap (best effort)
    if zerodha_manager.is_trade_ready():

        # Prefer DATA kite for pivots
        kite = (
            zerodha_manager.get_data_kite()
            or zerodha_manager.get_trade_kite()
        )

        if kite:
            ensure_instruments_dump(kite.api_key, kite.access_token)
            load_index_prev_close_once(kite)
            seed_index_ltp_once(kite)

            # 🔐 Initialize PivotCache with live broker session
            PivotCache.initialize(kite)
            write_audit_log("[PIVOT] PivotCache initialized with Zerodha session")

            write_audit_log("[ZERODHA] Instruments + index state loaded")


    # 🔟 Broker reconciliation thread
    threading.Thread(
        target=BrokerReconciliationJob(
            get_executor_for_broker("ZERODHA")
        ).run_forever,
        daemon=True,
    ).start()

    # PAPER EOD Scheduler
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        paper_trade_eod_job,
        trigger="cron",
        hour=15,
        minute=25,
        id="paper_trade_eod_squareoff",
        replace_existing=True,
    )
    scheduler.start()

    write_audit_log("[SYSTEM] Paper trade EOD scheduler started")

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
        host="127.0.0.1",
        port=SCALP_PORT,
        log_level="info",
        access_log=False,
    )

from fastapi import FastAPI
import asyncio
import threading
from pathlib import Path
import os

from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

# --------------------------------------------------
# RUNTIME ENV (STEP A2)
# --------------------------------------------------

SCALP_ENV = os.environ.get("SCALP_ENV", "dev")
SCALP_PORT = int(os.environ.get("SCALP_PORT", "8000"))

# --------------------------------------------------
# LICENSE (IMPORT ONLY — NO STATE COPIES)
# --------------------------------------------------

from app.license.machine_id import get_machine_id
from app.license.license_validator import validate_license
from app.license.license_state import LicenseStatus
from app.license import license_state
from app.event_bus.audit_logger import write_audit_log

# --------------------------------------------------
# APP PATHS
# --------------------------------------------------

from app.utils.app_paths import (
    ensure_app_dirs,
    export_env,
    STATE_DIR,
)

# --------------------------------------------------
# API ROUTES
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

from app.engine.selection_engine import selection_loop
from app.engine.exit_boot import start_exit_engine
from app.engine.startup_reconciliation import StartupReconciliation
from app.engine.broker_reconciliation import BrokerReconciliationJob

# --------------------------------------------------
# TRADING
# --------------------------------------------------

from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.trading.trade_state_manager import TradeStateManager
from app.trading.recovery import recover_trades_from_zerodha
from app.trading.gtt_reconciler import gtt_reconciliation_loop

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
# ROUTERS
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
# CORS (DESKTOP SAFE)
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
executor = ZerodhaOrderExecutor(zerodha_manager)
broker = ZerodhaBroker(zerodha_manager)

write_audit_log("[SYSTEM] LIVE TRADING MODE")

# --------------------------------------------------
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():
    write_audit_log("[SYSTEM] Backend startup initiated")

    # 0️⃣ APP HOME
    ensure_app_dirs()
    export_env()
    write_audit_log("[SYSTEM] App directories ensured")

    # 🔑 LICENSE CHECK (ONCE, AUTHORITATIVE)
    get_machine_id()
    validate_license()
    write_audit_log(
        f"[LICENSE] Startup status = {license_state.LICENSE_STATUS}"
    )

    # 1️⃣ DB
    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    # 2️⃣ LOG HOUSEKEEPING
    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    # 3️⃣ DB HOUSEKEEPING
    run_housekeeping()
    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping started")

    # 4️⃣ STATE DIR
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # 5️⃣ STARTUP RECON
    StartupReconciliation(broker).run()

    # 6️⃣ TRADE SLOTS
    TradeStateManager("CE_1", executor, STATE_DIR / "CE_1.json", None)
    TradeStateManager("CE_2", executor, STATE_DIR / "CE_2.json", None)
    TradeStateManager("PE_1", executor, STATE_DIR / "PE_1.json", None)
    TradeStateManager("PE_2", executor, STATE_DIR / "PE_2.json", None)
    write_audit_log("[SYSTEM] Trade slots initialized")

    # 7️⃣ RECOVERY
    recover_trades_from_zerodha()

    # 8️⃣ EXIT ENGINE
    start_exit_engine(broker)

    # 9️⃣ GTT RECON
    asyncio.create_task(gtt_reconciliation_loop())
    write_audit_log("[SYSTEM] GTT reconciliation loop started")

    # 🔟 ZERODHA DATA
    if zerodha_manager.is_ready():
        kite = zerodha_manager.get_kite()
        ensure_instruments_dump(kite.api_key, kite.access_token)
        load_index_prev_close_once(kite)
        seed_index_ltp_once(kite)
        write_audit_log("[ZERODHA] Instruments + index state loaded")

    # --------------------------------------------------
    # 🔒 LICENSE GATE — ENGINE
    # --------------------------------------------------

    if license_state.LICENSE_STATUS != LicenseStatus.VALID:
        write_audit_log(
            f"[ENGINE] License not valid ({license_state.LICENSE_STATUS}) — engine not started"
        )
        return

    # ✅ START BROKER RECON ONLY AFTER LICENSE + INIT
    threading.Thread(
        target=BrokerReconciliationJob(executor).run_forever,
        daemon=True,
    ).start()

    asyncio.create_task(selection_loop(zerodha_manager))
    write_audit_log("[SYSTEM] Selection engine started")

    # PAPER EOD
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
# ENTRYPOINT (STEP A2 — DESKTOP MODE)
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    write_audit_log(
        f"[SYSTEM] Starting backend via embedded Python (env={SCALP_ENV}, port={SCALP_PORT})"
    )

    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=SCALP_PORT,
        log_level="info",
        access_log=False,
    )

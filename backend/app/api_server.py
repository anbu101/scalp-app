from fastapi import FastAPI
import asyncio
import threading
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from app.api.health_routes import router as health_router


# --------------------------------------------------
# APP PATHS (SINGLE SOURCE OF TRUTH)
# --------------------------------------------------

from app.utils.app_paths import (
    ensure_app_dirs,
    export_env,
    STATE_DIR,
)

# --------------------------------------------------
# API ROUTES
# --------------------------------------------------

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

from app.marketdata.market_indices_state import MarketIndicesState

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

from app.event_bus.audit_logger import write_audit_log
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
# ROUTERS (EARLY)
# --------------------------------------------------

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
# BACKGROUND THREADS (SAFE TO START EARLY)
# --------------------------------------------------

threading.Thread(
    target=BrokerReconciliationJob(executor).run_forever,
    daemon=True,
).start()

# --------------------------------------------------
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():
    write_audit_log("[SYSTEM] Backend startup initiated")

    # --------------------------------------------------
    # 0️⃣ APP HOME INIT (~/.scalp-app)
    # --------------------------------------------------
    ensure_app_dirs()
    export_env()
    write_audit_log("[SYSTEM] App directories ensured")

    # --------------------------------------------------
    # 1️⃣ DB INIT + MIGRATIONS (NON-DESTRUCTIVE)
    # --------------------------------------------------
    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    # --------------------------------------------------
    # 2️⃣ LOG HOUSEKEEPING
    # --------------------------------------------------
    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    # --------------------------------------------------
    # 3️⃣ DB HOUSEKEEPING
    # --------------------------------------------------
    run_housekeeping()
    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping started")

    # --------------------------------------------------
    # 4️⃣ STATE DIR (FIXED — NO HARDCODED PATHS)
    # --------------------------------------------------
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # --------------------------------------------------
    # 5️⃣ STARTUP RECONCILIATION
    # --------------------------------------------------
    StartupReconciliation(broker).run()

    # --------------------------------------------------
    # 6️⃣ TRADE SLOTS (PERSISTED)
    # --------------------------------------------------
    TradeStateManager("CE_1", executor, STATE_DIR / "CE_1.json", None)
    TradeStateManager("CE_2", executor, STATE_DIR / "CE_2.json", None)
    TradeStateManager("PE_1", executor, STATE_DIR / "PE_1.json", None)
    TradeStateManager("PE_2", executor, STATE_DIR / "PE_2.json", None)

    write_audit_log("[SYSTEM] Trade slots initialized")

    # --------------------------------------------------
    # 7️⃣ RECOVERY
    # --------------------------------------------------
    recover_trades_from_zerodha()

    # --------------------------------------------------
    # 8️⃣ EXIT ENGINE
    # --------------------------------------------------
    start_exit_engine(broker)

    # --------------------------------------------------
    # 9️⃣ GTT RECONCILIATION
    # --------------------------------------------------
    asyncio.create_task(gtt_reconciliation_loop())
    write_audit_log("[SYSTEM] GTT reconciliation loop started")

    # --------------------------------------------------
    # 🔟 INSTRUMENTS + INDEX PREV CLOSE
    # --------------------------------------------------
    if zerodha_manager.is_ready():
        kite = zerodha_manager.get_kite()

        ensure_instruments_dump(kite)
        load_index_prev_close_once(kite)
        seed_index_ltp_once(kite)

        write_audit_log("[ZERODHA] Instruments + index state loaded")

    # --------------------------------------------------
    # 1️⃣1️⃣ SELECTION ENGINE
    # --------------------------------------------------
    asyncio.create_task(selection_loop(zerodha_manager))
    write_audit_log("[SYSTEM] Selection engine started")

    # --------------------------------------------------
    # STATUS
    # --------------------------------------------------
    if zerodha_manager.is_ready():
        write_audit_log("[ZERODHA] Broker READY")
    else:
        write_audit_log("[ZERODHA] Broker NOT READY (login required)")

    # --------------------------------------------------
    # 🕒 PAPER TRADE EOD SQUARE-OFF
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
    scheduler.start()

    write_audit_log("[SYSTEM] Paper trade EOD scheduler started")

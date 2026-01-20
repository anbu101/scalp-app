from fastapi import FastAPI
import asyncio
from pathlib import Path
import threading

from fastapi.middleware.cors import CORSMiddleware

# ---------------- API ROUTES ----------------

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

from apscheduler.schedulers.background import BackgroundScheduler
from app.jobs.paper_trade_eod import paper_trade_eod_job


# 🔹 INDEX PREV CLOSE
from app.marketdata.load_index_prev_close import (
    load_index_prev_close_once,
    seed_index_ltp_once,
)

# ---------------- CORE ENGINE ----------------

from app.engine.selection_engine import selection_loop
from app.engine.exit_boot import start_exit_engine
from app.engine.startup_reconciliation import StartupReconciliation
from app.engine.broker_reconciliation import BrokerReconciliationJob

# ---------------- TRADING ----------------

from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.trading.trade_state_manager import TradeStateManager
from app.trading.recovery import recover_trades_from_zerodha
from app.trading.gtt_reconciler import gtt_reconciliation_loop

# ---------------- BROKER ----------------

from app.brokers.zerodha_broker import ZerodhaBroker
from app.brokers.zerodha_manager import ZerodhaManager

# ---------------- DB ----------------

from app.db.sqlite import init_db
from app.db.migrations.runner import run_migrations

# 🔹 DB housekeeping
from app.db.housekeeping import run_housekeeping, housekeeping_loop

# 🔹 LOG housekeeping (DO NOT TOUCH)
from app.utils.housekeeping import run_housekeeping as run_log_housekeeping

# ---------------- UTILS ----------------

from app.event_bus.audit_logger import write_audit_log

# 🔹 MARKET INDICES STATE
from app.marketdata.market_indices_state import MarketIndicesState

# 🔹 INSTRUMENTS DUMP (CRITICAL)
from app.fetcher.zerodha_instruments import ensure_instruments_dump


# --------------------------------------------------
# APP
# --------------------------------------------------

app = FastAPI(title="Scalp App Backend")

app.include_router(log_router)
app.include_router(config_router)
app.include_router(debug_router)
app.include_router(debug_ui_router)
app.include_router(market_indices_router)
app.include_router(paper_trades_router)


# --------------------------------------------------
# CORE SINGLETONS
# --------------------------------------------------

zerodha_manager = ZerodhaManager()
executor = ZerodhaOrderExecutor(zerodha_manager)
broker = ZerodhaBroker(zerodha_manager)

write_audit_log("[SYSTEM] LIVE TRADING MODE")

# --------------------------------------------------
# BACKGROUND THREADS
# --------------------------------------------------

threading.Thread(
    target=BrokerReconciliationJob(executor).run_forever,
    daemon=True,
).start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------

app.include_router(status_router)
app.include_router(selection_router)
app.include_router(strategy_router)
app.include_router(zerodha_router)
app.include_router(trade_state_router)
app.include_router(trade_history_router)
app.include_router(positions_router)
app.include_router(signal_router)
app.include_router(ltp_router)

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
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def on_startup():
    write_audit_log("[SYSTEM] Backend started")

    # --------------------------------------------------
    # 0️⃣ DB INIT + MIGRATIONS
    # --------------------------------------------------
    conn = init_db()
    run_migrations(conn)
    write_audit_log("[DB] Migrations completed")

    # --------------------------------------------------
    # 1️⃣ LOG HOUSEKEEPING
    # --------------------------------------------------
    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    # --------------------------------------------------
    # 2️⃣ DB HOUSEKEEPING
    # --------------------------------------------------
    run_housekeeping()
    write_audit_log("[SYSTEM] DB housekeeping completed")

    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping loop started")

    # --------------------------------------------------
    # 3️⃣ STATE DIR
    # --------------------------------------------------
    state_dir = Path("/app/app/state")
    state_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # 4️⃣ STARTUP RECONCILIATION
    # --------------------------------------------------
    StartupReconciliation(broker).run()

    # --------------------------------------------------
    # 5️⃣ TRADE SLOTS
    # --------------------------------------------------
    TradeStateManager("CE_1", executor, state_dir / "CE_1.json", None)
    TradeStateManager("CE_2", executor, state_dir / "CE_2.json", None)
    TradeStateManager("PE_1", executor, state_dir / "PE_1.json", None)
    TradeStateManager("PE_2", executor, state_dir / "PE_2.json", None)

    write_audit_log("[SYSTEM] Trade slots initialized")

    # --------------------------------------------------
    # 6️⃣ RECOVERY
    # --------------------------------------------------
    recover_trades_from_zerodha()

    # --------------------------------------------------
    # 7️⃣ EXIT ENGINE
    # --------------------------------------------------
    start_exit_engine(broker)

    # --------------------------------------------------
    # 8️⃣ GTT RECONCILIATION
    # --------------------------------------------------
    asyncio.create_task(gtt_reconciliation_loop())
    write_audit_log("[SYSTEM] GTT reconciliation loop started")

    # --------------------------------------------------
    # 9️⃣ INSTRUMENTS DUMP + INDEX PREV CLOSE (ONCE)
    # --------------------------------------------------
    if zerodha_manager.is_ready():
        kite = zerodha_manager.get_kite()

        ensure_instruments_dump(kite)
        write_audit_log("[ZERODHA] Instruments dump ensured")

        load_index_prev_close_once(kite)
        seed_index_ltp_once(kite)

        write_audit_log("[INDEX] Prev close + LTP seeded at startup")


    # --------------------------------------------------
    # 🔟 SELECTION ENGINE
    # --------------------------------------------------
    asyncio.create_task(selection_loop(zerodha_manager))
    write_audit_log("[SYSTEM] Selection engine started")

    # --------------------------------------------------
    # STATUS
    # --------------------------------------------------
    if zerodha_manager.is_ready():
        write_audit_log("[ZERODHA] Broker READY (token valid)")
    else:
        write_audit_log("[ZERODHA] Broker NOT READY (login required)")

    # --------------------------------------------------
    # 🕒 PAPER TRADE EOD SQUARE-OFF (15:25 IST)
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

    write_audit_log("[SYSTEM] Paper trade EOD scheduler started (15:25 IST)")

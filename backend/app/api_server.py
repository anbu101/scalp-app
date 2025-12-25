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

# 🔹 DB housekeeping (NEW, SEPARATE)
from app.db.housekeeping import (
    run_housekeeping,
    housekeeping_loop,
)

# 🔹 LOG housekeeping (EXISTING – DO NOT TOUCH)
from app.utils.housekeeping import run_housekeeping as run_log_housekeeping

# ---------------- UTILS ----------------

from app.fetcher.zerodha_instruments import ensure_instruments_dump
from app.event_bus.audit_logger import write_audit_log

# --------------------------------------------------
# APP
# --------------------------------------------------

app = FastAPI(title="Scalp App Backend")

conn = init_db()
run_migrations(conn)

app.include_router(log_router)
app.include_router(config_router)
app.include_router(debug_router)
app.include_router(debug_ui_router)

# --------------------------------------------------
# CORE SINGLETONS
# --------------------------------------------------

zerodha_manager = ZerodhaManager()
executor = ZerodhaOrderExecutor(zerodha_manager)
broker = ZerodhaBroker(zerodha_manager)

if zerodha_manager.is_ready():
    ensure_instruments_dump(zerodha_manager.get_kite())

write_audit_log("[SYSTEM] LIVE TRADING MODE")

# --------------------------------------------------
# BACKGROUND THREADS
# --------------------------------------------------

threading.Thread(
    target=BrokerReconciliationJob(executor).run_forever,
    daemon=True,
).start()

# --------------------------------------------------
# PRICE PROVIDER
# --------------------------------------------------

class ZerodhaPriceProvider:
    def __init__(self, manager: ZerodhaManager):
        self.manager = manager

    def get_ltp(self, symbol: str):
        if not self.manager.is_ready():
            return None
        try:
            kite = self.manager.get_kite()
            data = kite.ltp([symbol])
            return data[symbol]["last_price"]
        except Exception:
            return None


price_provider = ZerodhaPriceProvider(zerodha_manager)

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
    # 0️⃣ LOG HOUSEKEEPING (EXISTING)
    # --------------------------------------------------
    run_log_housekeeping()
    write_audit_log("[SYSTEM] Log housekeeping completed")

    # --------------------------------------------------
    # 1️⃣ DB HOUSEKEEPING (NEW)
    # --------------------------------------------------
    run_housekeeping()
    write_audit_log("[SYSTEM] DB housekeeping completed")

    asyncio.create_task(housekeeping_loop())
    write_audit_log("[SYSTEM] DB housekeeping loop started")

    # --------------------------------------------------
    # 2️⃣ STATE DIR
    # --------------------------------------------------
    state_dir = Path("/app/app/state")
    state_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # 3️⃣ STARTUP RECONCILIATION
    # --------------------------------------------------
    StartupReconciliation(broker).run()

    # --------------------------------------------------
    # 4️⃣ TRADE SLOTS
    # --------------------------------------------------
    TradeStateManager("CE_1", executor, state_dir / "CE_1.json", price_provider)
    TradeStateManager("CE_2", executor, state_dir / "CE_2.json", price_provider)
    TradeStateManager("PE_1", executor, state_dir / "PE_1.json", price_provider)
    TradeStateManager("PE_2", executor, state_dir / "PE_2.json", price_provider)

    write_audit_log("[SYSTEM] Trade slots initialized")

    # --------------------------------------------------
    # 5️⃣ RECOVERY
    # --------------------------------------------------
    recover_trades_from_zerodha()

    # --------------------------------------------------
    # 6️⃣ EXIT ENGINE
    # --------------------------------------------------
    start_exit_engine(broker)

    # --------------------------------------------------
    # 7️⃣ GTT RECONCILIATION
    # --------------------------------------------------
    asyncio.create_task(gtt_reconciliation_loop())
    write_audit_log("[SYSTEM] GTT reconciliation loop started")

    # --------------------------------------------------
    # 8️⃣ SELECTION ENGINE
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

import os
os.environ["DB_PATH"] = "/data/app.db"

from datetime import datetime, timedelta
import pytz
import uuid

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

from app.backtest.inside_candle_engine import InsideCandleEngine
from app.backtest.exit_simulator import ExitSimulator

IST = pytz.timezone("Asia/Kolkata")

# -----------------------------
# CONFIG
# -----------------------------
STRATEGY = "INSIDE_CANDLE"
DAYS = 30

# -----------------------------
# BACKTEST RUN ENTRY
# -----------------------------
conn = get_conn()
cur = conn.cursor()

now = datetime.now(IST)
start = now - timedelta(days=DAYS)

backtest_run_id = str(uuid.uuid4())

cur.execute(
    """
    INSERT INTO backtest_runs
    (backtest_run_id, strategy_name, start_ts, end_ts, created_at)
    VALUES (?, ?, ?, ?, ?)
    """,
    (
        backtest_run_id,
        STRATEGY,
        int(start.timestamp()),
        int(now.timestamp()),
        int(now.timestamp()),
    )
)
conn.commit()

write_audit_log(
    f"[BACKTEST][RUN] {STRATEGY} run_id={backtest_run_id}"
)

# -----------------------------
# STEP 1–3 : ENTRY ENGINE
# -----------------------------
engine = InsideCandleEngine(backtest_run_id)

engine.run(
    start_ts=int(start.timestamp()),
    end_ts=int(now.timestamp())
)

# -----------------------------
# STEP 4 : EXIT SIMULATION
# -----------------------------
ExitSimulator().run()

write_audit_log(
    f"[BACKTEST][DONE] {STRATEGY} run_id={backtest_run_id}"
)

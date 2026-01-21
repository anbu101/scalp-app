# backend/app/backtest/export_backtest_trades.py

import csv
from datetime import datetime
from pathlib import Path

from app.db.sqlite import get_conn
from app.utils.app_paths import DATA_DIR, ensure_app_dirs


def split_ts(ts):
    if not ts:
        return "", ""
    dt = datetime.fromtimestamp(ts)
    return dt.date().isoformat(), dt.time().strftime("%H:%M:%S")


# --------------------------------------------------
# INIT PATHS
# --------------------------------------------------

ensure_app_dirs()

EXPORT_FILE = DATA_DIR / "inside_candle_backtest.csv"

# --------------------------------------------------
# DB
# --------------------------------------------------

conn = get_conn()
cur = conn.cursor()

rows = cur.execute(
    """
    SELECT
        backtest_run_id,
        symbol,
        atm_slot,
        side,
        entry_time,
        entry_price,
        sl_price,
        tp_price,
        exit_time,
        exit_price,
        exit_reason,
        sl_tp_same_candle
    FROM backtest_trades
    ORDER BY entry_time
    """
).fetchall()

# --------------------------------------------------
# EXPORT
# --------------------------------------------------

with EXPORT_FILE.open("w", newline="") as f:
    writer = csv.writer(f)

    writer.writerow([
        "backtest_run_id",
        "symbol",
        "atm_slot",
        "side",
        "entry_date",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "exit_date",
        "exit_time",
        "exit_price",
        "exit_reason",
        "sl_tp_same_candle",
    ])

    for r in rows:
        entry_date, entry_time = split_ts(r[4])
        exit_date, exit_time = split_ts(r[8])

        writer.writerow([
            r[0],
            r[1],
            r[2],
            r[3],
            entry_date,
            entry_time,
            r[5],
            r[6],
            r[7],
            exit_date,
            exit_time,
            r[9],
            r[10],
            r[11],
        ])

print(f"Exported {EXPORT_FILE}")

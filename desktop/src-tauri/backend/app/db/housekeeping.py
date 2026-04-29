# backend/app/db/housekeeping.py

import asyncio
import time
import sqlite3
from datetime import datetime, timedelta, date

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# --------------------------------------------------
# RETENTION POLICY
# --------------------------------------------------

# market_timeline: many rows per day (1 per candle per symbol), keep short
MARKET_TIMELINE_KEEP_DAYS = 10

# futures_candles: OHLC + indicator rows, needed for backtesting
# 365 days is safe — rows are compact (~200 bytes each).
# At 3m timeframe: ~110 candles/day × 365 = ~40K rows/year. Negligible.
FUTURES_CANDLES_KEEP_DAYS = 365

# closed trades: keep for P&L auditing
TRADES_KEEP_DAYS = 1000


async def housekeeping_loop():
    await asyncio.sleep(30)  # allow app startup

    while True:
        try:
            run_housekeeping()
        except Exception as e:
            write_audit_log(f"[HOUSEKEEPING][ERROR] {e}")

        await asyncio.sleep(600)  # every 10 minutes


def run_housekeeping():
    try:
        conn = get_conn()
        now = int(time.time())

        # -----------------------------
        # 1️⃣ market_timeline cleanup (10 days)
        # -----------------------------
        mt_cutoff_date = date.today() - timedelta(days=MARKET_TIMELINE_KEEP_DAYS)
        mt_cutoff_ts = int(datetime.combine(
            mt_cutoff_date,
            datetime.min.time()
        ).timestamp())

        cur1 = conn.execute(
            "DELETE FROM market_timeline WHERE ts < ?",
            (mt_cutoff_ts,),
        )

        # -----------------------------
        # 2️⃣ futures_candles cleanup (365 days)
        # Daily 1d candles and intraday 3m candles are both kept.
        # This preserves full historical data for backtesting.
        # -----------------------------
        fut_cutoff_date = date.today() - timedelta(days=FUTURES_CANDLES_KEEP_DAYS)
        fut_cutoff_ts = int(datetime.combine(
            fut_cutoff_date,
            datetime.min.time()
        ).timestamp())

        cur_fut = conn.execute(
            "DELETE FROM futures_candles WHERE ts < ?",
            (fut_cutoff_ts,),
        )

        # -----------------------------
        # 3️⃣ trades cleanup (closed only)
        # -----------------------------
        trades_cutoff = now - (TRADES_KEEP_DAYS * 86400)

        cur2 = conn.execute(
            """
            DELETE FROM trades
            WHERE exit_time IS NOT NULL
            AND exit_time < ?
            """,
            (trades_cutoff,),
        )

        conn.commit()

        if cur1.rowcount or cur2.rowcount or cur_fut.rowcount:
            write_audit_log(
                f"[HOUSEKEEPING] "
                f"market_timeline={cur1.rowcount} "
                f"futures_candles={cur_fut.rowcount} "
                f"trades={cur2.rowcount}"
            )

    except sqlite3.DatabaseError as e:
        write_audit_log(
            f"[HOUSEKEEPING][ERROR] Database error (skipping): {e}"
        )
        return

    except Exception as e:
        write_audit_log(f"[HOUSEKEEPING][ERROR] {e}")
        raise
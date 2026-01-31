import asyncio
import time
import sqlite3  
from datetime import datetime, timedelta, date

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


MARKET_TIMELINE_KEEP_DAYS = 8     #8 days only
TRADES_KEEP_DAYS = 90              # closed trades


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
        # market_timeline cleanup
        # -----------------------------
        cutoff_date = date.today() - timedelta(days=MARKET_TIMELINE_KEEP_DAYS)
        cutoff_ts = int(datetime.combine(cutoff_date, datetime.min.time()).timestamp())

        cur1 = conn.execute(
            "DELETE FROM market_timeline WHERE ts < ?",
            (cutoff_ts,),
        )

        # -----------------------------
        # trades cleanup (closed only)
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

        if cur1.rowcount or cur2.rowcount:
            write_audit_log(
                f"[HOUSEKEEPING] "
                f"market_timeline={cur1.rowcount} "
                f"trades={cur2.rowcount}"
            )
    except sqlite3.DatabaseError as e:
        write_audit_log(f"[HOUSEKEEPING][ERROR] Database error (skipping): {e}")
        return  # Skip housekeeping if DB is corrupted
    except Exception as e:
        write_audit_log(f"[HOUSEKEEPING][ERROR] {e}")
        raise

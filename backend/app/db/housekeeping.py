# backend/app/db/housekeeping.py

import asyncio
import time
import sqlite3
from datetime import datetime, timedelta, date

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# ============================================================
# RETENTION CONFIG
# ============================================================

MARKET_TIMELINE_KEEP_DAYS = 10
FUTURES_CANDLES_KEEP_DAYS = 365
HA_CANDLES_KEEP_DAYS      = 10

# IMPORTANT:
# Trade history is extremely valuable for:
# - analytics
# - debugging
# - tax/audit
# - strategy improvements
# - backtesting comparisons
#
# SQLite can easily handle years of trade history.
# So by default we NEVER auto-delete trades.
#
ENABLE_TRADES_CLEANUP = False

# Only used if ENABLE_TRADES_CLEANUP = True
TRADES_KEEP_DAYS = 36500   # ~100 years


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
        now  = int(time.time())

        # =====================================================
        # LOG DATABASE PATH (VERY USEFUL FOR DEBUGGING)
        # =====================================================

        try:
            db_path = conn.execute(
                "PRAGMA database_list"
            ).fetchone()[2]

            write_audit_log(f"[HOUSEKEEPING] DB_PATH={db_path}")

        except Exception:
            pass

        # =====================================================
        # 1. market_timeline cleanup
        # =====================================================

        cutoff_date = date.today() - timedelta(
            days=MARKET_TIMELINE_KEEP_DAYS
        )

        cutoff_ts = int(
            datetime.combine(
                cutoff_date,
                datetime.min.time()
            ).timestamp()
        )

        cur_market = conn.execute(
            """
            DELETE FROM market_timeline
            WHERE ts < ?
            """,
            (cutoff_ts,),
        )

        # =====================================================
        # 2. futures_candles cleanup
        # =====================================================

        futures_cutoff_date = date.today() - timedelta(
            days=FUTURES_CANDLES_KEEP_DAYS
        )

        futures_cutoff_ts = int(
            datetime.combine(
                futures_cutoff_date,
                datetime.min.time()
            ).timestamp()
        )

        cur_futures = conn.execute(
            """
            DELETE FROM futures_candles
            WHERE ts < ?
            """,
            (futures_cutoff_ts,),
        )

        # =====================================================
        # 3. ha_candles cleanup
        # =====================================================

        ha_cutoff_date = date.today() - timedelta(
            days=HA_CANDLES_KEEP_DAYS
        )

        ha_cutoff_ts = int(
            datetime.combine(
                ha_cutoff_date,
                datetime.min.time()
            ).timestamp()
        )

        cur_ha = conn.execute(
            """
            DELETE FROM ha_candles
            WHERE ts < ?
            """,
            (ha_cutoff_ts,),
        )

        # =====================================================
        # 4. trades cleanup (DISABLED BY DEFAULT)
        # =====================================================

        if ENABLE_TRADES_CLEANUP:

            trades_cutoff = now - (
                TRADES_KEEP_DAYS * 86400
            )

            cur_trades = conn.execute(
                """
                DELETE FROM trades
                WHERE exit_time IS NOT NULL
                  AND exit_time < ?
                """,
                (trades_cutoff,),
            )

        else:

            class DummyCursor:
                rowcount = 0

            cur_trades = DummyCursor()

        # =====================================================
        # COMMIT
        # =====================================================

        conn.commit()

        # =====================================================
        # LOG RESULTS
        # =====================================================

        if (
            cur_market.rowcount
            or cur_futures.rowcount
            or cur_ha.rowcount
            or cur_trades.rowcount
        ):

            write_audit_log(
                f"[HOUSEKEEPING] "
                f"market_timeline={cur_market.rowcount} "
                f"futures_candles={cur_futures.rowcount} "
                f"ha_candles={cur_ha.rowcount} "
                f"trades={cur_trades.rowcount}"
            )

    except sqlite3.DatabaseError as e:

        write_audit_log(
            f"[HOUSEKEEPING][ERROR] Database error (skipping): {e}"
        )

        return

    except Exception as e:

        write_audit_log(
            f"[HOUSEKEEPING][ERROR] {e}"
        )

        raise
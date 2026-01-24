import uuid
import time
from app.db.sqlite import get_conn
from app.db.db_lock import DB_LOCK


def is_slot_locked(symbol: str, atm_slot: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()

    row = cur.execute(
        """
        SELECT 1
        FROM backtest_trades
        WHERE symbol = ?
          AND atm_slot = ?
          AND state = 'OPEN'
        LIMIT 1
        """,
        (symbol, atm_slot),
    ).fetchone()

    return row is not None


def insert_backtest_trade(
    backtest_run_id: str,
    strategy_name: str,
    opt: dict,
    atm_slot: int,
    prices: dict,
    candle_ts: int,
):
    conn = get_conn()
    cur = conn.cursor()

    trade_id = str(uuid.uuid4())
    now_ts = int(time.time())

    with DB_LOCK:
        cur.execute(
            """
            INSERT INTO backtest_trades (
                backtest_trade_id,
                backtest_run_id,
                strategy_name,
                symbol,
                token,
                side,
                atm_slot,
                entry_time,
                entry_price,
                candle_ts,
                sl_price,
                tp_price,
                rr,
                lots,
                lot_size,
                qty,
                state,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                backtest_run_id,
                strategy_name,
                opt["symbol"],
                opt["token"],
                opt["option_type"],
                atm_slot,
                candle_ts,
                prices["entry_price"],
                candle_ts,
                prices["sl_price"],
                prices["tp_price"],
                prices["rr"],
                opt["lots"],
                opt["lot_size"],
                opt["qty"],
                "OPEN",
                now_ts,
            ),
        )

        conn.commit()

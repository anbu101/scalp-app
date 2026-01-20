import uuid
import json
from datetime import datetime

RR = 2
LOT_SIZE = 50
LOTS = 1


def insert_cpr_e21_trade(conn, run_id, signal, opt, sl, tp, signal_meta):
    """
    Insert CPR_E21 backtest trade with full signal metadata
    """

    cur = conn.cursor()
    trade_id = str(uuid.uuid4())

    qty = LOTS * LOT_SIZE
    now_ts = int(datetime.now().timestamp())

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

            signal_meta,
            state,
            created_at
        )
        VALUES (?, ?, 'CPR_E21',
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, 'OPEN', ?)
        """,
        (
            trade_id,
            run_id,

            opt["symbol"],
            0,                     # token not required for backtest
            opt["option_type"],
            0,                     # atm_slot unused (price-based)

            signal["ts"],
            opt["price"],
            signal["ts"],

            sl,
            tp,
            RR,

            LOTS,
            LOT_SIZE,
            qty,

            json.dumps(signal_meta),
            now_ts,
        ),
    )

    conn.commit()
    return trade_id

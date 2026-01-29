from fastapi import APIRouter
from typing import List, Dict, Any
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["paper-trades"])


@router.get("/paper_trades")
def get_paper_trades():
    """
    📄 Paper Trades – UI API

    - Returns OPEN and CLOSED separately
    - Includes Zerodha option charges + net PnL
    - Matches frontend contract
    """

    conn = get_conn()

    try:
        cur = conn.execute(
            """
            SELECT
                paper_trade_id,
                strategy_name,
                trade_mode,
                symbol,
                token,
                side,

                entry_time,
                entry_price,
                candle_ts,

                sl_price,
                tp_price,
                rr,

                lots,
                lot_size,
                qty,

                exit_time,
                exit_price,
                exit_reason,

                pnl_points,
                pnl_value,

                brokerage,
                stt,
                exchange_charges,
                sebi_charges,
                stamp_duty,
                gst,
                total_charges,
                net_pnl,

                state,
                created_at
            FROM paper_trades
            ORDER BY entry_time DESC
            """
        )

        rows = cur.fetchall()

        open_trades: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []

        for r in rows:
            trade = dict(r)

            if trade["state"] == "OPEN":
                open_trades.append(trade)
            else:
                closed_trades.append(trade)


        return {
            "open": open_trades,
            "closed": closed_trades,
        }

    except Exception as e:
        write_audit_log(
            f"[API][PAPER_TRADES][ERROR] {repr(e)}"
        )
        return {
            "open": [],
            "closed": [],
            "error": str(e),
        }

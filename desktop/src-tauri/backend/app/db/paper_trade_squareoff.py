from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_provider import get_ltp_for_token

EXIT_REASON_EOD = "EOD_SQUARE_OFF"


def square_off_open_paper_trades():
    """
    Force-close all OPEN paper trades at EOD.
    Safe to run multiple times.
    Uses LTP provider; handles market-closed scenario gracefully.
    """

    conn = get_conn()

    rows = conn.execute(
        """
        SELECT
            paper_trade_id,
            token,
            entry_price,
            qty
        FROM paper_trades
        WHERE state = 'OPEN'
        """
    ).fetchall()

    if not rows:
        write_audit_log("[EOD][PAPER] No open trades to square off")
        return

    write_audit_log(
        f"[EOD][PAPER] Squaring off {len(rows)} open trades"
    )

    closed_count = 0
    skipped_count = 0

    for r in rows:
        trade_id = r["paper_trade_id"]
        token = r["token"]
        entry_price = r["entry_price"]
        qty = r["qty"]

        # --- LTP RESOLUTION ---
        ltp = get_ltp_for_token(token)

        # Market is closed / WS not running / no tick available
        ltp = get_ltp_for_token(token)

        if ltp is None:
            ltp = entry_price  # EOD fallback
            write_audit_log(
                f"[EOD][PAPER][INFO] No LTP for token={token}. "
                f"Using entry price {entry_price} for EOD square-off."
            )


        conn.execute(
            """
            UPDATE paper_trades
            SET
                exit_price = ?,
                exit_time = strftime('%s','now'),
                exit_reason = ?,
                state = 'CLOSED'
            WHERE paper_trade_id = ?
              AND state = 'OPEN'
            """,
            (
                float(ltp),
                EXIT_REASON_EOD,
                trade_id,
            ),
        )

        closed_count += 1

        write_audit_log(
            f"[EOD][PAPER] Trade {trade_id} CLOSED @ {ltp} qty={qty}"
        )

    conn.commit()

    write_audit_log(
        f"[EOD][PAPER] Square-off completed | closed={closed_count}, skipped={skipped_count}"
    )

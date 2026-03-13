from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.db.paper_trades_repo import close_paper_trade

EXIT_REASON_EOD = "EOD_SQUARE_OFF"


def square_off_open_paper_trades():
    """
    Force-close all OPEN paper trades at EOD.
    Safe to run multiple times.

    FIX 1: Use LTPStore.get(symbol) instead of get_ltp_for_token(token).
            bb_tick_engine.on_tick() populates LTPStore keyed by symbol,
            not OptionTickState. The old provider always returned None,
            falling back to entry_price → gross P&L always 0.

    FIX 2: Call close_paper_trade() instead of raw UPDATE.
            close_paper_trade() calculates Zerodha charges (brokerage,
            STT, GST, exchange fees) and writes pnl_value / net_pnl.
            Raw UPDATE left all charge columns as NULL → "—" in UI.
    """

    conn = get_conn()

    rows = conn.execute(
        """
        SELECT
            paper_trade_id,
            symbol,
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

    # Import here to avoid circular import at module load time
    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception as e:
        write_audit_log(f"[EOD][PAPER][ERROR] Cannot import LTPStore: {e}")
        LTPStore = None

    closed_count = 0
    skipped_count = 0

    for r in rows:
        trade_id   = r["paper_trade_id"]
        symbol     = r["symbol"]
        token      = r["token"]
        entry_price = r["entry_price"]

        # --------------------------------------------------
        # FIX 1: LTP resolution
        # Primary  : LTPStore (populated by bb_tick_engine WS ticks)
        # Secondary: entry_price fallback with a clear warning
        # --------------------------------------------------
        ltp = None

        if LTPStore is not None:
            try:
                ltp = LTPStore.get(symbol)
            except Exception as e:
                write_audit_log(
                    f"[EOD][PAPER][WARN] LTPStore.get failed "
                    f"symbol={symbol} err={e}"
                )

        if ltp is None:
            # Last resort: use entry_price so the trade closes cleanly.
            # This means P&L = 0 but is better than leaving it open.
            # Happens when WS disconnected before squareoff ran.
            ltp = entry_price
            write_audit_log(
                f"[EOD][PAPER][WARN] LTP unavailable for {symbol} "
                f"(token={token}). Using entry_price={entry_price} as fallback. "
                f"P&L will be 0 for this trade."
            )
        else:
            write_audit_log(
                f"[EOD][PAPER] {symbol} LTP={ltp} (from LTPStore)"
            )

        # --------------------------------------------------
        # FIX 2: Use close_paper_trade() so charges are computed
        # --------------------------------------------------
        try:
            close_paper_trade(
                paper_trade_id=trade_id,
                exit_price=float(ltp),
                exit_reason=EXIT_REASON_EOD,
            )
            closed_count += 1
            write_audit_log(
                f"[EOD][PAPER] Trade {trade_id} CLOSED @ {ltp}"
            )
        except Exception as e:
            skipped_count += 1
            write_audit_log(
                f"[EOD][PAPER][ERROR] Failed to close trade_id={trade_id} "
                f"symbol={symbol} err={e}"
            )

    write_audit_log(
        f"[EOD][PAPER] Square-off completed | "
        f"closed={closed_count}, skipped={skipped_count}"
    )
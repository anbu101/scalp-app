from app.event_bus.audit_logger import write_audit_log
from app.db.paper_trade_squareoff import square_off_open_paper_trades
from app.db.paper_trades_reconcile import reconcile_closed_paper_trades


def paper_trade_eod_job():
    """
    End-of-day job for PAPER trades.
    Runs once daily at 15:25 IST.
    """
    write_audit_log("[EOD][PAPER] Job started")

    square_off_open_paper_trades()
    reconcile_closed_paper_trades()

    write_audit_log("[EOD][PAPER] Job finished")

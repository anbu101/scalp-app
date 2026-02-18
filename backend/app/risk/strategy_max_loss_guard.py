from app.db.trades_repo import get_total_pnl_for_strategy
from app.risk.strategy_risk_registry import get_max_loss
from app.event_bus.audit_logger import write_audit_log


def check_strategy_max_loss(strategy_id: str) -> bool:
    """
    Returns True if strategy exceeded its own max loss.
    Fail-safe: if PnL cannot be determined, block trading.
    """

    try:
        max_loss = get_max_loss(strategy_id)
    except Exception as e:
        write_audit_log(
            f"[RISK][ERROR] max_loss fetch failed STRATEGY={strategy_id} ERR={e}"
        )
        return True  # 🔒 Fail closed

    if max_loss is None or max_loss <= 0:
        return False

    try:
        pnl = get_total_pnl_for_strategy(strategy_id)
    except Exception as e:
        write_audit_log(
            f"[RISK][ERROR] pnl fetch failed STRATEGY={strategy_id} ERR={e}"
        )
        return True  # 🔒 Fail closed

    if pnl is None:
        write_audit_log(
            f"[RISK][WARN] pnl is None STRATEGY={strategy_id}"
        )
        return True  # 🔒 Fail closed

    return pnl <= -abs(max_loss)

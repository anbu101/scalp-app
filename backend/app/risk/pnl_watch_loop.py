import asyncio
from app.risk.max_loss_guard import check_max_loss


async def pnl_watch_loop(interval_sec: int = 10):
    """
    Background PnL watcher.
    Periodically checks max-loss condition.
    Safe to run continuously.
    """
    while True:
        try:
            check_max_loss()
        except Exception:
            # Never break loop on errors
            pass

        await asyncio.sleep(interval_sec)

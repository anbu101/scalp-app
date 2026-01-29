from datetime import datetime, time as dtime
import asyncio

from kiteconnect import KiteConnect
from app.brokers.zerodha_auth import load_access_token
from app.config.zerodha_credentials import API_KEY

from app.config.strategy_loader import load_strategy_config
from app.event_bus.log_bus import log_bus

# -------------------------
# Internal state
# -------------------------

_halted_today = False
_last_reset_date = None   # YYYY-MM-DD


# -------------------------
# Helpers
# -------------------------

def _now():
    return datetime.now()


def _should_reset():
    """
    Reset allowed only AFTER 09:15 and only once per calendar day.
    """
    global _last_reset_date

    now = _now()
    today = now.date()

    if now.time() < dtime(9, 15):
        return False

    if _last_reset_date == today:
        return False

    _last_reset_date = today
    return True


def _reset_halt():
    global _halted_today
    _halted_today = False

    msg = "[RISK] Daily max-loss reset at 09:15"
    print(msg)
    try:
        asyncio.create_task(log_bus.publish(msg))
    except RuntimeError:
        pass


# -------------------------
# Public API
# -------------------------

def is_halted() -> bool:
    return _halted_today


def check_max_loss() -> bool:
    """
    Returns True if trading must be halted.
    Performs daily auto-reset after 09:15.
    """
    global _halted_today

    # 🔄 Daily reset check
    if _should_reset():
        _reset_halt()

    if _halted_today:
        return True

    cfg = load_strategy_config()
    limit = cfg.get("risk", {}).get("max_loss_per_day")
    if not limit:
        return False

    token = load_access_token()
    if not token:
        return False

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    positions = kite.positions()


    try:
        positions = kite.positions()["net"]
        pnl = sum(p.get("pnl", 0.0) for p in positions)
    except Exception:
        return False

    if pnl <= -abs(limit):
        _halted_today = True

        msg = f"[RISK] MAX LOSS HIT: ₹{pnl} ≤ -{limit}. Trading halted for the day."
        print(msg)
        try:
            asyncio.create_task(log_bus.publish(msg))
        except RuntimeError:
            pass

        return True

    return False

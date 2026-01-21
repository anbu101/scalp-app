from fastapi import APIRouter
from datetime import datetime

from app.brokers.zerodha_auth import is_trading_enabled
from app.utils.version import get_version   # ✅ NEW

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status():
    return {
        "engine_running": True,
        "dry_run": not is_trading_enabled(),
        "timestamp": datetime.utcnow().isoformat(),

        # ✅ VERSION (A3.1)
        "version": get_version(),
    }

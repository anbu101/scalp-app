from fastapi import APIRouter
from datetime import datetime

from app.brokers.zerodha_auth import is_trading_enabled

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status():
    return {
        "engine_running": True,
        "dry_run": not is_trading_enabled(),
        "timestamp": datetime.utcnow().isoformat(),
    }

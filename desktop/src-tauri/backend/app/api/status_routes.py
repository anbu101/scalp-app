from fastapi import APIRouter
from datetime import datetime

from app.brokers.zerodha_auth import is_trading_enabled
from app.utils.version import get_version

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status():
    return {
        # 🔑 Canonical runtime status
        "backend": "UP",                  # backend process reachable
        "engine": "RUNNING",               # engine state
        "market": "OPEN",                  # placeholder (refine later)
        "mode": "LIVE" if is_trading_enabled() else "PAPER",

        # Metadata
        "version": get_version(),
        "timestamp": datetime.utcnow().isoformat(),
    }

from fastapi import APIRouter
from typing import Dict

from app.marketdata.ltp_store import LTPStore

router = APIRouter()
print("🔥 ltp_routes.py loaded 🔥")


@router.get("/ltp_snapshot")
def get_ltp_snapshot() -> Dict[str, float]:
    """
    Ultra-lightweight LTP snapshot.

    SOURCE OF TRUTH:
    - In-memory LTPStore (fed by Zerodha WebSocket)
    - NO broker calls
    - NO DB access
    - SAFE to poll frequently
    """
    print("LTPStore snapshot size:", len(LTPStore._prices))

    try:
        # Defensive copy so callers can't mutate store
        return dict(LTPStore._prices)
    except Exception:
        # Hard safety: never break UI polling
        return {}

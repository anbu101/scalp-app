from fastapi import APIRouter
from app.trading.signal_snapshot import get_signal_snapshot

router = APIRouter(tags=["signals"])


@router.get("/signals/last")
def last_signals():
    return get_signal_snapshot()

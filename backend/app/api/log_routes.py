from fastapi import APIRouter
from app.event_bus.log_bus import log_bus

router = APIRouter(tags=["logs"])

@router.get("/logs")
def get_logs():
    return log_bus.snapshot()

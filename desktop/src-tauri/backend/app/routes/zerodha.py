from fastapi import APIRouter

router = APIRouter(prefix="/zerodha", tags=["zerodha"])

@router.get("/status")
def status():
    # temporary stub – replace later with real kite state
    return {
        "connected": False,
        "user": None,
        "user_id": None
    }

@router.get("/login-url")
def login_url():
    # temporary stub – wire real Zerodha login later
    return {
        "login_url": "https://kite.zerodha.com"
    }

@router.post("/disconnect")
def disconnect():
    # safe no-op for now
    return {"ok": True}

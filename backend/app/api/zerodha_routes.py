from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect

from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import (
    is_token_valid,
    load_login_time,
    save_access_token,
    is_trading_enabled,
    enable_trading,
    disable_trading,
)
from app.brokers.zerodha_manager import ZerodhaManager


router = APIRouter(prefix="/zerodha", tags=["zerodha"])

# 🔒 SINGLE BACKEND AUTHORITY
zerodha_manager = ZerodhaManager()


# ==================================================
# Helpers
# ==================================================

def get_kite() -> KiteConnect:
    creds = load_credentials()
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Zerodha not configured"
        )

    return KiteConnect(api_key=creds["api_key"])


# ==================================================
# Routes
# ==================================================

@router.get("/status")
def status():
    """
    Single source of truth for UI.
    Backend-backed status (not file-only).
    """
    creds = load_credentials()

    if not creds:
        return {
            "configured": False,
            "connected": False,
            "session_expired": True,
            "login_at": None,
            "trading_enabled": False,
        }

    # 🔒 HARD REFRESH CHECK
    connected = zerodha_manager.refresh()

    return {
        "configured": True,
        "connected": connected,
        "session_expired": not connected,
        "login_at": load_login_time(),
        "trading_enabled": is_trading_enabled() if connected else False,
    }


@router.get("/login-url")
def login_url():
    kite = get_kite()
    return {
        "login_url": kite.login_url()
    }


@router.get("/callback")
def callback(request_token: str):
    """
    Zerodha redirects here after login.
    """
    creds = load_credentials()
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Zerodha not configured"
        )

    try:
        kite = KiteConnect(api_key=creds["api_key"])
        data = kite.generate_session(
            request_token=request_token,
            api_secret=creds["api_secret"],
        )

        save_access_token(data["access_token"])

        # 🔥 CRITICAL: refresh backend session immediately
        zerodha_manager.refresh()

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    # Redirect back to UI AFTER token is saved & backend refreshed
    return RedirectResponse(
        url="http://localhost:3000/zerodha"
    )


@router.post("/enable-trading")
def enable():
    if not zerodha_manager.refresh():
        raise HTTPException(
            status_code=400,
            detail="Zerodha session not active"
        )

    enable_trading()
    return {"trading_enabled": True}


@router.post("/disable-trading")
def disable():
    disable_trading()
    return {"trading_enabled": False}

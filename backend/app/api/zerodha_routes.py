from fastapi import APIRouter, HTTPException
from kiteconnect import KiteConnect

from app.config.zerodha_credentials_store import (
    load_credentials,
    save_credentials,
)
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


@router.post("/configure")
def configure(payload: dict):
    print("🔥 CONFIGURE CALLED", payload)
    api_key = payload.get("api_key")
    api_secret = payload.get("api_secret")

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=400,
            detail="Missing credentials"
        )

    save_credentials(api_key, api_secret)

    # 🔥 Clear any old session AFTER saving new credentials
    zerodha_manager.refresh()

    return {"configured": True}


@router.get("/callback")
def callback(request_token: str):
    """
    Zerodha redirects here after login.
    IMPORTANT:
    - Opened in SYSTEM BROWSER
    - Must NOT redirect or return HTML
    - Must NOT assume a closable window
    """
    print("🔥 ZERODHA CALLBACK HIT:", request_token)

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

    # ✅ JSON-only response (browser-safe, Tauri-safe)
    return {
        "status": "ok",
        "message": "Zerodha login successful. You can close this tab."
    }


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

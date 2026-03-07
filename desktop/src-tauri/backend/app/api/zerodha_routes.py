from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect
from app.brokers.zerodha_manager import ZerodhaManager
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
    creds = load_credentials()

    if not creds:
        return {
            "configured": False,
            "connected": False,
            "session_expired": True,
            "login_at": None,
            "trading_enabled": False,
        }

    connected = zerodha_manager.is_trade_ready()

    return {
        "configured": True,
        "connected": connected,
        "session_expired": not connected,
        "login_at": load_login_time(),
        "trading_enabled": is_trading_enabled() if connected else False,
    }



@router.get("/login-url")
def login_url(request: Request):
    """Generate login URL with state to track where user came from"""
    kite = get_kite()
    
    # Get the host from the request (e.g., "100.122.185.95:47321" or "127.0.0.1:47321")
    host = request.headers.get("host", "127.0.0.1:47321")
    
    # Extract just the IP:port for frontend redirect (change port to 3000)
    # This assumes frontend runs on port 3000
    if ":" in host:
        ip_part = host.split(":")[0]
        frontend_host = f"{ip_part}:3000"
    else:
        frontend_host = f"{host}:3000"
    
    # KiteConnect's login URL - callback is fixed in Kite app settings
    base_login_url = kite.login_url()
    
    # Append state parameter to track where user came from for redirect
    login_url_with_state = f"{base_login_url}&state={frontend_host}"
    
    print(f"[ZERODHA] Login URL generated with state={frontend_host}")
    
    return {
        "login_url": login_url_with_state
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
def callback(request: Request, request_token: str, state: str = ""):
    """
    Zerodha redirects here after login.
    After processing, redirect back to the frontend that initiated login.
    """

    print(f"🔥 ZERODHA CALLBACK HIT - request_token: {request_token}, state: {state}")
    
    # Determine frontend URL for redirect based on how we were accessed
    request_host = request.headers.get("host", "127.0.0.1:47321")
    request_scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
    
    print(f"[ZERODHA] Request host: {request_host}, scheme: {request_scheme}")
    
    # If accessed via Tailscale Funnel (HTTPS), determine frontend URL
    if "ts.net" in request_host or request_scheme == "https":
        # Accessed via Funnel - user is remote (mobile)
        # Use the state parameter if available, otherwise use Tailscale IP
        if state and not state.startswith("127.0.0.1"):
            frontend_url = f"http://{state}"
        else:
            # Fallback to Tailscale IP with port 3000
            frontend_url = "http://100.122.185.95:3000"
        print(f"[ZERODHA] Funnel access detected - redirecting to mobile: {frontend_url}")
    else:
        # Accessed via localhost - user is local (laptop)
        frontend_url = "http://127.0.0.1:3000"
        print(f"[ZERODHA] Local access detected - redirecting to laptop: {frontend_url}")

    creds = load_credentials()
    
    if not creds:
        # Redirect to frontend with error
        redirect_url = f"{frontend_url}/#/connections?zerodha=error&msg=not_configured"
        print(f"❌ ZERODHA NOT CONFIGURED - Redirecting to {redirect_url}")
        return RedirectResponse(url=redirect_url)

    try:
        kite = KiteConnect(api_key=creds["api_key"])

        data = kite.generate_session(
            request_token=request_token,
            api_secret=creds["api_secret"],
        )

        # -------------------------------------------------
        # 1️⃣ Save access token
        # -------------------------------------------------
        save_access_token(data["access_token"])

        # -------------------------------------------------
        # 2️⃣ Enable trading flag (CRITICAL)
        # -------------------------------------------------
        enable_trading()

        # -------------------------------------------------
        # 3️⃣ Refresh broker manager properly
        # -------------------------------------------------
        zerodha_manager.refresh()

        print(f"✅ ZERODHA LOGIN SUCCESS - Redirecting to {frontend_url}")

        # Redirect to Connections page with success message
        redirect_url = f"{frontend_url}/#/connections?zerodha=success"
        
        return RedirectResponse(url=redirect_url)

    except Exception as e:
        print(f"❌ ZERODHA LOGIN FAILED: {e}")
        
        # Redirect to Connections page with error
        error_msg = str(e).replace(" ", "_")
        redirect_url = f"{frontend_url}/#/connections?zerodha=error&msg={error_msg}"
        
        return RedirectResponse(url=redirect_url)


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
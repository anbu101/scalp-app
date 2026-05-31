from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
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


def _zerodha_result_page(ok: bool, title: str, message: str) -> HTMLResponse:
    """
    Self-contained HTML page returned to the Zerodha login tab.

    Design intentionally matches the app's dark theme so it doesn't
    look jarring. Auto-closes after 5 s; "Close this tab" button for
    environments where window.close() is blocked.

    The app tab is NEVER navigated — no redirect is issued.
    """
    icon      = "✓" if ok else "✕"
    accent    = "#10b981" if ok else "#ef4444"
    accent_bg = "rgba(16,185,129,0.12)" if ok else "rgba(239,68,68,0.12)"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Zerodha — {title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      background: #020817;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", sans-serif;
      color: #f1f5f9;
    }}
    .card {{
      background: #0f172a;
      border: 1px solid #1a2540;
      border-radius: 12px;
      padding: 40px 48px;
      text-align: center;
      max-width: 420px;
      width: 90%;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}
    .icon-wrap {{
      width: 64px; height: 64px; border-radius: 50%;
      background: {accent_bg};
      border: 2px solid {accent};
      display: flex; align-items: center; justify-content: center;
      font-size: 28px; color: {accent};
      margin: 0 auto 24px;
      font-weight: 700;
    }}
    h1  {{ font-size: 20px; font-weight: 700; color: {accent}; margin-bottom: 12px; }}
    p   {{ font-size: 14px; color: #94a3b8; line-height: 1.6; margin-bottom: 28px; }}
    .btn {{
      display: inline-block;
      padding: 10px 24px; border-radius: 6px;
      background: {accent}; color: #fff;
      font-size: 13px; font-weight: 600;
      border: none; cursor: pointer;
      transition: opacity 0.15s;
    }}
    .btn:hover {{ opacity: 0.8; }}
    .hint {{
      font-size: 11px; color: #334155; margin-top: 16px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon-wrap">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <button class="btn" onclick="window.close()">Close this tab</button>
    <p class="hint">
      This tab will close automatically in <span id="cd">5</span>s
    </p>
  </div>
  <script>
    var s = 5;
    var el = document.getElementById("cd");
    var t = setInterval(function () {{
      s--;
      if (el) el.textContent = s;
      if (s <= 0) {{ clearInterval(t); window.close(); }}
    }}, 1000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


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

@router.get("/funds")
def funds():
    """
    Returns Zerodha available balance (equity net cash) for the header pill.

    Degrades gracefully — never raises — so the frontend header can fall back
    to the status label if there's no session or the API call fails.
    Returns { "net": <float|null>, "connected": <bool> }.
    """
    kite = zerodha_manager.get_kite()
    if kite is None:
        return {"net": None, "connected": False}

    try:
        margins = kite.margins("equity")
        # KiteConnect margins("equity") returns a dict with a top-level "net".
        net = margins.get("net") if isinstance(margins, dict) else None
        if not isinstance(net, (int, float)):
            return {"net": None, "connected": True}
        return {"net": net, "connected": True}
    except Exception as e:
        print(f"[ZERODHA] funds fetch failed: {e}")
        return {"net": None, "connected": False}
    
@router.get("/login-url")
def login_url(request: Request):
    """
    Generate login URL.  The state param is kept for compatibility but
    the callback no longer uses it for redirecting — it returns HTML.
    """
    kite = get_kite()

    host = request.headers.get("host", "127.0.0.1:47321")
    hostname = host.split(":")[0]
    frontend_host = f"{hostname}:3000"

    base_login_url = kite.login_url()
    login_url_with_state = f"{base_login_url}&state={frontend_host}"

    print(f"[ZERODHA] Login URL generated with state={frontend_host}")

    return {"login_url": login_url_with_state}


@router.post("/configure")
def configure(payload: dict):
    print("🔥 CONFIGURE CALLED", payload)
    api_key    = payload.get("api_key")
    api_secret = payload.get("api_secret")

    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing credentials")

    save_credentials(api_key, api_secret)
    zerodha_manager.refresh()
    return {"configured": True}


@router.get("/callback")
def callback(request: Request, request_token: str, state: str = ""):
    """
    Zerodha redirects the browser here after the user completes login.

    Returns a self-contained HTML page — NOT a redirect to the app.
    This keeps the original app tab completely untouched; the login tab
    shows a success/failure message and closes itself after 5 s.
    """
    print(f"🔥 ZERODHA CALLBACK HIT — request_token: {request_token}, state: {state}")

    creds = load_credentials()
    if not creds:
        print("❌ ZERODHA NOT CONFIGURED")
        return _zerodha_result_page(
            ok=False,
            title="Not Configured",
            message=(
                "Zerodha credentials are not set up. "
                "Please add your API key and secret in the Connections page first."
            ),
        )

    try:
        kite = KiteConnect(api_key=creds["api_key"])
        data = kite.generate_session(
            request_token=request_token,
            api_secret=creds["api_secret"],
        )

        save_access_token(data["access_token"])
        enable_trading()
        zerodha_manager.refresh()

        print("✅ ZERODHA LOGIN SUCCESS")
        return _zerodha_result_page(
            ok=True,
            title="Login Successful",
            message="You are now connected to Zerodha. You can close this tab.",
        )

    except Exception as e:
        print(f"❌ ZERODHA LOGIN FAILED: {e}")
        return _zerodha_result_page(
            ok=False,
            title="Login Failed",
            message=str(e),
        )


@router.post("/enable-trading")
def enable():
    if not zerodha_manager.refresh():
        raise HTTPException(status_code=400, detail="Zerodha session not active")
    enable_trading()
    return {"trading_enabled": True}


@router.post("/disable-trading")
def disable():
    disable_trading()
    return {"trading_enabled": False}
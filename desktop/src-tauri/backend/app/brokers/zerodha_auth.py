# backend/app/brokers/zerodha_auth.py

from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Optional

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from app.config.zerodha_credentials_store import load_credentials
from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import APP_HOME, ensure_app_dirs


# ==================================================
# APP HOME (SINGLE SOURCE OF TRUTH)
# ==================================================

ensure_app_dirs()

ZERODHA_DIR = APP_HOME / "zerodha"
ZERODHA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = ZERODHA_DIR / "access_token.json"
TRADE_TOKEN_FILE = ZERODHA_DIR / "access_token_trade.json"
DATA_TOKEN_FILE  = ZERODHA_DIR / "access_token_data.json"
TRADING_FLAG_FILE = ZERODHA_DIR / "trading_enabled.json"


# ==================================================
# In-memory cache
# ==================================================

_access_token: Optional[str] = None
_login_at: Optional[str] = None
_trading_enabled: bool = False


# ==================================================
# Token helpers
# ==================================================

def load_access_token() -> Optional[str]:
    global _access_token, _login_at

    if _access_token:
        return _access_token

    if not TOKEN_FILE.exists():
        return None

    try:
        data = json.loads(TOKEN_FILE.read_text())
        _access_token = data.get("access_token")
        _login_at = data.get("login_at")
        return _access_token
    except Exception as e:
        write_audit_log(
            f"[ZERODHA_AUTH][WARN] Failed reading token file ERR={e}"
        )
        clear_access_token()
        return None


def load_login_time() -> Optional[str]:
    global _login_at

    if _login_at:
        return _login_at

    if not TOKEN_FILE.exists():
        return None

    try:
        data = json.loads(TOKEN_FILE.read_text())
        _login_at = data.get("login_at")
        return _login_at
    except Exception as e:
        write_audit_log(
            f"[ZERODHA_AUTH][WARN] Failed reading login time ERR={e}"
        )
        return None


def save_access_token(token: str):
    global _access_token, _login_at

    _access_token = token
    _login_at = datetime.now(timezone.utc).isoformat()

    payload = json.dumps(
        {
            "access_token": token,
            "login_at": _login_at,
        },
        indent=2,
    )

    TOKEN_FILE.write_text(payload)
    TRADE_TOKEN_FILE.write_text(payload)
    DATA_TOKEN_FILE.write_text(payload)

    write_audit_log("[ZERODHA_AUTH] Access token saved (trade + data)")


def clear_access_token():
    global _access_token, _login_at

    _access_token = None
    _login_at = None

    for f in (TOKEN_FILE, TRADE_TOKEN_FILE, DATA_TOKEN_FILE):
        if f.exists():
            f.unlink()

    write_audit_log("[ZERODHA_AUTH] Access token cleared")


# ==================================================
# Token validity
# ==================================================

def is_token_valid() -> bool:
    token = load_access_token()
    creds = load_credentials()

    if not token or not creds:
        return False

    try:
        kite = KiteConnect(api_key=creds["api_key"])
        kite.set_access_token(token)
        kite.profile()
        return True

    except TokenException:
        write_audit_log("[ZERODHA_AUTH] Token INVALID (TokenException)")
        clear_access_token()
        return False

    except Exception as e:
        write_audit_log(
            f"[ZERODHA_AUTH][WARN] Token validation transient failure ERR={e}"
        )
        return True


# ==================================================
# Trading flag helpers
# ==================================================

def is_trading_enabled() -> bool:
    global _trading_enabled

    if not is_token_valid():
        _trading_enabled = False
        return False

    if TRADING_FLAG_FILE.exists():
        _trading_enabled = TRADING_FLAG_FILE.read_text().strip() == "1"

    return _trading_enabled


def enable_trading():
    if not is_token_valid():
        raise RuntimeError("Zerodha session not active")

    global _trading_enabled
    _trading_enabled = True
    TRADING_FLAG_FILE.write_text("1")

    write_audit_log("[ZERODHA_AUTH] Trading ENABLED")


def disable_trading():
    global _trading_enabled
    _trading_enabled = False
    TRADING_FLAG_FILE.write_text("0")

    write_audit_log("[ZERODHA_AUTH] Trading DISABLED")


def reset_all():
    clear_access_token()
    disable_trading()


# ==================================================
# Kite helper
# ==================================================

def get_kite() -> Optional[KiteConnect]:
    if not is_token_valid():
        return None

    token = load_access_token()
    creds = load_credentials()

    if not token or not creds:
        return None

    kite = KiteConnect(api_key=creds["api_key"])
    kite.set_access_token(token)
    return kite

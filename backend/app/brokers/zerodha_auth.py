from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Optional

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException  # ✅ NEW (safe import)

from app.config.zerodha_credentials_store import load_credentials
from app.event_bus.audit_logger import write_audit_log  # ✅ NEW (logging)


# ==================================================
# Persistent base (Docker-safe, single source)
# ==================================================

BASE_DIR = Path("/data/zerodha")
BASE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = BASE_DIR / "access_token.json"
TRADE_TOKEN_FILE = BASE_DIR / "access_token_trade.json"
DATA_TOKEN_FILE  = BASE_DIR / "access_token_data.json"
TRADING_FLAG_FILE = BASE_DIR / "trading_enabled.json"


# ==================================================
# In-memory cache (SINGLE SOURCE)
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
    """
    🔒 BACKWARD COMPATIBILITY
    Required by zerodha_routes.py and others.
    """
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
    """
    🔒 AUTHORITATIVE WRITER
    Writes SAME token to ALL files (trade + data).
    """
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

    # Canonical
    TOKEN_FILE.write_text(payload)

    # Mirrors (no divergence possible)
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
# Token validity (AUTHORITATIVE, BALANCED FIX)
# ==================================================

def is_token_valid() -> bool:
    """
    ✅ BALANCED FIX:
    - Clear token ONLY on real TokenException
    - Treat network / API hiccups as TRANSIENT
    """
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
        # ❌ REAL invalid token (expired / revoked)
        write_audit_log("[ZERODHA_AUTH] Token INVALID (TokenException)")
        clear_access_token()
        return False

    except Exception as e:
        # ⚠️ TRANSIENT FAILURE — DO NOT CLEAR TOKEN
        write_audit_log(
            f"[ZERODHA_AUTH][WARN] Token validation transient failure ERR={e}"
        )
        return True   # ⬅️ CRITICAL: keep token valid


# ==================================================
# Trading flag helpers (UNCHANGED behavior)
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
# Kite helper (SAFE)
# ==================================================

def get_kite() -> Optional[KiteConnect]:
    """
    Returns KiteConnect ONLY if token is logically valid.
    """
    if not is_token_valid():
        return None

    token = load_access_token()
    creds = load_credentials()

    if not token or not creds:
        return None

    kite = KiteConnect(api_key=creds["api_key"])
    kite.set_access_token(token)
    return kite

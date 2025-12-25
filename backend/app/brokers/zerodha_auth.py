from pathlib import Path
from datetime import datetime, timezone
import json
from kiteconnect import KiteConnect

from app.config.zerodha_credentials_store import load_credentials


# ==================================================
# Persistent base (Docker-safe, single source)
# ==================================================

BASE_DIR = Path("/data/zerodha")
BASE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = BASE_DIR / "access_token.json"
TRADING_FLAG_FILE = BASE_DIR / "trading_enabled.json"


# ==================================================
# In-memory cache
# ==================================================

_access_token: str | None = None
_login_at: str | None = None
_trading_enabled: bool = False


# ==================================================
# Token helpers
# ==================================================

def load_access_token() -> str | None:
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
    except Exception:
        clear_access_token()
        return None


def load_login_time() -> str | None:
    global _login_at

    if _login_at:
        return _login_at

    if not TOKEN_FILE.exists():
        return None

    try:
        data = json.loads(TOKEN_FILE.read_text())
        _login_at = data.get("login_at")
        return _login_at
    except Exception:
        return None


def save_access_token(token: str):
    global _access_token, _login_at

    _access_token = token
    _login_at = datetime.now(timezone.utc).isoformat()

    TOKEN_FILE.write_text(
        json.dumps(
            {
                "access_token": token,
                "login_at": _login_at,
            },
            indent=2,
        )
    )


def clear_access_token():
    global _access_token, _login_at

    _access_token = None
    _login_at = None

    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


# ==================================================
# Token validity (AUTHORITATIVE)
# ==================================================

def is_token_valid() -> bool:
    """
    HARD validation against Zerodha.
    Tokens expire daily — this is the only truth.
    """
    token = load_access_token()
    creds = load_credentials()

    if not token or not creds:
        return False

    try:
        kite = KiteConnect(api_key=creds["api_key"])
        kite.set_access_token(token)

        # This fails instantly if token expired
        kite.profile()
        return True

    except Exception:
        clear_access_token()
        return False


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


def disable_trading():
    global _trading_enabled
    _trading_enabled = False
    TRADING_FLAG_FILE.write_text("0")


def reset_all():
    clear_access_token()
    disable_trading()


# ==================================================
# Kite helper (SAFE)
# ==================================================

def get_kite() -> KiteConnect | None:
    if not is_token_valid():
        return None

    token = load_access_token()
    creds = load_credentials()

    if not token or not creds:
        return None

    kite = KiteConnect(api_key=creds["api_key"])
    kite.set_access_token(token)
    return kite

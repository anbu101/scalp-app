# backend/app/config/zerodha_credentials_store.py

import json
from pathlib import Path
from datetime import datetime

from app.utils.app_paths import APP_HOME, ensure_app_dirs


# ==================================================
# SINGLE SOURCE OF TRUTH (USER HOME)
# ~/.scalp-app/zerodha/credentials.json
# ==================================================

ensure_app_dirs()

ZERODHA_DIR = APP_HOME / "zerodha"
ZERODHA_DIR.mkdir(parents=True, exist_ok=True)

CREDENTIALS_PATH = ZERODHA_DIR / "credentials.json"


# ==================================================
# LOAD
# ==================================================

def load_credentials() -> dict | None:
    if not CREDENTIALS_PATH.exists():
        return None

    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except Exception:
        return None


# ==================================================
# SAVE
# ==================================================

def save_credentials(api_key: str, api_secret: str):
    data = {
        "api_key": api_key.strip(),
        "api_secret": api_secret.strip(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))


# ==================================================
# CLEAR
# ==================================================

def clear_credentials():
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()

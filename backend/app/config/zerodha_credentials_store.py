# backend/app/config/zerodha_credentials_store.py

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional
from app.event_bus.audit_logger import write_audit_log
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

def load_credentials() -> Optional[dict]:
    #print("APP_HOME:", APP_HOME)
    #print("CREDENTIALS_PATH:", CREDENTIALS_PATH)
    #print("Exists:", CREDENTIALS_PATH.exists())

    if not CREDENTIALS_PATH.exists():
        #print("File does not exist")
        return None

    try:
        raw = CREDENTIALS_PATH.read_text()
        #print("RAW FILE CONTENT:", raw)
        data = json.loads(raw)
        #print("PARSED DATA:", data)
        return data
    except Exception as e:
        #print("JSON ERROR:", e)
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

    # ── TEST_HOME_GUARD_20261127 ── (DEF-113) all or nothing: a crash or power cut
    # mid-save leaves the old file, never a half-written one.
    fd, tmp = tempfile.mkstemp(dir=str(CREDENTIALS_PATH.parent), prefix=CREDENTIALS_PATH.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(CREDENTIALS_PATH))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ==================================================
# CLEAR
# ==================================================

def clear_credentials():
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()

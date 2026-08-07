# backend/app/config/angel_credentials_store.py
# ============================================================
# ACC2 BEGIN — Angel One (secondary account) credential store
# Mirrors zerodha_credentials_store.py conventions:
#   SINGLE SOURCE OF TRUTH: ~/.scalp-app/angelone/credentials.json
#   Atomic writes (tempfile + fsync + replace)
# Fields: api_key, client_code, pin, totp_secret, enabled
# ============================================================

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import APP_HOME, ensure_app_dirs

ensure_app_dirs()

ANGEL_DIR = APP_HOME / "angelone"
ANGEL_DIR.mkdir(parents=True, exist_ok=True)

CREDENTIALS_PATH = ANGEL_DIR / "credentials.json"
SESSION_PATH = ANGEL_DIR / "session.json"          # jwt + issue timestamp

_REQUIRED = ("api_key", "client_code", "pin", "totp_secret")


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ==================================================
# CREDENTIALS
# ==================================================

def load_credentials() -> Optional[dict]:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except Exception as e:
        write_audit_log(f"[ANGEL_CREDS][WARN] Unreadable credentials ERR={e}")
        return None
    if not all(data.get(k) for k in _REQUIRED):
        write_audit_log("[ANGEL_CREDS][WARN] Credentials incomplete")
        return None
    return data


def save_credentials(api_key: str, client_code: str, pin: str,
                     totp_secret: str, enabled: bool = True) -> None:
    _atomic_write(CREDENTIALS_PATH, {
        "api_key": api_key.strip(),
        "client_code": client_code.strip(),
        "pin": pin.strip(),
        "totp_secret": totp_secret.strip().replace(" ", ""),
        "enabled": bool(enabled),
    })
    write_audit_log("[ANGEL_CREDS] Credentials saved")


def is_enabled() -> bool:
    creds = load_credentials()
    return bool(creds and creds.get("enabled"))


def clear_credentials() -> None:
    for p in (CREDENTIALS_PATH, SESSION_PATH):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    write_audit_log("[ANGEL_CREDS] Credentials cleared")


# ==================================================
# SESSION (jwt persisted so a backend restart mid-day
# does not force a fresh login)
# ==================================================

def load_session() -> Optional[dict]:
    if not SESSION_PATH.exists():
        return None
    try:
        return json.loads(SESSION_PATH.read_text())
    except Exception:
        return None


def save_session(jwt_token: str, issued_at_iso: str) -> None:
    _atomic_write(SESSION_PATH, {
        "jwt_token": jwt_token,
        "issued_at": issued_at_iso,
    })

# ACC2 END
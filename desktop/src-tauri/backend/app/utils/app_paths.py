import os
from pathlib import Path
import sys


def _get_home_dir() -> Path:
    """
    Resolve user's home directory safely across:
    - macOS
    - Linux
    - Windows
    - Docker
    - PyInstaller
    """
    return Path.home()


# --------------------------------------------------
# APP HOME (SINGLE SOURCE OF TRUTH)
# --------------------------------------------------

APP_HOME = _get_home_dir() / ".scalp-app"

DATA_DIR = APP_HOME / "data"
STATE_DIR = APP_HOME / "state"
LOG_DIR = APP_HOME / "logs"
CONFIG_DIR = APP_HOME / "config"
ZERODHA_DIR = APP_HOME / "zerodha"


# --------------------------------------------------
# DB / FILE PATHS
# --------------------------------------------------

DB_PATH = DATA_DIR / "app.db"


# --------------------------------------------------
# INIT (SAFE TO CALL MULTIPLE TIMES)
# --------------------------------------------------

def ensure_app_dirs():
    """
    Create all required directories.
    Safe to call repeatedly.
    MUST be called before any IO.
    """
    for d in [
        APP_HOME,
        DATA_DIR,
        STATE_DIR,
        LOG_DIR,
        CONFIG_DIR,
        ZERODHA_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


def export_env():
    """
    Export critical paths to env so legacy code keeps working.
    This avoids breaking older modules that rely on env vars.
    """
    os.environ.setdefault("SCALP_APP_HOME", str(APP_HOME))
    os.environ.setdefault("DB_PATH", str(DB_PATH))
    os.environ.setdefault("SCALP_DATA_DIR", str(DATA_DIR))
    os.environ.setdefault("SCALP_LOG_DIR", str(LOG_DIR))
    os.environ.setdefault("SCALP_STATE_DIR", str(STATE_DIR))
    os.environ.setdefault("SCALP_CONFIG_DIR", str(CONFIG_DIR))
    os.environ.setdefault("SCALP_ZERODHA_DIR", str(ZERODHA_DIR))


# --------------------------------------------------
# ONE-LINE BOOTSTRAP (OPTIONAL BUT RECOMMENDED)
# --------------------------------------------------

def bootstrap():
    """
    Idempotent bootstrap for the entire app.
    Call this once at process startup.
    """
    ensure_app_dirs()
    export_env()

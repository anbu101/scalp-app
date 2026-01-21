from pathlib import Path
from datetime import datetime
import pytz
import os

# --------------------------------------------------
# TIMEZONE
# --------------------------------------------------

IST = pytz.timezone("Asia/Kolkata")


def _now():
    return datetime.now(IST)


# --------------------------------------------------
# LOG DIRECTORY (SINGLE SOURCE OF TRUTH)
# --------------------------------------------------
# Must align with ~/.scalp-app/logs
# NEVER derive from backend/app again

def _resolve_log_dir() -> Path:
    """
    Resolve log directory from canonical app home.
    Falls back safely if env is not yet exported.
    """
    app_home = os.environ.get("SCALP_APP_HOME")

    if app_home:
        return Path(app_home) / "logs"

    # Fallback (should not happen after bootstrap)
    return Path.home() / ".scalp-app" / "logs"


LOG_DIR = _resolve_log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------
# LOG FILE (DAILY ROTATION)
# --------------------------------------------------

def _log_file() -> Path:
    today = _now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.log"


# --------------------------------------------------
# PUBLIC API
# --------------------------------------------------

def write_audit_log(message: str):
    """
    Append a single line to today's audit log.
    Safe for multi-process (append-only).
    """
    ts = _now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}\n"

    try:
        with _log_file().open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # LAST RESORT: never crash the app due to logging
        print(f"[LOGGER_ERROR] {e} :: {line}")

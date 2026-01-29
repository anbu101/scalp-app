from fastapi import APIRouter
from datetime import date
from pathlib import Path

from app.utils.app_paths import LOG_DIR

router = APIRouter(tags=["logs"])


@router.get("/logs/today")
def get_today_logs(tail: int = 1000):
    """
    Returns last N lines from today's log file.
    """
    today = date.today().isoformat()
    log_file = LOG_DIR / f"{today}.log"

    if not log_file.exists():
        return {
            "date": today,
            "lines": [],
        }

    lines = log_file.read_text(errors="ignore").splitlines()

    if tail:
        lines = lines[-tail:]

    return {
        "date": today,
        "lines": lines,
    }

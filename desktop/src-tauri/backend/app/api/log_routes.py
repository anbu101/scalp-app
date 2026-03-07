from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pathlib import Path
from datetime import date

router = APIRouter(prefix="/logs", tags=["logs"])


def _today_log_path() -> Path:
    """
    Resolves today's log file path cross-platform.
      macOS / Linux : ~/.scalp-app/logs/YYYY-MM-DD.log
      Windows       : C:\\Users\\<user>\\.scalp-app\\logs\\YYYY-MM-DD.log
    Path.home() handles both correctly.
    """
    today = date.today().isoformat()          # e.g. "2026-03-07"
    return Path.home() / ".scalp-app" / "logs" / f"{today}.log"


@router.get("/today")
def today_log():
    """
    Returns today's log file as plain text.
    Consumed by the frontend DebugPanel log viewer.
    """
    log_path = _today_log_path()

    if not log_path.exists():
        return {
            "date":    date.today().isoformat(),
            "path":    str(log_path),
            "content": f"No log file found for today.\nExpected path: {log_path}",
            "lines":   0,
        }

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines   = content.count("\n")
        return {
            "date":    date.today().isoformat(),
            "path":    str(log_path),
            "content": content,
            "lines":   lines,
        }
    except Exception as e:
        return {
            "date":    date.today().isoformat(),
            "path":    str(log_path),
            "content": f"Error reading log file: {e}",
            "lines":   0,
        }
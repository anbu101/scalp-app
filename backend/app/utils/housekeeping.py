from pathlib import Path
from datetime import datetime, timedelta
import os

# Paths (already mounted volumes)
LOG_DIR = Path("/app/app/logs")
STATE_DIR = Path("/app/app/state")

LOG_RETENTION_DAYS = 10
CANDLE_DEBUG_RETENTION_DAYS = 7


def _cleanup_dir(path: Path, pattern: str, keep_days: int):
    if not path.exists():
        return

    cutoff = datetime.now() - timedelta(days=keep_days)

    for file in path.glob(pattern):
        try:
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                file.unlink()
        except Exception:
            pass  # never crash backend for housekeeping


def run_housekeeping():
    # 🔹 Regular logs
    _cleanup_dir(
        path=LOG_DIR,
        pattern="*.log",
        keep_days=LOG_RETENTION_DAYS,
    )

    # 🔹 Candle debug TSVs
    _cleanup_dir(
        path=STATE_DIR,
        pattern="candle_debug_*.tsv",
        keep_days=CANDLE_DEBUG_RETENTION_DAYS,
    )

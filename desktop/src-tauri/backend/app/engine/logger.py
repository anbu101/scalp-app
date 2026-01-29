# backend/engine/logger.py

import sys
from datetime import datetime
from threading import Lock

_LOG_LOCK = Lock()


def log(message: str):
    """
    Thread-safe, timestamped logger.
    """
    with _LOG_LOCK:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = f"{ts} {message}"
        print(out)
        sys.stdout.flush()

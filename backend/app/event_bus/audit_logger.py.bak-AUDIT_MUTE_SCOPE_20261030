from pathlib import Path
from datetime import datetime
import pytz
import os
import threading

IST = pytz.timezone("Asia/Kolkata")


def _now():
    return datetime.now(IST)


def _resolve_log_dir() -> Path:
    app_home = os.environ.get("SCALP_APP_HOME")
    if app_home:
        return Path(app_home) / "logs"
    return Path.home() / ".scalp-app" / "logs"


LOG_DIR = _resolve_log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log_file() -> Path:
    today = _now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.log"


_LOG_LOCK = threading.Lock()


# --------------------------------------------------
# BACKTEST MUTE  (perf + audit-integrity)
# --------------------------------------------------
# A backtest replays historical days but write_audit_log rotates on the CURRENT
# wall-clock date, so every backtest line would (a) cost a full file open/close
# on the hot path — hundreds of thousands of cycles over a multi-year run — and
# (b) pollute TODAY's live audit log with mis-dated replay noise. The backtest
# runner sets this flag for the duration of a run so audit writes become no-ops.
# It is OFF by default and only ever toggled by the backtest runner, so LIVE
# behaviour is completely unchanged. Guarded by the same lock for thread safety.
_MUTED = False


def set_audit_muted(muted: bool) -> None:
    """Enable/disable audit writes process-wide. Used ONLY by the backtest
    runner (via a try/finally) so a replay doesn't thrash the log file or
    corrupt the live daily audit trail. Never called on the live path."""
    global _MUTED
    with _LOG_LOCK:
        _MUTED = bool(muted)


def is_audit_muted() -> bool:
    return _MUTED


class audit_muted:
    """Context manager: mute audit writes for the duration of a backtest run,
    guaranteeing restoration even on exception.

        with audit_muted():
            run_ha_backtest(...)
    """
    def __enter__(self):
        self._prev = is_audit_muted()
        set_audit_muted(True)
        return self

    def __exit__(self, *exc):
        set_audit_muted(self._prev)
        return False


def write_audit_log(message: str):
    # Backtest mute: skip ALL work (no _now(), no string build, no file I/O).
    if _MUTED:
        return
    ts = _now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}\n"
    try:
        with _LOG_LOCK:
            with _log_file().open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        print(f"[LOGGER_ERROR] {e} :: {line}")
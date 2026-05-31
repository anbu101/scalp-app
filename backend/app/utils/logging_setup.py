# backend/app/utils/logging_setup.py
"""
Centralised logging for the Scalp backend.

Writes everything to ~/.scalp-app/logs/backend.log with rotation, so that
on a windowed (console=False) PyInstaller build — where stdout/stderr go
nowhere — there is still a diagnostic trail.

Captures:
  - app + library logging via the root logger
  - uvicorn / uvicorn.error / uvicorn.access loggers
  - uncaught exceptions (sys.excepthook)
  - uncaught exceptions in threads (threading.excepthook, py3.8+)
"""

import logging
import logging.handlers
import sys
import threading

from app.utils.app_paths import LOG_DIR, ensure_app_dirs

LOG_FILE = LOG_DIR / "backend.log"

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    Idempotent. Call once, as early as possible in process startup,
    AFTER ensure_app_dirs() (or it will call it itself).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Make sure ~/.scalp-app/logs exists before we open a file in it.
    ensure_app_dirs()

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler: 5 MB per file, keep 3 old copies.
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any handlers uvicorn/others may have pre-installed, so we
    # don't double-log or rely on a stderr stream that may be None.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)

    # Only add a console handler if we actually have a usable stderr.
    # On a windowed PyInstaller build, sys.stderr can be None — adding a
    # StreamHandler(None) would itself raise on first emit.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(fmt)
        console_handler.setLevel(level)
        root.addHandler(console_handler)

    # Route uvicorn's loggers through the root handlers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # ---- Uncaught exception hooks -----------------------------------

    def _excepthook(exc_type, exc_value, exc_tb):
        # Let Ctrl-C behave normally.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("scalp.fatal").critical(
            "UNCAUGHT EXCEPTION", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _excepthook

    # Thread exceptions (Python 3.8+). Backend runs threads (scheduler, ws).
    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            if issubclass(args.exc_type, KeyboardInterrupt):
                return
            logging.getLogger("scalp.fatal").critical(
                "UNCAUGHT THREAD EXCEPTION in %s",
                args.thread.name if args.thread else "?",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = _thread_excepthook

    _CONFIGURED = True
    logging.getLogger("scalp").info(
        "Logging initialised -> %s", LOG_FILE
    )


def get_log_file_path() -> str:
    """Convenience for the API / UI to surface the log location."""
    return str(LOG_FILE)
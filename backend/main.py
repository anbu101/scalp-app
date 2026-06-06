#!/usr/bin/env python3
"""
Standalone entry point for PyInstaller bundling.
Runs the FastAPI app with uvicorn programmatically.
"""
import sys
import os

# --- Force UTF-8 I/O BEFORE anything imports/prints ------------------
# Windows console defaults to cp1252, which crashes on emoji in print()
# (e.g. " ltp_routes.py loaded ") with UnicodeEncodeError, killing the
# backend at import time. macOS/Linux default to UTF-8 so they're unaffected.
# This must run before any app.* module is imported.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# reconfigure may not exist / stdout may be None on a windowed (console=False)
# build, so guard both.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging

# Ensure app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- Bootstrap dirs + logging BEFORE importing the app ---
# This guarantees that any import-time failure in app.* is written to
# ~/.scalp-app/logs/backend.log, even on a windowed build with no console.
from app.utils.app_paths import bootstrap
from app.utils.logging_setup import setup_logging

bootstrap()          # create ~/.scalp-app/* dirs, export env vars
setup_logging()      # rotating file log + exception hooks

log = logging.getLogger("scalp.boot")

try:
    import uvicorn
    from app.api_server import app
except Exception:
    # Import-time crash — capture it, since stdout/stderr may go nowhere.
    log.critical("Failed during startup imports", exc_info=True)
    raise

if __name__ == "__main__":
    host = os.getenv("SCALP_HOST", "127.0.0.1")
    port = int(os.getenv("SCALP_PORT", "47321"))

    log.info("Starting server on %s:%s", host, port)

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            # Don't let uvicorn install its own logging config and clobber ours.
            log_config=None,
        )
    except Exception:
        log.critical("uvicorn.run crashed", exc_info=True)
        raise
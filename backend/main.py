#!/usr/bin/env python3
#backend/main.py
"""
Standalone entry point for PyInstaller bundling.
Runs the FastAPI app with uvicorn programmatically.
backend/main.py
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

# --- multiprocessing guard (MUST precede all app.* imports) -----------
# ── TSG_PARALLEL ── In a frozen (PyInstaller) app, multiprocessing's
# spawn method re-executes THIS launcher for every worker child. Without
# freeze_support() each child would boot a full backend server (uvicorn,
# schedulers, port bind) instead of running its worker function.
# freeze_support() detects the child invocation, runs the worker, and
# exits — it is a no-op in the parent and in unfrozen dev runs. Needed by
# the parallel backtest path (parallel_workers > 1); harmless otherwise.
import multiprocessing
if __name__ == "__main__":
    multiprocessing.freeze_support()

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

# ── FD_SOFT_LIMIT BEGIN ───────────────────────────────────────────────
# 2026-07-06 incident: the bundled backend hit OSError(24, 'Too many open
# files') mid-session (224 degraded config reads; global trade_on clobbered
# twice; HA_V1 mode flapping). The process is launched by the Tauri app via
# launchd, so it inherits LAUNCHD'S RLIMIT_NOFILE soft limit — NOT the shell's
# `ulimit -n` — and macOS launchd defaults can be as low as 256.
#
# Raise the soft limit to min(hard, 8192) as early as possible and LOG both
# the before/after values so the effective limit is visible in every boot log.
# This treats the ceiling; the fd leak itself is diagnosed separately (lsof
# breakdown during the session).
#
# Windows has no `resource` module (its handle limits work differently and
# were not the failing platform) — the ImportError guard makes this block a
# clean no-op there.
try:
    import resource

    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if _hard == resource.RLIM_INFINITY:
        _target = 8192
    else:
        _target = min(_hard, 8192)

    if _soft < _target:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
        except Exception as _e:
            log.warning(
                "FD soft-limit raise failed (%r) — continuing with soft=%s hard=%s",
                _e, _soft, _hard,
            )

    _soft_now, _hard_now = resource.getrlimit(resource.RLIMIT_NOFILE)
    log.info(
        "FD limits: soft %s -> %s (hard %s)",
        _soft, _soft_now,
        "unlimited" if _hard_now == resource.RLIM_INFINITY else _hard_now,
    )
except ImportError:
    # Windows — no resource module; nothing to do.
    pass
except Exception as _e:
    log.warning("FD limit block failed unexpectedly: %r", _e)
# ── FD_SOFT_LIMIT END ─────────────────────────────────────────────────

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
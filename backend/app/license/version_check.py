# backend/app/license/version_check.py
"""
Advisory app-version check for Scalp Terminal.

Asks the license server for the lowest app version it considers current
(GET /min_version) and compares it against THIS build's version. The
result is PURELY ADVISORY:

  - It sets a flag the UI can read to show a soft "update available"
    banner.
  - It NEVER touches license_state, NEVER gates a strategy, NEVER blocks
    trading, NEVER changes the heartbeat or token logic.

Fail-OPEN is absolute. If the server is unreachable, the response is
malformed, the version strings don't parse, or anything else goes wrong,
the result is simply "no update needed" and the app proceeds normally. A
version nag must never become an outage — least of all on a live BB_V1
trading day.

Why a separate module (not folded into license_client):
  - It must be impossible for a bug here to affect license evaluation or
    the heartbeat loop. Physical separation makes that guarantee obvious.
  - The version endpoint is public and unauthenticated; the license flow
    is signed and machine-bound. Different trust levels, different files.

Exposed state (read by api_server / a system route, surfaced in the UI):
  version_state.UPDATE_AVAILABLE : bool   (default False)
  version_state.MIN_VERSION      : str|None
  version_state.CURRENT_VERSION  : str|None
  version_state.MESSAGE          : str
"""

import os
import time
from pathlib import Path

import httpx

from app.event_bus.audit_logger import write_audit_log

# Same server as the license client. Reuse the env override if present so
# test setups point both at the same place.
LICENSE_SERVER_URL = os.environ.get(
    "SCALP_LICENSE_SERVER", "http://139.59.8.202:9100"
)


# --------------------------------------------------
# ADVISORY STATE (module-level, read-only for the rest of the app)
# --------------------------------------------------

class _VersionState:
    UPDATE_AVAILABLE: bool = False
    MIN_VERSION: str | None = None
    CURRENT_VERSION: str | None = None
    MESSAGE: str = ""


version_state = _VersionState()


# --------------------------------------------------
# VERSION HELPERS
# --------------------------------------------------

def _parse_version_file(text: str) -> str | None:
    """
    Accept BOTH formats the app might have on disk:
      - a bare version string:           8.1.0
      - a key=value block (the existing VERSION file):
            app = scalp-app
            version = 8.1.0
            installed_at = ...
    Returns the version string, or None if it can't be found / is the
    placeholder 'unknown'.
    """
    text = text.strip()
    if not text:
        return None
    # key=value form: find a 'version = X' line
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip().lower() == "version":
                v = v.strip()
                return v if v and v.lower() != "unknown" else None
    # otherwise treat the whole thing as a bare version (single token)
    if "\n" not in text and " " not in text:
        return text if text.lower() != "unknown" else None
    return None


def _read_current_version() -> str | None:
    """
    This build's version. Sources, in priority order:
      1. SCALP_APP_VERSION env (tests / packaging may set this)
      2. version_stamp.txt next to this module — written at BUILD time by
         deploy-scalp.command with tauri.conf.json's real version. This is
         the reliable source for bundled apps (it travels inside the
         bundled backend/ resources).
      3. The existing VERSION file (key=value), in case it ever carries a
         real version instead of 'unknown'.
    Returns None if none resolve -> the check fails open (no nag).
    """
    # 1. Env override
    env_v = os.environ.get("SCALP_APP_VERSION")
    if env_v and env_v.strip().lower() != "unknown":
        return env_v.strip()

    # 2. Build-time stamp next to this file (most reliable for bundled app)
    stamp = Path(__file__).resolve().parent / "version_stamp.txt"
    try:
        if stamp.exists():
            v = _parse_version_file(stamp.read_text())
            if v:
                return v
    except Exception:
        pass

    # 3. The existing VERSION file (key=value or bare), wherever it lives
    for candidate in (
        Path.home() / ".scalp-app" / "VERSION",
        Path.home() / ".scalp-app" / "version.txt",
        Path(__file__).resolve().parents[3] / "VERSION",
    ):
        try:
            if candidate.exists():
                v = _parse_version_file(candidate.read_text())
                if v:
                    return v
        except Exception:
            pass

    return None


def _parse(v: str) -> tuple[int, ...]:
    """
    Parse a dotted version into a comparable tuple of ints. Tolerant:
    strips a leading 'v', ignores any non-numeric suffix on a part. Raises
    ValueError if there's nothing numeric to compare — caller treats that
    as 'can't compare -> fail open'.
    """
    v = v.strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    if not parts:
        raise ValueError(f"unparseable version: {v!r}")
    return tuple(parts)


def _is_older(current: str, minimum: str) -> bool:
    """
    True iff current < minimum. On ANY parse problem returns False
    (fail-open: if we can't be sure it's older, we don't nag).
    """
    try:
        c = _parse(current)
        m = _parse(minimum)
        # pad to equal length so (8,0) vs (8,0,1) compares correctly
        n = max(len(c), len(m))
        c += (0,) * (n - len(c))
        m += (0,) * (n - len(m))
        return c < m
    except Exception:
        return False


# --------------------------------------------------
# THE CHECK (called once at startup; safe to call again later)
# --------------------------------------------------

def check_for_update(timeout: float = 5.0):
    """
    One advisory check. Never raises. Updates version_state in place.
    Fail-open everywhere: any error leaves UPDATE_AVAILABLE False.
    """
    try:
        current = _read_current_version()
        version_state.CURRENT_VERSION = current

        resp = httpx.get(f"{LICENSE_SERVER_URL}/min_version", timeout=timeout)
        data = resp.json()
        minimum = data.get("min_version")
        version_state.MIN_VERSION = minimum

        # No minimum configured, or we can't read our own version ->
        # nothing to nag about.
        if not minimum or not current:
            version_state.UPDATE_AVAILABLE = False
            version_state.MESSAGE = ""
            return

        if _is_older(current, minimum):
            version_state.UPDATE_AVAILABLE = True
            version_state.MESSAGE = (
                f"A newer version is available (you have v{current}, "
                f"latest is v{minimum}). Please update when convenient."
            )
            write_audit_log(
                f"[VERSION] Update advised: current=v{current} < min=v{minimum}"
            )
        else:
            version_state.UPDATE_AVAILABLE = False
            version_state.MESSAGE = ""
    except Exception as e:
        # Fail OPEN — never let a version check disturb anything.
        version_state.UPDATE_AVAILABLE = False
        version_state.MESSAGE = ""
        write_audit_log(f"[VERSION] Advisory check skipped ({type(e).__name__})")


def snapshot() -> dict:
    """Plain dict for a system route to return to the UI."""
    return {
        "update_available": version_state.UPDATE_AVAILABLE,
        "min_version": version_state.MIN_VERSION,
        "current_version": version_state.CURRENT_VERSION,
        "message": version_state.MESSAGE,
    }
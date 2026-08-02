# backend/app/engine/ic_v1/ic_carry_store.py
#
# IC_V1 — ONE_NIGHT_MAX carry persistence (DA1, locked 2026-07-26)
# ============================================================================
# The overnight-carried group MUST survive an app restart: the desktop app is
# closed / the Mac sleeps overnight with near-certainty, while the positions
# and their GTTs remain live at the broker. Without this file, the restored
# process has no memory of the carry and the mandatory 09:16 close never
# fires — the single worst silent-loss mode of the IC_V2 amendment.
#
# Same atomic tempfile + fsync + os.replace pattern as the D7 day latch.
# Payload is versioned; an unknown version is treated as unreadable
# (CRITICAL alert path in the caller, never a silent drop).
#
# Lifecycle:
#   save_carry()  — at carry commit (session end, non-expiry entry day).
#   load_carry()  — at boot (ic_runtime) and defensively by the engine.
#   clear_carry() — ONLY after the morning square-off fully closed and
#                   reconciled every carried leg.
# ============================================================================

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.event_bus.audit_logger import write_audit_log

STATE_DIR    = Path.home() / ".scalp-app" / "state"
CARRY_PATH   = STATE_DIR / "IC_V1_carry.json"
# ── IC_RESTART ── mid-session snapshot (2026-07-31): survives a restart
# DURING market hours. Written on every group mutation, cleared when the
# group finalizes or the overnight carry commit supersedes it.
SESSION_PATH = STATE_DIR / "IC_V1_session.json"

CARRY_VERSION   = 1
SESSION_VERSION = 1


def save_carry(payload: dict) -> bool:
    """Atomic write. Returns True on success. The caller alerts on False —
    a failed carry save on a live overnight book is CRITICAL."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["version"] = CARRY_VERSION
        blob = json.dumps(payload, indent=1)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".ic_carry_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CARRY_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        write_audit_log(f"[IC][CARRY] snapshot saved "
                        f"legs={len(payload.get('legs') or [])} "
                        f"entry_date={payload.get('entry_date')}")
        return True
    except Exception as e:
        write_audit_log(f"[IC][CARRY][SAVE_FAIL] {e!r}")
        return False


def load_carry() -> Optional[dict]:
    """Returns the carry payload or None (absent OR unreadable — the caller
    distinguishes via carry_exists() and alerts on unreadable)."""
    try:
        if not CARRY_PATH.exists():
            return None
        d = json.loads(CARRY_PATH.read_text())
        if int(d.get("version") or 0) != CARRY_VERSION:
            write_audit_log(f"[IC][CARRY][VERSION_MISMATCH] "
                            f"{d.get('version')} != {CARRY_VERSION}")
            return None
        return d
    except Exception as e:
        write_audit_log(f"[IC][CARRY][READ_FAIL] {e!r}")
        return None


def carry_exists() -> bool:
    try:
        return CARRY_PATH.exists()
    except Exception:
        return False


def clear_carry():
    """Delete the snapshot. Called ONLY after full morning reconcile."""
    try:
        if CARRY_PATH.exists():
            CARRY_PATH.unlink()
            write_audit_log("[IC][CARRY] snapshot cleared")
    except Exception as e:
        write_audit_log(f"[IC][CARRY][CLEAR_FAIL] {e!r}")


# ── IC_RESTART ── session snapshot API (mirrors the carry API; quieter
# logging — this file is written on every mutation).

def save_session(payload: dict) -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["version"] = SESSION_VERSION
        blob = json.dumps(payload)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".ic_sess_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SESSION_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception as e:
        write_audit_log(f"[IC][SESSION][SAVE_FAIL] {e!r}")
        return False


def load_session():
    try:
        if not SESSION_PATH.exists():
            return None
        d = json.loads(SESSION_PATH.read_text())
        if int(d.get("version") or 0) != SESSION_VERSION:
            write_audit_log(f"[IC][SESSION][VERSION_MISMATCH] "
                            f"{d.get('version')} != {SESSION_VERSION}")
            return None
        return d
    except Exception as e:
        write_audit_log(f"[IC][SESSION][READ_FAIL] {e!r}")
        return None


def session_exists() -> bool:
    try:
        return SESSION_PATH.exists()
    except Exception:
        return False


def clear_session():
    try:
        if SESSION_PATH.exists():
            SESSION_PATH.unlink()
            write_audit_log("[IC][SESSION] snapshot cleared")
    except Exception as e:
        write_audit_log(f"[IC][SESSION][CLEAR_FAIL] {e!r}")

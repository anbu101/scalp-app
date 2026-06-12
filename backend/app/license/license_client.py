# backend/app/license/license_client.py
"""
License client for Scalp Terminal (PHASE 2 - replaces license_validator.py).

Talks to the license server, verifies Ed25519 tokens OFFLINE with the
embedded public key, and is the ONLY writer of license_state.

Design (locked in tracker):
  - Startup: LOCAL-ONLY token verification (milliseconds, no network on
    the critical path). One exception: if a key exists but the cached
    token is expired or clock-tamper is flagged (e.g. closed Friday,
    opened after a long gap), ONE short blocking heartbeat (6s timeout)
    runs so the user doesn't lose a session to a stale token while the
    server is perfectly reachable.
  - Heartbeat: background loop - on success sleep 6h, on failure retry
    in 30 min. Explicit server denials (revoked/expired/...) flip state
    immediately; transport failures never do (grace window covers them).
  - Token lifetime 4 days (server-side), GRACE banner when < 2 days left.
  - Clock-tamper guard: last seen server time is persisted; if the local
    clock is ever behind it (beyond small skew), status = CLOCK_TAMPER
    until a successful server contact refreshes reality.
  - EVERY public function is non-fatal: license problems update state,
    they never crash startup (fail-open pattern, like matplotlib).

Files under ~/.scalp-app/license/:
  license_key.txt        the SCLP-XXXX-XXXX-XXXX key
  token.jwt              latest server-issued token
  last_server_time.txt   epoch of last successful server contact
  license_meta.json      label / license_expires_at (display only)
  machine_id.txt         fingerprint cache (see machine_id.py)
"""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import jwt  # PyJWT - REMEMBER: add 'jwt' to spec hiddenimports + PyJWT to requirements.txt

from app.license import license_state
from app.license.license_state import LicenseStatus
from app.license.machine_id import get_machine_id
from app.event_bus.audit_logger import write_audit_log

# --------------------------------------------------
# CONSTANTS
# --------------------------------------------------

# Env override is for testing only; the constant is the real config.
LICENSE_SERVER_URL = os.environ.get(
    "SCALP_LICENSE_SERVER", "http://139.59.8.202:9100"
)

# >>> PASTE the contents of license_server_public_key.pem (saved on your
# >>> Mac by deploy-license-server.command) between the BEGIN/END lines.
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAVR6RdEguPjWsKRaSfAX8eqbyCbHJzbXiiDjYGvjKmEc=
-----END PUBLIC KEY-----"""

LICENSE_DIR = Path.home() / ".scalp-app" / "license"
KEY_FILE = LICENSE_DIR / "license_key.txt"
TOKEN_FILE = LICENSE_DIR / "token.jwt"
SERVER_TIME_FILE = LICENSE_DIR / "last_server_time.txt"
META_FILE = LICENSE_DIR / "license_meta.json"

HEARTBEAT_INTERVAL_OK = 6 * 3600       # 6h between successful heartbeats
HEARTBEAT_RETRY_FAIL = 30 * 60         # 30 min retry after a failure
GRACE_THRESHOLD_DAYS = 2.0             # token < 2 days left -> GRACE banner
CLOCK_SKEW_TOLERANCE = 300             # seconds of backward clock drift tolerated

# --------------------------------------------------
# SMALL FILE HELPERS (never raise)
# --------------------------------------------------

def _read_text(p: Path) -> str | None:
    try:
        if p.exists():
            v = p.read_text().strip()
            return v or None
    except Exception:
        pass
    return None


def _write_text(p: Path, v: str):
    try:
        LICENSE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(v)
    except Exception as e:
        write_audit_log(f"[LICENSE][WARN] Could not write {p.name}: {e}")


def _delete(p: Path):
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


def _set_state(status: LicenseStatus, message: str):
    license_state.LICENSE_STATUS = status
    license_state.LICENSE_MESSAGE = message
    if status not in (LicenseStatus.VALID, LicenseStatus.GRACE):
        license_state.ENTITLEMENTS = {}


# --------------------------------------------------
# LOCAL EVALUATION (no network)
# --------------------------------------------------

def _evaluate_local():
    """Derive license_state purely from files on disk + the public key."""
    key = _read_text(KEY_FILE)
    token = _read_text(TOKEN_FILE)

    # Display metadata (best effort)
    license_state.LABEL = ""
    license_state.LICENSE_EXPIRES_AT = ""
    try:
        meta = json.loads(_read_text(META_FILE) or "{}")
        license_state.LABEL = meta.get("label", "")
        license_state.LICENSE_EXPIRES_AT = meta.get("license_expires_at", "")
    except Exception:
        pass

    license_state.TIER = ""
    license_state.TOKEN_EXP = 0

    if not key:
        _set_state(LicenseStatus.UNACTIVATED, "Not activated - enter a license key")
        return

    if not token:
        _set_state(
            LicenseStatus.EXPIRED,
            "License needs renewal - connect to the internet",
        )
        return

    # 1. Signature (offline, embedded public key). verify_exp=False so we
    #    can still read claims of an expired token; expiry handled below.
    try:
        claims = jwt.decode(
            token,
            PUBLIC_KEY_PEM,
            algorithms=["EdDSA"],
            options={"verify_exp": False},
        )
    except Exception as e:
        _set_state(LicenseStatus.INVALID, f"License token invalid: {type(e).__name__}")
        return

    # 2. Machine binding
    if claims.get("machine_id") != get_machine_id():
        _set_state(LicenseStatus.INVALID, "License token is bound to a different machine")
        return

    license_state.TIER = claims.get("tier", "")
    license_state.TOKEN_EXP = int(claims.get("exp", 0))

    # 3. Clock-tamper guard
    stored_st = _read_text(SERVER_TIME_FILE)
    now = time.time()
    if stored_st:
        try:
            if now < float(stored_st) - CLOCK_SKEW_TOLERANCE:
                _set_state(
                    LicenseStatus.CLOCK_TAMPER,
                    "System clock is behind last verified server time - "
                    "fix the clock or reconnect",
                )
                return
        except Exception:
            pass

    # 4. Token expiry / grace
    exp = license_state.TOKEN_EXP
    if now >= exp:
        _set_state(LicenseStatus.EXPIRED, "License token expired - reconnect to renew")
        return

    license_state.ENTITLEMENTS = claims.get("entitlements") or {}
    days_left = (exp - now) / 86400
    if days_left < GRACE_THRESHOLD_DAYS:
        exp_date = license_state.LICENSE_EXPIRES_AT
        near_expiry = False
        if exp_date:
            try:
                from datetime import datetime as _dt
                near_expiry = (
                    _dt.strptime(exp_date, "%Y-%m-%d") - _dt.now()
                ).total_seconds() < GRACE_THRESHOLD_DAYS * 86400
            except Exception:
                pass
        if near_expiry:
            _set_state(
                LicenseStatus.GRACE,
                f"License expires on {exp_date} - contact the admin to extend",
            )
        else:
            _set_state(
                LicenseStatus.GRACE,
                f"License server not reached recently - {days_left:.1f} days of grace left",
            )
    else:
        _set_state(LicenseStatus.VALID, "License valid")


# --------------------------------------------------
# SERVER COMMUNICATION
# --------------------------------------------------

def _apply_server_response(data: dict):
    """Map a server JSON response onto local files + state."""
    status = data.get("status")

    if status == "ok":
        _write_text(TOKEN_FILE, data["token"])
        _write_text(SERVER_TIME_FILE, str(data.get("server_time", int(time.time()))))
        _write_text(META_FILE, json.dumps({
            "label": data.get("label", ""),
            "license_expires_at": data.get("license_expires_at", ""),
        }))
        _evaluate_local()
        return

    msg = data.get("message", "")
    if status == "revoked":
        _delete(TOKEN_FILE)
        _set_state(LicenseStatus.REVOKED, msg or "License revoked")
    elif status == "expired":
        _delete(TOKEN_FILE)
        _set_state(LicenseStatus.EXPIRED, msg or "License expired")
    elif status == "machine_mismatch":
        _set_state(LicenseStatus.INVALID, msg or "License bound to another machine")
    elif status == "unknown_key":
        _set_state(LicenseStatus.INVALID, msg or "License key not recognized")
    elif status == "not_activated":
        _set_state(LicenseStatus.INVALID, msg or "License unbound - activate again")
    # Unknown statuses: leave current state untouched.

    write_audit_log(f"[LICENSE] Server response status={status} -> {license_state.LICENSE_STATUS.value}")


def _heartbeat_sync(timeout: float = 10.0) -> bool:
    """
    One heartbeat. Returns True on ANY server response (including
    denials - those update state via _apply_server_response). Returns
    False ONLY on transport failure, which deliberately leaves the
    current state untouched (grace window logic).
    """
    key = _read_text(KEY_FILE)
    if not key:
        return False
    try:
        resp = httpx.post(
            f"{LICENSE_SERVER_URL}/heartbeat",
            json={"key": key, "machine_id": get_machine_id()},
            timeout=timeout,
        )
        _apply_server_response(resp.json())
        return True
    except Exception as e:
        write_audit_log(f"[LICENSE] Heartbeat unreachable ({type(e).__name__}) - grace window applies")
        return False


def activate(key: str) -> dict:
    """
    Called from POST /system/license/activate. Synchronous, short.
    Returns {"status": ..., "message": ...} for the UI.
    """
    key = (key or "").strip().upper()
    if not key:
        return {"status": "error", "message": "Empty license key"}
    try:
        resp = httpx.post(
            f"{LICENSE_SERVER_URL}/activate",
            json={"key": key, "machine_id": get_machine_id()},
            timeout=10.0,
        )
        data = resp.json()
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not reach license server ({type(e).__name__}) - check internet",
        }

    if data.get("status") == "ok":
        _write_text(KEY_FILE, key)
    _apply_server_response(data)
    write_audit_log(f"[LICENSE] Activation attempt -> {data.get('status')}")
    return {
        "status": data.get("status", "error"),
        "message": data.get("message", "Activated" if data.get("status") == "ok" else ""),
    }


# --------------------------------------------------
# PUBLIC ENTRY POINTS (called from api_server.py)
# --------------------------------------------------

def initialize_license():
    """
    Startup path. Local verification only - UNLESS a key exists but the
    token is expired/tampered, in which case ONE short blocking refresh
    runs (6s cap) so a weekend-stale token doesn't cost the session.
    NEVER raises.
    """
    try:
        LICENSE_DIR.mkdir(parents=True, exist_ok=True)
        _evaluate_local()
        if (
            license_state.LICENSE_STATUS
            in (LicenseStatus.EXPIRED, LicenseStatus.CLOCK_TAMPER)
            and _read_text(KEY_FILE)
        ):
            write_audit_log("[LICENSE] Stale token at startup - attempting one quick refresh")
            _heartbeat_sync(timeout=6.0)
    except Exception as e:
        _set_state(LicenseStatus.INVALID, f"License init error: {e}")
        write_audit_log(f"[LICENSE][ERROR] initialize_license: {e}")
    write_audit_log(
        f"[LICENSE] Status={license_state.LICENSE_STATUS.value} "
        f"tier={license_state.TIER or '-'} msg={license_state.LICENSE_MESSAGE}"
    )


async def heartbeat_loop():
    """
    Background task (launched from on_startup, runs forever).
    Keeps running in ALL states - an unrevoke or extension on the server
    revives a blocked app within one cycle, no restart needed for the
    status itself (strategy launch still happens at next restart -
    Option A enforcement).
    """
    await asyncio.sleep(5)  # let the port bind / boot settle first
    while True:
        ok = False
        try:
            if _read_text(KEY_FILE):
                ok = await asyncio.to_thread(_heartbeat_sync, 10.0)
            else:
                _evaluate_local()  # stays UNACTIVATED until a key appears
        except Exception as e:
            write_audit_log(f"[LICENSE][ERROR] heartbeat_loop: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL_OK if ok else HEARTBEAT_RETRY_FAIL)
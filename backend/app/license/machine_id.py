# backend/app/license/machine_id.py
"""
Machine fingerprint for license binding.

PHASE 2 REWRITE (v2) - changes vs v1:
  - Stable inputs ONLY: platform.system(), platform.machine(), MAC via
    uuid.getnode(). v1 also used gethostname() (changes with network on
    macOS) and platform.processor() (often empty / inconsistent).
  - Recomputed on EVERY launch. v1 trusted the cached machine_id.txt
    blindly forever, which meant copying that one file to a second
    laptop cloned the identity and defeated machine binding.
  - The cache file is now only a verification record: a mismatch between
    recomputed and cached is logged (hardware changed, or someone copied
    the file). Legitimate hardware changes go through the admin
    /license_rebind flow.
  - Fallback: if uuid.getnode() returns a randomly generated node (its
    multicast bit set - happens when no MAC is readable), the recomputed
    value would differ every process, so we fall back to the cached
    value to stay stable, and log it.
"""

import hashlib
import platform
import uuid
from pathlib import Path

LICENSE_DIR = Path.home() / ".scalp-app" / "license"
MACHINE_ID_FILE = LICENSE_DIR / "machine_id.txt"


def _stable_node() -> str:
    """MAC-derived node id, or '' if the value is randomly generated
    (multicast bit set) and therefore unstable across runs."""
    n = uuid.getnode()
    if (n >> 40) & 0x01:
        return ""
    return hex(n)


def _compute() -> str | None:
    node = _stable_node()
    if not node:
        return None
    raw = "|".join([platform.system(), platform.machine(), node])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_machine_id() -> str:
    """
    Returns the machine fingerprint, recomputed from stable hardware
    inputs on every call. Never raises.
    """
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)

    cached = None
    if MACHINE_ID_FILE.exists():
        try:
            cached = MACHINE_ID_FILE.read_text().strip() or None
        except Exception:
            cached = None

    computed = _compute()

    if computed is None:
        # No stable MAC available - stay on the cached identity if any.
        if cached:
            return cached
        # Last resort: persist a one-time random identity (stable via file).
        fallback = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        try:
            MACHINE_ID_FILE.write_text(fallback)
        except Exception:
            pass
        return fallback

    if cached and cached != computed:
        # Hardware changed OR the cache file was copied from another
        # machine. The recomputed value wins; server-side binding will
        # mismatch and the admin rebind flow resolves legitimate cases.
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(
                "[LICENSE][WARN] machine_id cache mismatch - "
                "recomputed fingerprint differs from cached"
            )
        except Exception:
            pass

    if cached != computed:
        try:
            MACHINE_ID_FILE.write_text(computed)
        except Exception:
            pass

    return computed
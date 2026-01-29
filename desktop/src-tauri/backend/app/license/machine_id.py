from pathlib import Path
import hashlib
import platform
import socket
import uuid

LICENSE_DIR = Path.home() / ".scalp-app" / "license"
MACHINE_ID_FILE = LICENSE_DIR / "machine_id.txt"


def _raw_fingerprint() -> str:
    """
    Stable inputs across reboot & upgrades.
    No personal data, only system-level identifiers.
    """
    parts = [
        platform.system(),
        platform.machine(),
        platform.processor(),
        socket.gethostname(),
        hex(uuid.getnode()),  # MAC-based, hashed later
    ]
    return "|".join(parts)


def _hash_fingerprint(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_machine_id() -> str:
    """
    Returns persistent machine_id.
    Generates once, then reuses.
    """
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)

    if MACHINE_ID_FILE.exists():
        return MACHINE_ID_FILE.read_text().strip()

    raw = _raw_fingerprint()
    machine_id = _hash_fingerprint(raw)

    MACHINE_ID_FILE.write_text(machine_id)
    return machine_id

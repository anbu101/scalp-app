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

── MACHINE_ID_STABLE_20261119 ──
uuid.getnode() returns the MAC of whichever interface the OS lists first,
and that order is not fixed: on 2026-11-19 Dhivakar's Intel Mac woke from a
battery-empty standby with its T2 bridge (en5) re-created at the end of the
list, getnode() moved to ap1 (a random MAC), and the licence bound to en5
was refused as "another machine". An identity is now kept as long as the MAC
it was made from is still on this machine: first the one the signed licence
token was bound to (`prefer`), then the cached one; only when neither MAC is
present is it recomputed as before. A copied file or token still fails on
another machine, which does not have that MAC.

── MACHINE_ID_HW_20261119 ──
The MAC is not unique: on every Intel Mac with a T2 chip the T2 bridge (en5)
is ac:de:48:00:11:22, so a licence copied to another such Mac validated. The
licence id is now made from the machine's hardware UUID (hardware_id()):
macOS IOPlatformUUID, Windows MachineGuid, Linux /etc/machine-id. The MAC id
above stays as the LEGACY id: the client sends both, and the server moves a
licence still bound to the legacy id onto the hardware id at its next
heartbeat. Until every licence has moved, a token bound to a legacy id stays
valid here under the MACHINE_ID_STABLE rules above.
"""

import hashlib
import platform
import re
import subprocess
import sys
import uuid
from pathlib import Path

LICENSE_DIR = Path.home() / ".scalp-app" / "license"
MACHINE_ID_FILE = LICENSE_DIR / "machine_id.txt"
HARDWARE_ID_FILE = LICENSE_DIR / "hardware_id.txt"   # MACHINE_ID_HW_20261119

_MAC_RE = re.compile(r"\bether ((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\b")


def _stable_node() -> str:
    """MAC-derived node id, or '' if the value is randomly generated
    (multicast bit set) and therefore unstable across runs."""
    n = uuid.getnode()
    if (n >> 40) & 0x01:
        return ""
    return hex(n)


def _id_for(node: str) -> str:
    raw = "|".join([platform.system(), platform.machine(), node])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute() -> str | None:
    node = _stable_node()
    if not node:
        return None
    return _id_for(node)


def _local_macs() -> list:
    """Every link-layer address on this machine, in no particular order."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["/sbin/ifconfig", "-a"], capture_output=True,
                                 text=True, timeout=3).stdout
            return _MAC_RE.findall(out)
        if sys.platform.startswith("linux"):
            return [p.read_text().strip() for p in Path("/sys/class/net").glob("*/address")]
    except Exception:
        pass
    return []


def _present_ids() -> set:
    """The fingerprint each MAC on this machine would give."""
    ids = set()
    for mac in _local_macs():
        try:
            n = int(mac.replace(":", ""), 16)
        except ValueError:
            continue
        if n and not (n >> 40) & 0x01:
            ids.add(_id_for(hex(n)))
    computed = _compute()
    if computed:
        ids.add(computed)
    return ids


def _log(msg: str) -> None:
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:
        pass


def legacy_machine_id(prefer: str | None = None) -> str:
    """
    The MAC-based fingerprint (MACHINE_ID_STABLE_20261119 rules). `prefer`
    is the machine_id a verified licence token was bound to: kept if its MAC
    is on this machine. Never raises.
    """
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)

    cached = None
    if MACHINE_ID_FILE.exists():
        try:
            cached = MACHINE_ID_FILE.read_text().strip() or None
        except Exception:
            cached = None

    present = _present_ids()
    if prefer and prefer in present:
        if cached != prefer:
            _log("[LICENSE] machine_id: kept the licensed identity - its network "
                 "address is still on this machine (interface order changed)")
            try:
                MACHINE_ID_FILE.write_text(prefer)
            except Exception:
                pass
        return prefer
    if cached and cached in present:
        return cached

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
        _log("[LICENSE][WARN] machine_id cache mismatch - "
             "recomputed fingerprint differs from cached")

    if cached != computed:
        try:
            MACHINE_ID_FILE.write_text(computed)
        except Exception:
            pass

    return computed


# ── MACHINE_ID_HW_20261119 ── hardware-UUID identity ────────────────────
_IOREG_RE = re.compile(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{32,40})"')


def _hardware_uuid() -> str:
    """The machine's hardware UUID, lower-case, or '' when unreadable."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = _IOREG_RE.search(out or "")
            return m.group(1).lower() if m else ""
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                v = winreg.QueryValueEx(k, "MachineGuid")[0]
            return str(v).strip().lower()
        if sys.platform.startswith("linux"):
            return Path("/etc/machine-id").read_text().strip().lower()
    except Exception:
        pass
    return ""


def hardware_id() -> str | None:
    """
    sha256 of the hardware UUID: unique per machine (T2 Macs included) and
    independent of network interfaces. platform.machine() is left out (an
    Intel build under Rosetta reports x86_64 on Apple Silicon). The value is
    cached, and the cache is used only when the live read fails (an ioreg
    timeout must not lock out a migrated licence). None when neither exists.
    """
    u = _hardware_uuid()
    if len(u) >= 16:
        hid = hashlib.sha256(f"scalp-mid-v2|{platform.system()}|{u}".encode("utf-8")).hexdigest()
        try:
            if not HARDWARE_ID_FILE.exists() or HARDWARE_ID_FILE.read_text().strip() != hid:
                LICENSE_DIR.mkdir(parents=True, exist_ok=True)
                HARDWARE_ID_FILE.write_text(hid)
        except Exception:
            pass
        return hid
    try:
        cached = HARDWARE_ID_FILE.read_text().strip()
        if len(cached) == 64:
            _log("[LICENSE][WARN] hardware id unreadable - using the cached one")
            return cached
    except Exception:
        pass
    return None


def get_machine_id(prefer: str | None = None) -> str:
    """
    The id this machine presents to the licence server. `prefer` is the
    machine_id a verified licence token is bound to: returned when it is
    this machine's hardware id, or a legacy MAC id whose MAC is still here
    (a token from before the move to hardware ids). Otherwise the hardware
    id, or the legacy id where no hardware id can be read. Never raises.
    """
    hid = hardware_id()
    if prefer and prefer == hid:
        return hid
    if prefer:
        leg = legacy_machine_id(prefer)
        if leg == prefer:
            return prefer
        return hid or leg
    return hid or legacy_machine_id()


def device_ids() -> dict:
    """The ids for /activate, /heartbeat, /liveness: machine_id, plus
    legacy_machine_id while it differs (the server moves a licence still
    bound to the legacy id onto machine_id)."""
    leg = legacy_machine_id()
    mid = hardware_id() or leg
    out = {"machine_id": mid}
    if leg and leg != mid:
        out["legacy_machine_id"] = leg
    return out

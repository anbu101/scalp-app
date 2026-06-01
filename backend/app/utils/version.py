from pathlib import Path
from datetime import datetime
import json
import os

VERSION_FILE = Path.home() / ".scalp-app" / "VERSION"


def _read_tauri_version() -> str:
    """
    Best-effort read of the app version from tauri.conf.json.
    Checks an env var first (if Tauri injects one), then known relative paths.
    Returns 'unknown' if nothing is found.
    """
    # 1. Env var, if the Tauri layer injects it at spawn time
    env_v = os.getenv("SCALP_VERSION")
    if env_v:
        return env_v

    # 2. Look for tauri.conf.json relative to the bundled backend / source tree
    candidates = [
        Path(__file__).resolve().parents[3] / "desktop" / "src-tauri" / "tauri.conf.json",
        Path(__file__).resolve().parents[2] / "tauri.conf.json",
        Path.cwd() / "desktop" / "src-tauri" / "tauri.conf.json",
    ]
    for p in candidates:
        try:
            if p.exists():
                conf = json.loads(p.read_text())
                # Tauri v2: top-level "version"; some configs nest under "package"
                v = conf.get("version") or conf.get("package", {}).get("version")
                if v:
                    return v
        except Exception:
            continue

    return "unknown"


def write_version_file() -> None:
    """
    Write ~/.scalp-app/VERSION so get_version() can report the real app version.
    Safe to call on every startup; cheap and non-fatal.
    """
    try:
        VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        version = _read_tauri_version()
        lines = [
            "app = scalp-app",
            f"version = {version}",
            f"installed_at = {datetime.utcnow().isoformat()}",
        ]
        VERSION_FILE.write_text("\n".join(lines) + "\n")
    except Exception:
        # Never let version writing break startup
        pass


def get_version() -> dict:
    if not VERSION_FILE.exists():
        return {
            "app": "scalp-app",
            "version": "unknown",
            "installed_at": None,
        }

    data = {}
    for line in VERSION_FILE.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()

    return data
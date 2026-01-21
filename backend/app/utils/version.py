from pathlib import Path

VERSION_FILE = Path.home() / ".scalp-app" / "VERSION"

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

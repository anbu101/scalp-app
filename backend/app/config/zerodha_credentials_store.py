import json
from pathlib import Path
from datetime import datetime

# 🔥 SINGLE SOURCE OF TRUTH (Docker volume)
CREDENTIALS_PATH = Path("/data/zerodha/credentials.json")


def load_credentials() -> dict | None:
    if not CREDENTIALS_PATH.exists():
        return None

    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except Exception:
        return None


def save_credentials(api_key: str, api_secret: str):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "api_key": api_key.strip(),
        "api_secret": api_secret.strip(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))


def clear_credentials():
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()

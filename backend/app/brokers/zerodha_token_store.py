from pathlib import Path
import json

TOKEN_FILE = Path("zerodha_token.json")


def save_access_token(token: str):
    TOKEN_FILE.write_text(json.dumps({"access_token": token}))


def load_access_token():
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text()).get("access_token")


def clear_access_token():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()

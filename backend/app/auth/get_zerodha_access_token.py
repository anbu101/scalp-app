from pathlib import Path

# -------------------------
# Internal state
# -------------------------

_access_token: str | None = None

_TOKEN_FILE = Path(".zerodha_token")


# -------------------------
# Token helpers
# -------------------------

def load_access_token() -> str | None:
    """
    GRACEFUL MODE:
    - Return access token if available
    - Return None if not logged in
    - NEVER raise during startup
    """
    global _access_token

    if _access_token:
        return _access_token

    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text().strip()
        if token:
            _access_token = token
            return token

    return None


def save_access_token(token: str):
    global _access_token
    _access_token = token
    _TOKEN_FILE.write_text(token)


def clear_access_token():
    global _access_token
    _access_token = None
    if _TOKEN_FILE.exists():
        _TOKEN_FILE.unlink()

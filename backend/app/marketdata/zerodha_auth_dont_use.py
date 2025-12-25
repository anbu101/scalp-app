import json
from pathlib import Path
from kiteconnect import KiteConnect


# Default location where access token is stored
TOKEN_PATH = Path.home() / ".scalp-app" / "zerodha_token.json"


def get_kite_client() -> KiteConnect:
    """
    Returns an authenticated KiteConnect client.

    Expects a token file created earlier via login flow:
    ~/.scalp-app/zerodha_token.json

    File format:
    {
        "api_key": "...",
        "access_token": "..."
    }
    """
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"Zerodha token file not found at {TOKEN_PATH}. "
            f"Run Zerodha login flow first."
        )

    with open(TOKEN_PATH, "r") as f:
        data = json.load(f)

    api_key = data.get("api_key")
    access_token = data.get("access_token")

    if not api_key or not access_token:
        raise ValueError(
            "Invalid zerodha_token.json. "
            "Expected keys: api_key, access_token"
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    return kite

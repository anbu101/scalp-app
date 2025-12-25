from kiteconnect import KiteConnect
from typing import Optional

from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import load_access_token


class ZerodhaManager:
    """
    SINGLE SOURCE OF TRUTH for Zerodha connectivity.

    - Loads API key / secret from credentials store
    - Loads access token
    - Exposes authenticated KiteConnect
    """

    def __init__(self):
        self._kite: Optional[KiteConnect] = None
        self._ready: bool = False
        self._init()

    def _init(self):
        creds = load_credentials()
        if not creds:
            return

        api_key = creds.get("api_key")
        if not api_key:
            return

        token = load_access_token()
        if not token:
            return

        kite = KiteConnect(api_key=api_key)
        try:
            kite.set_access_token(token)
            # lightweight sanity check
            kite.profile()
        except Exception:
            return

        self._kite = kite
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def get_kite(self) -> Optional[KiteConnect]:
        return self._kite

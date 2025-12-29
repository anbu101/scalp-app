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
    - Supports runtime refresh after login / token expiry
    """

    def __init__(self):
        self._kite: Optional[KiteConnect] = None
        self._ready: bool = False
        self._init()

    # --------------------------------------------------
    # INITIAL BOOTSTRAP (called once)
    # --------------------------------------------------

    def _init(self):
        """
        Initial attempt to bind Zerodha session.
        Silent failure is expected on cold boot.
        """
        self.refresh()

    # --------------------------------------------------
    # RUNTIME REFRESH (AUTHORITATIVE)
    # --------------------------------------------------

    def refresh(self) -> bool:
        """
        Re-load credentials + access token and validate session.

        Returns:
            True  -> broker ready
            False -> broker NOT ready
        """
        creds = load_credentials()
        if not creds:
            self._kite = None
            self._ready = False
            return False

        api_key = creds.get("api_key")
        if not api_key:
            self._kite = None
            self._ready = False
            return False

        token = load_access_token()
        if not token:
            self._kite = None
            self._ready = False
            return False

        try:
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(token)

            # 🔒 HARD VALIDATION (network + auth)
            kite.profile()

        except Exception:
            # Token expired / invalid / network issue
            self._kite = None
            self._ready = False
            return False

        # ✅ SUCCESS
        self._kite = kite
        self._ready = True
        return True

    # --------------------------------------------------
    # STATUS
    # --------------------------------------------------

    def is_ready(self) -> bool:
        """
        Returns cached readiness state.
        Does NOT auto-refresh.
        """
        return self._ready

    def get_kite(self) -> Optional[KiteConnect]:
        """
        Returns authenticated KiteConnect or None.
        """
        return self._kite

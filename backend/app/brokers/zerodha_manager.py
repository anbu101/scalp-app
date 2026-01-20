from kiteconnect import KiteConnect
from typing import Optional

from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import load_access_token as _load_access_token
from app.event_bus.audit_logger import write_audit_log


# ==================================================
# 🔒 Backward-compatible access token shim
# ==================================================

def load_access_token(kind: str = "trade"):
    """
    Shim layer to support:
    - legacy single-token auth (access_token.json)
    - new DATA token file (access_token_data.json)

    Does NOT modify existing auth flow.
    """
    if kind == "data":
        try:
            from pathlib import Path
            import json
            p = Path("/data/zerodha/access_token_data.json")
            if p.exists():
                return json.loads(p.read_text()).get("access_token")
        except Exception as e:
            write_audit_log(
                f"[ZERODHA_MANAGER][WARN] Failed reading DATA token ERR={e}"
            )
            return None

    # fallback to legacy trade token
    return _load_access_token()


class ZerodhaManager:
    """
    SINGLE SOURCE OF TRUTH for Zerodha connectivity.

    ARCHITECTURE (PRODUCTION SAFE):
    - One Zerodha account
    - One API key
    - TWO access tokens:
        - DATA token  → WebSocket / market data
        - TRADE token → Order placement / positions
    """

    def __init__(self):
        self._kite_trade: Optional[KiteConnect] = None
        self._kite_data: Optional[KiteConnect] = None

        self._trade_ready: bool = False
        self._data_ready: bool = False

        # 🔒 NEW: broker certainty flag
        self._broker_certain: bool = False

        self._init()

    # --------------------------------------------------
    # INITIAL BOOTSTRAP
    # --------------------------------------------------

    def _init(self):
        self.refresh()

    # --------------------------------------------------
    # RUNTIME REFRESH (AUTHORITATIVE, SAFE)
    # --------------------------------------------------

    def refresh(self) -> bool:
        """
        Refresh BOTH trade and data sessions.

        SAFETY RULES:
        - NEVER drop a valid session due to transient errors
        - Only replace session objects on SUCCESS
        - Trade session is AUTHORITATIVE

        Returns:
            True  -> trade session ready
            False -> trade session not ready
        """
        creds = load_credentials()
        if not creds:
            write_audit_log("[ZERODHA_MANAGER] No credentials → reset")
            self._reset()
            return False

        api_key = creds.get("api_key")
        if not api_key:
            write_audit_log("[ZERODHA_MANAGER] Missing api_key → reset")
            self._reset()
            return False

        trade_token = load_access_token("trade")
        data_token = load_access_token("data")

        # ----------------------------------------------
        # TRADE SESSION (MANDATORY)
        # ----------------------------------------------
        if trade_token:
            try:
                kite_trade = KiteConnect(api_key=api_key)
                kite_trade.set_access_token(trade_token)
                kite_trade.profile()

                # ✅ swap only on success
                self._kite_trade = kite_trade
                self._trade_ready = True
                self._broker_certain = True  # ✅ certainty restored

            except Exception as e:
                write_audit_log(
                    f"[ZERODHA_MANAGER][WARN] Trade refresh failed ERR={e}"
                )
                # ❌ do NOT destroy old session
                self._broker_certain = False
        else:
            self._kite_trade = None
            self._trade_ready = False
            self._broker_certain = False

        # ----------------------------------------------
        # DATA SESSION (OPTIONAL)
        # ----------------------------------------------
        if data_token:
            try:
                kite_data = KiteConnect(api_key=api_key)
                kite_data.set_access_token(data_token)
                kite_data.profile()

                self._kite_data = kite_data
                self._data_ready = True

            except Exception as e:
                write_audit_log(
                    f"[ZERODHA_MANAGER][WARN] Data refresh failed ERR={e}"
                )
        else:
            self._kite_data = None
            self._data_ready = False

        return self._trade_ready

    # --------------------------------------------------
    # INTERNAL RESET (HARD)
    # --------------------------------------------------

    def _reset(self):
        self._kite_trade = None
        self._kite_data = None
        self._trade_ready = False
        self._data_ready = False
        self._broker_certain = False

    # --------------------------------------------------
    # STATUS (BACKWARD COMPATIBLE)
    # --------------------------------------------------

    def is_ready(self) -> bool:
        return self._trade_ready

    def is_trade_ready(self) -> bool:
        return self._trade_ready

    def is_data_ready(self) -> bool:
        return self._data_ready

    def is_broker_certain(self) -> bool:
        """
        🔒 AUTHORITATIVE certainty signal.
        False during network / API instability.
        """
        return self._broker_certain

    # --------------------------------------------------
    # ACCESSORS (BACKWARD COMPATIBLE)
    # --------------------------------------------------

    def get_kite(self) -> Optional[KiteConnect]:
        """Backward compatible"""
        return self._kite_trade

    def get_trade_kite(self) -> Optional[KiteConnect]:
        return self._kite_trade

    def get_data_kite(self) -> Optional[KiteConnect]:
        return self._kite_data

# backend/app/brokers/zerodha_manager.py

from kiteconnect import KiteConnect
from typing import Optional
import json

from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import (
    load_access_token as _load_access_token,
    is_trading_enabled,
)
from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import APP_HOME
from app.brokers.zerodha_auth import is_token_valid, is_trading_enabled

# ==================================================
# 🔒 Backward-compatible access token shim
# ==================================================

def load_access_token(kind: str = "trade"):
    """
    Supports:
    - Legacy single-token auth
    - Split trade/data tokens
    """

    base = APP_HOME / "zerodha"

    if kind == "data":
        p = base / "access_token_data.json"
    else:
        p = base / "access_token_trade.json"

    if p.exists():
        try:
            return json.loads(p.read_text()).get("access_token")
        except Exception as e:
            write_audit_log(
                f"[ZERODHA_MANAGER][WARN] Failed reading {p.name} ERR={e}"
            )
            return None

    # legacy fallback (trade only)
    if kind == "trade":
        return _load_access_token()

    return None


# ==================================================
# Zerodha Manager
# ==================================================

class ZerodhaManager:
    """
    SINGLE SOURCE OF TRUTH for Zerodha connectivity.

    DESIGN PRINCIPLES:
    - refresh() is the ONLY place where validation happens
    - is_trade_ready() NEVER re-validates
    - No hidden state mutation
    - No zombie flags
    """

    def __init__(self):
        # -------------------------------------------------
        # Session objects
        # -------------------------------------------------
        self._kite_trade: Optional[KiteConnect] = None
        self._kite_data: Optional[KiteConnect] = None

        # -------------------------------------------------
        # Broker certainty flag
        # True only after a successful refresh
        # -------------------------------------------------
        self._broker_certain: bool = False

        # -------------------------------------------------
        # Backward-compatibility aliases
        # Some parts of the system may still reference
        # _trade_kite / _data_kite
        # -------------------------------------------------
        self._trade_kite = None
        self._data_kite = None

        self._last_ready_state = None
        # -------------------------------------------------
        # Initial refresh attempt
        # Safe even if token not yet available
        # -------------------------------------------------
        self.refresh()


    # --------------------------------------------------
    # RUNTIME REFRESH (ATOMIC + CLEAN)
    # --------------------------------------------------

    def refresh(self) -> bool:
        """
        Rebuild sessions from disk state.
        Fully resets internal state first.
        """

        # 🔥 Always reset first (prevents stale state)
        self._kite_trade = None
        self._kite_data = None
        self._broker_certain = False

        creds = load_credentials()
        if not creds:
            write_audit_log("[ZERODHA_MANAGER] No credentials found")
            return False

        api_key = creds.get("api_key")
        if not api_key:
            write_audit_log("[ZERODHA_MANAGER] Missing api_key")
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

                # 🔒 Validate ONCE here only
                kite_trade.profile()

                self._kite_trade = kite_trade
                self._broker_certain = True

                write_audit_log("[ZERODHA_MANAGER] Trade session refreshed")

            except Exception as e:
                write_audit_log(
                    f"[ZERODHA_MANAGER][WARN] Trade validation failed ERR={e}"
                )
                self._kite_trade = None
                return False
        else:
            return False

        # ----------------------------------------------
        # DATA SESSION (OPTIONAL)
        # ----------------------------------------------

        if data_token:
            try:
                kite_data = KiteConnect(api_key=api_key)
                kite_data.set_access_token(data_token)

                kite_data.profile()

                self._kite_data = kite_data

                write_audit_log("[ZERODHA_MANAGER] Data session refreshed")

            except Exception as e:
                write_audit_log(
                    f"[ZERODHA_MANAGER][WARN] Data validation failed ERR={e}"
                )
                self._kite_data = None

        return self.is_trade_ready()

    # --------------------------------------------------
    # STATUS (NO REVALIDATION HERE)
    # --------------------------------------------------

    def is_ready(self):
        """
        Broker readiness check.

        READY when:
        - token valid
        - trading enabled
        - trade session available

        Data session is OPTIONAL.
        """

        # Ensure trade session exists (auto-refresh)
        if self._kite_trade is None:
            try:
                self.refresh()
            except Exception as e:
                write_audit_log(f"[ZERODHA_MANAGER] refresh failed ERR={e}")
                return False

        # After refresh, verify token + trading
        if not is_token_valid():
            return False

        if not is_trading_enabled():
            return False

        state = (
            is_token_valid(),
            is_trading_enabled(),
            self._kite_trade is not None,
            self._kite_data is not None,
        )

        if state != self._last_ready_state:
            write_audit_log(
                f"[ZERODHA_MANAGER][READY_CHECK] "
                f"token={state[0]} "
                f"trading={state[1]} "
                f"trade_session={state[2]} "
                f"data_session={state[3]}"
            )
            self._last_ready_state = state

        return self._kite_trade is not None


    def is_trade_ready(self) -> bool:
        """
        Trade readiness is derived strictly from:
        - active kite object
        - trading enabled flag

        NO token revalidation here.
        """

        if self._kite_trade is None:
            return False

        if not is_trading_enabled():
            return False

        return True

    def is_data_ready(self) -> bool:
        return self._kite_data is not None

    def is_broker_certain(self) -> bool:
        return self._broker_certain

    # --------------------------------------------------
    # ACCESSORS
    # --------------------------------------------------

    def get_kite(self) -> Optional[KiteConnect]:
        return self._kite_trade

    def get_trade_kite(self) -> Optional[KiteConnect]:
        return self._kite_trade

    def get_data_kite(self) -> Optional[KiteConnect]:
        return self._kite_data
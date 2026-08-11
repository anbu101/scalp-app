# backend/app/brokers/zerodha_manager.py

from kiteconnect import KiteConnect
from typing import Optional
import json
import os
import threading
import time as _time

from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import (
    load_access_token as _load_access_token,
    is_trading_enabled,
    TOKEN_FILE as _LEGACY_TOKEN_FILE,          # ── TOKEN_ROTATE ──
    TRADE_TOKEN_FILE as _TRADE_TOKEN_FILE,
    DATA_TOKEN_FILE as _DATA_TOKEN_FILE,
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

        # ── TOKEN_ROTATE ── D9 (2026-08-11 incident). Zerodha rotates
        # access tokens daily; a manager whose kite was built yesterday
        # holds a valid-looking object with a DEAD token, and the old
        # code only refreshed when the kite was None — never on rotation.
        # Compounding it, multiple ZerodhaManager instances exist
        # (api_server, zerodha_routes, executor_factory, ad-hoc), and the
        # morning reconnect refreshed only the zerodha_routes one. The
        # fix is per-instance lazy self-healing: every accessor first
        # runs a cheap token-FILE staleness check (mtime stat, 2s TTL)
        # and triggers a full refresh() when the on-disk token differs
        # from the one the kite was built with. Every instance heals
        # itself on first use after the morning login — no cross-module
        # rewiring, no restart. Bonus: the first login after boot also
        # brings sessions up without a restart.
        self._built_trade_token: Optional[str] = None
        self._built_data_token: Optional[str] = None
        self._token_file_sig = None          # mtime tuple at last refresh
        self._rotate_lock = threading.Lock()
        self._last_rotate_check = 0.0
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

        # ── TOKEN_ROTATE ── capture the file signature BEFORE reading the
        # tokens: if a login lands mid-refresh, the next accessor check
        # sees a newer mtime and refreshes again rather than missing it.
        self._token_file_sig = self._token_files_sig()

        # 🔥 Always reset first (prevents stale state)
        self._kite_trade = None
        self._kite_data = None
        self._broker_certain = False
        self._built_trade_token = None      # ── TOKEN_ROTATE ──
        self._built_data_token = None

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
                self._built_trade_token = trade_token   # ── TOKEN_ROTATE ──

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
                self._built_data_token = data_token     # ── TOKEN_ROTATE ──

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

    # ── TOKEN_ROTATE BEGIN ─────────────────────────────────────────────
    @staticmethod
    def _token_files_sig():
        """mtime tuple over the three token files (None where absent).
        stat-only — no reads, no parsing — cheap enough for hot paths."""
        sig = []
        for p in (_TRADE_TOKEN_FILE, _DATA_TOKEN_FILE, _LEGACY_TOKEN_FILE):
            try:
                sig.append(os.stat(p).st_mtime)
            except OSError:
                sig.append(None)
        return tuple(sig)

    def _maybe_rotate(self):
        """Self-heal on daily token rotation: if a token file changed on
        disk since this instance's kites were built AND the token string
        actually differs, run a full refresh() (the single place where
        validation happens, per this class's doctrine). Fail-open: any
        error here leaves the existing sessions untouched. 2s TTL keeps
        the stat cost negligible under per-second pollers."""
        now = _time.time()
        if now - self._last_rotate_check < 2.0:
            return
        with self._rotate_lock:
            if now - self._last_rotate_check < 2.0:
                return
            self._last_rotate_check = now
            try:
                sig = self._token_files_sig()
                if sig == self._token_file_sig:
                    return
                new_trade = load_access_token("trade")
                new_data = load_access_token("data")
                if (new_trade == self._built_trade_token
                        and new_data == self._built_data_token):
                    # files rewritten with identical tokens (save_access_token
                    # rewrites all three on every login) — nothing rotated
                    self._token_file_sig = sig
                    return
                write_audit_log(
                    "[ZERODHA_MANAGER][TOKEN_ROTATE] token change on disk "
                    "detected — rebuilding sessions")
                self.refresh()
            except Exception as e:
                # fail-open: a disk hiccup must never kill a live session
                write_audit_log(
                    f"[ZERODHA_MANAGER][TOKEN_ROTATE][WARN] check failed "
                    f"ERR={e}")
    # ── TOKEN_ROTATE END ───────────────────────────────────────────────

    def get_kite(self) -> Optional[KiteConnect]:
        self._maybe_rotate()                 # ── TOKEN_ROTATE ──
        return self._kite_trade

    def get_trade_kite(self) -> Optional[KiteConnect]:
        self._maybe_rotate()                 # ── TOKEN_ROTATE ──
        return self._kite_trade

    def get_data_kite(self) -> Optional[KiteConnect]:
        self._maybe_rotate()                 # ── TOKEN_ROTATE ──
        return self._kite_data
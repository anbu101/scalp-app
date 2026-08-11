# backend/app/brokers/angel_manager.py
# ============================================================
# ACC2 BEGIN — Angel One (secondary account) session manager
#
# Mirrors ZerodhaManager design principles:
#   - refresh() is the ONLY place where validation happens
#   - is_trade_ready() NEVER re-validates (cheap, derived state)
#   - No hidden state mutation, no zombie flags
#
# Angel-specific facts (probe-verified 2026-08-07):
#   - Login: POST loginByPassword with clientcode + pin + TOTP
#     -> data.jwtToken; session dies at IST midnight (NSE daily
#     logout mandate), so readiness includes an IST-date check.
#   - Reads are not IP-gated; writes fail AG7002 from an
#     unregistered IP (handled in the executor, not here).
# ============================================================

import datetime as dt
import threading                              # ── TOKEN_ROTATE ──
import time                                   # ── TOKEN_ROTATE ──
from typing import Optional

import requests

from app.config.angel_credentials_store import (
    load_credentials,
    load_session,
    save_session,
)
from app.event_bus.audit_logger import write_audit_log

try:
    import pyotp  # NEW backend dependency (requirements.txt + bundle)
except ImportError:  # pragma: no cover
    pyotp = None

ANGEL_BASE = "https://apiconnect.angelone.in"
EP_LOGIN = ANGEL_BASE + "/rest/auth/angelbroking/user/v1/loginByPassword"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ist_now() -> dt.datetime:
    return dt.datetime.now(tz=IST)


class AngelManager:
    """
    SINGLE SOURCE OF TRUTH for Angel One connectivity (secondary account).
    """

    def __init__(self, public_ip: Optional[str] = None):
        self._jwt: Optional[str] = None
        self._jwt_issued_at: Optional[dt.datetime] = None
        self._broker_certain: bool = False
        self._last_error: Optional[str] = None
        self._public_ip = public_ip or "127.0.0.1"

        # ── TOKEN_ROTATE ── day-roll auto-heal (2026-08-11, mirrors the
        # Zerodha D9 fix). Angel sessions die at IST midnight and this
        # class already DETECTS that (is_trade_ready rejects yesterday's
        # jwt) — but nothing HEALED it: auth_headers() returned None, no
        # request went out, so the executor's auth-error relogin hook
        # never fired. After an overnight app run, every ACC2 operation
        # sat fail-closed until a manual Force Login. Since Angel login
        # is fully programmatic (TOTP), auth_headers now attempts ONE
        # cooldown-guarded automatic re-login when the jwt exists but is
        # date-stale. jwt=None (no creds / disabled / failed boot login)
        # keeps its existing behaviour — boot and manual paths own that.
        self._auto_login_lock = threading.Lock()
        self._auto_login_last_ts = 0.0
        self._AUTO_LOGIN_COOLDOWN_S = 180.0   # never hammer EP_LOGIN

        # Boot path: reuse a persisted same-IST-day session if present,
        # otherwise attempt one fresh login. Safe if creds absent.
        self._adopt_persisted_session()
        if self._jwt is None:
            self.refresh()

    # --------------------------------------------------
    # HEADERS
    # --------------------------------------------------

    def _headers(self, with_auth: bool) -> dict:
        creds = load_credentials() or {}
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._public_ip,
            "X-ClientPublicIP": self._public_ip,
            "X-MACAddress": "aa:bb:cc:dd:ee:ff",
            "X-PrivateKey": creds.get("api_key", ""),
        }
        if with_auth and self._jwt:
            h["Authorization"] = "Bearer " + self._jwt
        return h

    # --------------------------------------------------
    # PERSISTED SESSION ADOPTION (restart mid-day)
    # --------------------------------------------------

    def _adopt_persisted_session(self) -> None:
        sess = load_session()
        if not sess:
            return
        try:
            issued = dt.datetime.fromisoformat(sess["issued_at"])
        except Exception:
            return
        if issued.astimezone(IST).date() != _ist_now().date():
            return  # stale: sessions die at IST midnight
        self._jwt = sess.get("jwt_token") or None
        self._jwt_issued_at = issued
        if self._jwt:
            self._broker_certain = True
            write_audit_log("[ANGEL_MANAGER] Adopted persisted same-day session")

    # --------------------------------------------------
    # RUNTIME REFRESH (ATOMIC + CLEAN)
    # Full TOTP login. This is also the Force Login path.
    # --------------------------------------------------

    def refresh(self) -> bool:
        # Always reset first (prevents stale state)
        self._jwt = None
        self._jwt_issued_at = None
        self._broker_certain = False
        self._last_error = None

        creds = load_credentials()
        if not creds or not creds.get("enabled", True):
            self._last_error = "no_credentials"
            write_audit_log("[ANGEL_MANAGER] No credentials / disabled")
            return False

        if pyotp is None:
            self._last_error = "pyotp_missing"
            write_audit_log("[ANGEL_MANAGER][ERR] pyotp not installed")
            return False

        try:
            totp = pyotp.TOTP(creds["totp_secret"]).now()
        except Exception as e:
            self._last_error = f"totp_secret_invalid:{e}"
            write_audit_log(f"[ANGEL_MANAGER][ERR] TOTP secret invalid ERR={e}")
            return False

        try:
            r = requests.post(
                EP_LOGIN,
                headers=self._headers(with_auth=False),
                json={
                    "clientcode": creds["client_code"],
                    "password": creds["pin"],
                    "totp": totp,
                    "state": "scalp",
                },
                timeout=20,
            )
            body = r.json()
        except Exception as e:
            self._last_error = f"login_network:{e}"
            write_audit_log(f"[ANGEL_MANAGER][WARN] Login network error ERR={e}")
            return False

        if not (body.get("status") is True and (body.get("data") or {}).get("jwtToken")):
            # NEVER log the request payload (contains PIN/TOTP).
            self._last_error = f"login_rejected:{body.get('message')}"
            write_audit_log(
                f"[ANGEL_MANAGER][WARN] Login rejected "
                f"msg={body.get('message')} code={body.get('errorcode') or body.get('errorCode')}"
            )
            return False

        self._jwt = body["data"]["jwtToken"]
        self._jwt_issued_at = _ist_now()
        self._broker_certain = True
        save_session(self._jwt, self._jwt_issued_at.isoformat())
        write_audit_log("[ANGEL_MANAGER] Trade session refreshed (login OK)")
        return True

    # --------------------------------------------------
    # STATUS (NO REVALIDATION HERE)
    # --------------------------------------------------

    def is_trade_ready(self) -> bool:
        if self._jwt is None or self._jwt_issued_at is None:
            return False
        # Session dies at IST midnight — derived check only, no network.
        if self._jwt_issued_at.astimezone(IST).date() != _ist_now().date():
            return False
        return True

    def is_broker_certain(self) -> bool:
        return self._broker_certain

    def last_error(self) -> Optional[str]:
        return self._last_error

    def status(self) -> dict:
        """For the Connections page card."""
        return {
            "broker": "ANGELONE",
            "connected": self.is_trade_ready(),
            "last_login": (
                self._jwt_issued_at.isoformat() if self._jwt_issued_at else None
            ),
            "last_error": self._last_error,
        }

    # --------------------------------------------------
    # ACCESSORS
    # --------------------------------------------------

    def get_jwt(self) -> Optional[str]:
        return self._jwt if self.is_trade_ready() else None

    def auth_headers(self) -> Optional[dict]:
        """None when not trade-ready — callers must fail closed on None."""
        if not self.is_trade_ready():
            self._maybe_auto_relogin()        # ── TOKEN_ROTATE ──
        if not self.is_trade_ready():
            return None
        return self._headers(with_auth=True)

    # ── TOKEN_ROTATE BEGIN ─────────────────────────────────────────────
    def _maybe_auto_relogin(self) -> None:
        """Self-heal the DAY-ROLL case only: a jwt that exists but was
        issued on a previous IST date gets one automatic re-login per
        cooldown window. Fail-open on any error — worst case is exactly
        today's behaviour (fail-closed None from auth_headers)."""
        try:
            if self._jwt is None or self._jwt_issued_at is None:
                return                        # no-session case: not ours
            if self._jwt_issued_at.astimezone(IST).date() == _ist_now().date():
                return                        # same-day: not stale
            now = time.time()
            if now - self._auto_login_last_ts < self._AUTO_LOGIN_COOLDOWN_S:
                return
            with self._auto_login_lock:
                if time.time() - self._auto_login_last_ts \
                        < self._AUTO_LOGIN_COOLDOWN_S:
                    return                    # another thread just tried
                self._auto_login_last_ts = time.time()
                # re-check under the lock: a racing thread may have
                # already refreshed a same-day session
                if (self._jwt_issued_at is not None
                        and self._jwt_issued_at.astimezone(IST).date()
                        == _ist_now().date()):
                    return
                write_audit_log("[ANGEL_MANAGER][TOKEN_ROTATE] session "
                                "date-stale (IST day rolled) — automatic "
                                "re-login")
                self.refresh()
        except Exception as e:
            write_audit_log(f"[ANGEL_MANAGER][TOKEN_ROTATE][WARN] "
                            f"auto re-login skipped ERR={e}")
    # ── TOKEN_ROTATE END ───────────────────────────────────────────────

    # --------------------------------------------------
    # AUTH-ERROR HOOK (one intraday auto re-login, D3)
    # Executor calls this on an auth-class error; at most one
    # automatic retry per incident is enforced by the caller.
    # --------------------------------------------------

    def relogin_once(self) -> bool:
        write_audit_log("[ANGEL_MANAGER] Intraday re-login attempt")
        return self.refresh()

# ACC2 END
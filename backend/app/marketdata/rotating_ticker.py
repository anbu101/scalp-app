# backend/app/marketdata/rotating_ticker.py
#
# ── TOKEN_ROTATE ── D10 (2026-08-11 incident)
#
# WHY: kiteconnect.KiteTicker freezes api_key + access_token into
# `socket_url` at construction. Zerodha rotates access tokens daily, so a
# ticker built yesterday reconnect-loops forever with a dead token — the
# observed symptom was hours of `[WS] Error 1006 ... (403 - Forbidden)`
# across every WS engine and a 13-hour FUT-tick drought, curable only by
# a full app restart.
#
# HOW: this subclass re-reads the CURRENT token from the on-disk token
# file (the same files the morning login writes) at the two moments a new
# handshake is about to happen:
#
#   1. connect()      — fresh URL before the first dial
#   2. _on_reconnect() — fired by KiteTickerClientFactory from
#      clientConnectionFailed/clientConnectionLost BEFORE retry() dials
#      again (verified against kiteconnect 5.x ticker.py). Between
#      retries the handshake URL lives in the autobahn factory's session
#      parameters, so we push the rotated URL in with
#      factory.setSessionParameters(), preserving origin/protocols/
#      useragent/headers/proxy explicitly (setSessionParameters
#      overwrites every field it is not given).
#
# The net effect: after the user's morning Zerodha login rewrites the
# token files, every reconnect-looping ticker self-heals on its next
# retry (retries run every few seconds during an auth storm) — no app
# restart, no engine watchdog changes, no new threads.
#
# SCOPE / SAFETY:
#   * Drop-in: constructor signature is KiteTicker's plus optional
#     `kind` ("data"|"trade" — which token file to follow) and
#     `token_provider` (callable overriding the file read; used by
#     tests). Engines change exactly one line.
#   * Fail-open everywhere: any error in rotation logic leaves the
#     ticker behaving exactly like a stock KiteTicker.
#   * If the token has NOT changed, this class is behaviourally
#     identical to KiteTicker (URL untouched, super() called).

from typing import Callable, Optional

from kiteconnect import KiteTicker

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:                                    # standalone use
    def write_audit_log(msg: str) -> None:
        print(msg)


def _file_token(kind: str) -> Optional[str]:
    """Read the current access token for `kind` from disk (lazy import to
    avoid a brokers<->marketdata import cycle at module load)."""
    from app.brokers.zerodha_manager import load_access_token
    return load_access_token(kind)


class RotatingKiteTicker(KiteTicker):
    """KiteTicker that picks up daily access-token rotation on its next
    (re)connect instead of dying with the old token."""

    def __init__(self, api_key, access_token, *args,
                 kind: str = "data",
                 token_provider: Optional[Callable[[], Optional[str]]] = None,
                 **kwargs):
        super().__init__(api_key, access_token, *args, **kwargs)
        self._rk_api_key = api_key
        self._rk_last_token = access_token
        self._rk_kind = kind
        self._rk_provider = token_provider or (lambda: _file_token(kind))

    # -- internals ----------------------------------------------------

    def _rk_rotated_url(self) -> Optional[str]:
        """Fresh socket URL when the on-disk token differs from the one
        currently baked into this ticker; None when unchanged/unavailable."""
        try:
            tok = self._rk_provider()
        except Exception as e:
            write_audit_log(f"[WS][TOKEN_ROTATE][WARN] token read failed "
                            f"kind={self._rk_kind} ERR={e}")
            return None
        if not tok or tok == self._rk_last_token:
            return None
        self._rk_last_token = tok
        return "{root}?api_key={api_key}&access_token={access_token}".format(
            root=self.root, api_key=self._rk_api_key, access_token=tok)

    def _rk_apply_to_factory(self, url: str) -> None:
        """Push a rotated URL into the live autobahn factory so the NEXT
        retry handshakes with it. Preserves every other session parameter
        explicitly — setSessionParameters overwrites fields it isn't
        given."""
        f = getattr(self, "factory", None)
        if f is None:
            return
        f.setSessionParameters(
            url=url,
            origin=getattr(f, "origin", None),
            protocols=getattr(f, "protocols", None),
            useragent=getattr(f, "useragent", None),
            headers=getattr(f, "headers", None),
            proxy=getattr(f, "proxy", None),
        )

    # -- KiteTicker overrides -----------------------------------------

    def connect(self, **kwargs):
        try:
            url = self._rk_rotated_url()
            if url:
                self.socket_url = url
                write_audit_log(f"[WS][TOKEN_ROTATE] kind={self._rk_kind} "
                                f"connecting with rotated token")
        except Exception as e:
            write_audit_log(f"[WS][TOKEN_ROTATE][WARN] connect-time "
                            f"rotation skipped ERR={e}")
        return super().connect(**kwargs)

    def _on_reconnect(self, attempts_count):
        """Factory fires this BEFORE scheduling the next retry — the one
        moment a rotated token can be injected into an auth-failing
        reconnect loop."""
        try:
            url = self._rk_rotated_url()
            if url:
                self.socket_url = url
                self._rk_apply_to_factory(url)
                write_audit_log(
                    f"[WS][TOKEN_ROTATE] kind={self._rk_kind} factory URL "
                    f"rotated on retry attempt={attempts_count}")
        except Exception as e:
            write_audit_log(f"[WS][TOKEN_ROTATE][WARN] retry-time rotation "
                            f"skipped ERR={e}")
        return super()._on_reconnect(attempts_count)
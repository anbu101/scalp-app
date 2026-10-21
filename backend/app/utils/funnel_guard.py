# backend/app/utils/funnel_guard.py
# >>> TS_FUNNEL_SCOPE_20261017 <<<
"""
Funnel guard: the backend side of the Tailscale Funnel scope fix.

Until TS_FUNNEL_SCOPE_20261017 the desktop app ran `tailscale funnel --bg 47321`,
which published EVERY route on this backend to the public internet at
https://<machine>.ts.net with no API auth behind it. The desktop runtime now
funnels only /zerodha/callback. This middleware enforces the same rule from
inside the backend, so the API stays private even if an old Funnel config is
still active (an older app build, an old Tailscale CLI, or a manual command).

Rule: a request that arrived through Tailscale's serve/funnel proxy may only be
    GET /zerodha/callback          (Kite's login redirect)
Everything else proxied by Tailscale gets a bare 404.

How a proxied request is recognised (both are set by tailscaled itself, which
also strips any client-supplied copies, so they cannot be forged from outside):
  * Tailscale-Funnel-Request: ?1     -> came in from the public internet
  * X-Forwarded-Host: *.ts.net       -> came in via `tailscale serve`/funnel

Requests that do NOT come through Tailscale's proxy are untouched: the Tauri
webview (127.0.0.1:47321), the phone on the tailnet (http://100.x.y.z:47321),
LAN access, scripts on this machine.

Pure ASGI, no dependencies, so it costs nothing on the normal path.
"""

import time

FENCE = "TS_FUNNEL_SCOPE_20261017"

CALLBACK_PATH = "/zerodha/callback"

# (method, path) pairs a Tailscale-proxied request may reach.
ALLOWED = frozenset({("GET", CALLBACK_PATH)})

_LOG_EVERY_SEC = 10.0


def _audit(msg):
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:
        pass


def _header_values(scope, name):
    """All values of a (lower-case, bytes) header name, decoded latin-1."""
    out = []
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            try:
                out.append(v.decode("latin-1"))
            except Exception:
                out.append("")
    return out


def is_tailscale_proxied(scope):
    """True if tailscaled's serve/funnel proxy forwarded this request."""
    if any(v.strip() for v in _header_values(scope, b"tailscale-funnel-request")):
        return True
    for v in _header_values(scope, b"x-forwarded-host"):
        host = v.split(",")[0].strip().lower()
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host.endswith(".ts.net"):
            return True
    return False


class FunnelGuard:
    """ASGI middleware. Add it as the OUTERMOST middleware."""

    def __init__(self, app):
        self.app = app
        self._last_log = 0.0
        self._suppressed = 0

    def _log_block(self, scope, method, path):
        now = time.monotonic()
        if now - self._last_log < _LOG_EVERY_SEC:
            self._suppressed += 1
            return
        fwd = (_header_values(scope, b"x-forwarded-for") or ["?"])[0]
        extra = f" (+{self._suppressed} more suppressed)" if self._suppressed else ""
        _audit(f"[FUNNEL_GUARD] blocked {method} {path} via Tailscale proxy from {fwd}{extra}")
        self._last_log = now
        self._suppressed = 0

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket") or not is_tailscale_proxied(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""

        # Older tailscaled builds proxy an exact-mount request to "<target>/"
        # (trailing slash). FastAPI would answer that with a 307 to an http://
        # URL that does not exist on Funnel, so normalise it here instead.
        if path == CALLBACK_PATH + "/":
            scope = dict(scope)
            scope["path"] = CALLBACK_PATH
            scope["raw_path"] = CALLBACK_PATH.encode("ascii")
            path = CALLBACK_PATH

        if stype == "http" and (scope.get("method", "").upper(), path) in ALLOWED:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "WS") if stype == "http" else "WS"
        self._log_block(scope, method, path)

        if stype == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return

        body = b"Not Found"
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

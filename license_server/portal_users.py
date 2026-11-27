"""
license_server/portal_users.py — ── PORTAL_USERS_ADMIN_20261127 ── (TD-113)

The License Admin page manages Fleet Monitor portal accounts. The monitor
(scalp-monitor, 127.0.0.1:9200 on this droplet) owns the accounts; this module
only forwards the admin page's calls to its internal API, with the token the
monitor wrote to its secrets folder. stdlib only.

No password ever passes through here: an invite or reset returns a one-time
HTTPS link that the person opens on the portal to set their own password and
authenticator.

  forward(method, path, body=None) -> (status, dict)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MONITOR_URL = os.environ.get("LICSRV_MONITOR_URL", "http://127.0.0.1:9200")
TOKEN_FILE = Path(os.environ.get("LICSRV_MONITOR_TOKEN", "/opt/scalp-monitor/secrets/internal_token.txt"))
ACTIONS = ("reset", "disable", "enable", "unlock", "remove", "cancel_invite")


def _token() -> Optional[str]:
    try:
        t = TOKEN_FILE.read_text().strip()
        return t or None
    except Exception:
        return None


def forward(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    tok = _token()
    if not tok:
        return 503, {"ok": False, "error": "Fleet Monitor is not installed on this droplet "
                                           "(no internal token) — run monitor_server/deploy-monitor.command"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MONITOR_URL + path, data=data, method=method,
                                 headers={"X-Internal-Token": tok, "X-Admin-Name": "license-admin",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"ok": False, "error": f"Fleet Monitor answered HTTP {e.code}"}
    except Exception as e:
        return 503, {"ok": False, "error": f"Fleet Monitor not reachable ({type(e).__name__})"}

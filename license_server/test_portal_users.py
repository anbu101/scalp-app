# license_server/test_portal_users.py
# ── PORTAL_USERS_ADMIN_20261127 ── (TD-113) TC-LICPU-0001…0007
# ── MONITOR_DEPLOY_FILES_20261127 ── (DEF-110) TC-LICPU-0008
# ── PORTAL_COPY_FIX_20261127 ── (DEF-111) TC-LICPU-0009
# The License Admin page manages Fleet Monitor portal accounts: the license
# server forwards /admin/portal/* to the monitor's internal API with its token.
# A fake monitor (http.server on a free port) records what it receives.
#   cd license_server && python3 test_portal_users.py
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="licsrv_pu_"))
(TMP / "secrets").mkdir()
(TMP / "secrets" / "admin_secret.txt").write_text("test-admin-secret")
(TMP / "mon_token.txt").write_text("T" * 43 + "\n")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey   # noqa: E402
from cryptography.hazmat.primitives import serialization                          # noqa: E402
(TMP / "secrets" / "private_key.pem").write_bytes(Ed25519PrivateKey.generate().private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

SEEN = []


class Mon(BaseHTTPRequestHandler):
    def _go(self, method):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        SEEN.append({"method": method, "path": self.path, "token": self.headers.get("x-internal-token"),
                     "by": self.headers.get("x-admin-name"), "body": body})
        if self.path.endswith("/invite") and (body or {}).get("username") == "taken":
            code, out = 400, {"ok": False, "error": "taken already has an account — use Reset"}
        elif self.path.startswith("/internal/users/") and self.path.count("/") == 4:
            code, out = 200, {"ok": True, "url": "https://x/setup/abc", "expires": 1, "username": "u", "role": "viewer"}
        elif self.path == "/internal/users/invite":
            code, out = 200, {"ok": True, "url": "https://x/setup/abc", "expires": 1,
                              "username": body["username"], "role": body["role"]}
        else:
            code, out = 200, {"ok": True, "users": [{"username": "ravi"}], "invites": []}
        b = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def log_message(self, *a):
        pass


mon = HTTPServer(("127.0.0.1", 0), Mon)
threading.Thread(target=mon.serve_forever, daemon=True).start()
os.environ.update(LICSRV_SECRETS_DIR=str(TMP / "secrets"), LICSRV_DB=str(TMP / "licenses.db"),
                  LICSRV_DATA_DIR=str(TMP / "data"), LICSRV_MONITOR_URL=f"http://127.0.0.1:{mon.server_address[1]}",
                  LICSRV_MONITOR_TOKEN=str(TMP / "mon_token.txt"))
sys.path.insert(0, str(HERE))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1
        print(f"  PASS  {n}")
    else:
        FAILS.append(n)
        print(f"  FAIL  {n}  [{str(d)[:300]}]")


import notify          # noqa: E402
notify.start_expiry_watcher = lambda fn: None
import liveness_watch  # noqa: E402
liveness_watch.start_watcher = lambda: None
import portal_users    # noqa: E402
import server          # noqa: E402
from fastapi import HTTPException   # noqa: E402

A = "test-admin-secret"


def status_of(fn):
    try:
        return 200, fn()
    except HTTPException as e:
        return e.status_code, e.detail


r = [status_of(lambda: server.admin_portal_users(x_admin_secret="nope")),
     status_of(lambda: server.admin_portal_invite(server.PortalInviteRequest(username="x"), x_admin_secret=None)),
     status_of(lambda: server.admin_portal_action(server.PortalActionRequest(username="x", action="reset"),
                                                  x_admin_secret="nope"))]
check("TC-LICPU-0001 every portal-user endpoint needs the admin secret (nothing reaches the monitor)",
      all(c == 401 for c, _ in r) and SEEN == [], (r, SEEN))

c, d = status_of(lambda: server.admin_portal_users(x_admin_secret=A))
check("TC-LICPU-0002 the list is forwarded to the monitor's internal API with its token",
      c == 200 and d["users"] == [{"username": "ravi"}] and SEEN[-1]["path"] == "/internal/users"
      and SEEN[-1]["token"] == "T" * 43 and SEEN[-1]["by"] == "license-admin", (c, d, SEEN[-1:]))

c, d = status_of(lambda: server.admin_portal_invite(server.PortalInviteRequest(username="  Ravi2 ", role="admin"),
                                                     x_admin_secret=A))
c2, d2 = status_of(lambda: server.admin_portal_invite(server.PortalInviteRequest(username="taken"), x_admin_secret=A))
check("TC-LICPU-0003 invite: name trimmed + lower-cased, role passed, the link comes back; the monitor's refusal "
      "reaches the page with its reason",
      c == 200 and d["url"] == "https://x/setup/abc" and SEEN[-2]["body"] == {"username": "ravi2", "role": "admin"}
      and c2 == 400 and "already has an account" in d2, (c, d, c2, d2))

n = len(SEEN)
b1 = status_of(lambda: server.admin_portal_action(server.PortalActionRequest(username="../x", action="remove"),
                                                  x_admin_secret=A))
b2 = status_of(lambda: server.admin_portal_action(server.PortalActionRequest(username="ravi", action="drop_db"),
                                                  x_admin_secret=A))
g = status_of(lambda: server.admin_portal_action(server.PortalActionRequest(username="Ravi", action="disable"),
                                                 x_admin_secret=A))
check("TC-LICPU-0004 actions: a bad name or an unknown action is refused here (400); a good one goes to "
      "/internal/users/<name>/<action>",
      b1[0] == 400 and b2[0] == 400 and g[0] == 200 and len(SEEN) == n + 1
      and SEEN[-1]["path"] == "/internal/users/ravi/disable", (b1, b2, g, SEEN[-1:]))

mon.shutdown()
mon.server_close()
down = status_of(lambda: server.admin_portal_users(x_admin_secret=A))
(TMP / "mon_token.txt").unlink()
missing = status_of(lambda: server.admin_portal_users(x_admin_secret=A))
check("TC-LICPU-0005 monitor down → 503 'not reachable'; no monitor installed (no token) → 503 that says so",
      down[0] == 503 and "not reachable" in down[1] and missing[0] == 503 and "not installed" in missing[1],
      (down, missing))

ui = (HERE / "admin_ui.html").read_text()
sec = ui[ui.index('<section id="portalSec"'):ui.index("</section>", ui.index('<section id="portalSec"'))]
js = ui[ui.index("PORTAL_USERS_ADMIN_20261127 BEGIN"):ui.index("PORTAL_USERS_ADMIN_20261127 END")]
html_js = js[:js.index("function puShowLink")] + js[js.index("async function portalInvite"):]   # puShowLink: textContent only
check("TC-LICPU-0006 admin page: a Portal users section with no password field, loaded at sign-in and on reload; "
      "every name and value is escaped",
      'type="password"' not in sec and "loadPortalUsers(); return;" in ui
      and ui.count("loadPortalUsers();") >= 2 and "${esc(" in js
      and not re.search(r"\$\{(i|u|d)\.(username|role|url)\}", html_js)
      and "textContent = d.url" in js, sec[:200])
dep = (HERE / "deploy-license-server.command").read_text()
check("TC-LICPU-0007 the deploy copies portal_users.py to the droplet", " portal_users.py " in dep)
scp_line = next(ln for ln in dep.splitlines() if ln.startswith("scp ") and "server.py" in ln)
sanity = next(ln for ln in dep.splitlines() if ln.startswith("for f in server.py"))
imps = [m for m in re.findall(r"^import (\w+)", (HERE / "server.py").read_text(), re.M) if (HERE / (m + ".py")).exists()]
check("TC-LICPU-0008 every module server.py imports from this folder is copied AND checked present by the deploy (DEF-110)",
      imps and all(f" {m}.py " in scp_line + " " and f" {m}.py " in sanity + " " for m in imps), (imps, scp_line, sanity))

cp = ui[ui.index("function copyText"):ui.index("let toastT")]
check("TC-LICPU-0009 Copy works on this plain-HTTP page: the async clipboard only in a secure context, else a "
      "hidden textarea + execCommand('copy'); a failure says so (DEF-111)",
      "window.isSecureContext" in cp and 'execCommand("copy")' in cp and "Copy failed" in cp
      and "navigator.clipboard?.writeText(t).then" not in cp, cp[:200])

print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({OK[0]}/{OK[0] + len(FAILS)})")
sys.exit(1 if FAILS else 0)

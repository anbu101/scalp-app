# license_server/test_admin_https_only.py
# ── LICENSE_ADMIN_HTTPS_ONLY_20261127 ── (TD-111) TC-LICADM-0001…0005
# /admin/* answers only over HTTPS (Caddy :8443). The real license server runs
# under uvicorn as on the droplet (proxy headers trusted from 127.0.0.1 only).
#   python3 license_server/test_admin_https_only.py      (from the repo root)
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="licsrv_admhttps_"))
(TMP / "secrets").mkdir()
(TMP / "secrets" / "admin_secret.txt").write_text("test-admin-secret")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey   # noqa: E402
from cryptography.hazmat.primitives import serialization                          # noqa: E402
(TMP / "secrets" / "private_key.pem").write_bytes(Ed25519PrivateKey.generate().private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
os.environ.update(LICSRV_SECRETS_DIR=str(TMP / "secrets"), LICSRV_DB=str(TMP / "licenses.db"),
                  LICSRV_DATA_DIR=str(TMP / "data"))
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
import httpx           # noqa: E402
import uvicorn         # noqa: E402
import server          # noqa: E402


def run(**kw):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    uv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning", **kw))
    threading.Thread(target=uv.run, daemon=True).start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    return uv, f"http://127.0.0.1:{port}"


uv, base = run()                                     # as on the droplet: proxy headers trusted from 127.0.0.1
c = httpx.Client(base_url=base, timeout=10)
SEC = {"X-Admin-Secret": "test-admin-secret"}
CADDY = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"}

plain = [c.get("/admin/list", headers=SEC).status_code, c.get("/admin/ui").status_code,
         c.post("/admin/revoke", json={"key": "X"}, headers=SEC).status_code]
check("TC-LICADM-0001 plain HTTP (the old :9100): every /admin/* answers 404, even with the right secret",
      plain == [404, 404, 404], plain)

via = [c.get("/admin/list", headers=CADDY).status_code, c.get("/admin/list", headers={**CADDY, **SEC}).status_code,
       c.get("/admin/ui", headers=CADDY).status_code]
check("TC-LICADM-0002 through Caddy (X-Forwarded-Proto https from 127.0.0.1): admin works as before (401 without "
      "the secret, 200 with it, the page loads)", via == [401, 200, 200], via)

uv2, base2 = run(forwarded_allow_ips="")           # a caller the server does not trust (= anyone outside)
forged = httpx.get(f"{base2}/admin/list", headers={**CADDY, **SEC}, timeout=10).status_code
check("TC-LICADM-0003 a caller from outside cannot forge it: X-Forwarded-Proto from an untrusted sender is ignored → 404",
      forged == 404, forged)

app_calls = [c.get("/health").status_code, c.get("/min_version").status_code,
             c.post("/heartbeat", json={"key": "SCLP-NOPE-NOPE-NOPE", "machine_id": "M" * 16}).json().get("status")]
check("TC-LICADM-0004 the app's calls still work on plain HTTP (the fallback): /health, /min_version, /heartbeat",
      app_calls == [200, 200, "unknown_key"], app_calls)

dep = (HERE / "deploy-license-server.command").read_text()
adm = [ln for ln in dep.splitlines() if "/admin/" in ln and "curl" in ln]
check("TC-LICADM-0005 the deploy's own /admin calls (on the droplet) say https, so the deploy still works",
      len(adm) == 2 and all("X-Forwarded-Proto: https" in ln for ln in adm), adm)

uv.should_exit = uv2.should_exit = True
print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({OK[0]}/{OK[0] + len(FAILS)})")
sys.exit(1 if FAILS else 0)

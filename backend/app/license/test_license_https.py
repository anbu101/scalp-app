# backend/app/license/test_license_https.py — ── LICENSE_HTTPS_20261127 ── (TD-111) TC-LICHTTPS-0001…0006
#
# The app reaches the licence server over HTTPS (Caddy :8443, sslip.io certificate)
# and falls back to the old plain-HTTP port only on a network error or a proxy
# answer, so a broken proxy or DNS never locks a licence out. Hermetic: httpx is
# faked; no network.
#   cd backend && python3 app/license/test_license_https.py
import importlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
os.environ.setdefault("SCALP_TEST_MODE", "1")
os.environ.pop("SCALP_LICENSE_SERVER", None)

import httpx                                  # noqa: E402
import app.license.license_client as LC       # noqa: E402
LC = importlib.reload(LC)

FAILS, calls, logs = [], [], []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


def fake(plan):
    """plan: url-prefix → Resp or an exception to raise."""
    def f(url, json=None, timeout=None):
        calls.append((url, json))
        for pre, ans in plan.items():
            if url.startswith(pre):
                if isinstance(ans, Exception):
                    raise ans
                return ans
        raise AssertionError(url)
    return f


LC.write_audit_log = lambda m: logs.append(m)
_real_post, _real_get = httpx.post, httpx.get
H, P = "https://139-59-8-202.sslip.io:8443", "http://139.59.8.202:9100"
try:
    check("TC-LICHTTPS-0001 HTTPS first (https://139-59-8-202.sslip.io:8443), old plain port only as the fallback",
          LC.LICENSE_SERVER_URL == H and LC.LICENSE_SERVER_FALLBACK == P)

    calls.clear()
    httpx.post = fake({H: Resp(200, {"status": "ok"})})
    r = LC.post_device("/heartbeat", {"key": "K", "machine_id": "M"}, 5)
    check("TC-LICHTTPS-0002 HTTPS answers: one call, to the HTTPS URL, nothing else",
          r.status_code == 200 and calls == [(H + "/heartbeat", {"key": "K", "machine_id": "M"})], calls)

    calls.clear(); logs.clear()
    httpx.post = fake({H: httpx.ConnectError("dns"), P: Resp(200, {"status": "ok"})})
    LC.post_device("/liveness", {"key": "K", "machine_id": "M", "telegram": {"bot_token": "t"}}, 5)
    LC.post_device("/liveness", {"key": "K", "machine_id": "M"}, 5)
    fb_logs = [m for m in logs if "fallback" in m]
    httpx.post = fake({H: Resp(200, {"status": "ok"})})
    LC.post_device("/heartbeat", {"key": "K", "machine_id": "M"}, 5)
    check("TC-LICHTTPS-0003 a network error falls back to plain HTTP with the same body; logged ONCE; back on HTTPS "
          "it says so once",
          [c[0] for c in calls] == [H + "/liveness", P + "/liveness", H + "/liveness", P + "/liveness", H + "/heartbeat"]
          and calls[1][1]["telegram"] == {"bot_token": "t"} and len(fb_logs) == 1
          and logs[-1] == "[LICENSE] server over HTTPS", (calls, logs))

    res = {}
    for code in (404, 502, 503, 504, 401, 422, 500):
        calls.clear()
        httpx.post = fake({H: Resp(code, {}), P: Resp(200, {"status": "ok"})})
        LC.license_request("POST", "/activate", json={"key": "K"}, timeout=5)
        res[code] = len(calls)
    check("TC-LICHTTPS-0004 a proxy answer (404/502/503/504) falls back; a real answer (401/422/500) does not",
          res == {404: 2, 502: 2, 503: 2, 504: 2, 401: 1, 422: 1, 500: 1}, res)

    calls.clear()
    httpx.get = fake({H: httpx.ReadTimeout("slow"), P: Resp(200, {"min_version": "12.0.26"})})
    import app.license.version_check as VC   # noqa: E402
    VC.write_audit_log = lambda m: None
    VC.check_for_update(timeout=1)
    check("TC-LICHTTPS-0005 the version check goes through the same helper (HTTPS, then the fallback)",
          [c[0] for c in calls] == [H + "/min_version", P + "/min_version"]
          and VC.version_state.MIN_VERSION == "12.0.26", calls)

    os.environ["SCALP_LICENSE_SERVER"] = "http://127.0.0.1:9999"
    LC2 = importlib.reload(LC)
    calls.clear()
    httpx.post = fake({"http://127.0.0.1:9999": httpx.ConnectError("down")})
    try:
        LC2.license_request("POST", "/heartbeat", json={}, timeout=1)
        raised = False
    except httpx.ConnectError:
        raised = True
    check("TC-LICHTTPS-0006 SCALP_LICENSE_SERVER (tests / dev) = that URL only: no fallback, errors surface",
          LC2.LICENSE_SERVER_URL == "http://127.0.0.1:9999" and LC2.LICENSE_SERVER_FALLBACK is None
          and raised and len(calls) == 1, calls)
finally:
    httpx.post, httpx.get = _real_post, _real_get
    os.environ.pop("SCALP_LICENSE_SERVER", None)

print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({6 - len(FAILS)}/6)")
sys.exit(1 if FAILS else 0)

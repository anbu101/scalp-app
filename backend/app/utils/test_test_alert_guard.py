# backend/app/utils/test_test_alert_guard.py — fence TEST_ALERT_GUARD_20261015
#
#   1. inside a test process all three Telegram send sites refuse (no HTTP)
#   2. the APP still sends: a subprocess that looks like scalp-backend calls
#      send_telegram_message and the request goes out (HTTP stubbed)
#   3. SCALP_TEST_MODE=1 blocks even a non-test script
#   4. test_tma2_entry_paths' unwind still raises its alert — into a stub
#
# Run: cd backend && python3 app/utils/test_test_alert_guard.py

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())

FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


class Req:
    def __init__(self):
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1

        class R:
            status_code, ok, text = 200, True, ""
        return R()


def t_in_test():
    print("── 1. inside a test process ──")
    from app.utils.test_guard import outbound_blocked
    check("outbound_blocked() is True when the script is test_*.py", outbound_blocked())
    import app.api.telegram_api as T
    import app.api.telegram_summary_send as S
    import app.services.relay_deployer as RD
    rq = Req()
    T.requests, S.requests, RD.requests = rq, rq, rq
    r1 = T.send_telegram_message("tok", "chat", "Timberwolf: SELL entry FAILED … NIFTYX_H")
    r2 = S._send_photo("tok", "chat", b"png", "cap")
    RD._get_telegram_credentials = lambda: ("tok", "chat")
    RD._send_relay_telegram("relay down")
    check("send_telegram_message: no HTTP request, returns False", rq.calls == 0 and r1 is False, (rq.calls, r1))
    check("EOD card _send_photo: no HTTP request", rq.calls == 0 and r2 is False, (rq.calls, r2))
    check("relay alert: no HTTP request", rq.calls == 0)


PROBE = r"""
import sys
sys.argv = ['{argv0}']
sys.path.insert(0, {backend!r})
import app.api.telegram_api as T
class R:
    status_code = 200
class Q:
    n = 0
    def post(self, *a, **k):
        Q.n += 1
        return R()
T.requests = Q()
ok = T.send_telegram_message('tok', 'chat', 'hello')
print('SENT', Q.n, ok)
"""


def probe(argv0, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "SCALP_TEST_MODE"}
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-c", PROBE.format(argv0=argv0, backend=str(BACKEND))],
                       capture_output=True, text=True, env=env, timeout=120)
    line = [l for l in r.stdout.splitlines() if l.startswith("SENT")]
    return line[-1] if line else (r.stdout[-300:] + r.stderr[-600:])


def t_app():
    print("── 2/3. the app still alerts ──")
    out = probe("scalp-backend")
    check("as scalp-backend (frozen app): the request goes out", out == "SENT 1 True", out)
    out = probe("uvicorn")
    check("as uvicorn (dev run): the request goes out", out == "SENT 1 True", out)
    out = probe("scalp-backend", {"SCALP_TEST_MODE": "1"})
    check("SCALP_TEST_MODE=1 blocks even a non-test process", out == "SENT 0 False", out)


def t_tma2_suite():
    print("── 4. test_tma2_entry_paths is hermetic ──")
    s = (BACKEND / "app/engine/tma2/test_tma2_entry_paths.py").read_text(encoding="utf-8")
    check("suite stubs notify_critical + record_alert before the unwind calls",
          "TM.notify_critical = " in s and "TM.record_alert = " in s
          and s.find("TM.notify_critical = ") < s.find('m._unwind_hedge("NIFTYX_H"'))
    check("…and asserts both unwinds still raise their alert (into the stub)",
          "unwind still raises its critical alert" in s)


if __name__ == "__main__":
    t_in_test()
    t_app()
    t_tma2_suite()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TEST ALERT GUARD CHECKS PASSED")

"""
test_ui_server_guard.py — UITEST_PORT_GUARD_20261120: ui_server.start() refuses to run when its
port is already taken (on Anbu's Mac: the running Scalp app on 47321), so a UI test can never take
over the live app's window again. Uses throw-away ports only; never touches 47321.

    PYTHONPATH=../backend python3 uitest/test_ui_server_guard.py
"""
import json
import os
import socket
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402

fails, N = [], [0]


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# a stand-in for the running app: something listening on every address, like the app's *:47321
busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
busy.bind(("0.0.0.0", 0))
busy.listen(5)
bport = busy.getsockname()[1]
check("TC-UITEST-0001", U.port_in_use(bport) and not U.port_in_use(free_port()),
      f"port_in_use sees a listener on *:{bport} and not a free port")
try:
    U.start(bport)
    msg = None
except SystemExit as e:
    msg = str(e)
check("TC-UITEST-0002", msg is not None and "already listening" in msg and "Quit the app" in msg,
      f"start() on a taken port stops before serving, and says to quit the app ({msg!r})")
busy.close()

fp = free_port()
srv = U.start(fp)
with urllib.request.urlopen(f"http://127.0.0.1:{fp}/system/license", timeout=2) as r:
    got = json.loads(r.read())
check("TC-UITEST-0003", srv is not None and got == U.LICENSE, f"start() on a free port serves its own fake licence ({got})")
srv.should_exit = True

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks" + (f" — {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)

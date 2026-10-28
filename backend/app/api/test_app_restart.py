# backend/app/api/test_app_restart.py
# ── APP_RESTART_20261028 ── POST /api/system/restart + GET /api/system/boot-id.
# Hermetic: HOME points at a temp folder; the launcher spawn and the self-exit
# are replaced by recorders, except in section 6, which runs the real /bin/sh
# launcher against a throwaway port and a harmless target command.
#
#   cd backend && HOME=$(mktemp -d) python3 app/api/test_app_restart.py
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.api.system_restart_routes as R           # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid


POPEN, EXITS = [], []


def rec_popen(argv, **kw):
    POPEN.append((argv, kw))
    return FakeProc()


def rec_exit(delay):
    EXITS.append(delay)


def reset():
    POPEN.clear()
    EXITS.clear()
    R._restarting["on"] = False
    R._popen = rec_popen
    R._schedule_exit = rec_exit


# ── 1) what gets relaunched ─────────────────────────────────────────────────
sys.frozen = True
check("frozen build: relaunch the same launcher binary",
      R.target_command() == [sys.executable])
del sys.frozen
cmd = R.target_command()
check("source run: same interpreter + original argv",
      cmd[0] == sys.executable and len(cmd) >= 2, repr(cmd))

# ── 2) launcher command lines ───────────────────────────────────────────────
pl = R.posix_launcher(["/Apps/Scalp.app/Contents/Resources/backend/scalp-backend"], 47321)
check("posix: /bin/sh -c", pl[:2] == ["/bin/sh", "-c"])
check("posix: waits on the backend port", "-iTCP:47321 -sTCP:LISTEN" in pl[2])
check("posix: bounded wait", f"-lt {R.WAIT_MAX_TICKS}" in pl[2])
check("posix: exec's the target", pl[2].rstrip().endswith('exec "$@"')
      and pl[-1].endswith("scalp-backend"))
wl = R.windows_launcher([r"C:\Program Files\Anbu's Scalp\scalp-backend.exe"], 47321,
                        r"C:\Program Files\Anbu's Scalp")
s = wl[-1]
check("windows: powershell", wl[0] == "powershell.exe")
check("windows: waits on the backend port", "-LocalPort 47321 -State Listen" in s)
check("windows: apostrophe in path escaped", "Anbu''s Scalp\\scalp-backend.exe'" in s, s)
check("windows: no -ArgumentList for a bare exe", "-ArgumentList" not in s)
wl2 = R.windows_launcher(["python.exe", "-m", "uvicorn", "app:app"], 47321, "C:\\x")
check("windows: args forwarded", "-ArgumentList @('-m','uvicorn','app:app')" in wl2[-1])

# ── 3) the handler: confirmation, single-flight, failure safety ────────────
reset()
for bad in (None, {}, {"confirm": "yes"}, {"confirm": "restart"}, "RESTART"):
    st, body = R.request_restart(bad)
    check(f"no/invalid confirm {bad!r} -> 400", st == 400 and not body["success"])
check("invalid confirm: nothing spawned, no exit", POPEN == [] and EXITS == [])

st, body = R.request_restart({"confirm": "RESTART"})
check("confirmed -> 200 restarting", st == 200 and body["restarting"] is True, repr(body))
check("confirmed: returns this boot id", body["boot_id"] == R.BOOT_ID)
check("confirmed: launcher spawned once", len(POPEN) == 1)
argv, kw = POPEN[0]
if os.name != "nt":
    check("posix: own session (survives our exit)", kw.get("start_new_session") is True)
    check("posix: launcher argv", argv[:2] == ["/bin/sh", "-c"])
check("launcher env: PyInstaller reset", kw["env"].get("PYINSTALLER_RESET_ENVIRONMENT") == "1")
check("launcher env: inherits SCALP_* settings",
      all(kw["env"].get(k) == v for k, v in os.environ.items() if k.startswith("SCALP_")))
check("launcher: stdio detached", kw["stdin"] == subprocess.DEVNULL
      and kw["stdout"] == subprocess.DEVNULL)
check("confirmed: exit scheduled once after the reply", EXITS == [R.EXIT_DELAY_S])

st, body = R.request_restart({"confirm": "RESTART"})
check("double press -> 409, no second launcher", st == 409 and len(POPEN) == 1
      and EXITS == [R.EXIT_DELAY_S])

reset()


def boom(argv, **kw):
    raise OSError("no /bin/sh")


R._popen = boom
st, body = R.request_restart({"confirm": "RESTART"})
check("launcher fails -> 500", st == 500 and "keeps running" in body["error"], repr(body))
check("launcher fails -> process does NOT exit", EXITS == [])
check("launcher fails -> not stuck in restarting", R._restarting["on"] is False)
R._popen = rec_popen
st, _ = R.request_restart({"confirm": "RESTART"})
check("after a failure a new attempt works", st == 200 and len(EXITS) == 1)

# ── 4) Windows flags (job breakaway, then without) ─────────────────────────
reset()
R.spawn_relauncher(os_name="nt")
check("windows: breakaway + new group + no window",
      POPEN[-1][1]["creationflags"] == 0x01000000 | 0x00000200 | 0x08000000)
reset()
calls = []


def no_breakaway(argv, **kw):
    calls.append(kw["creationflags"])
    if kw["creationflags"] & 0x01000000:
        raise OSError("access denied (job)")
    return FakeProc()


R._popen = no_breakaway
R.spawn_relauncher(os_name="nt")
check("windows: retries without breakaway",
      calls == [0x01000000 | 0x00000200 | 0x08000000, 0x00000200 | 0x08000000], repr(calls))

# ── 5) the HTTP routes, driven through ASGI (no TestClient) ────────────────
from fastapi import FastAPI                          # noqa: E402

app = FastAPI()
app.include_router(R.router)


def asgi(method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    sent, out = [False], {}

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] = out.get("body", b"") + msg.get("body", b"")

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "scheme": "http", "server": ("127.0.0.1", 47321),
             "client": ("127.0.0.1", 5555),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(raw)).encode())]}
    asyncio.run(app(scope, receive, send))
    return out["status"], json.loads(out["body"] or b"{}")


reset()
st, b = asgi("GET", "/api/system/boot-id")
check("GET boot-id", st == 200 and b["boot_id"] == R.BOOT_ID and b["restarting"] is False, repr(b))
st, b = asgi("POST", "/api/system/restart", {"confirm": "nope"})
check("POST without confirm -> 400 over HTTP", st == 400 and POPEN == [])
st, b = asgi("POST", "/api/system/restart")
check("POST with no body -> 400 over HTTP", st == 400 and POPEN == [])
st, b = asgi("POST", "/api/system/restart", {"confirm": "RESTART"})
check("POST confirmed -> 200 over HTTP", st == 200 and b["boot_id"] == R.BOOT_ID
      and len(POPEN) == 1 and EXITS == [R.EXIT_DELAY_S], repr(b))
st, b = asgi("GET", "/api/system/boot-id")
check("boot-id reports restarting", b["restarting"] is True)

# ── 6) the real POSIX launcher (real clock, real port) ─────────────────────
if os.name != "nt" and Path("/bin/sh").exists():
    import shutil

    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def wait_for(path, secs):
        end = time.time() + secs
        while time.time() < end:
            if path.exists():
                return True
            time.sleep(0.1)
        return path.exists()

    td = Path(tempfile.mkdtemp())
    marker = td / "started"
    target = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ok')"]

    port = free_port()
    t0 = time.time()
    subprocess.Popen(R.posix_launcher(target, port), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    ok = wait_for(marker, 8)
    check("real launcher: starts the target once the port is free", ok)
    check("real launcher: waits the 2 s grace first", ok and time.time() - t0 >= 2.0)

    if shutil.which("lsof"):
        marker.unlink(missing_ok=True)
        port = free_port()
        holder = socket.socket()
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        subprocess.Popen(R.posix_launcher(target, port), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        time.sleep(4.5)
        check("real launcher: does NOT start while the old backend still listens",
              not marker.exists())
        holder.close()
        check("real launcher: starts within seconds after the port frees",
              wait_for(marker, 4))
    else:
        print("  (lsof not installed — port-wait check skipped)")
    shutil.rmtree(td, ignore_errors=True)

total = PASSES[0] + len(FAILS)
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")

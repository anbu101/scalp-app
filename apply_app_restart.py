#!/usr/bin/env python3
# apply_app_restart.py — APP_RESTART_20261028
#
# "↻ Restart App" button beside the Trading Enable/Disable button on the
# Connections page. Asks for confirmation, then restarts the app. Works from
# the desktop window and from the phone.
#
#   * Desktop window: Tauri relaunch() (already permitted for the updater)
#     restarts the whole app: window, Rust shell and backend.
#   * Phone / browser: POST /api/system/restart {"confirm": "RESTART"}. The
#     backend spawns a detached launcher that waits until port 47321 is free,
#     then starts a fresh copy of the same backend binary, and the old process
#     exits 1.5 s after replying. The page polls GET /api/system/boot-id and
#     reloads when a different process answers (~3 s in tests, longer with the
#     full fleet boot).
#   * If the launcher cannot be started, the backend does NOT exit; the page
#     says the restart failed and the app keeps running.
#   * During market hours (Mon–Fri 09:15–15:30 IST) the confirmation also
#     warns that app-managed exits pause until the app is back.
#
# Touches:
#   backend/app/api/system_restart_routes.py   NEW  (+ desktop mirror)
#   backend/app/api/test_app_restart.py        NEW  (42 checks, + mirror)
#   backend/app/api_server.py                  register the router (+ mirror)
#   frontend/src/components/RestartAppButton.jsx   NEW
#   frontend/src/pages/Connections.jsx         button beside the trading switch
#                                              (and under Login when Zerodha is
#                                              not connected)
#
# USAGE (repo root):
#   python3 apply_app_restart.py --dry-run
#   python3 apply_app_restart.py
#   python3 apply_app_restart.py --revert
# Then: ./desktop/build-scalp.sh both
import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "APP_RESTART_20261028"
BAK = ".bak-" + FENCE
MIRROR = Path("desktop/src-tauri/backend")

API_EDITS = [
('''app.include_router(live_sizer_router)    # ── LIVE_SIZER_20261024 ── /api/live-sizer/*
''',
'''app.include_router(live_sizer_router)    # ── LIVE_SIZER_20261024 ── /api/live-sizer/*
from app.api.system_restart_routes import router as system_restart_router   # ── ''' + FENCE + ''' ──
app.include_router(system_restart_router)   # ── ''' + FENCE + ''' ── /api/system/restart, /api/system/boot-id
'''),
]

JSX_EDITS = [
('''import AngelAccountCard from "../components/AngelAccountCard"; // ACC2
''',
'''import AngelAccountCard from "../components/AngelAccountCard"; // ACC2
import RestartAppButton from "../components/RestartAppButton";   // ── ''' + FENCE + ''' ──
'''),
('''                      {tradingEnabled
                        ? <Button onClick={disable} variant="danger">⏸ Disable</Button>
                        : <Button onClick={enable} variant="success">▶ Enable</Button>}
''',
'''                      <div style={{ display: "flex", gap: spacing.sm, flexWrap: "wrap", justifyContent: "flex-end" }}>   {/* ── ''' + FENCE + ''' ── */}
                        {tradingEnabled
                          ? <Button onClick={disable} variant="danger">⏸ Disable</Button>
                          : <Button onClick={enable} variant="success">▶ Enable</Button>}
                        <RestartAppButton />
                      </div>
'''),
('''                    <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ flex: 1 }}>Edit Credentials</Button>
                  </div>
''',
'''                    <Button onClick={() => setEditingZerodha(true)} variant="secondary" style={{ flex: 1 }}>Edit Credentials</Button>
                  </div>
                  <RestartAppButton block />   {/* ── ''' + FENCE + ''' ── */}
'''),
]

# (rel, edits, prerequisite markers, mirrored)
EDITS = [
    ("backend/app/api_server.py", API_EDITS, ["LIVE_SIZER_20261024"], True),
    ("frontend/src/pages/Connections.jsx", JSX_EDITS, ["AngelAccountCard"], False),
]
# (rel, payload, mirrored)
NEW_FILES = [
    ("backend/app/api/system_restart_routes.py", r'''# backend/app/api/system_restart_routes.py
# ── APP_RESTART_20261028 ── "Restart App" from the Connections page (desktop or
# phone).
#
# The desktop window restarts the whole app with Tauri's relaunch(). A phone
# only reaches this backend, so the backend restarts itself:
#
#   1. spawn a detached launcher (own session / process group) that waits until
#      nothing listens on SCALP_PORT any more, then starts a fresh copy of the
#      same backend (same binary, cwd and environment);
#   2. answer the request, then exit this process ~1.5 s later.
#
# The backend restarts itself because Tauri's watchdog cannot: after the first
# successful start it never clears BACKEND_STARTING, so a backend that exits
# stays down until the app is relaunched. When the user quits the app, Tauri's
# kill_process_on_port(47321) still stops the relaunched backend.
#
# If the launcher cannot be started, this process does NOT exit: the request
# fails and the app keeps running.
#
# GET /api/system/boot-id identifies the running process. The page polls it and
# reloads once the id changes, which proves the new backend is the one
# answering.
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.event_bus.audit_logger import write_audit_log

router = APIRouter()

BOOT_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()
EXIT_DELAY_S = 1.5
WAIT_MAX_TICKS = 60            # launcher: up to 60 × 0.5 s for the port to free
CONFIRM_WORD = "RESTART"

_lock = threading.Lock()
_restarting = {"on": False}

# Seams the test replaces.
_popen = subprocess.Popen
_schedule_exit = None          # set below; callable(delay_s) -> None


def _port() -> int:
    try:
        return int(os.getenv("SCALP_PORT", "47321"))
    except ValueError:
        return 47321


def target_command() -> List[str]:
    """The command that starts this same backend again."""
    if getattr(sys, "frozen", False):              # PyInstaller onedir launcher
        return [sys.executable]
    orig = getattr(sys, "orig_argv", None)          # keeps `-m uvicorn …` intact
    if orig:
        return [sys.executable] + list(orig[1:])
    return [sys.executable] + list(sys.argv)


def posix_launcher(cmd: List[str], port: int) -> List[str]:
    script = (
        "sleep 2; i=0; "
        f'while [ $i -lt {WAIT_MAX_TICKS} ] && '
        f'lsof -nP -iTCP:{port} -sTCP:LISTEN -t >/dev/null 2>&1; '
        "do sleep 0.5; i=$((i+1)); done; "
        'sleep 0.5; exec "$@"'
    )
    return ["/bin/sh", "-c", script, "scalp-relaunch"] + list(cmd)


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def windows_launcher(cmd: List[str], port: int, cwd: str) -> List[str]:
    exe, args = cmd[0], cmd[1:]
    arglist = (" -ArgumentList @(" + ",".join(_ps_quote(a) for a in args) + ")") if args else ""
    script = (
        "Start-Sleep -Seconds 2; $i = 0; "
        f"while (($i -lt {WAIT_MAX_TICKS}) -and "
        f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue)) "
        "{ Start-Sleep -Milliseconds 500; $i++ }; "
        f"Start-Process -FilePath {_ps_quote(exe)}{arglist} "
        f"-WorkingDirectory {_ps_quote(cwd)} -WindowStyle Hidden"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script]


def spawn_relauncher(os_name: Optional[str] = None) -> int:
    """Start the detached launcher; returns its pid. Raises on failure."""
    os_name = os_name or os.name
    port, cwd, cmd = _port(), os.getcwd(), target_command()
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"     # fresh, independent instance
    env["SCALP_RESTARTED_FROM_UI"] = "1"
    common = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os_name == "nt":
        argv = windows_launcher(cmd, port, cwd)
        base = 0x00000200 | 0x08000000             # NEW_PROCESS_GROUP | NO_WINDOW
        try:
            proc = _popen(argv, creationflags=base | 0x01000000, **common)  # BREAKAWAY
        except OSError:
            proc = _popen(argv, creationflags=base, **common)
    else:
        argv = posix_launcher(cmd, port)
        proc = _popen(argv, start_new_session=True, **common)
    write_audit_log(f"[SYSTEM][RESTART] launcher pid={proc.pid} will start: "
                    f"{' '.join(shlex.quote(c) for c in cmd)} (port {port})")
    return proc.pid


def _default_schedule_exit(delay_s: float) -> None:
    def _go():
        write_audit_log(f"[SYSTEM][RESTART] exiting pid={os.getpid()} for restart")
        os._exit(0)
    t = threading.Timer(delay_s, _go)
    t.daemon = True
    t.start()


_schedule_exit = _default_schedule_exit


def request_restart(payload: Optional[dict]):
    """Pure handler: returns (http_status, body)."""
    if not isinstance(payload, dict) or payload.get("confirm") != CONFIRM_WORD:
        return 400, {"success": False,
                     "error": f'send {{"confirm": "{CONFIRM_WORD}"}} to restart'}
    with _lock:
        if _restarting["on"]:
            return 409, {"success": False, "error": "a restart is already in progress",
                         "boot_id": BOOT_ID}
        try:
            pid = spawn_relauncher()
        except Exception as e:                       # never exit without a launcher
            write_audit_log(f"[SYSTEM][RESTART][ERROR] launcher failed: {e!r} — not restarting")
            return 500, {"success": False,
                         "error": f"could not start the relauncher ({e}); the app keeps running"}
        _restarting["on"] = True
        write_audit_log(f"[SYSTEM][RESTART] requested from the UI; boot {BOOT_ID} exits "
                        f"in {EXIT_DELAY_S}s")
        _schedule_exit(EXIT_DELAY_S)
    return 200, {"success": True, "restarting": True, "boot_id": BOOT_ID,
                 "launcher_pid": pid}


@router.post("/api/system/restart")
async def restart_app(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = None
    status, body = request_restart(payload)
    return JSONResponse(body, status_code=status)


@router.get("/api/system/boot-id")
def boot_id():
    return {"boot_id": BOOT_ID, "pid": os.getpid(),
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "restarting": _restarting["on"]}
''', True),
    ("backend/app/api/test_app_restart.py", r'''# backend/app/api/test_app_restart.py
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
''', True),
    ("frontend/src/components/RestartAppButton.jsx", r'''// frontend/src/components/RestartAppButton.jsx
// ── APP_RESTART_20261028 ── "Restart App" next to the trading switch on the
// Connections page. It asks for confirmation, then:
//   * desktop window: Tauri relaunch() restarts the whole app (the new
//     instance clears port 47321 and starts a fresh backend);
//   * phone / browser: POST /api/system/restart. The backend starts its
//     replacement and exits; this page polls /api/system/boot-id and reloads
//     once a different process answers.
import { useEffect, useRef, useState } from "react";
import { colors, alpha } from "../tokens";
import { getApiBase } from "../api/base";

const POLL_MS = 2000;
const GIVE_UP_MS = 120000;

function nativeRelaunch() {
  try {
    return window.__TAURI__?.process?.relaunch || null;
  } catch (e) {
    return null;
  }
}

// Mon–Fri 09:15–15:30 IST (UTC+5:30 fixed, no DST).
function marketOpenIST(now = new Date()) {
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  const d = ist.getDay();
  const m = ist.getHours() * 60 + ist.getMinutes();
  return d >= 1 && d <= 5 && m >= 9 * 60 + 15 && m < 15 * 60 + 30;
}

async function fetchJson(path, options = {}) {
  const res = await fetch(`${getApiBase()}${path}`, {
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

const btnBase = {
  padding: "8px 16px", borderRadius: 6, border: "none", fontSize: 13,
  fontWeight: 600, cursor: "pointer", minHeight: 36,
};

export default function RestartAppButton({ block = false }) {
  const [stage, setStage] = useState("idle"); // idle | confirm | restarting | slow | error
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const timers = useRef([]);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  function later(fn, ms) {
    timers.current.push(setTimeout(fn, ms));
  }

  function waitForNewBackend(oldId, started) {
    const tick = async () => {
      const ms = Date.now() - started;
      setElapsed(Math.round(ms / 1000));
      try {
        const b = await fetchJson("/api/system/boot-id");
        if (b.boot_id && b.boot_id !== oldId) {
          window.location.reload();
          return;
        }
      } catch (e) {
        /* backend down mid-restart — expected */
      }
      if (ms > GIVE_UP_MS) {
        setStage("slow");
        return;
      }
      later(tick, POLL_MS);
    };
    later(tick, POLL_MS);
  }

  async function restartViaBackend() {
    let oldId = null;
    try {
      oldId = (await fetchJson("/api/system/boot-id")).boot_id;
    } catch (e) {
      /* older backend without boot-id: the restart call below reports it */
    }
    const r = await fetchJson("/api/system/restart", {
      method: "POST",
      body: JSON.stringify({ confirm: "RESTART" }),
    });
    waitForNewBackend(r.boot_id || oldId, Date.now());
  }

  async function doRestart() {
    setError("");
    setElapsed(0);
    setStage("restarting");
    const relaunch = nativeRelaunch();
    try {
      if (relaunch) {
        try {
          await relaunch();       // the process is replaced; nothing below runs
          return;
        } catch (e) {
          console.warn("[RESTART] relaunch failed, restarting the backend instead", e);
        }
      }
      await restartViaBackend();
    } catch (e) {
      setError(e.message || String(e));
      setStage("error");
    }
  }

  const open = marketOpenIST();
  const isDesktop = !!nativeRelaunch();

  return (
    <>
      <button
        type="button"
        onClick={() => setStage("confirm")}
        style={{
          ...btnBase,
          background: colors.bg.tertiary,
          color: colors.text.secondary,
          border: `1px solid ${colors.border.medium}`,
          ...(block ? { width: "100%" } : {}),
        }}
        title="Close and reopen Scalp Terminal"
      >
        ↻ Restart App
      </button>

      {stage !== "idle" && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="restart-app-title"
          onClick={(e) => e.target === e.currentTarget && stage === "confirm" && setStage("idle")}
          style={{
            position: "fixed", inset: 0, zIndex: 10000,
            background: alpha("#000000", 60),
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 16,
          }}
        >
          <div style={{
            width: "100%", maxWidth: 420, background: colors.bg.secondary || colors.bg.page,
            border: `1px solid ${colors.border.medium}`, borderRadius: 10, padding: 20,
            color: colors.text.primary, boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
          }}>
            <h3 id="restart-app-title" style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
              {stage === "confirm" && "Restart Scalp Terminal?"}
              {stage === "restarting" && "Restarting…"}
              {stage === "slow" && "Still restarting"}
              {stage === "error" && "Restart failed"}
            </h3>

            {stage === "confirm" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  The app closes and starts again, which takes about 20–40 seconds.
                  Settings, strategies and open positions load back from disk. Nothing
                  is squared off.
                </p>
                {open && (
                  <p style={{
                    fontSize: 13, lineHeight: 1.5, margin: "12px 0 0", padding: "8px 10px",
                    borderRadius: 6, color: colors.warning,
                    background: alpha(colors.warning, 12),
                    border: `1px solid ${alpha(colors.warning, 40)}`,
                  }}>
                    The market is open. Live positions stay open at the broker, but
                    app-managed exits and stop checks pause until the app is back.
                  </p>
                )}
                {!isDesktop && (
                  <p style={{ fontSize: 12, color: colors.text.muted, margin: "10px 0 0" }}>
                    This page reloads by itself when the app is back.
                  </p>
                )}
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18, flexWrap: "wrap" }}>
                  <button type="button" onClick={() => setStage("idle")}
                    style={{ ...btnBase, background: colors.bg.tertiary, color: colors.text.secondary }}>
                    Cancel
                  </button>
                  <button type="button" onClick={doRestart} autoFocus
                    style={{ ...btnBase, background: colors.danger, color: colors.text.primary }}>
                    Restart app
                  </button>
                </div>
              </>
            )}

            {stage === "restarting" && (
              <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                Waiting for the app to come back{elapsed ? ` (${elapsed}s)` : ""}. Keep this
                page open; it reloads by itself.
              </p>
            )}

            {stage === "slow" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  The app has not answered for 2 minutes. Check Scalp Terminal on the
                  computer: if it is closed, open it there.
                </p>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
                  <button type="button" onClick={() => window.location.reload()}
                    style={{ ...btnBase, background: colors.primary, color: colors.text.primary }}>
                    Reload page
                  </button>
                </div>
              </>
            )}

            {stage === "error" && (
              <>
                <p style={{ fontSize: 13, lineHeight: 1.5, color: colors.text.secondary, margin: "12px 0 0" }}>
                  {error}. The app was not restarted and is still running.
                </p>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
                  <button type="button" onClick={() => setStage("idle")}
                    style={{ ...btnBase, background: colors.bg.tertiary, color: colors.text.secondary }}>
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
''', False),
]
SUITES = ["app/api/test_app_restart.py"]


def die(msg, code=2):
    print("\nABORTED — nothing written.\n  " + msg)
    sys.exit(code)


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path(__file__).resolve().parent]:
        if (c / "backend/app/api_server.py").exists() and (c / "frontend/src/pages").exists():
            return c.resolve()
    die("Could not find the repo root. Run from the repo root or pass --repo PATH.")


def dests(root, rel, mirrored):
    out = [root / rel]
    if mirrored and rel.startswith("backend/"):
        m = root / MIRROR / Path(rel).relative_to("backend")
        if m.exists() or m.parent.exists():
            out.append(m)
    return out


def git_dirty(root, rel):
    if not (root / ".git").exists() or not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", rel],
                       capture_output=True, text=True)
    return any(l[:2].strip() and not l.startswith("??") for l in r.stdout.splitlines())


def patch(text, edits, label):
    for i, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            die(f"{label}: anchor #{i} found {n}× (expected 1) — not the version this "
                f"script targets. Push it to GitHub and ask for a rebase.\n  anchor: "
                f"{old.strip().splitlines()[0][:90]}")
        text = text.replace(old, new, 1)
    return text


def run_suite(root, staged):
    tmp = Path(tempfile.mkdtemp(prefix="apprestart_suite_"))
    try:
        ign = shutil.ignore_patterns("__pycache__", "*.bak-*", "*.pyc", "data", "secrets")
        shutil.copytree(root / "backend/app", tmp / "backend/app", ignore=ign)
        for rel, txt in staged.items():
            if rel.startswith("backend/"):
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tmp / rel).write_text(txt, encoding="utf-8")
        results = []
        for t in SUITES:
            home = Path(tempfile.mkdtemp(prefix="home_", dir=tmp))
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       SCALP_TEST_MODE="1", PYTHONPATH=str(tmp / "backend"))
            r = subprocess.run([sys.executable, t], cwd=tmp / "backend", env=env,
                               capture_output=True, text=True, timeout=300)
            results.append((t, r.returncode == 0, (r.stdout + r.stderr).strip()))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def jsx_check(root, name, txt):
    fe = root / "frontend"
    eb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not eb.exists():
        return None, "esbuild not installed in frontend/ — JSX parse check skipped"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / name
        f.write_text(txt, encoding="utf-8")
        r = subprocess.run([str(eb), str(f), "--loader:.jsx=jsx", "--log-level=error",
                            "--outfile=" + str(Path(td) / "o.js")],
                           capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "esbuild parse OK").strip()


def revert(root):
    n = 0
    for rel, _e, _p, mirrored in EDITS:
        for p in dests(root, rel, mirrored):
            b = p.with_name(p.name + BAK)
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
                n += 1
                print(f"  restored  {p.relative_to(root)}")
    for rel, _t, mirrored in NEW_FILES:
        for p in dests(root, rel, mirrored):
            if p.exists() and FENCE in p.read_text(encoding="utf-8", errors="ignore"):
                p.unlink()
                print(f"  removed   {p.relative_to(root)}")
    print(f"\nRevert done ({n} file(s) restored). Rebuild: ./desktop/build-scalp.sh both")
    return 0


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"{FENCE}\nRepo: {root}")
    if a.revert:
        return revert(root)

    edited = [p for rel, _e, _pa, mi in EDITS for p in dests(root, rel, mi) if p.exists()]
    fenced = [p for p in edited if FENCE in p.read_text(encoding="utf-8")]
    if fenced and len(fenced) == len(edited):
        print("Already applied — nothing to do.")
        return 0
    if fenced:
        die("mixed state — already fenced:\n  " + "\n  ".join(
            str(p.relative_to(root)) for p in fenced) + "\n  Run --revert first.")
    for rel, _t, mi in NEW_FILES:
        for p in dests(root, rel, mi):
            if p.exists():
                die(f"{p.relative_to(root)} already exists — run --revert first "
                    f"(or remove a hand-placed copy).")

    staged = {}
    for rel, edits, parents, _mi in EDITS:
        if git_dirty(root, rel) and not a.allow_dirty:
            die(f"{rel} has uncommitted modifications (git status). Commit/stash first, "
                f"or re-run with --allow-dirty if that is intended (e.g. the "
                f"TG_SUMMARY_ALL_MODES patch is applied but not committed yet).")
        src = (root / rel).read_text(encoding="utf-8")
        for pf in parents:
            if pf not in src:
                die(f"{rel}: prerequisite marker '{pf}' not found.")
        staged[rel] = patch(src, edits, rel)
    rel = "backend/app/api_server.py"
    m = root / MIRROR / Path(rel).relative_to("backend")
    if m.exists() and m.read_text(encoding="utf-8") != (root / rel).read_text(encoding="utf-8"):
        die(f"{m.relative_to(root)} differs from {rel} — sync the trees "
            f"(./desktop/build-scalp.sh backend) and re-run.")
    for rel, txt, _mi in NEW_FILES:
        staged[rel] = txt

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                t = Path(td) / (rel.replace("/", "_"))
                t.write_text(txt, encoding="utf-8")
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    print("  py_compile: OK")
    for rel in ("frontend/src/pages/Connections.jsx", "frontend/src/components/RestartAppButton.jsx"):
        ok, msg = jsx_check(root, Path(rel).name, staged[rel])
        print(f"  {Path(rel).name}: {msg}")
        if ok is False:
            die(f"{rel} does not parse after the edit.")

    print("  running suites…")
    bad = False
    for name, passed, out in run_suite(root, staged):
        summ = next((l for l in reversed(out.splitlines()) if "checks" in l), "")
        print(f"    {'PASS' if passed else 'FAIL'}  {name}: {summ}")
        if not passed:
            bad = True
            print("      " + "\n      ".join(out.splitlines()[-20:]))
    if bad:
        die("a suite failed (see above).")

    plan = []
    mirrored_of = {r: mi for r, _e, _p, mi in EDITS}
    mirrored_of.update({r: mi for r, _t, mi in NEW_FILES})
    for rel, txt in staged.items():
        for d in dests(root, rel, mirrored_of[rel]):
            plan.append((d, txt))
    if a.dry_run:
        for d, _ in plan:
            print(f"  would write  {d.relative_to(root)}")
        print("\nDry run OK — nothing written.")
        return 0

    backups, created = [], []
    try:
        for d, _ in plan:
            if d.exists():
                b = d.with_name(d.name + BAK)
                shutil.copy2(d, b)
                backups.append((d, b))
        for d, txt in plan:
            if not d.exists():
                created.append(d)
            d.parent.mkdir(parents=True, exist_ok=True)
            tmpf = d.with_name(d.name + ".tmp-" + FENCE)
            tmpf.write_text(txt, encoding="utf-8")
            os.replace(tmpf, d)
            print(f"  wrote  {d.relative_to(root)}")
    except Exception as e:
        for d, b in backups:
            shutil.copy2(b, d)
        for d in created:
            if d.exists():
                d.unlink()
        die(f"write failed ({e!r}) — every file restored.")

    print("""
Done. Next:
  1. ./desktop/build-scalp.sh both
  2. Open the rebuilt app once yourself (the button only exists in the new
     build). From then on: Connections → ↻ Restart App.
  3. Phone check: Connections → ↻ Restart App → confirm → the page reloads by
     itself when the app is back. Audit log: [SYSTEM][RESTART] lines.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# backend/app/api/system_restart_routes.py
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

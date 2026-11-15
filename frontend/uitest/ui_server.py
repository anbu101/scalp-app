"""ui_server.py — the BUILT frontend + the REAL backend routes it is tested on.

Real: /api/app/* (settings + the in-app event feed that drives the bell) and
/api/replay/* (the Dashboard live chart's "open position" rule), both on a
throw-away HOME with the real app.db migrations. Everything else the UI asks
for gets a quiet, empty answer so the shell renders.
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "backend"))
TMP = tempfile.mkdtemp(prefix="uitest_home_")
os.environ["HOME"] = TMP
os.environ.setdefault("SCALP_TEST_MODE", "1")

from fastapi import FastAPI, Request                                  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse              # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                    # noqa: E402

import app.backtest.replay.replay_core as RC                          # noqa: E402
RC.runs_db_path = lambda: os.path.join(TMP, "backtest.db")
from app.db.sqlite import get_conn                                    # noqa: E402
from app.db.migrations.runner import run_migrations                   # noqa: E402
from app.event_bus.audit_logger import audit_muted                    # noqa: E402
from app.api import app_settings_api, live_replay_routes              # noqa: E402

with audit_muted():
    run_migrations(get_conn())

BUILD = REPO / "frontend" / "build"
LICENSE = {"status": "active", "ui_level": "user",
           "strategies": ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1"]}
MODES = {"IC_V2": "LIVE", "TMA_V2": "LIVE", "TSG_V1": "PAPER", "VET_V1": "LIVE"}
SEEN = []                       # every path the UI asked for (for the report)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(app_settings_api.router)
app.include_router(live_replay_routes.router)


@app.get("/status")
def status():
    return {"backend": "running", "engine": "running", "market": "open", "mode": "LIVE",
            "version": "uitest"}


@app.get("/zerodha/status")
def zstatus():
    return {"connected": True, "configured": True, "trading_enabled": True}


@app.get("/system/license")
def lic():
    return LICENSE


@app.get("/api/config")
def cfg(strategy_id: str = ""):
    return {"config": {"trade_execution_mode": MODES.get(strategy_id, "PAPER")}}


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def quiet(path: str, request: Request):
    SEEN.append(path)
    return JSONResponse({})


@app.api_route("/system/{path:path}", methods=["GET", "POST"])
async def quiet_sys(path: str):
    SEEN.append("system/" + path)
    return JSONResponse({})


@app.api_route("/zerodha/{path:path}", methods=["GET", "POST"])
async def quiet_z(path: str):
    SEEN.append("zerodha/" + path)
    return JSONResponse({})


@app.get("/{path:path}")
def spa(path: str):
    f = BUILD / path
    if path and f.is_file():
        return FileResponse(str(f))
    return FileResponse(str(BUILD / "index.html"))


def start(port: int = 47321):
    import uvicorn
    cfg_ = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg_)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    import time
    import urllib.request
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/system/license", timeout=0.5)
            return srv
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("ui server did not start")

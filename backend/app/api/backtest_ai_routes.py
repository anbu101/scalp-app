# backend/app/api/backtest_ai_routes.py
#
# ── AI_ROUTES ── /api/backtest/ai/* — Ollama management + report narrative.
# Sub-router included by backtest_routes.py, so it inherits the SAME
# admin-license gate as every backtest route, and the same standing rule:
# stays OFF the public Tailscale Funnel until the auth audit is done.
#
# Everything here is optional infrastructure: if Ollama is absent, /status
# says so with install guidance and every other feature of the app —
# including full report generation — works unchanged (fail-open, like the
# rest of the terminal).
#
# Settings (active model + base URL) persist in STATE_DIR/ai_settings.json,
# same atomic-write pattern as the Dhan credentials file. No secrets here —
# it's a localhost URL and a model name.

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.event_bus.audit_logger import write_audit_log

ai_router = APIRouter(prefix="/ai", tags=["backtest-ai"])

_REPORT_NAME_RX = re.compile(r"^[A-Za-z0-9_\-]+\.md$")


# ----------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------
def _settings_path() -> Path:
    from app.utils.app_paths import STATE_DIR
    return STATE_DIR / "ai_settings.json"


def _load_settings() -> dict:
    from app.backtest.report.ai_narrative import DEFAULT_BASE_URL
    try:
        d = json.loads(_settings_path().read_text())
    except Exception:
        d = {}
    return {"base_url": d.get("base_url") or DEFAULT_BASE_URL,
            "model": d.get("model") or None}


def _save_settings(s: dict) -> None:
    from app.utils.app_paths import STATE_DIR
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(s))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(_settings_path()))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ----------------------------------------------------------------------
# pull job (one at a time — same in-memory pattern as the Dhan backfill)
# ----------------------------------------------------------------------
_PULL = {"running": False, "name": None, "progress": None, "error": None,
         "started_at": None, "cancel": False}
_PULL_LOCK = threading.Lock()


class AiSettingsRequest(BaseModel):
    model: Optional[str] = None
    base_url: Optional[str] = None


class AiPullRequest(BaseModel):
    name: str


class AiDeleteRequest(BaseModel):
    name: str


class AiNarrateRequest(BaseModel):
    report: str          # report file name, e.g. "report-24runs_20260705_122613.md"


# ----------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------
@ai_router.get("/status")
def ai_status():
    from app.backtest.report.ai_narrative import (
        CURATED_MODELS, get_version, list_models, OllamaError)
    s = _load_settings()
    out = {"base_url": s["base_url"], "active_model": s["model"],
           "curated": CURATED_MODELS, "installed": False, "version": None,
           "models": [],
           "pull": {"running": _PULL["running"], "name": _PULL["name"],
                    "progress": _PULL["progress"], "error": _PULL["error"]}}
    try:
        out["version"] = get_version(s["base_url"])
        out["installed"] = True
        out["models"] = list_models(s["base_url"])
    except OllamaError:
        pass  # not installed / not running — the UI shows install guidance
    return out


@ai_router.post("/settings")
def ai_settings(req: AiSettingsRequest):
    s = _load_settings()
    if req.model is not None:
        s["model"] = req.model.strip() or None
    if req.base_url is not None:
        s["base_url"] = req.base_url.strip() or s["base_url"]
    _save_settings(s)
    write_audit_log(f"[BACKTEST_AI] settings saved (model={s['model']})")
    return {"ok": True, **s}


@ai_router.post("/pull/start")
def ai_pull_start(req: AiPullRequest):
    name = req.name.strip()
    if not name or len(name) > 100 or any(ch.isspace() for ch in name):
        raise HTTPException(400, "invalid model name")
    with _PULL_LOCK:
        if _PULL["running"]:
            raise HTTPException(409, "a model download is already running")
        _PULL.update(running=True, name=name, progress=None, error=None,
                     started_at=time.time(), cancel=False)

    base = _load_settings()["base_url"]

    def _worker():
        from app.backtest.report.ai_narrative import (
            pull_model, OllamaError, OllamaCancelled)
        try:
            def _cb(p):
                tot, done = p.get("total"), p.get("completed")
                pct = (100.0 * done / tot) if (tot and done is not None) else None
                _PULL["progress"] = {**p, "pct": round(pct, 1) if pct is not None else None}
            pull_model(name, _cb, lambda: _PULL["cancel"], base_url=base)
            write_audit_log(f"[BACKTEST_AI] model pulled: {name}")
            # first successful pull with no active model → auto-select it
            s = _load_settings()
            if not s["model"]:
                s["model"] = name
                _save_settings(s)
        except OllamaCancelled:
            _PULL["error"] = "cancelled"
            write_audit_log(f"[BACKTEST_AI] pull cancelled: {name}")
        except OllamaError as e:
            _PULL["error"] = str(e)
            write_audit_log(f"[BACKTEST_AI] pull error: {name}: {e}")
        finally:
            _PULL["running"] = False
            _PULL["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="ai-model-pull").start()
    return {"status": "started", "name": name}


@ai_router.get("/pull/status")
def ai_pull_status():
    return {"running": _PULL["running"], "name": _PULL["name"],
            "progress": _PULL["progress"], "error": _PULL["error"],
            "started_at": _PULL["started_at"]}


@ai_router.post("/pull/cancel")
def ai_pull_cancel():
    if not _PULL["running"]:
        return {"status": "not_running"}
    _PULL["cancel"] = True
    return {"status": "cancelling"}


@ai_router.post("/model/delete")
def ai_model_delete(req: AiDeleteRequest):
    from app.backtest.report.ai_narrative import delete_model, OllamaError
    s = _load_settings()
    try:
        delete_model(req.name.strip(), base_url=s["base_url"])
    except OllamaError as e:
        raise HTTPException(502, str(e))
    if s["model"] == req.name.strip():
        s["model"] = None
        _save_settings(s)
    write_audit_log(f"[BACKTEST_AI] model deleted: {req.name}")
    return {"ok": True, "name": req.name}


@ai_router.post("/narrate")
def ai_narrate(req: AiNarrateRequest):
    """Fill section 7 of a saved report with a locally generated narrative.
    Requires the report's .json data sidecar (saved by the report route);
    older reports without one must be regenerated first. Idempotent —
    re-narrating replaces the previous narrative."""
    name = req.report.strip()
    if not _REPORT_NAME_RX.match(name):
        raise HTTPException(400, "invalid report name")
    from app.utils.app_paths import APP_HOME
    rdir = (APP_HOME / "backtest" / "reports")
    md_path = rdir / name
    if md_path.resolve().parent != rdir.resolve() or not md_path.is_file():
        raise HTTPException(404, "report not found")
    sidecar = md_path.with_suffix(".json")
    if not sidecar.is_file():
        raise HTTPException(400, "this report has no data sidecar — regenerate "
                                 "it (📄 Report) and narrate the new one")
    s = _load_settings()
    if not s["model"]:
        raise HTTPException(400, "no active model — download and select one in the AI panel")

    from app.backtest.report.ai_narrative import (
        generate_narrative, insert_narrative, OllamaError)
    from datetime import datetime, timedelta
    try:
        summary = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"couldn't read report data: {e}")
    t0 = time.time()
    try:
        narrative = generate_narrative(summary, s["model"], base_url=s["base_url"])
    except OllamaError as e:
        raise HTTPException(502, f"narrative failed (report unchanged): {e}")
    when = (datetime(1970, 1, 1) + timedelta(seconds=int(time.time()) + 19800)
            ).strftime("%Y-%m-%d %H:%M IST")
    updated = insert_narrative(md_path.read_text(encoding="utf-8"),
                               narrative, s["model"], when)
    md_path.write_text(updated, encoding="utf-8")
    write_audit_log(f"[BACKTEST_AI] narrative added to {name} "
                    f"(model={s['model']}, {time.time() - t0:.1f}s)")
    return {"ok": True, "name": name, "model": s["model"],
            "markdown": updated, "seconds": round(time.time() - t0, 1)}
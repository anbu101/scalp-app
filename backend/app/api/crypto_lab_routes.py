# ── CRYPTO_LAB BEGIN ──
# backend/app/api/crypto_lab_routes.py
#
# Crypto Options Lab API — /api/backtest/crypto/*
#
# Mounted as a SUB-ROUTER of backtest_routes.router (same pattern as
# AI_ROUTES): inherits the /api/backtest prefix AND the _require_admin_ui
# gate applied at app.include_router() in api_server.py. No api_server.py
# change needed; the gate fails closed for non-admin licenses.
#
# All endpoints are backtest-scoped: public Delta Exchange market data in,
# local corpus DB out. No broker sessions, no live engine, no order paths.

from __future__ import annotations

import csv
import io
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.backtest.crypto import delta_corpus as corpus
from app.backtest.crypto import crypto_ic_engine as engine

crypto_router = APIRouter(prefix="/crypto", tags=["crypto-lab"])


# ----------------------------------------------------------------------
# In-memory job state (single-user app; mirrors backtest_routes._JobState)
# ----------------------------------------------------------------------
class _CryptoJobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.corpus = {"running": False, "progress": None, "result": None,
                       "error": None, "started_at": None}
        self.run = {"running": False, "progress": None, "result": None,
                    "error": None, "started_at": None, "run_id": None}
        self.corpus_cancel = threading.Event()
        self.run_cancel = threading.Event()


_JOBS = _CryptoJobs()


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------
class CorpusBackfillRequest(BaseModel):
    months: int = 24
    span_pct: float = 4.0
    window_h: int = 25          # 25 = trade window; up to 48 = full life
    pace_s: float = 0.35
    include_perp: bool = True


class LabRunRequest(BaseModel):
    structure: str = "condor"
    entry_days_before: int = 1
    entry_hm: str = "17:45"
    exit_hm: str = "17:15"
    short_mode: str = "premium_ratio"
    short_ratio: float = 0.25
    short_otm_pct: float = 1.5
    wing_mode: str = "premium_ratio"
    wing_prem_ratio: float = 0.25
    wing_gap_pct: float = 1.0
    sl_mult: float = 1.5
    tp_ratio: float = 0.0
    contracts: int = 100
    fee_mult: float = 1.0
    gst_pct: float = 0.0
    margin_buffer_pct: float = 10.0
    margin_shock_pct: float = 10.0
    date_from: str = ""
    date_to: str = ""
    weekdays: list[int] = [0, 1, 2, 3, 4, 5, 6]
    exclude_dates: list[str] = []


# ----------------------------------------------------------------------
# Corpus endpoints
# ----------------------------------------------------------------------
@crypto_router.get("/corpus/status")
def corpus_status():
    with _JOBS.lock:
        job = dict(_JOBS.corpus)
    # cheap stats only — never COUNT(*) the big option table on a poll path
    try:
        stats = corpus.corpus_stats()
    except Exception as e:            # stats must never break the poll
        stats = {"error": str(e)}
    return {"job": job, "stats": stats}


@crypto_router.post("/corpus/backfill/start")
def corpus_backfill_start(req: CorpusBackfillRequest):
    if not (1 <= req.months <= 36):
        raise HTTPException(400, "months must be 1..36")
    if not (1.0 <= req.span_pct <= 10.0):
        raise HTTPException(400, "span_pct must be 1..10")
    if not (2 <= req.window_h <= 48):
        raise HTTPException(400, "window_h must be 2..48")
    if not (0.2 <= req.pace_s <= 2.0):
        raise HTTPException(400, "pace_s must be 0.2..2.0")
    with _JOBS.lock:
        if _JOBS.corpus["running"]:
            raise HTTPException(409, "corpus job already running")
        if _JOBS.run["running"]:
            raise HTTPException(
                409, "a backtest run is active; results during collection "
                     "are non-authoritative — wait for it to finish")
        _JOBS.corpus_cancel = threading.Event()
        cancel = _JOBS.corpus_cancel
        _JOBS.corpus.update(running=True, progress=None, result=None,
                            error=None, started_at=time.time())

    def _cb(p):
        with _JOBS.lock:
            _JOBS.corpus["progress"] = p

    def _worker():
        try:
            if req.include_perp:
                corpus.backfill_perp(req.months, progress_cb=_cb,
                                     cancel=cancel, pace_s=req.pace_s)
            res = corpus.backfill_days(
                req.months, span_pct=req.span_pct, window_h=req.window_h,
                pace_s=req.pace_s, progress_cb=_cb, cancel=cancel)
            res["cancelled"] = cancel.is_set()
            with _JOBS.lock:
                _JOBS.corpus.update(running=False, result=res)
        except corpus.DiskGuardError as e:
            with _JOBS.lock:
                _JOBS.corpus.update(running=False,
                                    error=f"DISK GUARD: {e}")
        except Exception as e:
            with _JOBS.lock:
                _JOBS.corpus.update(running=False, error=repr(e))

    threading.Thread(target=_worker, name="crypto-corpus-backfill",
                     daemon=True).start()
    return {"ok": True}


@crypto_router.post("/corpus/backfill/cancel")
def corpus_backfill_cancel():
    with _JOBS.lock:
        if not _JOBS.corpus["running"]:
            return {"ok": True, "note": "no corpus job running"}
        _JOBS.corpus_cancel.set()
    return {"ok": True, "note": "cancel requested — job stops at the next "
                                "expiry boundary (resumable later)"}


# ----------------------------------------------------------------------
# Backtest run endpoints
# ----------------------------------------------------------------------
@crypto_router.post("/run/start")
def run_start(req: LabRunRequest):
    try:
        cfg = engine.LabConfig.from_dict(req.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _JOBS.lock:
        if _JOBS.run["running"]:
            raise HTTPException(409, "a lab run is already active")
        corpus_busy = _JOBS.corpus["running"]
        _JOBS.run_cancel = threading.Event()
        cancel = _JOBS.run_cancel
        _JOBS.run.update(running=True, progress=None, result=None,
                         error=None, started_at=time.time(), run_id=None)

    def _cb(p):
        with _JOBS.lock:
            _JOBS.run["progress"] = p

    def _worker():
        try:
            res = engine.run_lab_backtest(cfg, progress_cb=_cb, cancel=cancel)
            res["corpus_busy_during_run"] = corpus_busy
            res["cancelled"] = cancel.is_set()
            with _JOBS.lock:
                _JOBS.run.update(running=False, result=res,
                                 run_id=res["run_id"])
        except Exception as e:
            with _JOBS.lock:
                _JOBS.run.update(running=False, error=repr(e))

    threading.Thread(target=_worker, name="crypto-lab-run",
                     daemon=True).start()
    return {"ok": True, "corpus_busy": corpus_busy,
            "note": ("corpus collection is running: results will be "
                     "non-authoritative" if corpus_busy else "")}


@crypto_router.get("/run/status")
def run_status():
    with _JOBS.lock:
        return dict(_JOBS.run)


@crypto_router.post("/run/cancel")
def run_cancel():
    with _JOBS.lock:
        if not _JOBS.run["running"]:
            return {"ok": True, "note": "no run active"}
        _JOBS.run_cancel.set()
    return {"ok": True}


# ----------------------------------------------------------------------
# Run library
# ----------------------------------------------------------------------
@crypto_router.get("/runs")
def runs_list(limit: int = 50):
    return {"runs": engine.list_runs(max(1, min(200, limit)))}


@crypto_router.get("/runs/{run_id}")
def run_detail(run_id: str):
    r = engine.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return r


@crypto_router.get("/runs/{run_id}/csv", response_class=PlainTextResponse)
def run_csv(run_id: str):
    r = engine.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    cols = ["expiry", "date", "weekday", "spot",
            "entry_ist", "exit_ist", "entry_ts", "exit_ts", "hold_min",
            "sc", "sc_prem", "sc_xprem", "sp", "sp_prem", "sp_xprem",
            "wc", "wc_prem", "wc_xprem", "wp", "wp_prem", "wp_xprem",
            "credit", "exit_debit", "sl_level", "tp_level", "exit_reason",
            "pnl_unit", "best_unit", "worst_unit",
            "usd_gross", "usd_fees", "usd_net",
            "margin_unit", "margin_usd"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore",
                       restval="")
    w.writeheader()
    w.writerows(r["trades"])
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="crypto_lab_{run_id}.csv"'})
# ── CRYPTO_LAB END ──
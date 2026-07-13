# backend/app/api/backtest_routes.py
#
# FastAPI routes for the backtest feature. Mount in api_server.py behind the
# SAME admin-license gate used for debug routes:
#
#     from app.api.backtest_routes import router as backtest_router
#     app.include_router(backtest_router, dependencies=[Depends(_require_admin_ui)])
#
# SECURITY NOTE: the backfill route hits the broker historical API and the run
# route exposes strategy behavior. It is admin-gated here. It must remain OFF
# the public Tailscale Funnel until the API auth audit is complete.
#
# Background-job model: backfill (~12 min) and run (seconds–minutes) execute in
# daemon threads. A single in-memory job registry tracks progress; the UI polls
# status. One job of each kind at a time (simple lock) — single-user app.

from __future__ import annotations

import threading
import time
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.event_bus.audit_logger import write_audit_log

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ----------------------------------------------------------------------
# In-memory job state (single-user app; not persisted across restarts)
# ----------------------------------------------------------------------
class _JobState:
    def __init__(self):
        self.lock = threading.Lock()
        self.backfill = {"running": False, "progress": None, "result": None,
                         "error": None, "started_at": None, "cancel": False}
        self.run = {"running": False, "progress": None, "result": None,
                    "error": None, "started_at": None, "run_id": None,
                    "cancel": False}
        # Dhan backfill (expired-options corpus fill) — data-only, backtest-scoped.
        self.dhan = {"running": False, "progress": None, "result": None,
                     "error": None, "started_at": None, "cancel": False}
        # BANKNIFTY futures backfill (continuous front-month series for BB).
        # ── SPOT_BACKFILL ── NIFTY index 1m job (sibling of the options job)
        self.spot = {"running": False, "progress": None, "result": None,
                     "error": None, "started_at": None, "cancel": False}
        self.dhan_fut = {"running": False, "progress": None, "result": None,
                         "error": None, "started_at": None, "cancel": False}
        # BANKNIFTY options backfill (per-contract ATM-band, for BB).
        self.bnf_opt = {"running": False, "progress": None, "result": None,
                        "error": None, "started_at": None, "cancel": False}


_JOBS = _JobState()


class _JobCancelled(Exception):
    """Raised inside a worker's progress callback to stop a running job."""
    pass


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------
class BackfillRequest(BaseModel):
    underlyings: list[str] = ["NIFTY"]
    lookback_days: int = 60
    forward_buffer_days: int = 14


class RunRequest(BaseModel):
    strategy_id: str = "SCALP_V1"
    underlying: str = "NIFTY"
    date_from: str            # YYYY-MM-DD
    date_to: str              # YYYY-MM-DD
    config_override: Optional[dict] = None

class DhanCredsRequest(BaseModel):
    client_id: str
    access_token: str


class DhanBackfillRequest(BaseModel):
    underlying: str = "NIFTY"
    date_from: str            # YYYY-MM-DD
    date_to: str              # YYYY-MM-DD
    atm_window: int = 10

class DhanFutBackfillRequest(BaseModel):
    underlying: str = "BANKNIFTY"
    date_from: str            # YYYY-MM-DD
    date_to: str              # YYYY-MM-DD

class BnfOptBackfillRequest(BaseModel):
    underlying: str = "BANKNIFTY"
    date_from: str            # YYYY-MM-DD
    date_to: str              # YYYY-MM-DD
    atm_band: int = 50        # ATM±band strikes (step 100)


class QueueJobRequest(BaseModel):
    strategy_id: str
    underlying: str = "NIFTY"
    date_from: str
    date_to: str
    config_override: dict
    label: str | None = None


# ── QUEUE_REORDER BEGIN ──
class QueueMoveRequest(BaseModel):
    direction: str    # "up" | "down" | "top"
# ── QUEUE_REORDER END ──

# ── REPORT_ENGINE BEGIN ──
class ReportRequest(BaseModel):
    run_ids: list[str]
    title: str | None = None
# ── REPORT_ENGINE END ──

# ----------------------------------------------------------------------
# BACKFILL
# ----------------------------------------------------------------------
@router.post("/backfill/start")
def backfill_start(req: BackfillRequest):
    with _JOBS.lock:
        if _JOBS.backfill["running"]:
            raise HTTPException(409, "A backfill is already running")
        _JOBS.backfill.update(running=True, progress=None, result=None,
                              error=None, started_at=time.time(), cancel=False)

    def _worker():
        try:
            from app.backtest.backfill.kite_backfill import run_backfill

            def _cb(p):
                _JOBS.backfill["progress"] = p
                # cooperative cancel: raising stops the backfill loop
                if _JOBS.backfill.get("cancel"):
                    raise _JobCancelled("backfill cancelled by user")

            result = run_backfill(
                underlyings=req.underlyings,
                lookback_days=req.lookback_days,
                forward_buffer_days=req.forward_buffer_days,
                progress_cb=_cb,
            )
            _JOBS.backfill["result"] = result
            if result.get("status") == "error":
                _JOBS.backfill["error"] = result.get("error")
        except _JobCancelled:
            write_audit_log("[BACKTEST_API][BACKFILL_CANCELLED]")
            _JOBS.backfill["error"] = "cancelled"
        except Exception as e:
            write_audit_log(f"[BACKTEST_API][BACKFILL_ERR] {e!r}")
            _JOBS.backfill["error"] = str(e)
        finally:
            _JOBS.backfill["running"] = False
            _JOBS.backfill["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="backtest-backfill").start()
    return {"status": "started"}


@router.get("/backfill/status")
def backfill_status():
    b = _JOBS.backfill
    elapsed, eta, pct = _eta(b["progress"], b["started_at"], "done", "total")
    return {"running": b["running"], "progress": b["progress"],
            "result": b["result"], "error": b["error"],
            "started_at": b["started_at"],
            "elapsed_s": round(elapsed), "eta_s": round(eta) if eta is not None else None,
            "pct": round(pct, 1)}


# ----------------------------------------------------------------------
# RUN
# ----------------------------------------------------------------------
@router.post("/run/start")
def run_start(req: RunRequest):
    if req.strategy_id not in ("SCALP_V1", "SCALP_V3", "SCALP_V4", "SCALP_V5", "HA_V1", "HA_SELL", "WICK_V1", "IC_V1", "PST_V1", "PST_SELL", "BB_V1", "BB_V2"):
        raise HTTPException(400, "Supported: SCALP_V1, SCALP_V3, SCALP_V4, SCALP_V5, HA_V1, HA_SELL, WICK_V1, IC_V1, PST_V1, PST_SELL, BB_V1, BB_V2")
    try:
        df = datetime.strptime(req.date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(req.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if dt < df:
        raise HTTPException(400, "date_to is before date_from")

    with _JOBS.lock:
        if _JOBS.run["running"]:
            raise HTTPException(409, "A backtest is already running")
        _JOBS.run.update(running=True, progress=None, result=None,
                         error=None, started_at=time.time(), run_id=None,
                         cancel=False)

    def _worker():
        meta = {"strategy_id": req.strategy_id, "underlying": req.underlying,
                "date_from": req.date_from, "date_to": req.date_to,
                "created_at": int(time.time())}
        try:
            from app.backtest.repo.backtest_repo import persist_run

            def _cb(p):
                _JOBS.run["progress"] = p
                if _JOBS.run.get("cancel"):
                    raise _JobCancelled("backtest cancelled by user")

            # ── AUDIT_MUTE BEGIN ── mute the runner replay (per-candle logging +
            # runner START/DONE/DIAG lines). Only the dispatch is wrapped; persist_run
            # and the RUN_ERR/RUN_CANCELLED audit lines stay OUTSIDE the mute so job
            # outcomes remain auditable. The flag defaults OFF and is restored on every
            # exit path, so live logging is unaffected.
            from app.event_bus.audit_logger import audit_muted
            with audit_muted():
                if req.strategy_id in ("BB_V1", "BB_V2"):
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.bb.backtest_bb_runner import run_bb_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    bb = run_bb_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        date_from=df, date_to=dt,
                        config=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    # adapt BB report → the persist/summary shape the UI expects
                    import uuid as _uuid
                    result = {
                        "run_id": str(_uuid.uuid4()),
                        "summary": bb["summary"],
                        "config": (req.config_override or {}),
                        "trades": bb["trades"],
                        "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id in ("SCALP_V3", "SCALP_V4"):
                    from app.backtest.runner.backtest_hedge_runner import run_hedge_backtest
                    result = run_hedge_backtest(
                        strategy_id=req.strategy_id, underlying=req.underlying,
                        date_from=df, date_to=dt,
                        config_override=req.config_override, progress_cb=_cb,
                    )
                elif req.strategy_id == "SCALP_V5":
                    # SCALP_V5: LONG option-BUYING, single instrument, 3m candles.
                    # Indicators run on the OPTION contract itself; entry = green ∧
                    # EMA8 crosses above EMA20_HIGH ∧ close>EMA20_HIGH; exit = first
                    # of EMA_EXIT / SL / TP / MAX_LOSS / MAX_PROFIT / EOD. The runner
                    # already returns run_id / summary / config / trades in the UI's
                    # render shape.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.scalpv5.backtest_scalpv5_runner import run_scalpv5_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    v5 = run_scalpv5_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": v5["run_id"],
                        "summary": v5["summary"],
                        "config": v5.get("config", (req.config_override or {})),
                        "trades": v5["trades"],
                        "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "HA_V1":
                    # HA_V1: LONG option-BUYING, Heikin Ashi, 1-minute candles.
                    # Indicators (HA + EMA20-of-HA-low) run on the OPTION contract;
                    # entry = COND1/COND2/COND3 (HA pattern vs EMA20-low); exit =
                    # first of TP (intrabar 1m high) / SL (1m close <= sl) / EOD.
                    # SINGLE GLOBAL open trade with same-1m-candle highest-premium
                    # arbitration. The runner returns run_id / summary / config /
                    # trades in the UI's render shape.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.ha.backtest_ha_runner import run_ha_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    ha = run_ha_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": ha["run_id"],
                        "summary": ha["summary"],
                        "config": ha.get("config", (req.config_override or {})),
                        "trades": ha["trades"],
                        "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "WICK_V1":
                    # WICK_V1: rejection-wick + midpoint pivot-reclaim reversal,
                    # LONG option-buying on the option's own premium candles.
                    # Multi-timeframe signal (1/3/5/10/15m), 1m-resolution entry
                    # and exit. Book AT SL/TP level, no slippage. Standard shape.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.wick.backtest_wick_runner import run_wick_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    w = run_wick_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": w["run_id"],
                        "summary": w["summary"],
                        "config": w.get("config", (req.config_override or {})),
                        "trades": w["trades"],
                        "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "HA_SELL":
                    # HA_SELL: HA_V1 signal inverted to SHORT (option selling).
                    # Same selected contract sold at entry, bought back to exit.
                    # SL/TP roles swap for the seller: TP = HA SL level (below,
                    # triggers on 1m close, books at close); SL = HA TP level
                    # (above, triggers on 1m high, books at the SL level).
                    # Charges on the sell/entry leg. Returns the standard shape.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.ha.backtest_ha_sell_runner import run_ha_sell_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    has = run_ha_sell_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": has["run_id"],
                        "summary": has["summary"],
                        "config": has.get("config", (req.config_override or {})),
                        "trades": has["trades"],
                        "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "PST_V1":
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.pst.backtest_pst_runner import run_pst_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    psr = run_pst_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": psr["run_id"], "summary": psr["summary"],
                        "config": psr.get("config", (req.config_override or {})),
                        "trades": psr["trades"], "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "PST_SELL":
                    # PST_SELL: PST_V1's signal inverted to SHORT (option
                    # selling). Same selected contract, sold at entry, bought
                    # back to exit. Roles swap: seller TP = V1's premium-SL
                    # level (fills AT the level); seller SL = V1's spot-target
                    # level (fills at that minute's option CLOSE, SPOT_SL).
                    # Charges on the sell/entry leg (charges_for_short_trade).
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.pst.backtest_pst_sell_runner import run_pst_sell_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    pss = run_pst_sell_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": pss["run_id"], "summary": pss["summary"],
                        "config": pss.get("config", (req.config_override or {})),
                        "trades": pss["trades"], "strategy_id": req.strategy_id,
                    }
                elif req.strategy_id == "IC_V1":
                    # IC_V1: iron condor — decision logic in ic_v1_engine
                    # (pure, unit-tested); runner does corpus/charges/DIAG.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.ic.backtest_ic_runner import run_ic_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    icr = run_ic_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": icr["run_id"],
                        "summary": icr["summary"],
                        "config": icr.get("config", (req.config_override or {})),
                        "trades": icr["trades"],
                        "strategy_id": req.strategy_id,
                    }
                else:
                    from app.backtest.runner.backtest_runner import run_backtest
                    result = run_backtest(
                        strategy_id=req.strategy_id, underlying=req.underlying,
                        date_from=df, date_to=dt,
                        config_override=req.config_override, progress_cb=_cb,
                    )
            # ── AUDIT_MUTE END ──
            # ── ABORTED_RUN_GUARD BEGIN ── runners return {run_id: None,
            # aborted: True, reason: ...} when the corpus has no data for the
            # range. Persisting that shape mints a backtest_runs row with a
            # NULL run_id — a ghost the UI shows as all-zeros and can never
            # open or delete ("run not found"). Surface the reason as the job
            # error instead; nothing is persisted.
            if result.get("aborted") or not result.get("run_id"):
                reason = result.get("reason") or "aborted: no data for the requested range"
                write_audit_log(f"[BACKTEST_API][RUN_ABORTED] {req.strategy_id} — {reason}")
                _JOBS.run["error"] = reason
                return
            # ── ABORTED_RUN_GUARD END ──
            result["meta"] = meta
            persist_run(result)
            _JOBS.run["run_id"] = result["run_id"]
            # return a JSON-safe summary (trades are dataclasses) for the UI
            _JOBS.run["result"] = {
                "run_id": result["run_id"],
                "summary": result["summary"],
                "config": result["config"],
            }
        except _JobCancelled:
            write_audit_log("[BACKTEST_API][RUN_CANCELLED]")
            _JOBS.run["error"] = "cancelled"
        except Exception as e:
            import traceback
            write_audit_log(f"[BACKTEST_API][RUN_ERR] {e!r}\n{traceback.format_exc()}")
            _JOBS.run["error"] = str(e)
            try:
                from app.backtest.repo.backtest_repo import mark_run_error
                mark_run_error(str(uuid.uuid4()), str(e), meta)
            except Exception:
                pass
        finally:
            _JOBS.run["running"] = False
            _JOBS.run["cancel"] = False
            # Defensive: ensure the backtest config override never outlives the
            # job, even on exception/cancel (the runner clears on the normal
            # path; this is belt-and-suspenders for the worker thread).
            try:
                from app.config.strategy_loader import clear_backtest_config_override
                clear_backtest_config_override()
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True, name="backtest-run").start()
    return {"status": "started"}


def _eta(progress, started_at, done_key, total_key):
    """Tentative ETA in seconds from elapsed × remaining/done. Returns
    (elapsed_s, eta_s, pct). For the backtest run, blends day-level progress
    with intra-day minute progress so a SINGLE-day run still animates smoothly
    instead of jumping from 0 to 100 (minute/minutes_total, when present)."""
    if not started_at:
        return (0, None, 0.0)
    elapsed = max(0.0, time.time() - started_at)
    if not progress:
        return (elapsed, None, 0.0)
    done = progress.get(done_key) or 0
    total = progress.get(total_key) or 0
    if total <= 0:
        return (elapsed, None, 0.0)

    # Fine-grained fraction: (completed_days + fraction_of_current_day) / total.
    minute = progress.get("minute")
    minutes_total = progress.get("minutes_total")
    if minute is not None and minutes_total:
        # `done` here is the current day index (1-based). Completed days =
        # done-1; add the current day's minute fraction.
        day_frac = min(1.0, float(minute) / float(minutes_total))
        completed = max(0, done - 1) + day_frac
        frac = completed / total
    else:
        frac = done / total

    frac = max(0.0, min(1.0, frac))
    pct = 100.0 * frac
    if frac <= 0.0:
        return (elapsed, None, pct)
    eta = elapsed * (1.0 - frac) / frac
    return (elapsed, eta, pct)


@router.get("/run/status")
def run_status():
    r = _JOBS.run
    elapsed, eta, pct = _eta(r["progress"], r["started_at"], "day", "total_days")
    return {"running": r["running"], "progress": r["progress"],
            "result": r["result"], "error": r["error"],
            "started_at": r["started_at"], "run_id": r["run_id"],
            "elapsed_s": round(elapsed), "eta_s": round(eta) if eta is not None else None,
            "pct": round(pct, 1)}


@router.post("/run/cancel")
def run_cancel():
    if not _JOBS.run["running"]:
        return {"status": "not_running"}
    _JOBS.run["cancel"] = True
    return {"status": "cancelling"}


@router.post("/backfill/cancel")
def backfill_cancel():
    if not _JOBS.backfill["running"]:
        return {"status": "not_running"}
    _JOBS.backfill["cancel"] = True
    return {"status": "cancelling"}


# ----------------------------------------------------------------------
# DHAN BACKFILL (expired-options corpus fill) — DATA-ONLY, backtest-scoped.
# Dhan is used ONLY to backfill historical option candles into backtest.db.
# It is NEVER an order/trade path. Credentials live in a backtest-scoped file,
# separate from the live Zerodha connection.
# ----------------------------------------------------------------------
import json as _json
from pathlib import Path as _Path


def _dhan_creds_path() -> "_Path":
    from app.utils.app_paths import STATE_DIR
    return STATE_DIR / "dhan_creds.json"


def _load_dhan_creds() -> Optional[dict]:
    p = _dhan_creds_path()
    if not p.exists():
        return None
    try:
        d = _json.loads(p.read_text())
        if d.get("client_id") and d.get("access_token"):
            return d
    except Exception:
        return None
    return None


@router.post("/dhan/creds")
def dhan_save_creds(req: DhanCredsRequest):
    """Persist Dhan data-API credentials (backfill-only). Token is 24h; the user
    re-pastes when it expires. Stored in the backtest-scoped state file."""
    from app.utils.app_paths import STATE_DIR
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _dhan_creds_path()
    # Atomic write.
    import os, tempfile
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_json.dumps({"client_id": req.client_id.strip(),
                                 "access_token": req.access_token.strip()}))
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, str(p))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    write_audit_log("[BACKTEST_API][DHAN] credentials saved (data-only)")
    return {"status": "saved"}


@router.get("/dhan/status")
def dhan_status():
    d = _JOBS.dhan
    creds = _load_dhan_creds()
    elapsed, eta, pct = _eta(d["progress"], d["started_at"], "done", "planned")
    return {"creds_set": creds is not None,
            "client_id": (creds or {}).get("client_id"),
            "running": d["running"], "progress": d["progress"],
            "result": d["result"], "error": d["error"],
            "started_at": d["started_at"],
            "elapsed_s": round(elapsed),
            "eta_s": round(eta) if eta is not None else None,
            "pct": round(pct, 1)}


# ── SPOT_BACKFILL BEGIN ── NIFTY index 1m → corpus (SPOT rows). Same creds,
# same job pattern as the options backfill; module has zero order capability.
class DhanSpotBackfillRequest(BaseModel):
    date_from: str
    date_to: str


@router.post("/dhan/spot/start")
def dhan_spot_start(req: DhanSpotBackfillRequest):
    creds = _load_dhan_creds()
    if not creds:
        raise HTTPException(400, "Dhan credentials not set — add them in Connections")
    try:
        df = datetime.strptime(req.date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(req.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if dt < df:
        raise HTTPException(400, "date_to is before date_from")

    with _JOBS.lock:
        if _JOBS.spot["running"]:
            raise HTTPException(409, "A spot backfill is already running")
        _JOBS.spot.update(running=True, progress=None, result=None,
                          error=None, started_at=time.time(), cancel=False)

    def _worker():
        try:
            from app.utils.app_paths import APP_HOME
            from app.backtest.dhan.dhan_spot_backfill import backfill_nifty_spot
            db = APP_HOME / "backtest" / "backtest.db"

            def _cb(p):
                _JOBS.spot["progress"] = p
                if _JOBS.spot.get("cancel"):
                    raise _JobCancelled("spot backfill cancelled by user")

            report = backfill_nifty_spot(
                db_path=str(db), client_id=creds["client_id"],
                access_token=creds["access_token"],
                date_from=df, date_to=dt, progress_cb=_cb,
                cancel_cb=lambda: _JOBS.spot.get("cancel", False),
            )
            _JOBS.spot["result"] = report
            if report.get("cancelled"):
                _JOBS.spot["error"] = "cancelled"
        except _JobCancelled:
            write_audit_log("[BACKTEST_API][SPOT] backfill cancelled")
            _JOBS.spot["error"] = "cancelled"
        except Exception as e:
            import traceback
            write_audit_log(f"[BACKTEST_API][SPOT] backfill error: {e!r}\n{traceback.format_exc()}")
            _JOBS.spot["error"] = str(e)
        finally:
            _JOBS.spot["running"] = False
            _JOBS.spot["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="dhan-spot-backfill").start()
    return {"status": "started"}


@router.get("/dhan/spot/status")
def dhan_spot_status():
    d = _JOBS.spot
    prog = d.get("progress") or {}
    pct = None
    if prog.get("total_chunks"):
        pct = round(100.0 * prog.get("chunk", 0) / prog["total_chunks"], 1)
    return {"running": d["running"], "progress": prog, "pct": pct,
            "result": d.get("result"), "error": d.get("error"),
            "started_at": d.get("started_at")}


@router.post("/dhan/spot/cancel")
def dhan_spot_cancel():
    if not _JOBS.spot["running"]:
        return {"status": "not_running"}
    _JOBS.spot["cancel"] = True
    return {"status": "cancelling"}
# ── SPOT_BACKFILL END ──


@router.post("/dhan/backfill/start")
def dhan_backfill_start(req: DhanBackfillRequest):
    creds = _load_dhan_creds()
    if not creds:
        raise HTTPException(400, "Dhan credentials not set — add them in Connections")
    try:
        df = datetime.strptime(req.date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(req.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if dt < df:
        raise HTTPException(400, "date_to is before date_from")

    with _JOBS.lock:
        if _JOBS.dhan["running"]:
            raise HTTPException(409, "A Dhan backfill is already running")
        _JOBS.dhan.update(running=True, progress=None, result=None,
                          error=None, started_at=time.time(), cancel=False)

    def _worker():
        try:
            from app.utils.app_paths import APP_HOME
            from app.backtest.dhan.dhan_client import DhanDataClient
            from app.backtest.dhan.dhan_backfill import backfill_nifty_dhan
            db = APP_HOME / "backtest" / "backtest.db"

            client = DhanDataClient(creds["client_id"], creds["access_token"])

            def _cb(p):
                _JOBS.dhan["progress"] = p
                if _JOBS.dhan.get("cancel"):
                    raise _JobCancelled("dhan backfill cancelled by user")

            report = backfill_nifty_dhan(
                db_path=str(db), client=client,
                date_from=df, date_to=dt, atm_window=int(req.atm_window),
                progress_cb=_cb,
                cancel_cb=lambda: _JOBS.dhan.get("cancel", False),
            )
            _JOBS.dhan["result"] = report
            if report.get("errors"):
                # surface count but don't treat as fatal
                write_audit_log(f"[BACKTEST_API][DHAN] backfill finished with "
                                f"{len(report['errors'])} call errors")
        except _JobCancelled:
            write_audit_log("[BACKTEST_API][DHAN] backfill cancelled")
            _JOBS.dhan["error"] = "cancelled"
        except Exception as e:
            import traceback
            write_audit_log(f"[BACKTEST_API][DHAN] backfill error: {e!r}\n{traceback.format_exc()}")
            _JOBS.dhan["error"] = str(e)
        finally:
            _JOBS.dhan["running"] = False
            _JOBS.dhan["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="dhan-backfill").start()
    return {"status": "started"}


@router.post("/dhan/backfill/cancel")
def dhan_backfill_cancel():
    if not _JOBS.dhan["running"]:
        return {"status": "not_running"}
    _JOBS.dhan["cancel"] = True
    return {"status": "cancelling"}

@router.get("/dhan/fut/status")
def dhan_fut_status():
    d = _JOBS.dhan_fut
    elapsed, eta, pct = _eta(d["progress"], d["started_at"], "done", "planned")
    return {"running": d["running"], "progress": d["progress"],
            "result": d["result"], "error": d["error"],
            "started_at": d["started_at"],
            "elapsed_s": round(elapsed),
            "eta_s": round(eta) if eta is not None else None,
            "pct": round(pct, 1)}


@router.post("/dhan/fut/backfill/start")
def dhan_fut_backfill_start(req: DhanFutBackfillRequest):
    creds = _load_dhan_creds()
    if not creds:
        raise HTTPException(400, "Dhan credentials not set — add them in Connections")
    try:
        df = datetime.strptime(req.date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(req.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if dt < df:
        raise HTTPException(400, "date_to is before date_from")

    with _JOBS.lock:
        if _JOBS.dhan_fut["running"]:
            raise HTTPException(409, "A BANKNIFTY FUT backfill is already running")
        _JOBS.dhan_fut.update(running=True, progress=None, result=None,
                              error=None, started_at=time.time(), cancel=False)

    def _worker():
        try:
            from app.utils.app_paths import APP_HOME
            from app.backtest.dhan.dhan_client import DhanDataClient
            from app.backtest.dhan.fut_backfill import backfill_banknifty_futures
            db = APP_HOME / "backtest" / "backtest.db"

            client = DhanDataClient(creds["client_id"], creds["access_token"])

            report = backfill_banknifty_futures(
                db_path=str(db), client=client,
                date_from=df, date_to=dt, underlying=req.underlying,
                progress_cb=lambda p: _JOBS.dhan_fut.__setitem__("progress", p),
                cancel_cb=lambda: _JOBS.dhan_fut.get("cancel", False),
            )
            _JOBS.dhan_fut["result"] = report
            if report.get("errors"):
                write_audit_log(f"[BACKTEST_API][DHAN_FUT] finished with "
                                f"{len(report['errors'])} call errors")
        except Exception as e:
            import traceback
            write_audit_log(f"[BACKTEST_API][DHAN_FUT] error: {e!r}\n{traceback.format_exc()}")
            _JOBS.dhan_fut["error"] = str(e)
        finally:
            _JOBS.dhan_fut["running"] = False
            _JOBS.dhan_fut["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="dhan-fut-backfill").start()
    return {"status": "started"}


@router.post("/dhan/fut/backfill/cancel")
def dhan_fut_backfill_cancel():
    if not _JOBS.dhan_fut["running"]:
        return {"status": "not_running"}
    _JOBS.dhan_fut["cancel"] = True
    return {"status": "cancelling"}

@router.get("/bnf/opt/status")
def bnf_opt_status():
    d = _JOBS.bnf_opt
    elapsed, eta, pct = _eta(d["progress"], d["started_at"], "done", "planned")
    return {"running": d["running"], "progress": d["progress"],
            "result": d["result"], "error": d["error"],
            "started_at": d["started_at"],
            "elapsed_s": round(elapsed),
            "eta_s": round(eta) if eta is not None else None,
            "pct": round(pct, 1)}


@router.post("/bnf/opt/backfill/start")
def bnf_opt_backfill_start(req: BnfOptBackfillRequest):
    creds = _load_dhan_creds()
    if not creds:
        raise HTTPException(400, "Dhan credentials not set — add them in Connections")
    try:
        df = datetime.strptime(req.date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(req.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if dt < df:
        raise HTTPException(400, "date_to is before date_from")

    with _JOBS.lock:
        if _JOBS.bnf_opt["running"]:
            raise HTTPException(409, "A BANKNIFTY options backfill is already running")
        _JOBS.bnf_opt.update(running=True, progress=None, result=None,
                             error=None, started_at=time.time(), cancel=False)

    def _worker():
        try:
            from app.utils.app_paths import APP_HOME
            from app.backtest.dhan.dhan_client import DhanDataClient
            from app.backtest.dhan.bnf_options_backfill import backfill_banknifty_options
            db = APP_HOME / "backtest" / "backtest.db"
            client = DhanDataClient(creds["client_id"], creds["access_token"])
            report = backfill_banknifty_options(
                db_path=str(db), client=client,
                date_from=df, date_to=dt, atm_band=int(req.atm_band),
                underlying=req.underlying,
                progress_cb=lambda p: _JOBS.bnf_opt.__setitem__("progress", p),
                cancel_cb=lambda: _JOBS.bnf_opt.get("cancel", False),
            )
            _JOBS.bnf_opt["result"] = report
            if report.get("errors"):
                write_audit_log(f"[BACKTEST_API][BNF_OPT] finished with "
                                f"{len(report['errors'])} errors")
        except Exception as e:
            import traceback
            write_audit_log(f"[BACKTEST_API][BNF_OPT] error: {e!r}\n{traceback.format_exc()}")
            _JOBS.bnf_opt["error"] = str(e)
        finally:
            _JOBS.bnf_opt["running"] = False
            _JOBS.bnf_opt["cancel"] = False

    threading.Thread(target=_worker, daemon=True, name="bnf-opt-backfill").start()
    return {"status": "started"}


@router.post("/bnf/opt/backfill/cancel")
def bnf_opt_backfill_cancel():
    if not _JOBS.bnf_opt["running"]:
        return {"status": "not_running"}
    _JOBS.bnf_opt["cancel"] = True
    return {"status": "cancelling"}

# ----------------------------------------------------------------------
# HISTORY + DETAIL + CSV
# ----------------------------------------------------------------------
@router.get("/runs")
def list_runs(limit: int = 50):
    from app.backtest.repo.backtest_repo import list_runs as _list
    return {"runs": _list(limit=limit)}


@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    from app.backtest.repo.backtest_repo import get_run
    d = get_run(run_id)
    if d is None:
        raise HTTPException(404, "run not found")
    return d


@router.get("/runs/{run_id}/csv")
def run_csv(run_id: str):
    from app.backtest.repo.backtest_repo import run_trades_csv
    csv_text = run_trades_csv(run_id)
    if csv_text is None:
        raise HTTPException(404, "run not found")
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="backtest_{run_id[:8]}.csv"'},
    )


# ----------------------------------------------------------------------
# DATA COVERAGE (what's in the corpus — helps the UI pick a date range)
# ----------------------------------------------------------------------
@router.get("/coverage")
def coverage(underlying: str = "NIFTY"):
    """Min/max candle dates available for the underlying, so the UI can default
    the date range to what's actually backfilled."""
    import sqlite3
    from app.utils.app_paths import APP_HOME
    db = APP_HOME / "backtest" / "backtest.db"
    if not db.exists():
        return {"available": False}
    try:
        c = sqlite3.connect(str(db))
        row = c.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM backtest_candles_1m WHERE underlying = ?",
            (underlying,),
        ).fetchone()
        c.close()
        if not row or row[0] is None:
            return {"available": False}
        from datetime import datetime, timedelta
        IST = 5 * 3600 + 30 * 60
        def d(e): return (datetime(1970, 1, 1) + timedelta(seconds=e + IST)).strftime("%Y-%m-%d")
        return {"available": True, "date_from": d(row[0]), "date_to": d(row[1]),
                "candles": row[2]}
    except Exception as e:
        return {"available": False, "error": str(e)}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    # ── IDEMPOTENT_DELETE ── deleting an id that isn't there is success, not
    # a 404: the desired end-state (run gone) already holds. The old 404 turned
    # every stale/ghost row into a scary "run not found" the user can't clear.
    from app.backtest.repo.backtest_repo import delete_run as _delete
    n = _delete(run_id)
    return {"ok": True, "run_id": run_id, "deleted": int(n)}


# ----------------------------------------------------------------------
# QUEUE (scheduled back-to-back runs)
# ----------------------------------------------------------------------
@router.post("/queue/enqueue")
def queue_enqueue(req: QueueJobRequest):
    from app.backtest.repo import backtest_queue_repo as q
    res = q.enqueue(
        strategy_id=req.strategy_id, underlying=req.underlying,
        date_from=req.date_from, date_to=req.date_to,
        config=req.config_override, label=req.label,
    )
    return {"ok": True, **res}


@router.get("/queue/status")
def queue_status():
    from app.backtest import queue_worker
    return queue_worker.status()


@router.post("/queue/start")
def queue_start():
    from app.backtest import queue_worker
    started = queue_worker.start_queue()
    return {"ok": True, "started": started}


@router.post("/queue/cancel")
def queue_cancel():
    """Cancel the whole queue (running job + all pending)."""
    from app.backtest import queue_worker
    queue_worker.cancel_queue()
    return {"ok": True}


@router.post("/queue/cancel-current")
def queue_cancel_current():
    """Cancel just the currently-running job; queue continues with the next."""
    from app.backtest import queue_worker
    queue_worker.cancel_current_job()
    return {"ok": True}


# ── QUEUE_ROW_DELETE BEGIN ── status-aware remove:
#   pending           → cancelled (tombstone stays until deleted/cleared)
#   done/error/cancel → the queue ROW is deleted (the persisted RUN is untouched)
#   running           → 409 (use /queue/cancel-current to stop it)
#   unknown           → idempotent success (desired end state already holds)
@router.delete("/queue/{job_id}")
def queue_remove_job(job_id: str):
    from app.backtest.repo import backtest_queue_repo as q
    st = q.job_status(job_id)
    if st is None:
        return {"ok": True, "job_id": job_id, "action": "noop"}
    if st == "running":
        raise HTTPException(409, "job is running — use /queue/cancel-current to stop it")
    if st == "pending":
        n = q.cancel_job(job_id)
        return {"ok": True, "job_id": job_id, "action": "cancelled" if n else "noop"}
    n = q.delete_job(job_id)
    return {"ok": True, "job_id": job_id, "action": "deleted" if n else "noop"}
# ── QUEUE_ROW_DELETE END ──


# ── QUEUE_REORDER BEGIN ── reorder a PENDING job among the pending set.
# No-op (unknown job / not pending / already at the edge) is SUCCESS with
# moved=0 — idempotent-delete philosophy: the desired end state holds, and the
# UI edge-disables anyway, so a stale click during a poll gap never errors.
@router.post("/queue/{job_id}/move")
def queue_move_job(job_id: str, req: QueueMoveRequest):
    if req.direction not in ("up", "down", "top"):
        raise HTTPException(400, "direction must be up, down, or top")
    from app.backtest.repo import backtest_queue_repo as q
    moved = q.move_job(job_id, req.direction)
    return {"ok": True, "job_id": job_id, "direction": req.direction, "moved": int(moved)}
# ── QUEUE_REORDER END ──


@router.post("/queue/clear")
def queue_clear():
    """Remove finished/cancelled/errored jobs from the list."""
    from app.backtest.repo import backtest_queue_repo as q
    n = q.clear_finished()
    return {"ok": True, "cleared": n}

# ── REPORT_ENGINE BEGIN ── deterministic report over selected runs. Loads via
# the repo, computes in report_engine (pure module), saves markdown to
# ~/.scalp-app/backtest/reports/. Every number is computed; the Observations
# section stays a placeholder until the Phase-3 narrative layer.
@router.post("/report")
def generate_report_route(req: ReportRequest):
    if not (2 <= len(req.run_ids) <= 60):
        raise HTTPException(400, "Select between 2 and 60 runs for a report")
    from app.backtest.repo.backtest_repo import get_run
    runs, missing = [], []
    for rid in req.run_ids:
        d = get_run(rid)
        (runs if d is not None else missing).append(d if d is not None else rid)
    if missing:
        raise HTTPException(404, f"runs not found: {', '.join(m[:8] for m in missing)}")
    # ── REPORT_ERR_SURFACE ── every failure becomes a REAL 4xx/5xx message.
    # An unhandled exception here returns a 500 that can bypass the CORS
    # middleware, which the Tauri webview reports as the useless "Load
    # failed" — never let that happen again.
    try:
        from app.backtest.report.report_engine import generate_report
        title = (req.title or f"report-{len(runs)}runs").strip()
        rep = generate_report(runs, title=title)
        from app.utils.app_paths import APP_HOME
        rdir = APP_HOME / "backtest" / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in title if ch.isalnum() or ch in "-_") or "report"
        fname = f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.md"
        (rdir / fname).write_text(rep["markdown"], encoding="utf-8")
        # ── AI_NARRATIVE ── data sidecar for the narrative layer
        import json as _sidecar_json
        (rdir / (fname[:-3] + ".json")).write_text(
            _sidecar_json.dumps(rep["summary"], default=str), encoding="utf-8")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        write_audit_log(f"[BACKTEST_API][REPORT_ERR] {e!r}\n{traceback.format_exc()}")
        raise HTTPException(500, f"report failed: {e!r}")
    write_audit_log(f"[BACKTEST_API][REPORT] {len(runs)} runs -> {fname}")
    return {"ok": True, "file": str(rdir / fname),
            "markdown": rep["markdown"], "summary": rep["summary"]}
# ── REPORT_ENGINE END ──

# ── REPORT_LIBRARY BEGIN ── browse/reopen/delete saved reports.
# SECURITY: name is validated by strict regex AND the resolved path must stay
# inside the reports dir — no traversal, ever (this file is on the pre-Funnel
# audit list; a file-serving route is exactly where traversal bugs live).
import re as _re
_REPORT_NAME_RX = _re.compile(r"^[A-Za-z0-9_\-]+\.md$")


def _reports_dir():
    from app.utils.app_paths import APP_HOME
    d = APP_HOME / "backtest" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_report_path(name: str):
    if not _REPORT_NAME_RX.match(name):
        raise HTTPException(400, "invalid report name")
    p = _reports_dir() / name
    if p.resolve().parent != _reports_dir().resolve():
        raise HTTPException(400, "invalid report name")
    return p


@router.get("/reports")
def list_reports():
    try:
        out = []
        for p in sorted(_reports_dir().glob("*.md"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size,
                            "modified": int(st.st_mtime)})
            except Exception:
                continue
        return {"reports": out}
    except Exception as e:
        import traceback
        write_audit_log(f"[BACKTEST_API][REPORT_LIST_ERR] {e!r}\n{traceback.format_exc()}")
        raise HTTPException(500, f"couldn't list reports: {e!r}")


@router.get("/reports/{name}")
def get_report(name: str):
    p = _safe_report_path(name)
    if not p.is_file():
        raise HTTPException(404, "report not found")
    return {"name": name, "markdown": p.read_text(encoding="utf-8")}


@router.delete("/reports/{name}")
def delete_report(name: str):
    # idempotent: already-gone is success (same philosophy as run/queue deletes)
    p = _safe_report_path(name)
    deleted = 0
    if p.is_file():
        p.unlink()
        deleted = 1
    sj = p.with_suffix(".json")   # ── AI_NARRATIVE ── data sidecar goes with it
    if sj.is_file():
        sj.unlink()
    return {"ok": True, "name": name, "deleted": deleted}
# ── REPORT_LIBRARY END ──
# ── AI_ROUTES ── /api/backtest/ai/* (Ollama management + report narrative).
# Sub-router: inherits this router's mount AND the admin gate applied at
# app.include_router() — no api_server.py change needed.
from app.api.backtest_ai_routes import ai_router
router.include_router(ai_router)
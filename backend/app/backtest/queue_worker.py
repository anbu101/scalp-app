# backend/app/backtest/queue_worker.py
#
# Sequential queue worker. Pulls pending jobs oldest-first, runs each through the
# existing backtest runner dispatch (SAME as /run/start), persists it (so it
# shows in Compare Runs), then moves on. One job at a time. Cancellable per-job
# (current) and whole-queue (pending).

from __future__ import annotations

import threading
import time
import uuid
from datetime import date

from app.event_bus.audit_logger import write_audit_log
from app.backtest.repo import backtest_queue_repo as q


class _QueueState:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.active = False              # worker loop running
        self.cancel_all = False          # stop the whole queue after current job
        self.cancel_current = False      # cancel the in-flight job
        self.current_job_id: str | None = None
        self.current_progress: dict | None = None
        self.started_at: float | None = None


STATE = _QueueState()


def _dispatch_run(*, strategy_id, underlying, df, dt, config, progress_cb, cancel_cb):
    """Run one backtest via the SAME per-strategy dispatch /run/start uses.
    Returns a result dict with run_id / summary / config / trades. Keep this in
    sync with backtest_routes.run_start's _worker dispatch."""
    from app.utils.app_paths import APP_HOME
    db = APP_HOME / "backtest" / "backtest.db"

    if strategy_id in ("BB_V1", "BB_V2"):
        from app.backtest.bb.backtest_bb_runner import run_bb_backtest
        bb = run_bb_backtest(
            db_path=str(db), strategy_id=strategy_id,
            date_from=df, date_to=dt, config=(config or {}),
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
        return {"run_id": str(uuid.uuid4()), "summary": bb["summary"],
                "config": (config or {}), "trades": bb["trades"],
                "strategy_id": strategy_id}

    if strategy_id in ("SCALP_V3", "SCALP_V4"):
        from app.backtest.runner.backtest_hedge_runner import run_hedge_backtest
        return run_hedge_backtest(
            strategy_id=strategy_id, underlying=underlying,
            date_from=df, date_to=dt,
            config_override=config, progress_cb=progress_cb,
        )

    if strategy_id == "SCALP_V5":
        from app.backtest.scalpv5.backtest_scalpv5_runner import run_scalpv5_backtest
        v5 = run_scalpv5_backtest(
            db_path=str(db), strategy_id=strategy_id,
            underlying=underlying, date_from=df, date_to=dt,
            config_override=(config or {}), progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        return {"run_id": v5["run_id"], "summary": v5["summary"],
                "config": v5.get("config", (config or {})),
                "trades": v5["trades"], "strategy_id": strategy_id}

    if strategy_id == "HA_V1":
        from app.backtest.ha.backtest_ha_runner import run_ha_backtest
        ha = run_ha_backtest(
            db_path=str(db), strategy_id=strategy_id,
            underlying=underlying, date_from=df, date_to=dt,
            config_override=(config or {}), progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        return {"run_id": ha["run_id"], "summary": ha["summary"],
                "config": ha.get("config", (config or {})),
                "trades": ha["trades"], "strategy_id": strategy_id}

    # SCALP_V1 (and any default)
    from app.backtest.runner.backtest_runner import run_backtest
    return run_backtest(
        strategy_id=strategy_id, underlying=underlying,
        date_from=df, date_to=dt,
        config_override=config, progress_cb=progress_cb,
    )


def _run_one(job: dict):
    """Run a single queued job via the shared dispatch + persist_run."""
    from app.backtest.repo.backtest_repo import persist_run, mark_run_error

    job_id = job["job_id"]
    cfg = job["config"]
    strat = job["strategy_id"]
    underlying = job["underlying"]
    d_from = date.fromisoformat(job["date_from"])
    d_to = date.fromisoformat(job["date_to"])

    q.mark_running(job_id)
    STATE.current_job_id = job_id
    STATE.current_progress = {"day": 0, "total_days": 0}

    def _progress(p):
        STATE.current_progress = p

    def _cancelled():
        return STATE.cancel_current or STATE.cancel_all

    meta = {
        "strategy_id": strat, "underlying": underlying,
        "date_from": job["date_from"], "date_to": job["date_to"],
        "config": cfg, "created_at": int(time.time()),
    }

    try:
        result = _dispatch_run(
            strategy_id=strat, underlying=underlying, df=d_from, dt=d_to,
            config=cfg, progress_cb=_progress, cancel_cb=_cancelled,
        )
        result["meta"] = meta
        if STATE.cancel_current or STATE.cancel_all:
            run_id = result.get("run_id") or str(uuid.uuid4())
            mark_run_error(run_id, "cancelled", meta)
            q.mark_error(job_id, "cancelled")
            write_audit_log(f"[BACKTEST][QUEUE] job {job_id[:8]} cancelled")
        else:
            persist_run(result)
            q.mark_done(job_id, result["run_id"])
            write_audit_log(f"[BACKTEST][QUEUE] job {job_id[:8]} done → run {result['run_id'][:8]}")
    except Exception as e:
        import traceback
        write_audit_log(f"[BACKTEST][QUEUE][ERROR] job {job_id[:8]}: {e!r}\n{traceback.format_exc()}")
        try:
            mark_run_error(str(uuid.uuid4()), str(e), meta)
        except Exception:
            pass
        q.mark_error(job_id, str(e))
    finally:
        STATE.current_job_id = None
        STATE.current_progress = None
        STATE.cancel_current = False
        # clear any backtest config override left by the runner (belt + braces)
        try:
            from app.config.strategy_loader import clear_backtest_config_override
            clear_backtest_config_override()
        except Exception:
            pass


def _worker_loop():
    write_audit_log("[BACKTEST][QUEUE] worker started")
    STATE.active = True
    STATE.started_at = time.time()
    try:
        while True:
            if STATE.cancel_all:
                n = q.cancel_all_pending()
                write_audit_log(f"[BACKTEST][QUEUE] cancel_all — {n} pending cancelled")
                break
            job = q.next_pending()
            if not job:
                break
            _run_one(job)
    finally:
        STATE.active = False
        STATE.cancel_all = False
        STATE.cancel_current = False
        STATE.current_job_id = None
        write_audit_log("[BACKTEST][QUEUE] worker stopped")


def start_queue() -> bool:
    """Start the worker if not already running. Returns True if it started.
    Fully resets BOTH cancel flags first so a stuck flag from a previous
    cancel (e.g. cancelling a job while the worker was idle) can't immediately
    abort the new run."""
    with STATE.lock:
        if STATE.active:
            return False
        # CRITICAL: clear stale cancel flags before (re)starting.
        STATE.cancel_all = False
        STATE.cancel_current = False
        STATE.thread = threading.Thread(target=_worker_loop, daemon=True,
                                        name="backtest-queue-worker")
        STATE.thread.start()
        return True


def cancel_current_job():
    STATE.cancel_current = True


def cancel_queue():
    """Stop the whole queue: cancel the running job and all pending. If the
    worker isn't running, just cancel pending jobs directly and DON'T leave the
    cancel_all flag stuck on (that would poison the next start)."""
    if STATE.active:
        STATE.cancel_all = True
        STATE.cancel_current = True
    else:
        # Idle: no worker loop to consume/reset the flag — cancel pending now.
        n = q.cancel_all_pending()
        write_audit_log(f"[BACKTEST][QUEUE] cancel_queue (idle) — {n} pending cancelled")


def status() -> dict:
    return {
        "active": STATE.active,
        "current_job_id": STATE.current_job_id,
        "progress": STATE.current_progress,
        "cancelling": STATE.cancel_all or STATE.cancel_current,
        "jobs": q.list_jobs(),
    }


def resume_on_startup():
    """Reset any orphaned 'running' jobs to pending (app crashed mid-run) AND
    clear cancel flags. Call once at app startup. Does NOT auto-start."""
    STATE.cancel_all = False
    STATE.cancel_current = False
    n = q.reset_orphaned_running()
    if n:
        write_audit_log(f"[BACKTEST][QUEUE] reset {n} orphaned running job(s) to pending")
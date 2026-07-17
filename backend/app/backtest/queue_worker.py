# backend/app/backtest/queue_worker.py
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
        self.active = False
        self.cancel_all = False
        self.cancel_current = False
        self.current_job_id: str | None = None
        self.current_progress: dict | None = None
        self.started_at: float | None = None


STATE = _QueueState()


def _dispatch_run(*, strategy_id, underlying, df, dt, config, progress_cb, cancel_cb):
    """Mute audit logging for the whole replay, then delegate to the real
    dispatch. Muting at THIS single chokepoint silences every runner
    (V1/V3/V4/V5/HA/BB) WITHOUT editing any runner body.

    WHY: write_audit_log opens+closes the daily log file on EVERY call and the
    runners log per candle — that both slows multi-year runs (hundreds of
    thousands of file open/close cycles) AND pollutes TODAY's live audit log
    with mis-dated replay lines (the logger rotates on wall-clock date, not the
    simulated date). The mute flag defaults OFF and is restored on every exit
    path via the context manager, so LIVE logging is completely unaffected.

    Note: the [BACKTEST][QUEUE] orchestration lines live in _run_one, OUTSIDE
    this call, so job start/done/error auditing stays fully visible.
    """
    from app.event_bus.audit_logger import audit_muted
    with audit_muted():
        return _dispatch_run_impl(
            strategy_id=strategy_id, underlying=underlying, df=df, dt=dt,
            config=config, progress_cb=progress_cb, cancel_cb=cancel_cb,
        )


def _dispatch_run_impl(*, strategy_id, underlying, df, dt, config, progress_cb, cancel_cb):
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

    if strategy_id == "WICK_V1":
        from app.backtest.wick.backtest_wick_runner import run_wick_backtest
        w = run_wick_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                              date_from=df, date_to=dt, config_override=(config or {}),
                              progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": w["run_id"], "summary": w["summary"],
                "config": w.get("config", (config or {})), "trades": w["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "HA_SELL":
        # HA_SELL: HA_V1 signal inverted to SHORT (option selling). Same
        # selected contract, sold at entry, bought back to exit. SL/TP roles
        # swap (seller TP = HA SL level below; seller SL = HA TP level above);
        # TP triggers on 1m close and books at close, SL triggers on 1m high
        # and books at the SL level. Charges on the sell/entry leg.
        from app.backtest.ha.backtest_ha_sell_runner import run_ha_sell_backtest
        ha = run_ha_sell_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                                  date_from=df, date_to=dt, config_override=(config or {}),
                                  progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": ha["run_id"], "summary": ha["summary"],
                "config": ha.get("config", (config or {})), "trades": ha["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "PST_V1":
        # PST_V1: pivot/SMA/SuperTrend spot-signal option scalper
        from app.backtest.pst.backtest_pst_runner import run_pst_backtest
        ps = run_pst_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                              date_from=df, date_to=dt, config_override=(config or {}),
                              progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": ps["run_id"], "summary": ps["summary"],
                "config": ps.get("config", (config or {})), "trades": ps["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "PST_HEDGE":
        # PST_HEDGE: PST_V1's signal, option side flipped, still BUYING
        # (bull→PE, bear→CE). Exit logic is PST_V1's verbatim; only the
        # signal side is inverted (capital-light proxy for PST_SELL).
        from app.backtest.pst.backtest_pst_hedge_runner import run_pst_hedge_backtest
        psh = run_pst_hedge_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                                     date_from=df, date_to=dt, config_override=(config or {}),
                                     progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": psh["run_id"], "summary": psh["summary"],
                "config": psh.get("config", (config or {})), "trades": psh["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "PST_SELL":
        # PST_SELL: PST_V1's signal inverted to SHORT (option selling).
        # Seller TP = V1's premium-SL level (fills at level); seller SL =
        # V1's spot-target level (fills at that minute's option close).
        from app.backtest.pst.backtest_pst_sell_runner import run_pst_sell_backtest
        pss = run_pst_sell_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                                    date_from=df, date_to=dt, config_override=(config or {}),
                                    progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": pss["run_id"], "summary": pss["summary"],
                "config": pss.get("config", (config or {})), "trades": pss["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "TMA_V1":
        # ── TMA_V1 ── triple-EMA (5/13/89 @5m) spot-signal option buying;
        # independent C1/C2 condition slots, crossover + SL/TP + EOD exits.
        from app.backtest.tma.backtest_tma_runner import run_tma_backtest
        tma = run_tma_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                               date_from=df, date_to=dt, config_override=(config or {}),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": tma["run_id"], "summary": tma["summary"],
                "config": tma.get("config", (config or {})), "trades": tma["trades"],
                "strategy_id": strategy_id}

    if strategy_id == "IC_V1":
        # IC_V1: time-entry premium-defined iron condor (SELL body + BUY
        # wings), per-leg SL/TP, Move-To-Cost cross-leg rule, EOD square-off.
        from app.backtest.ic.backtest_ic_runner import run_ic_backtest
        ic = run_ic_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,
                             date_from=df, date_to=dt, config_override=(config or {}),
                             progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": ic["run_id"], "summary": ic["summary"],
                "config": ic.get("config", (config or {})), "trades": ic["trades"],
                "strategy_id": strategy_id}

    from app.backtest.runner.backtest_runner import run_backtest
    return run_backtest(
        strategy_id=strategy_id, underlying=underlying,
        date_from=df, date_to=dt,
        config_override=config, progress_cb=progress_cb,
    )


def _run_one(job: dict):
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
        # ── ABORTED_RUN_GUARD (queue) BEGIN ── same contract as backtest_routes:
        # runners return {run_id: None, aborted: True, reason} when the corpus
        # has no data for the range. Never persist that shape — it mints a
        # NULL-run_id ghost in backtest_runs (all-zero row, undeletable from
        # the UI) and result["run_id"][:8] below would raise on None AFTER the
        # ghost landed. The queue JOB goes to error with the human-readable
        # reason so the Queue tab shows exactly which staged job hit an
        # uncovered range and why; the rest of the queue continues.
        elif result.get("aborted") or not result.get("run_id"):
            reason = result.get("reason") or "aborted: no data for the requested range"
            write_audit_log(f"[BACKTEST][QUEUE] job {job_id[:8]} aborted — {reason}")
            q.mark_error(job_id, reason)
        # ── ABORTED_RUN_GUARD (queue) END ──
        else:
            persist_run(result)
            q.mark_done(job_id, result["run_id"])
            write_audit_log(f"[BACKTEST][QUEUE] job {job_id[:8]} done -> run {result['run_id'][:8]}")
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
                write_audit_log(f"[BACKTEST][QUEUE] cancel_all - {n} pending cancelled")
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
    with STATE.lock:
        if STATE.active:
            return False
        STATE.cancel_all = False
        STATE.cancel_current = False
        STATE.thread = threading.Thread(target=_worker_loop, daemon=True,
                                        name="backtest-queue-worker")
        STATE.thread.start()
        return True


def cancel_current_job():
    STATE.cancel_current = True


def cancel_queue():
    if STATE.active:
        STATE.cancel_all = True
        STATE.cancel_current = True
    else:
        n = q.cancel_all_pending()
        write_audit_log(f"[BACKTEST][QUEUE] cancel_queue (idle) - {n} pending cancelled")


def status() -> dict:
    return {
        "active": STATE.active,
        "current_job_id": STATE.current_job_id,
        "progress": STATE.current_progress,
        "cancelling": STATE.cancel_all or STATE.cancel_current,
        "jobs": q.list_jobs(),
    }


def resume_on_startup():
    STATE.cancel_all = False
    STATE.cancel_current = False
    n = q.reset_orphaned_running()
    if n:
        write_audit_log(f"[BACKTEST][QUEUE] reset {n} orphaned running job(s) to pending")
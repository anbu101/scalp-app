# backend/app/jobs/scheduler_observability.py
#
# ── EOD_OBS_20260821 BEGIN (new module) ─────────────────────────────────────
# Born 2026-08-21 15:15/15:25 incident: five EOD jobs (paper sweep, BB_V1,
# BB_V2, HA, SCALP_V3) produced ZERO audit output while four siblings on the
# same scheduler (SCALP_V1, SCALP_V5, IC, watchdog) fired on the exact
# second. A job that raises, is missed, or is skipped is reported by
# APScheduler on ITS OWN Python logger — which goes to the backend's stderr
# and is discarded in the packaged app. This module closes that blind spot:
#
#   attach_scheduler_observability(scheduler)
#     * scheduler listener → one audit line per job lifecycle event:
#         [APS][SUBMITTED]          job handed to the executor
#         [APS][OK]                 job function returned
#         [APS][ERROR] + traceback  job function raised
#         [APS][MISSED]             fire time passed misfire_grace_time
#         [APS][SKIP_MAX_INSTANCES] previous run still alive
#     * logging bridge on the 'apscheduler' logger (INFO+) → [APS][LOG]
#       lines, catching scheduler-internal messages the listener cannot
#       see (e.g. "Error submitting job").
#
#   log_scheduled_jobs(scheduler)
#     * dumps every registered job id + next_run_time right after start(),
#       so the boot log proves what is actually scheduled.
#
# Volume: ~15 jobs/day × a few lines each — negligible.
# FAIL DIRECTION: observability must never hurt the observed — every path
# here is wrapped; a broken audit write can never break a job or the
# scheduler. Idempotent: safe to call attach twice (restart-in-process).
# ── EOD_OBS_20260821 END (header) ───────────────────────────────────────────

import logging

from app.event_bus.audit_logger import write_audit_log

try:
    from apscheduler.events import (
        EVENT_JOB_SUBMITTED,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_ERROR,
        EVENT_JOB_MISSED,
        EVENT_JOB_MAX_INSTANCES,
    )
    _APS_EVENTS_OK = True
except Exception:  # pragma: no cover — apscheduler always present in app
    _APS_EVENTS_OK = False

_ATTACHED_FLAG = "_eod_obs_20260821_attached"
_BRIDGE_HANDLER_NAME = "eod_obs_20260821_audit_bridge"


def _fmt_exc(event) -> str:
    exc = getattr(event, "exception", None)
    tb = getattr(event, "traceback", None)
    parts = []
    if exc is not None:
        parts.append(f"exc={type(exc).__name__}: {exc}")
    if tb:
        # Traceback is the payload we have been missing — keep it, capped.
        parts.append("trace=" + str(tb)[-1500:].replace("\n", " | "))
    return " ".join(parts) if parts else "no exception detail"


def _listener(event) -> None:
    """One audit line per job lifecycle event. Never raises."""
    try:
        jid = getattr(event, "job_id", "?")
        code = getattr(event, "code", None)
        when = getattr(event, "scheduled_run_time", None) or getattr(
            event, "scheduled_run_times", None
        )
        if code == EVENT_JOB_SUBMITTED:
            write_audit_log(f"[APS][SUBMITTED] id={jid} scheduled={when}")
        elif code == EVENT_JOB_EXECUTED:
            write_audit_log(f"[APS][OK] id={jid} scheduled={when}")
        elif code == EVENT_JOB_ERROR:
            write_audit_log(
                f"[APS][ERROR] id={jid} scheduled={when} {_fmt_exc(event)}"
            )
        elif code == EVENT_JOB_MISSED:
            write_audit_log(
                f"[APS][MISSED] id={jid} scheduled={when} — fire time passed "
                f"misfire_grace_time; job did NOT run"
            )
        elif code == EVENT_JOB_MAX_INSTANCES:
            write_audit_log(
                f"[APS][SKIP_MAX_INSTANCES] id={jid} scheduled={when} — a "
                f"previous run of this job is still alive; this fire skipped"
            )
        else:
            write_audit_log(f"[APS][EVENT] id={jid} code={code}")
    except Exception:
        pass


class _AuditBridgeHandler(logging.Handler):
    """Forward 'apscheduler' logger records into the audit log. Never raises."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            write_audit_log(
                f"[APS][LOG][{record.levelname}] {record.getMessage()}"
            )
        except Exception:
            pass


def attach_scheduler_observability(scheduler) -> None:
    """Attach listener + logging bridge. Idempotent. Never raises."""
    try:
        if getattr(scheduler, _ATTACHED_FLAG, False):
            return

        if _APS_EVENTS_OK:
            scheduler.add_listener(
                _listener,
                EVENT_JOB_SUBMITTED
                | EVENT_JOB_EXECUTED
                | EVENT_JOB_ERROR
                | EVENT_JOB_MISSED
                | EVENT_JOB_MAX_INSTANCES,
            )
            write_audit_log("[APS] job lifecycle listener attached")
        else:
            write_audit_log(
                "[APS][WARN] apscheduler.events import failed — "
                "listener NOT attached; logging bridge only"
            )

        aps_logger = logging.getLogger("apscheduler")
        if not any(h.get_name() == _BRIDGE_HANDLER_NAME
                   for h in aps_logger.handlers):
            h = _AuditBridgeHandler(level=logging.INFO)
            h.set_name(_BRIDGE_HANDLER_NAME)
            aps_logger.addHandler(h)
            # The bridge must SEE INFO records even if the root config is
            # stricter; this widens only the 'apscheduler' logger.
            if aps_logger.level in (logging.NOTSET,) or \
                    aps_logger.level > logging.INFO:
                aps_logger.setLevel(logging.INFO)
            write_audit_log("[APS] logging bridge attached (INFO+)")

        setattr(scheduler, _ATTACHED_FLAG, True)
    except Exception as e:
        try:
            write_audit_log(f"[APS][WARN] observability attach failed: {e!r}")
        except Exception:
            pass


def log_scheduled_jobs(scheduler) -> None:
    """Dump the registered jobs table to the audit log. Never raises."""
    try:
        jobs = scheduler.get_jobs()
        write_audit_log(f"[APS] scheduled jobs after start: {len(jobs)}")
        for j in jobs:
            write_audit_log(
                f"[APS]   id={j.id} next_run={getattr(j, 'next_run_time', '?')}"
            )
    except Exception as e:
        try:
            write_audit_log(f"[APS][WARN] job dump failed: {e!r}")
        except Exception:
            pass
#!/usr/bin/env python3
# apply_eod_obs_20260821.py
#
# ── EOD_OBS_20260821 ── Assert-anchored edit script (run from repo root):
#     python3 apply_eod_obs_20260821.py
#
# Prerequisite: EOD_1515_FIX_20260821 already applied (it is — verified on
# main). Also copy the two module files into backend/app/jobs/ first:
#     scheduler_observability.py   (new)
#     eod_safety.py                (replaces the EOD_1515 version)
#
# Edits to backend/app/api_server.py:
#   O1  import attach_scheduler_observability + log_scheduled_jobs
#   O2  attach observability BEFORE scheduler.start(); dump the scheduled
#       jobs table AFTER start(), so the boot log proves what is scheduled
#       and every job fire/skip/error/miss lands in the audit log.
#
# Every anchor must appear EXACTLY ONCE or the script aborts with no writes.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = ROOT / "backend" / "app" / "api_server.py"

EDITS = [
    (
        "O1 imports",
        """from app.jobs.eod_safety import (
    boot_close_stale_paper_rows,
    eod_open_row_watchdog_job,
)
# ── EOD_1515_FIX_20260821 END (imports) ──""",
        """from app.jobs.eod_safety import (
    boot_close_stale_paper_rows,
    eod_open_row_watchdog_job,
)
# ── EOD_1515_FIX_20260821 END (imports) ──
# ── EOD_OBS_20260821 ── APScheduler is silent in the audit log: a job that
# raises/misses/skips is reported only on apscheduler's own logger (stderr,
# discarded in the packaged app) — exactly how five EOD jobs went dark on
# 2026-08-21 with zero trace. This bridges every job lifecycle event and
# scheduler log line into the audit log, and dumps the jobs table at boot.
from app.jobs.scheduler_observability import (
    attach_scheduler_observability,
    log_scheduled_jobs,
)
# ── EOD_OBS_20260821 END (imports) ──""",
    ),
    (
        "O2 attach + start + dump",
        """        scheduler.start()
        write_audit_log("[SYSTEM] All EOD schedulers started)")
        lap("schedulers")""",
        """        # ── EOD_OBS_20260821 ── listener + logging bridge BEFORE start
        # (so even the very first fire is covered), jobs-table dump AFTER
        # (so the boot log proves what is actually scheduled).
        attach_scheduler_observability(scheduler)
        scheduler.start()
        log_scheduled_jobs(scheduler)
        # ── EOD_OBS_20260821 END ──
        write_audit_log("[SYSTEM] All EOD schedulers started)")
        lap("schedulers")""",
    ),
]


def main():
    if not API.exists():
        print(f"ABORT: {API} not found — run from repo root.")
        sys.exit(1)
    src = API.read_text(encoding="utf-8")
    for label, old, _ in EDITS:
        n = src.count(old)
        if n != 1:
            print(f"ABORT: anchor for {label!r} matched {n}x (need exactly 1). "
                  f"NO FILES WRITTEN.")
            sys.exit(1)
    for label, old, new in EDITS:
        src = src.replace(old, new, 1)
        print(f"  applied {label}")
    API.write_text(src, encoding="utf-8")

    checks = [
        ("attach_scheduler_observability(scheduler)", 1),
        ("log_scheduled_jobs(scheduler)", 1),
        ("from app.jobs.scheduler_observability import", 1),
    ]
    src = API.read_text(encoding="utf-8")
    ok = True
    for needle, want in checks:
        got = src.count(needle)
        print(f"  [{'OK ' if got == want else 'FAIL'}] {needle!r} x{got} (want {want})")
        ok = ok and got == want
    jobs_dir = ROOT / "backend" / "app" / "jobs"
    for fname in ("scheduler_observability.py", "eod_safety.py"):
        present = (jobs_dir / fname).exists()
        print(f"  [{'OK ' if present else 'FAIL'}] backend/app/jobs/{fname} present")
        ok = ok and present
    marker = (jobs_dir / "eod_safety.py").read_text(encoding="utf-8") \
        if (jobs_dir / "eod_safety.py").exists() else ""
    has_heal = "EOD_WATCHDOG_FORCECLOSE" in marker
    print(f"  [{'OK ' if has_heal else 'FAIL'}] eod_safety.py is the "
          f"self-healing (EOD_OBS_20260821) version")
    ok = ok and has_heal
    if not ok:
        print("POST-CHECK FAILED — inspect before building.")
        sys.exit(1)
    print("ALL EDITS APPLIED + VERIFIED.")


if __name__ == "__main__":
    main()

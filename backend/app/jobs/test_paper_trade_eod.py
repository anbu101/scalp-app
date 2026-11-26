# backend/app/jobs/test_paper_trade_eod.py — ── PAPER_EOD_TEST_20261126 ── (DEF-107) TC-JOB-0088…0091
#
# The 15:25 EOD paper job (jobs/paper_trade_eod.py) around the square-off that
# db/test_paper_trade_squareoff.py tests: the NSE-holiday gate, then square-off,
# then reconcile, and its place on the app scheduler. Hermetic: both steps and the
# calendar are stubbed, no database is opened, nothing is squared off.
# Replaces app/dev/test_paper_eod.py, a manual trigger with no checks that ran the
# REAL job against whatever ~/.scalp-app/app.db it found.
#
#   cd backend && python3 app/jobs/test_paper_trade_eod.py
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))          # .../backend

os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.jobs.paper_trade_eod as J               # noqa: E402
import app.utils.market_hours as MH                # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        FAILS.append(name)


calls, logs = [], []
J.square_off_open_paper_trades = lambda: calls.append("square_off")
J.reconcile_closed_paper_trades = lambda: calls.append("reconcile")
J.write_audit_log = lambda m: logs.append(m)
_real_is_trading_day = MH.is_trading_day

try:
    MH.is_trading_day = lambda d=None: False
    J.paper_trade_eod_job()
    check("TC-JOB-0088 NSE holiday / weekend: nothing squared off or reconciled, one no-op log line",
          calls == [] and any("non-trading day" in m for m in logs), (calls, logs))

    calls.clear()
    logs.clear()
    MH.is_trading_day = lambda d=None: True
    J.paper_trade_eod_job()
    check("TC-JOB-0089 trading day: square-off, then reconcile, each once, between 'Job started' and 'Job finished'",
          calls == ["square_off", "reconcile"]
          and [m for m in logs if "Job" in m] == ["[EOD][PAPER] Job started", "[EOD][PAPER] Job finished"], (calls, logs))

    calls.clear()
    logs.clear()

    def _boom():
        calls.append("square_off")
        raise RuntimeError("db locked")
    J.square_off_open_paper_trades = _boom
    try:
        J.paper_trade_eod_job()
        raised = False
    except RuntimeError:
        raised = True
    check("TC-JOB-0090 a failing square-off is not swallowed (the scheduler logs it) and reconcile does not run "
          "on a half-done square-off", raised and calls == ["square_off"], (raised, calls))
finally:
    MH.is_trading_day = _real_is_trading_day

src = (HERE.parents[1] / "api_server.py").read_text(encoding="utf-8")
m = re.search(r"add_job\(\s*paper_trade_eod_job,\s*trigger=\"cron\",\s*hour=(\d+),\s*minute=(\d+)", src)
check("TC-JOB-0091 the app scheduler runs it at 15:25 IST (cron; the job's own gate skips weekends and holidays)",
      m is not None and (m.group(1), m.group(2)) == ("15", "25") and 'timezone="Asia/Kolkata"' in src, m)

print(f"\n{4 - len(FAILS)} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL PAPER EOD JOB CHECKS PASSED")

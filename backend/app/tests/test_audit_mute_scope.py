# backend/app/tests/test_audit_mute_scope.py — ── AUDIT_MUTE_SCOPE_20261030 ──
#   cd backend && PYTHONPATH=$PWD python3 app/tests/test_audit_mute_scope.py
import os
import sys
import tempfile
import threading

os.environ["HOME"] = tempfile.mkdtemp(prefix="auditmute_")
import app.event_bus.audit_logger as A                      # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


def lines():
    p = A._log_file()
    return p.read_text().splitlines() if p.exists() else []


in_bt = threading.Event()
go_on = threading.Event()


def backtest():
    with A.audit_muted():
        A.write_audit_log("[BT] replay noise")
        in_bt.set()
        go_on.wait(5)
        A.write_audit_log("[BT] more noise")


t = threading.Thread(target=backtest)
t.start()
in_bt.wait(5)
A.write_audit_log("[EMC][ENTRY][LIVE] while a backtest runs")    # the live engine's thread
check("the muting thread reports muted; another thread does not",
      A.is_audit_muted() is False)
go_on.set()
t.join()
L = lines()
check("a live line written WHILE a backtest runs is kept (was dropped by the process-wide flag)",
      any("while a backtest runs" in x for x in L), L)
check("the backtest's own lines stay muted", not any("[BT]" in x for x in L), L)

with A.audit_muted():
    with A.audit_muted():
        A.write_audit_log("nested")
    A.write_audit_log("still muted after the inner block")
A.write_audit_log("unmuted after both")
L = lines()
check("nested mutes restore correctly", not any("nested" in x or "still muted" in x for x in L)
      and any("unmuted after both" in x for x in L), L)
A.set_audit_muted(True)
A.write_audit_log("set_audit_muted(True) line")
A.set_audit_muted(False)
A.write_audit_log("set_audit_muted(False) line")
L = lines()
check("set_audit_muted still works on the calling thread", not any("(True) line" in x for x in L)
      and any("(False) line" in x for x in L), L)
seen = {}
A.set_audit_muted(True)
th = threading.Thread(target=lambda: seen.setdefault("m", A.is_audit_muted()))
th.start()
th.join()
A.set_audit_muted(False)
check("a thread started by a muted thread starts un-muted (live engines are never silenced)", seen["m"] is False)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL AUDIT_MUTE_SCOPE CHECKS PASSED")

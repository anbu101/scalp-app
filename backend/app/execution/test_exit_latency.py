# test_exit_latency.py — EXIT_LATENCY_20261115
# The REAL ZerodhaOrderExecutor.cancel_gtt_verified() on a fake Kite and a fake
# clock: how long does an exit wait on its GTT before the exit order can go,
# and how many times is the broker asked?
#   PYTHONPATH=backend python3 backend/app/execution/test_exit_latency.py
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402
ZE.write_audit_log = lambda m: None
ZE._load_relay_config = lambda: None

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Clock:
    t = 0.0


ZE.time.sleep = lambda s: setattr(Clock, "t", Clock.t + s)


class Kite:
    """The GTT list shows a delete `lag` seconds after it was sent."""

    def __init__(self, status="active", lag=0.0, readable=True, sticks=False):
        self.status, self.lag, self.readable, self.sticks = status, lag, readable, sticks
        self.deleted_at = None
        self.reads = self.deletes = 0

    def delete_gtt(self, gid):
        if not self.readable:
            raise RuntimeError("Incorrect `api_key` or `access_token`.")
        self.deletes += 1
        if self.status == "active" and self.deleted_at is None and not self.sticks:
            self.deleted_at = Clock.t

    def get_gtts(self):
        self.reads += 1
        if not self.readable:
            raise RuntimeError("Incorrect `api_key` or `access_token`.")
        if self.deleted_at is not None and Clock.t - self.deleted_at >= self.lag:
            return []
        return [{"id": 9, "status": self.status}]


def run(kite, retries=4):
    Clock.t = 0.0
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: kite, get_data_kite=lambda: kite)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ok = ex.cancel_gtt_verified("9", retries=retries)
    return ok, round(Clock.t, 2), kite, ex


def t_fast_broker():
    ok, waited, k, _ = run(Kite(lag=0.1))
    check("broker shows the delete within 0.1 s → exit released after 0.25 s (was 1.5 s)",
          ok and waited == 0.25 and k.deletes == 1 and k.reads == 1, f"ok={ok} waited={waited}s deletes={k.deletes} reads={k.reads}")


def t_slow_broker():
    ok, waited, k, _ = run(Kite(lag=0.8))
    check("broker shows the delete after 0.8 s → released at the 1.0 s check, one cancel only",
          ok and waited == 1.0 and k.deletes == 1, f"ok={ok} waited={waited}s deletes={k.deletes}")
    ok, waited, k, _ = run(Kite(lag=1.4))
    check("broker shows the delete after 1.4 s → released at 1.5 s, exactly as before",
          ok and waited == 1.5 and k.deletes == 1, f"ok={ok} waited={waited}s deletes={k.deletes}")


def t_never_slower_than_before():
    ok, waited, k, _ = run(Kite(sticks=True))                 # the delete never takes
    check("GTT stays armed → NOT verified, same 5 cancels and the same 7.5 s as before",
          ok is False and waited == 7.5 and k.deletes == 5, f"ok={ok} waited={waited}s deletes={k.deletes}")
    ok, waited, k, _ = run(Kite(readable=False))
    check("list unreadable → NOT verified, the same 7.5 s and ONE read per attempt (5), as before",
          ok is False and waited == 7.5 and k.reads == 5, f"ok={ok} waited={waited}s reads={k.reads}")
    ok, waited, k, _ = run(Kite(sticks=True), retries=0)
    check("retries=0 (the kill list) → one attempt, 1.5 s", ok is False and waited == 1.5 and k.deletes == 1,
          f"waited={waited}s deletes={k.deletes}")


def t_triggered_is_remembered():
    ok, waited, k, ex = run(Kite(status="triggered"))
    check("a GTT that had already triggered → 'gone' after 0.25 s, and the executor remembers it saw 'triggered'",
          ok and waited == 0.25 and ex.gtt_last_state("9") == "triggered",
          f"ok={ok} waited={waited}s last={ex.gtt_last_state('9')}")
    from app.execution.execution_router import ExecutionRouter
    check("…readable through the ExecutionRouter with no broker call",
          ExecutionRouter(ex).gtt_last_state("9") == "triggered" and ExecutionRouter(object()).gtt_last_state("9") is None)
    ok, waited, k, ex = run(Kite(lag=0.1))
    check("a GTT that was cancelled is remembered as 'missing', not 'triggered'", ex.gtt_last_state("9") == "missing",
          str(ex.gtt_last_state("9")))


if __name__ == "__main__":
    for t in (t_fast_broker, t_slow_broker, t_never_slower_than_before, t_triggered_is_remembered):
        try:
            t()
        except Exception as e:
            import traceback
            FAILS.append(t.__name__)
            print(f"[ERROR] {t.__name__}: {e!r}\n{traceback.format_exc()[-400:]}")
    print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
    if FAILS:
        sys.exit(1)
    print("ALL EXIT-LATENCY CHECKS PASSED")

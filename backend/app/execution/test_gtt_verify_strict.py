# backend/app/execution/test_gtt_verify_strict.py
#
# ── GTT_VERIFY_STRICT_20261112 ── ZerodhaOrderExecutor.cancel_gtt_verified()
# must never call a GTT "gone" when it could not READ the GTT list.
# 12 Nov 2026: pre-market (before the day's Kite login) every cancel raised,
# get_gtts() answered [] on the failed read, and the old code reported both
# overnight GTTs CONFIRMED_GONE — they stayed armed and one later fired.
# Run:  PYTHONPATH=backend python3 backend/app/execution/test_gtt_verify_strict.py
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LOG = []
import app.event_bus.audit_logger as AL                               # noqa: E402
AL.write_audit_log = lambda m: LOG.append(m)
import app.execution.zerodha_executor as ZE                           # noqa: E402
ZE.write_audit_log = lambda m: LOG.append(m)
ZE._load_relay_config = lambda: None
ZE.time.sleep = lambda s: None
from app.execution.execution_router import ExecutionRouter            # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class TokenException(Exception):
    pass


class Kite:
    """A Kite whose session can be stale (before the day's login)."""

    def __init__(self, gtts, logged_in=True):
        self.gtts = dict(gtts)                 # id -> status
        self.logged_in = logged_in
        self.deletes = 0
        self.lag = 0                           # checks that still show "active" after a delete

    def delete_gtt(self, gid):
        if not self.logged_in:
            raise TokenException("Incorrect `api_key` or `access_token`.")
        self.deletes += 1
        if self.gtts.get(str(gid)) == "active":
            self.gtts[str(gid)] = "deleted" if not self.lag else "active"
            self._pending = str(gid)

    def get_gtts(self):
        if not self.logged_in:
            raise TokenException("Incorrect `api_key` or `access_token`.")
        if self.lag:
            self.lag -= 1
            if self.lag == 0 and getattr(self, "_pending", None):
                self.gtts[self._pending] = "deleted"
        return [{"id": int(k), "status": v} for k, v in self.gtts.items() if v != "deleted"]


def ex_for(kite, ready=True):
    bm = types.SimpleNamespace(is_trade_ready=lambda: ready, refresh=lambda: None,
                               get_trade_kite=lambda: kite, get_data_kite=lambda: kite)
    return ZE.ZerodhaOrderExecutor(bm)


def t_stale_token():
    LOG.clear()
    k = Kite({"344181347": "active"}, logged_in=False)
    ok = ex_for(k).cancel_gtt_verified("344181347")
    check("12 Nov replay: stale token (cancel raises, list unreadable) → NOT verified (was True)",
          ok is False, str(LOG[-3:]))
    check("…never logs CONFIRMED_GONE", not any("CONFIRMED_GONE" in l for l in LOG))
    check("…says why: list unreadable, treated as still armed",
          any("NOT VERIFIED" in l and "unreadable" in l for l in LOG), str(LOG[-1:]))
    check("…and the GTT really is still armed at the broker", k.gtts["344181347"] == "active")


def t_session_not_ready():
    LOG.clear()
    k = Kite({"7": "active"})
    ok = ex_for(k, ready=False).cancel_gtt_verified("7")
    check("session not ready at all → NOT verified", ok is False and k.gtts["7"] == "active")


def t_normal_cancel():
    LOG.clear()
    k = Kite({"1": "active", "2": "active"})
    ok = ex_for(k).cancel_gtt_verified("1")
    check("logged in: cancel + clean read without the id → True on the first try",
          ok and k.gtts["1"] == "deleted" and any("CONFIRMED_GONE GTT_ID=1" in l and "attempt=0" in l for l in LOG))
    check("…other GTTs untouched", k.gtts["2"] == "active")


def t_broker_lag():
    LOG.clear()
    k = Kite({"5": "active"})
    k.lag = 2
    ok = ex_for(k).cancel_gtt_verified("5")
    check("Kite list lags the delete for two reads → retried, then True",
          ok and any("STILL_ARMED GTT_ID=5" in l for l in LOG))


def t_spent_states():
    for st in ("triggered", "disabled", "cancelled", "expired"):
        k = Kite({"9": st})
        check(f"status {st} → gone/spent (True)", ex_for(k).cancel_gtt_verified("9") is True)


def t_unreadable_then_readable():
    LOG.clear()
    k = Kite({"11": "active"}, logged_in=False)
    ex = ex_for(k)
    calls = {"n": 0}
    real = k.get_gtts

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            k.logged_in = True               # the morning login lands mid-retry
        return real()
    k.get_gtts = flaky
    ok = ex.cancel_gtt_verified("11")
    check("unreadable, then login lands → cancels and verifies (True)", ok and k.gtts["11"] == "deleted")


def t_still_armed():
    LOG.clear()
    k = Kite({"21": "active"})
    k.delete_gtt = lambda gid: None          # delete "succeeds" but nothing changes (observed live)
    ok = ex_for(k).cancel_gtt_verified("21", retries=2)
    check("delete ack'd but GTT stays active → False after every retry",
          ok is False and any("STILL ARMED after 3 attempts" in l for l in LOG))


def t_router():
    k = Kite({"1": "active"}, logged_in=False)
    r = ExecutionRouter(ex_for(k))
    check("router forwards the strict read: unreadable → None (not [])", r.get_gtts_or_none() is None)
    k.logged_in = True
    check("…readable → the list", isinstance(r.get_gtts_or_none(), list))
    r2 = ExecutionRouter(types.SimpleNamespace(get_gtts=lambda: []))
    check("executor without a strict read → None (fail-closed)", r2.get_gtts_or_none() is None)


if __name__ == "__main__":
    for fn in (t_stale_token, t_session_not_ready, t_normal_cancel, t_broker_lag, t_spent_states,
               t_unreadable_then_readable, t_still_armed, t_router):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL GTT_VERIFY_STRICT CHECKS PASSED")

# test_strict_reads_all.py — STRICT_READS_ALL_20261115
# Strategies outside the core four, and the older slot reconciliation job,
# must never conclude "gone" from an answer the broker could not give.
#   PYTHONPATH=backend python3 backend/app/execution/test_strict_reads_all.py
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = os.environ.get("SCALP_BACKEND") or str(HERE.parents[2])
sys.path.insert(0, ROOT)
os.environ.setdefault("SCALP_TEST_MODE", "1")

from app.execution import broker_truth as BT           # noqa: E402

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Zerodha:
    """A broker WITH strict readers. `ready=False` = stale token / session reset:
    the old reads answer [] ("nothing there"), the strict reads answer None."""

    def __init__(self, positions=(), gtts=(), ready=True):
        self.positions, self.gtts, self.ready = list(positions), list(gtts), ready

    def get_open_positions(self):
        return list(self.positions) if self.ready else []

    def get_gtts(self):
        return list(self.gtts) if self.ready else []

    def get_open_positions_or_none(self):
        return list(self.positions) if self.ready else None

    def get_gtts_or_none(self):
        return list(self.gtts) if self.ready else None


class Legacy:
    """A broker WITHOUT strict readers (Angel, Dhan, old stubs)."""

    def __init__(self, positions=()):
        self.positions = list(positions)

    def get_open_positions(self):
        return list(self.positions)

    def get_gtts(self):
        return []


POS = [{"tradingsymbol": "NIFTY26N1725000CE", "quantity": -65}]
SESSION = [True]
BT.session_open = lambda: SESSION[0]


def raises(fn):
    try:
        fn()
        return False
    except BT.BrokerUnreadable:
        return True


def t_helpers():
    SESSION[0] = True
    check("readable broker: the same list as before",
          BT.positions_strict(Zerodha(POS)) == POS and BT.gtts_strict(Zerodha(gtts=[{"id": 1}])) == [{"id": 1}])
    check("readable and truly empty: an empty list (flat is a real answer)", BT.positions_strict(Zerodha()) == [])
    check("unreadable broker: raises instead of answering 'nothing there'",
          raises(lambda: BT.positions_strict(Zerodha(POS, ready=False)))
          and raises(lambda: BT.gtts_strict(Zerodha(ready=False))))
    check("broker without strict readers: the old call, unchanged", BT.positions_strict(Legacy(POS)) == POS)
    SESSION[0] = False
    check("outside the session a monitor read raises (nothing may be inferred)",
          raises(lambda: BT.positions_strict(Zerodha(POS), in_session_only=True))
          and raises(lambda: BT.gtts_strict(Zerodha(), in_session_only=True)))
    check("…a start-up read (no session rule) still answers", BT.positions_strict(Zerodha(POS)) == POS)
    check("…and a broker without strict readers is not gated", BT.positions_strict(Legacy(POS), in_session_only=True) == POS
          and BT.infer_allowed(Legacy()) and not BT.infer_allowed(Zerodha()))
    SESSION[0] = True
    check("through the ExecutionRouter the capability is still detected",
          raises(lambda: BT.positions_strict(_router(Zerodha(POS, ready=False))))
          and BT.positions_strict(_router(Legacy(POS))) == POS)


def _router(ex):
    from app.execution.execution_router import ExecutionRouter
    return ExecutionRouter(ex)


def t_scalp_v3_job():
    from app.jobs import scalp_v3_gtt_reconcile as J
    J.write_audit_log = lambda m: None

    class M:
        pass
    m = M()
    SESSION[0] = True
    m.executor = Zerodha(POS, ready=False)
    check("hedge-GTT reconcile job: unreadable positions → hedge treated as STILL OPEN",
          J._hedge_flat_at_broker(m, "NIFTY26N1725000CE") is False)
    m.executor = Zerodha([])
    check("…readable and flat → flat", J._hedge_flat_at_broker(m, "NIFTY26N1725000CE") is True)
    SESSION[0] = False
    check("…outside the session → still open (no inference)", J._hedge_flat_at_broker(m, "NIFTY26N1725000CE") is False)
    SESSION[0] = True


def t_tma_monitor():
    from app.engine.tma import tma_gtt_monitor as T
    mon = object.__new__(T.TMAGTTMonitor)
    sell = {"symbol": "NIFTY26N1725000CE"}
    mon.executor = Zerodha(POS, ready=False)
    check("older Timberwolf monitor: unreadable positions → position treated as OPEN",
          mon._position_open(sell) is True)
    mon.executor = Zerodha([])
    check("…readable and flat → not open", mon._position_open(sell) is False)


def t_slot_reconciliation():
    from app.engine import broker_reconciliation as B
    job = object.__new__(B.BrokerReconciliationJob)
    SESSION[0] = True
    job.executor = Zerodha(POS, ready=False)
    check("slot reconciliation job: unreadable positions → raises (its run() skips the cycle)",
          raises(job._get_broker_positions))
    job.executor = Zerodha(POS)
    check("…readable → the positions", job._get_broker_positions() == {"NIFTY26N1725000CE": {"qty": 65, "avg_price": None}})
    SESSION[0] = False
    check("…outside the session → raises (never force-closes a slot pre-market)", raises(job._get_broker_positions))
    SESSION[0] = True


def t_no_old_reads_left():
    """Source check for the two strategies without a harness here (HA, BB): the
    decision paths no longer call the old reads."""
    app = Path(ROOT) / "app"
    sites = {"engine/ha_options/ha_trade_manager.py": [r"positions = self\.executor\.get_open_positions\(\)"],
             "engine/bb_options/gtt_monitor.py": [r"positions = self\.executor\.get_open_positions\(\)"],
             "engine/bb_options/bb_trade_state_manager.py": [r"positions = self\.executor\.get_open_positions\(\)"],
             "engine/tma/tma_gtt_monitor.py": [r"self\.executor\.get_gtts\(\)", r"self\.executor\.get_open_positions\(\)"],
             "jobs/scalp_v3_gtt_reconcile.py": [r"executor\.get_open_positions\(\) or", r"executor\.get_gtts\(\) or"],
             "engine/broker_reconciliation.py": [r"positions = self\.executor\.get_open_positions\(\)"]}
    left = []
    for f, pats in sites.items():
        src = (app / f).read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        left += [f"{f}: {p}" for p in pats if re.search(p, code)]
        if "STRICT_READS_ALL_20261115" not in src:
            left.append(f"{f}: not converted")
    check("no old empty-means-gone read left on a decision path (6 files)", not left, str(left))
    src = (app / "engine/bb_options/gtt_monitor.py").read_text(encoding="utf-8")
    check("BB monitor asks whether inference is allowed before it reads the GTT list",
          "infer_allowed(self.executor)" in src and src.index("infer_allowed(self.executor)") < src.index("gtts = kite.get_gtts()"))


if __name__ == "__main__":
    for t in (t_helpers, t_scalp_v3_job, t_tma_monitor, t_slot_reconciliation, t_no_old_reads_left):
        try:
            t()
        except Exception as e:
            import traceback
            FAILS.append(t.__name__)
            print(f"[ERROR] {t.__name__}: {e!r}\n{traceback.format_exc()[-500:]}")
    print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
    if FAILS:
        sys.exit(1)
    print("ALL STRICT-READ CHECKS PASSED")

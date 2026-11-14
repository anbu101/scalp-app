# backend/app/execution/test_broker_truth.py
#
# ── BROKER_TRUTH_20261114 ── the rules every strategy now shares:
#   unreadable is never "empty" · nothing is inferred outside the session ·
#   a net position is "mine" only when no other strategy holds the contract.
# Run:  PYTHONPATH=backend python3 backend/app/execution/test_broker_truth.py
import gc
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.execution import broker_truth as BT                        # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Ex:
    def __init__(self, pos=None, gtts=None, readable=True):
        self.pos, self.gtts, self.readable = pos or [], gtts or [], readable

    def get_open_positions_or_none(self):
        return self.pos if self.readable else None

    def get_gtts_or_none(self):
        return self.gtts if self.readable else None


class Holder:
    def __init__(self, legs):
        self.legs = legs

    def view(self):
        return dict(self.legs)


def t_strict_reads():
    ex = Ex([{"tradingsymbol": "A", "quantity": -130}, {"tradingsymbol": "A", "quantity": -65},
             {"tradingsymbol": "B", "quantity": 0}], [{"id": 1}])
    check("positions: summed per contract", BT.read_positions(ex) == {"A": -195, "B": 0})
    check("GTTs: the list", BT.read_gtts(ex) == [{"id": 1}])
    ex.readable = False
    check("unreadable positions → None (never {})", BT.read_positions(ex) is None)
    check("unreadable GTTs → None (never [])", BT.read_gtts(ex) is None)
    check("an executor without strict reads is unreadable", BT.read_positions(object()) is None
          and BT.read_gtts(object()) is None)

    class Boom:
        def get_open_positions_or_none(self):
            raise RuntimeError("TokenException")
        get_gtts_or_none = get_open_positions_or_none
    check("a raising read is unreadable", BT.read_positions(Boom()) is None and BT.read_gtts(Boom()) is None)


def t_session():
    import app.utils.market_hours as MH
    real = MH.is_market_open
    MH.is_market_open = lambda: True
    check("session open follows the derivatives calendar", BT.session_open() is True)
    MH.is_market_open = lambda: (_ for _ in ()).throw(RuntimeError("calendar"))
    check("calendar unreadable → CLOSED (fail closed)", BT.session_open() is False)
    MH.is_market_open = real


def t_ledger():
    L = BT.LiveLedger()
    vet, tma = Holder({"S": -130, "W": 130}), Holder({"S": -130, "H": 130})
    L.register("VET_V1", vet.view)
    L.register("TMA_V2", tma.view)
    check("others(): what the OTHER strategies hold", L.others("S", "VET_V1") == -130
          and L.others("W", "TMA_V2") == 130 and L.others("H", "VET_V1") == 130)
    check("shared(): true only for a contract someone else holds",
          L.shared("S", "VET_V1") and not L.shared("W", "VET_V1") and not L.shared("X", "VET_V1"))
    tma.legs = {}
    check("a closed position stops being shared at once", not L.shared("S", "VET_V1"))
    L.reserve("TSG_V1", {"S": -65})
    check("a reservation (entry in flight) makes the contract shared", L.shared("S", "VET_V1"))
    L.release("TSG_V1")
    check("…until it is released", not L.shared("S", "VET_V1"))

    def boom():
        raise RuntimeError("x")
    L.register("IC_V2", boom)
    check("a provider that fails → unknown → treated as shared (cautious)",
          L.others("S", "VET_V1") is None and L.shared("S", "VET_V1"))

    L2 = BT.LiveLedger()
    live, paper = Holder({"S": -130}), Holder({})
    L2.register("VET_V1", live.view)
    L2.register("VET_V1", paper.view)              # a second (paper) object for the same strategy
    check("a paper instance cannot hide the live one's holdings", L2.others("S", "TMA_V2") == -130)
    dup = Holder({"S": -130})
    L2.register("VET_V1", dup.view)                # e.g. a restore building a new manager
    check("two objects describing ONE position do not double it", L2.others("S", "TMA_V2") == -130)
    del live, dup
    gc.collect()
    check("a manager object that is gone drops out by itself", L2.others("S", "TMA_V2") == 0)


def t_own_net_qty():
    BT.LEDGER.clear()
    BT._SHARED_LOGGED.clear()
    ex = Ex([{"tradingsymbol": "S", "quantity": -260}, {"tradingsymbol": "W", "quantity": 130}])
    vet, tma = Holder({"S": -130, "W": 130}), Holder({"S": -130})
    BT.LEDGER.register("VET_V1", vet.view)
    BT.LEDGER.register("TMA_V2", tma.view)
    logs = []
    check("shared contract → None (act on own orders only)",
          BT.own_net_qty(ex, "S", "VET_V1", log=logs.append) is None)
    BT.own_net_qty(ex, "S", "VET_V1", log=logs.append)
    check("…explained once, not on every call", len(logs) == 1 and "TMA_V2" in logs[0], str(logs))
    check("sole holder → the broker's number", BT.own_net_qty(ex, "W", "VET_V1") == 130)
    check("absent contract → 0", BT.own_net_qty(ex, "Z", "VET_V1") == 0)
    ex.readable = False
    check("unreadable → None", BT.own_net_qty(ex, "W", "VET_V1") is None)
    check("expected_net(): own + others", BT.expected_net("S", "TMA_V2", -130) == -260
          and BT.expected_net("W", "TMA_V2", 0) == 130)
    BT.LEDGER.clear()


def t_unregistered_strategy_from_db_rows():
    """A live strategy outside the core four has no view; its OPEN LIVE rows
    still make a contract shared."""
    BT.LEDGER.clear()
    vet = Holder({"S": -130})
    BT.LEDGER.register("VET_V1", vet.view)
    BT._DB_BOOK.update(ts=1e18, who={"S": {"EMC_V1": 65, "VET_V1": -130}, "Q": {"EMC_V1": 65}})
    check("an unregistered strategy's live row makes the contract shared",
          BT.LEDGER.shared("S", "VET_V1") and BT.LEDGER.others("S", "VET_V1") == 65)
    check("…a registered strategy is never double-counted from its rows",
          BT.LEDGER.others("S", "EMC_V1") == -130)
    BT._DB_BOOK.update(ts=0.0, who={})
    real = BT._db_holders
    BT.DB_FALLBACK = False
    check("the DB fallback can be switched off", real() == {})
    BT.DB_FALLBACK = True
    BT.LEDGER.clear()


def t_threads():
    L = BT.LiveLedger()
    hs = [Holder({f"S{i % 5}": -65}) for i in range(20)]
    errs = []

    def work(i):
        try:
            for _ in range(300):
                L.register(f"ST{i}", hs[i].view)
                L.reserve(f"ST{i}", {"S0": -65})
                L.others("S0", f"ST{i}")
                L.shared("S1", f"ST{i}")
                L.release(f"ST{i}")
        except Exception as e:
            errs.append(repr(e))
    th = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    [t.start() for t in th]
    [t.join() for t in th]
    check("ledger is safe under 20 threads", not errs, str(errs[:2]))


if __name__ == "__main__":
    for fn in (t_strict_reads, t_session, t_ledger, t_own_net_qty, t_unregistered_strategy_from_db_rows, t_threads):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL BROKER_TRUTH CHECKS PASSED")

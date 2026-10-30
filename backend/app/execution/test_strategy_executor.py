# backend/app/execution/test_strategy_executor.py
#
# ── ACCT_SWITCH_20261030 ── behavioural suite: the execution account follows
# Settings without a restart; an open position or an in-flight order
# sequence never changes account. Hermetic: fake routers, temp DB, temp pin.
#
#   cd backend && PYTHONPATH=$PWD python3 app/execution/test_strategy_executor.py

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


import app.execution.strategy_executor as SE

ALERTS = []
SE._alert = lambda kind, msg, sid, severity="info": ALERTS.append((kind, msg))
TMP = Path(tempfile.mkdtemp(prefix="acct_switch_"))


class FakeRouter:
    def __init__(self, broker):
        self.broker_name = broker
        self.calls = []
        self.broker_manager = f"mgr-{broker}"

    def place_buy(self, symbol, token, qty):
        self.calls.append(("place_buy", symbol))
        return f"{self.broker_name}-oid", 0.0, qty

    def get_order_fill(self, oid):
        self.calls.append(("get_order_fill", oid))
        return {"status": "COMPLETE"}

    def cancel_gtt(self, gid):
        self.calls.append(("cancel_gtt", gid))
        return True


class Rig:
    """One strategy with a mutable binding, open-row count and clock."""
    T0 = 1_761_800_000.0           # real epoch scale (TSG_BANK_FIX1 real-clock rule)

    def __init__(self, binding="ANGELONE", open_rows=0, pin=None, fail_build=()):
        self.binding = binding
        self.open = open_rows
        self.now = self.T0
        self.routers = {}
        self.fail_build = set(fail_build)
        self.pin_path = TMP / f"pin_{id(self)}.json"
        if pin:
            self.pin_path.write_text(json.dumps({"IC_V2": pin}))
        self.ex = SE.StrategyExecutor(
            "IC_V2", build=self.build, resolve=lambda: self.binding,
            is_open=lambda: self.open, clock=lambda: self.now, pin_path=self.pin_path)

    def build(self, broker):
        if broker in self.fail_build:
            raise RuntimeError(f"{broker} not configured")
        return self.routers.setdefault(broker, FakeRouter(broker))

    def tick(self, s):
        self.now += s

    def pin(self):
        try:
            return json.loads(self.pin_path.read_text()).get("IC_V2")
        except Exception:
            return None


print("── 1. the 2026-10-30 bug: switched in Settings while flat → next entry follows")
r = Rig(binding="ANGELONE")
check("boot follows binding", r.ex.broker == "ANGELONE")
r.binding = "ZERODHA"
oid = r.ex.place_buy("NIFTY25N0424000CE", 1, 65)[0]
check("first entry order goes to Zerodha, no restart", oid == "ZERODHA-oid", oid)
check("Angel saw no order", "ANGELONE" not in r.routers or not any(c[0] == "place_buy" for c in r.routers["ANGELONE"].calls))
check("switch announced in the bell", any(k == "ACCOUNT_SWITCHED" for k, _ in ALERTS))
check("pin follows the order", r.pin() == "ZERODHA")

print("── 2. open position never changes account")
ALERTS.clear()
r = Rig(binding="ANGELONE")
r.ex.place_buy("A", 1, 65)
r.open = 4                               # condor booked
r.tick(SE.STICKY_S + 60)
r.binding = "ZERODHA"
r.ex.cancel_gtt("g1")
check("exit-side call stays on Angel", r.routers["ANGELONE"].calls[-1] == ("cancel_gtt", "g1"))
check("pending alert raised once", sum(1 for k, _ in ALERTS if k == "ACCOUNT_SWITCH_PENDING") == 1)
r.tick(10); r.ex.get_order_fill("x"); r.tick(10); r.ex.get_order_fill("y")
check("no alert spam on repeated calls", sum(1 for k, _ in ALERTS if k == "ACCOUNT_SWITCH_PENDING") == 1)
r.open = 0
r.tick(SE.STICKY_S + 10)
r.ex.place_buy("B", 1, 65)
check("after the position exits, next entry goes to Zerodha", r.routers["ZERODHA"].calls[-1] == ("place_buy", "B"))

print("── 3. a multi-leg entry never straddles accounts (rows booked after all legs)")
r = Rig(binding="ANGELONE")
r.ex.place_buy("L3", 1, 65)              # wing filled; no DB rows yet (open=0)
r.tick(2)
r.binding = "ZERODHA"                    # user flips mid-entry
r.ex.place_buy("L4", 1, 65)
check("second leg stays on Angel inside the sticky window", r.routers["ANGELONE"].calls[-1] == ("place_buy", "L4"))
check("Zerodha untouched", "ZERODHA" not in r.routers or not r.routers["ZERODHA"].calls)
r.tick(SE.STICKY_S - 5)
r.ex.get_order_fill("z")
check("still Angel just before the window ends", r.ex.broker == "ANGELONE")
r.tick(10)
r.ex.get_order_fill("z2")
check("switches once quiet and flat", r.ex.broker == "ZERODHA")

print("── 4. uncertain DB → stay put")
r = Rig(binding="ANGELONE")
r.open = None
r.binding = "ZERODHA"
r.tick(SE.STICKY_S + 1)
r.ex.place_buy("Q", 1, 65)
check("DB unknown never switches", r.ex.broker == "ANGELONE")

print("── 5. restart with an overnight carry")
r = Rig(binding="ZERODHA", open_rows=2, pin="ANGELONE")
check("boot keeps the pinned account while open", r.ex.broker == "ANGELONE")
r.open = 0
r.tick(SE.FLAT_CACHE_S + 1)
r.ex.get_order_fill("p")
check("moves to the binding once flat", r.ex.broker == "ZERODHA")
r = Rig(binding="ZERODHA", open_rows=0, pin="ANGELONE")
check("flat at boot → binding wins over a stale pin", r.ex.broker == "ZERODHA")
r = Rig(binding="ZERODHA", open_rows=None, pin="ANGELONE")
check("unknown at boot → pinned account (fail safe)", r.ex.broker == "ANGELONE")

print("── 6. build failure on the new account → stay on the old one")
r = Rig(binding="ZERODHA", fail_build={"ANGELONE"})
r.binding = "ANGELONE"
r.tick(SE.STICKY_S + 1)
r.ex.place_buy("F", 1, 65)
check("order still routed to Zerodha", r.routers["ZERODHA"].calls[-1] == ("place_buy", "F"))

print("── 7. forwarding semantics")
r = Rig(binding="ZERODHA")
check("plain attribute passes through", r.ex.broker_manager == "mgr-ZERODHA")
try:
    r.ex._nope
    check("private attr raises", False)
except AttributeError:
    check("private attr raises", True)
check("bound method name kept", r.ex.place_buy.__name__ == "place_buy")
check("reads do not stamp the sticky window",
      (r.ex.get_order_fill("x") is not None) and r.ex._last_order == 0.0)

print("── 8. default wall clock")
pp = TMP / "pin_wall.json"
state = {"b": "ANGELONE"}
ex = SE.StrategyExecutor("IC_V2", build=lambda b: FakeRouter(b), resolve=lambda: state["b"],
                         is_open=lambda: 0, pin_path=pp)
ex.place_buy("W", 1, 65)
state["b"] = "ZERODHA"
ex.place_buy("W2", 1, 65)
check("real time.time(): sticky window holds right after an order", ex.broker == "ANGELONE")
check("sticky stamp is epoch-scale", ex._last_order > 1_600_000_000)

print("── 9. open_live_rows across every live store")
db = TMP / "app.db"
with sqlite3.connect(db) as c:
    c.executescript("""
    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, exit_time INTEGER);
    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, trade_mode TEXT, exit_price REAL);
    CREATE TABLE scalp_v3_trades (v3_trade_id TEXT, strategy_name TEXT, paper INTEGER, exit_time INTEGER);
    CREATE TABLE tma2_trades (id INTEGER, mode TEXT, status TEXT);
    INSERT INTO trades VALUES ('a','IC_V2',NULL), ('b','IC_V2',123), ('c','TSG_V1',NULL);
    INSERT INTO paper_trades VALUES ('SHD:x','IC_V2','PAPER',NULL), ('p','ORB_V1','LIVE',NULL),
                                    ('q','ORB_V1','PAPER',NULL), ('r','ORB_V1','LIVE',101.0);
    INSERT INTO scalp_v3_trades VALUES ('v','SCALP_V3',0,NULL), ('w','SCALP_V3',1,NULL);
    INSERT INTO tma2_trades VALUES (1,'LIVE','OPEN'), (2,'PAPER','OPEN'), (3,'LIVE','CLOSED'), (4,'LIVE','STALE');
    """)
check("IC_V2: open trades row only (twin + closed ignored)", SE.open_live_rows("IC_V2", str(db)) == 1)
check("ORB_V1: LIVE paper_trades row only", SE.open_live_rows("ORB_V1", str(db)) == 1)
check("SCALP_V3: paper=0 rows only", SE.open_live_rows("SCALP_V3", str(db)) == 1)
check("TMA_V2: LIVE + OPEN only", SE.open_live_rows("TMA_V2", str(db)) == 1)
check("VET_V1: table absent → 0", SE.open_live_rows("VET_V1", str(db)) == 0)
check("BRK_V1: nothing open → 0", SE.open_live_rows("BRK_V1", str(db)) == 0)
check("unreadable DB → None", SE.open_live_rows("IC_V2", str(TMP / "missing" / "x.db")) is None)
bad = TMP / "bad.db"
bad.write_bytes(b"not a database at all" * 100)
check("corrupt DB → None", SE.open_live_rows("IC_V2", str(bad)) is None)

print("── 10. factory: one facade per bindable strategy")
try:
    import app.execution.executor_factory as EF
    EF.get_executor_for_broker = lambda b: FakeRouter(b)
    EF._strategy_executors.clear()
    a1 = EF.get_executor_for_strategy("IC_V2")
    a2 = EF.get_executor_for_strategy("IC_V2")
    check("bindable → StrategyExecutor", isinstance(a1, SE.StrategyExecutor))
    check("same instance for every caller", a1 is a2)
    g = EF.get_executor_for_strategy("GC_V1")
    check("non-bindable → plain router (unchanged)", isinstance(g, FakeRouter))
except ModuleNotFoundError as e:
    print(f"  SKIP  executor_factory import ({e}) — run on the build Mac")

print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")

# backend/app/slippage/test_slip.py — ── SLIP_CAPTURE_20261029 ── behavioural suite.
#   cd backend && PYTHONPATH=$PWD python3 app/slippage/test_slip.py
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


TMP = tempfile.mkdtemp(prefix="slip_")
os.environ["HOME"] = TMP
IST = timezone(timedelta(hours=5, minutes=30))

from app.slippage import slip_store as SS                  # noqa: E402
from app.execution.zerodha_executor import ZerodhaOrderExecutor as ZerodhaExecutor  # noqa: E402
from app.marketdata.ltp_store import LTPStore              # noqa: E402

check("throw-away HOME", str(SS.db_path()).startswith(TMP))
ex = object.__new__(ZerodhaExecutor)
calls = []


def inner(relay_fn, direct_fn, op, sym):
    calls.append(op)
    return "OID-1" if len(calls) == 1 else f"OID-{len(calls)}"


ex._relay_call_inner = inner
LTPStore.update("NIFTY26N0324000CE", 101.5)


def rows():
    c = SS.connect()
    try:
        return c.execute("SELECT order_id, op, side, symbol, ref_ltp, ref_age_s, error, status, fill_price, strategy_id "
                         "FROM order_stamps ORDER BY id").fetchall()
    finally:
        c.close()


print("── 1. stamping at the executor chokepoint")
r = ex._relay_call(None, None, "SELL_ENTRY", "NIFTY26N0324000CE")
SS.wait()
check("the order call returns the broker's order id unchanged", r == "OID-1" and calls == ["SELL_ENTRY"])
st = rows()
check("one stamp: SELL, ref = the LTP the app held, age measured", len(st) == 1 and st[0][:5] ==
      ("OID-1", "SELL_ENTRY", "SELL", "NIFTY26N0324000CE", 101.5) and st[0][5] is not None and 0 <= st[0][5] < 5, st)
ex._relay_call(None, None, "GTT", "NIFTY26N0324000CE")
ex._relay_call(None, None, "CANCEL_ORDER", "")
SS.wait()
check("GTT / cancel / modify are not orders sent at a price → not stamped", len(rows()) == 1 and calls[-2:] == ["GTT", "CANCEL_ORDER"])
ex._relay_call(None, None, "BUY_EXIT", "NIFTY26N0399999PE")
SS.wait()
check("no LTP for the symbol → stamped with ref None (never invented)", rows()[-1][4] is None and rows()[-1][2] == "BUY", rows())


def boom(*a):
    raise RuntimeError("broker rejected")


ex._relay_call_inner = boom
try:
    ex._relay_call(None, None, "BUY", "NIFTY26N0324000CE")
    raised = False
except RuntimeError:
    raised = True
SS.wait()
check("a failing order still raises to the engine, and the attempt is stamped with the error",
      raised and rows()[-1][0] is None and "broker rejected" in (rows()[-1][6] or ""))
ex._relay_call_inner = inner
_w = SS._write
SS._write = lambda row: time.sleep(2.0)
t0 = time.monotonic()
ex._relay_call(None, None, "SELL", "NIFTY26N0324000CE")
dt = time.monotonic() - t0
check("the order path never waits on the stamp (written on its own thread)", dt < 0.5, dt)
SS._write = _w
_rs = SS.record_send
SS.record_send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
check("a stamping failure never breaks the order", str(ex._relay_call(None, None, "BUY", "NIFTY26N0324000CE")).startswith("OID-"))
SS.record_send = _rs
SS.wait(5)

print("── 2. fills from kite.orders() (real clock, IST wall-clock timestamps)")
now = int(time.time())
nowi = datetime.fromtimestamp(now, IST).replace(tzinfo=None)


class FakeKite:
    def __init__(self, orders):
        self._o = orders

    def orders(self):
        return list(self._o)


c = SS.connect()
c.execute("INSERT INTO order_stamps (order_id, op, side, symbol, ref_ltp, sent_at) VALUES ('OID-OLD','BUY','BUY','X',1,?)",
          (now - 2 * 86400,))
c.commit()
c.close()
SS._strategy = lambda oid: {"OID-1": "TSG_V1"}.get(oid)
k = FakeKite([{"order_id": "OID-1", "status": "COMPLETE", "quantity": 650, "filled_quantity": 650, "average_price": 100.9,
               "order_timestamp": nowi - timedelta(seconds=3), "exchange_update_timestamp": (nowi - timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S")},
              {"order_id": "OID-OLD", "status": "COMPLETE", "average_price": 5}])
res = SS.fetch_fills(k, now)
c = SS.connect()
f = c.execute("SELECT status, qty, filled_qty, fill_price, order_ts, fill_ts, strategy_id, ref_ltp FROM order_stamps "
              "WHERE order_id='OID-1' ORDER BY id LIMIT 1").fetchone()
old = c.execute("SELECT status FROM order_stamps WHERE order_id='OID-OLD'").fetchone()
c.close()
check("filled: status, qty, average fill, strategy resolved from the order id",
      f[:4] == ("COMPLETE", 650, 650, 100.9) and f[6] == "TSG_V1", f)
check("exchange timestamps read as IST (fill 2 s before now)", f[5] is not None and abs((now - 2) - f[5]) <= 1, (f[5], now))
check("slippage of that SELL = ref − fill = 101.5 − 100.9 = 0.6 per unit paid away",
      abs((f[7] - f[3]) - 0.6) < 1e-9)
check("yesterday's stamp is not touched (Kite only returns today's book)", old == (None,))
k2 = FakeKite([{"order_id": "OID-1", "status": "OPEN", "quantity": 650, "filled_quantity": 0, "average_price": 0}])
check("an order already final is not re-read", SS.fetch_fills(k2, now)["updated"] == 0)


class BadKite:
    def orders(self):
        raise RuntimeError("token expired")


check("kite failure → reported, never raised", "token expired" in SS.fetch_fills(BadKite(), now).get("error", ""))

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL SLIP_CAPTURE CHECKS PASSED")

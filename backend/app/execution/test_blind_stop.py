# test_blind_stop.py — BLIND_STOP_20261115 (O1)
# broker_truth.blind_stop_orders: what a stop that fired unseen placed.
#   PYTHONPATH=backend python3 backend/app/execution/test_blind_stop.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SCALP_TEST_MODE", "1")

from app.execution import broker_truth as BT            # noqa: E402

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Ex:
    def __init__(self, book=None, boom=False):
        self.book, self.boom, self.calls = book or [], boom, 0

    def get_orders(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("orders: timeout")
        return self.book


def o(oid, qty, st="COMPLETE", px=141.0, side="BUY", sym="X", ts="2026-11-16 10:00:00"):
    return {"order_id": oid, "tradingsymbol": sym, "transaction_type": side, "quantity": qty,
            "filled_quantity": qty if st == "COMPLETE" else 0, "status": st,
            "average_price": px if st == "COMPLETE" else 0, "price": px,
            "order_timestamp": ts}


def run(book, **kw):
    BT.reset_app_orders()
    for a in kw.pop("app", []):
        BT.note_app_order(a)
    args = dict(qtys=[195], want_qty=195, stop_px=140.0)
    args.update(kw)
    ex = Ex(book)
    return ex, BT.blind_stop_orders(ex, "X", True, **args)


ex, r = run([o("G1", 195)])
check("a filled non-app buy of the leg's quantity at the stop's price is counted", r == ([], 195, 141.0), r)
check("…with ONE order-book read", ex.calls == 1, ex.calls)
_, r = run([o("G1", 195, st="OPEN", px=147.0)])
check("a working one is returned for adoption", r == (["G1"], 0, None), r)
_, r = run([o("A1", 195)], app=["A1"])
check("an order this app placed (any strategy) is never taken", r == ([], 0, None), r)
_, r = run([o("M1", 65)])
check("another quantity (a manual 65) is not taken", r == ([], 0, None), r)
_, r = run([o("G1", 195, px=97.0)])
check("a fill below the stop IS taken (a resting stop limit fills at any price up to its limit)",
      r == ([], 195, 97.0), r)
_, r = run([o("M1", 195, st="OPEN", px=90.0)])
check("a working buy far below the stop's limit is not taken", r == ([], 0, None), r)
_, r = run([o("M1", 195, px=190.0)])
check("a fill far above the stop's limit is not taken", r == ([], 0, None), r)
_, r = run([o("S1", 195, side="SELL")])
check("the entry side is not taken", r == ([], 0, None), r)
_, r = run([o("Y1", 195, sym="Y")])
check("another contract is not taken", r == ([], 0, None), r)
_, r = run([o("R1", 195, st="REJECTED"), o("C1", 195, st="CANCELLED")])
check("rejected / cancelled orders are not taken", r == ([], 0, None), r)
_, r = run([o("G1", 195), o("G2", 195)])
check("never more than the leg's quantity", r[1] == 195, r)
_, r = run([o("G1", 195, ts="2026-11-13 10:00:00")],
           not_before_ts=int(__import__("datetime").datetime(2026, 11, 16, 9, 30).timestamp()))
check("an order from before the leg opened is not taken", r == ([], 0, None), r)
BT.reset_app_orders()
ex = Ex([o("G1", 195)], boom=True)
r = BT.blind_stop_orders(ex, "X", True, qtys=[195], want_qty=195, stop_px=140.0)
check("book unreadable: nothing taken (the exit goes as before)", r == ([], 0, None), r)
BT.reset_app_orders()
ex = Ex([o("G1", 195)])
r1 = BT.blind_stop_orders(ex, "X", True, qtys=[195], want_qty=195, stop_px=140.0)
r2 = BT.blind_stop_orders(ex, "X", True, qtys=[195], want_qty=195, stop_px=140.0)
check("a taken order is claimed: a second strategy cannot count it too", r1[1] == 195 and r2 == ([], 0, None), (r1, r2))
BT.reset_app_orders()
r = BT.blind_stop_orders(Ex([o("G1", 130, side="SELL", px=19.0)]), "X", False,
                         qtys=[130], want_qty=130, stop_px=20.0)
check("a long leg's sell-stop is matched the same way", r == ([], 130, 19.0), r)

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL BLIND-STOP CHECKS PASSED")

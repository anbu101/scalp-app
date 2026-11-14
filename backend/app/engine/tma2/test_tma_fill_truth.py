# backend/app/engine/tma2/test_tma_fill_truth.py
#
# ── FILL_TRUTH_TMA_20261102 ── TMA_V1 / TMA_V2: a GTT exit is booked at the
# buy-back of THIS trade, never an earlier trade's buy-back on the same strike.
# Run standalone:  python3 test_tma_fill_truth.py
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tma2_e2e as E                                           # noqa: E402  (harness)
from app.engine.tma import tma_gtt_monitor as M1                    # noqa: E402
from app.engine.tma import tma_trade_manager as TM1                 # noqa: E402
from app.engine.tma2 import tma2_gtt_monitor as M2                  # noqa: E402
M2.TMA2GTTMonitor._session_open = lambda self: True         # ── BROKER_TRUTH_20261114 ── these tests simulate a live session
from app.engine.tma2 import tma2_trade_manager as TM2               # noqa: E402

FAILS = []
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def kts(hh, mm, ss=0):
    return f"2026-11-02 {hh:02d}:{mm:02d}:{ss:02d}"


def epoch(hh, mm, ss=0):
    return int(datetime(2026, 11, 2, hh, mm, ss, tzinfo=IST).timestamp())


S, H = "NIFTY26N0323300CE", "NIFTY26N0324000CE"


def o(oid, t, side, sym, px, q=65, status="COMPLETE"):
    return {"order_id": oid, "order_timestamp": t, "transaction_type": side, "tradingsymbol": sym,
            "status": status, "average_price": px, "filled_quantity": q, "quantity": q}


# trade A on strike S (10:00 → TP buy-back 10:40 @ 9.50), trade B on the SAME strike from 11:00
BOOK_A = [o("hA", kts(10, 0), "BUY", H, 4.0), o("sA", kts(10, 0, 2), "SELL", S, 160.0),
          o("xA", kts(10, 40), "BUY", S, 9.50), o("xhA", kts(10, 40, 2), "SELL", H, 1.0),
          o("hB", kts(11, 0), "BUY", H, 3.5), o("sB", kts(11, 0, 2), "SELL", S, 150.0)]
GTT_BUY = o("gB", kts(13, 0), "BUY", S, 185.0)


class Ex:
    def __init__(self, book, fills=None):
        self.book, self.fills = list(book), dict(fills or {})

    def get_orders(self):
        return list(self.book)

    def get_order_fill(self, oid):
        return self.fills.get(oid, {"status": None, "avg_price": 0.0, "found": False})

    def get_open_positions(self):
        return []


def group_b():
    return {"sig_ts": epoch(10, 59), "sell": {"symbol": S, "qty": 65, "order_id": "sB", "sl": 168.0},
            "hedge": {"symbol": H, "qty": 65, "order_id": "hB"}}


class Mgr:
    def __init__(self, g):
        self.group, self._sid = g, "TMA_TEST"


def old_rule(book, sym):                    # the pre-fix fallback, for the record
    for x in reversed(book):
        if x["tradingsymbol"] == sym and x["transaction_type"] == "BUY" and x["status"] == "COMPLETE":
            return x["average_price"]


# ── 1. monitors (both strategies) ────────────────────────────────────
for tag, M, cls in (("TMA_V1", M1, "TMAGTTMonitor"), ("TMA_V2", M2, "TMA2GTTMonitor")):
    M.write_audit_log = lambda m: None
    race = {"id": "G", "status": "triggered", "orders": [{"result": None}]}
    mon = getattr(M, cls)(Ex(BOOK_A), Mgr(group_b()))
    check(f"{tag}: old rule took trade A's buy-back (9.50) for trade B", old_rule(BOOK_A, S) == 9.50)
    check(f"{tag}: GTT race, B's buy-back not in the book yet → retry, not 9.50",
          mon._resolve_fill(race, group_b()["sell"]) == (None, None))
    mon = getattr(M, cls)(Ex(BOOK_A + [GTT_BUY]), Mgr(group_b()))
    check(f"{tag}: once it lands → B's real buy-back 185.0",
          mon._resolve_fill(race, group_b()["sell"]) == (185.0, "gB"))
    named = {"id": "G", "status": "triggered", "orders": [{"result": {"order_result": {"order_id": "gB"}}}]}
    working = dict(GTT_BUY, status="OPEN", average_price=0.0, filled_quantity=0)
    stray = o("zz", kts(13, 0, 5), "BUY", S, 190.0)          # someone else's buy on the strike
    mon = getattr(M, cls)(Ex(BOOK_A + [working, stray], {"gB": {"status": "OPEN", "avg_price": 0.0}}),
                          Mgr(group_b()))
    check(f"{tag}: GTT names its order and it is still working → no guess",
          mon._resolve_fill(named, group_b()["sell"]) == (None, None))
    mon = getattr(M, cls)(Ex(BOOK_A + [GTT_BUY], {"gB": {"status": "COMPLETE", "avg_price": 185.0}}), Mgr(group_b()))
    check(f"{tag}: GTT names its order and it completed → that fill",
          mon._resolve_fill(named, group_b()["sell"]) == (185.0, "gB"))
    carried = {"sig_ts": epoch(10, 0) - 86400 * 3, "sell": {"symbol": S, "qty": 65, "order_id": "sOLD"},
               "hedge": {"symbol": H, "qty": 65, "order_id": "hOLD"}}
    book_c = [o("x1", kts(9, 20), "BUY", S, 140.0)]
    mon = getattr(M, cls)(Ex(book_c), Mgr(carried))
    check(f"{tag}: carried position (entry not in today's book) → today's buy-back",
          mon._fill_from_orders(carried["sell"]) == (140.0, "x1"))

# ── 2. managers' flat-at-broker lookup (both strategies) ─────────────
for tag, TM, cls in (("TMA_V1", TM1, "TMATradeManager"), ("TMA_V2", TM2, "TMA2TradeManager")):
    TM.write_audit_log = lambda m: None
    stub = Mgr(group_b())
    stub.executor = Ex(BOOK_A + [GTT_BUY, o("xhB", kts(13, 0, 3), "SELL", H, 0.5)])
    lf = getattr(TM, cls)._last_fill_px
    check(f"{tag}: sell leg flat → B's buy-back, not A's", lf(stub, S, "BUY") == 185.0)
    check(f"{tag}: hedge flat → B's hedge sale, not A's", lf(stub, H, "SELL") == 0.5)
    stub.executor = Ex(BOOK_A)
    check(f"{tag}: nothing for B yet → None (model price, audit flag)", lf(stub, S, "BUY") is None)
    stub = Mgr(None)
    stub.executor = Ex(BOOK_A + [o("u1", kts(11, 0, 9), "SELL", H, 3.4)])
    check(f"{tag}: hedge unwind lookup uses its own floor (not A's 10:40 sale)",
          lf(stub, H, "SELL", not_before=epoch(11, 0, 5)) == 3.4)

# ── 3. TMA_V2 end to end: same strike twice in a day, GTT race on trade B ──
m, kite, ch, db = E.setup()
_orig_new = kite._new_order


def _stamped(*a, **k):                       # Kite stamps every order; the fake does not
    oid = _orig_new(*a, **k)
    for x in kite.orders:
        x.setdefault("order_timestamp", datetime.fromtimestamp(int(kite.clock.t), IST)
                     .strftime("%Y-%m-%d %H:%M:%S"))
    return oid


kite._new_order = _stamped
ts = E.enter(m, ch)                          # trade A
kite.clock.t += 600
kite.set_px(E.SELL, 9.5)
m.on_minute(ts + 120, None, ch)              # TP: app buy-back @ 9.5
check("e2e: trade A closed by TP at 9.5", m.group is None and E.rows(db)[0][3] == 9.5, str(E.rows(db)))
kite.clock.t += 3600
kite.set_px(E.SELL, 160.0)
kite.set_px(E.HEDGE, 4.5)
ts_b = E.enter(m, ch, at=(11, 30))           # trade B — same strike
check("e2e: trade B entered on the same strike", m.group and m.group["sell"]["symbol"] == E.SELL)
kite.clock.t += 3600
kite.set_px(E.SELL, 185.0)                   # B's SL GTT fills at the broker
gtt_oid = [x for x in kite.orders if x.get("src") == "GTT"][-1]["order_id"]
for g in kite.gtts.values():
    if g["status"] == "triggered":
        g["orders"][0]["result"] = None      # Zerodha race: no order id yet
_real_get = kite.get_orders
kite.get_orders = lambda: [x for x in _real_get() if x["order_id"] != gtt_oid]   # nor in the book yet
mon2 = E.mon(m, kite)
mon2._sweep()
check("e2e: race sweep did NOT book trade A's 9.5 as B's exit", m.group is not None)
kite.get_orders = _real_get                  # the order lands
for _ in range(2):
    if m.group is not None:
        mon2._sweep()
r = E.rows(db)
b_sell = [x for x in r if x[0] == "SELL"][-1]
check("e2e: trade B booked at its real GTT fill 185.0", m.group is None and b_sell[3] == 185.0, str(r))
check("e2e: broker flat afterwards", kite.flat() and not kite.open_orders())

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL TMA FILL-TRUTH CHECKS PASSED")

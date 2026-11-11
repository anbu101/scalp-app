# backend/app/engine/ic/test_ic_wing_fast_fill.py
#
# ── WING_FAST_FILL_20261111 ── IC condor wings on a book with a SPREAD: a
# buy fills only at the ask. Before: wings bid LTP + 1% (one tick on a ₹4
# wing) with no re-price for up to 45 s while the shorts waited. Now: ask +
# max(₹1, 20%) and a re-price off a fresh ask every 2 s.
# Run:  cd backend/app/engine/ic && python3 test_ic_wing_fast_fill.py
import sys

import test_ic_group_manager as H
from test_ic_group_manager import GM, make_selection, clean   # noqa: F401  (clean = autouse fixture)
from ic_live_core import G_OPEN

FK = H._FK


def _wing_lim(ask):
    buf = min(max(1.0, ask * 0.20), 3.0)
    return round((-(-round((ask + buf) * 100) // 5)) * 0.05, 2)


class SpreadKite(FK.FakeKite):
    def __init__(self, prices, spread=0.10, **k):
        self.sp = spread
        super().__init__(prices, **k)

    def ask(self, s):
        return FK.r05(self.px[s] + self.sp)

    def tick(self):
        for o in self.orders:
            if o["status"] != FK.WORKING or o["tradingsymbol"] not in self.px:
                continue
            s, buy = o["tradingsymbol"], o["transaction_type"] == "BUY"
            fp = self.ask(s) if buy else self.px[s]
            if (buy and o["price"] >= fp) or (not buy and o["price"] <= fp):
                o.update(status="COMPLETE", filled_quantity=o["quantity"], pending_quantity=0,
                         average_price=fp)
                self.net[s] = self.net.get(s, 0) + (o["quantity"] if buy else -o["quantity"])

    def place_wing_buy(self, symbol, token, qty):
        lim = _wing_lim(self.ask(symbol))
        return (self._new_order(symbol, "BUY", qty, lim), lim, qty)

    def fresh_wing_buy_limit(self, symbol):
        return (_wing_lim(self.ask(symbol)), self.ask(symbol), "ask")


def _spread_e2e(monkeypatch):
    monkeypatch.setattr(FK, "FakeKite", SpreadKite)
    monkeypatch.setattr(GM, "_ENTRY_FILL_CAP_S", 45)     # production cap (the fixture shortens it)
    return H._e2e(monkeypatch)


def test_condor_wings_fill_at_the_ask_and_shorts_follow(monkeypatch):
    m, kite = _spread_e2e(monkeypatch)
    t0 = kite.clock()
    assert m.enter_day(make_selection(), mode="LIVE")
    assert m.current_group().state == G_OPEN
    buys = [o for o in kite.orders if o["transaction_type"] == "BUY"]
    assert len(buys) == 2 and all(o["status"] == "COMPLETE" for o in buys)
    assert all(o["price"] == _wing_lim(kite.ask(o["tradingsymbol"])) for o in buys)
    assert all(o["average_price"] == kite.ask(o["tradingsymbol"]) for o in buys)   # paid the ask, not the cap
    assert [e[0] for e in kite.events[:4]] == ["BUY", "BUY", "SELL", "SELL"]
    assert kite.clock() - t0 < 2.0                                                  # no wing wait


def test_wing_reprices_when_the_ask_runs(monkeypatch):
    m, kite = _spread_e2e(monkeypatch)
    real = kite.place_wing_buy

    def jumpy(symbol, token, qty):            # the ask jumps ₹3 before the order can match
        if symbol != "N23200PE":
            return real(symbol, token, qty)
        lim = _wing_lim(kite.ask(symbol))
        oid = kite._new_order(symbol, "BUY", qty, lim, match=False)
        kite.px[symbol] += 3.0
        return (oid, lim, qty)
    kite.place_wing_buy = jumpy
    t0 = kite.clock()
    assert m.enter_day(make_selection(), mode="LIVE")
    l4 = next(o for o in kite.orders if o["tradingsymbol"] == "N23200PE")
    assert l4["status"] == "COMPLETE" and l4["average_price"] == kite.ask("N23200PE")
    assert kite.clock() - t0 < 4.0             # one 2 s re-price, not a 45 s wait


if __name__ == "__main__":
    import os
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(os.path.join(here, "..", "..", "..")), "."]))
    # this file plus the IC suites whose entry path it changes, one at a time
    # (the shared harness stubs the app package, so they cannot share a run)
    rc = 0
    for f in (os.path.basename(__file__), "test_ic_group_manager.py",
              "test_ic_entry_fail_report.py", "test_ic_paper_fallback.py",
              "test_ic_fill_anchor.py", "test_ic_fill_truth.py", "test_ic_wing_reach.py"):
        if not os.path.exists(os.path.join(here, f)):
            continue
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f],
                           cwd=here, env=env)
        rc = rc or r.returncode
    sys.exit(rc)

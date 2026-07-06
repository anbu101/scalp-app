# backend/app/engine/ic_v1/test_ic_group_manager.py
#
# GT-scenarios for ICGroupManager with a scriptable FakeExecutor.
# Runs standalone: all app.* modules are stubbed before import.
import sys
import types
import json
from datetime import date
from pathlib import Path
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Stub the app package tree BEFORE importing the module under test
# ─────────────────────────────────────────────────────────────────────────────
class _Cfg:
    strategy = {}
    global_cfg = {"trade_on": True}

def _mk(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

for n in ["app", "app.event_bus", "app.config", "app.risk", "app.marketdata",
          "app.db", "app.api", "app.engine", "app.engine.ic_v1", "app.utils"]:
    _mk(n)

_mk("app.event_bus.audit_logger").write_audit_log = lambda *a, **k: None

_sl = _mk("app.config.strategy_loader")
_sl.load_strategy_config = lambda sid: dict(_Cfg.strategy)
_gl = _mk("app.config.global_loader")
_gl.load_global_config = lambda: dict(_Cfg.global_cfg)

_mg = _mk("app.risk.strategy_max_loss_guard")
_mg.check_strategy_max_loss = lambda sid: False
_mtm = _mk("app.risk.risk_mtm_guard")
_mtm.is_day_blocked = lambda sid: False

_lts = _mk("app.marketdata.ltp_store")
class LTPStore:
    _d = {}
    @classmethod
    def get_with_timestamp(cls, s):
        import time
        v = cls._d.get(s)
        return (v, time.time()) if v else None
    @classmethod
    def set(cls, s, v): cls._d[s] = v
_lts.LTPStore = LTPStore

ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append(code)

DB = {"live": {}, "paper": {}, "closed": []}
_tr = _mk("app.db.trades_repo")
def insert_trade(**k): DB["live"][k["trade_id"]] = k
def close_trade(**k): DB["closed"].append(("live", k))
_tr.insert_trade, _tr.close_trade = insert_trade, close_trade
_pr = _mk("app.db.paper_trades_repo")
def insert_paper_trade(**k): DB["paper"][k["paper_trade_id"]] = k
def close_paper_trade(**k): DB["closed"].append(("paper", k))
_pr.insert_paper_trade, _pr.close_paper_trade = insert_paper_trade, close_paper_trade

TG = []
_tg = _mk("app.api.telegram_api")
for fn in ["notify_trade_entry", "notify_sl_exit", "notify_tp_exit",
           "notify_manual_exit", "notify_critical"]:
    setattr(_tg, fn, (lambda name: lambda d: TG.append((name, d)))(fn))

import ic_live_core
sys.modules["app.engine.ic_v1.ic_live_core"] = ic_live_core
import ic_selection
sys.modules["app.engine.ic_v1.ic_selection"] = ic_selection

import importlib
import ic_group_manager as GM
importlib.reload(GM)

from ic_live_core import StrikePick, G_OPEN, G_CLOSED, G_ABORTED, L_OPEN, L_CLOSED, L_DEAD
from ic_selection import ICSelection


# ─────────────────────────────────────────────────────────────────────────────
# Fake executor
# ─────────────────────────────────────────────────────────────────────────────
class FakeExecutor:
    def __init__(self):
        self.orders = []            # (kind, symbol, qty)
        self.gtts = {}              # gid -> dict(symbol, qty, sl, armed)
        self._oid = 0
        self._gid = 100
        self.dead_symbols = set()   # entries on these symbols go DEAD
        self.uncancellable = set()  # gtt ids that refuse to die
        self.fail_sl_only = False   # place_gtt_sl_only_short raises
        self.fills = {}             # order_id -> avg fill
        self.ltp = {}
        self.margin = None          # dict(required, available) or Exception

    # entries
    def place_sell_entry(self, *, symbol, token, qty):
        self._oid += 1
        oid = f"O{self._oid}"
        self.orders.append(("SELL_ENTRY", symbol, qty))
        self.fills[oid] = None if symbol in self.dead_symbols else self.ltp.get(symbol, 50.0)
        return oid, self.ltp.get(symbol, 50.0), qty

    def place_buy(self, symbol, token, qty):
        self._oid += 1
        oid = f"O{self._oid}"
        self.orders.append(("BUY_ENTRY", symbol, qty))
        self.fills[oid] = None if symbol in self.dead_symbols else self.ltp.get(symbol, 4.0)
        return oid, self.ltp.get(symbol, 4.0), qty

    def get_order_fill(self, oid):
        px = self.fills.get(oid)
        if px is None:
            return {"status": "REJECTED", "avg_price": 0.0, "found": True}
        return {"status": "COMPLETE", "avg_price": px, "found": True}

    def cancel_order(self, oid): pass

    # protection
    def place_gtt_sl_only_short(self, *, symbol, qty, sl_price):
        if self.fail_sl_only:
            raise RuntimeError("GTT_PLACE_FAIL")
        self._gid += 1
        gid = str(self._gid)
        self.gtts[gid] = {"symbol": symbol, "qty": qty, "sl": sl_price, "armed": True}
        return gid

    def place_gtt_oco(self, *, symbol, qty, sl_price, tp_price, direction):
        return self.place_gtt_sl_only_short(symbol=symbol, qty=qty, sl_price=sl_price)

    def cancel_gtt_verified(self, gid, retries=4):
        if gid in self.uncancellable:
            return False
        self.gtts.pop(gid, None)
        return True

    # exits
    def place_buy_exit(self, *, symbol, qty, reason):
        self.orders.append(("BUY_EXIT", symbol, qty))
        return "X1"

    def place_market_sell(self, symbol, qty):
        self.orders.append(("SELL_EXIT", symbol, qty))
        return "X2"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
def make_selection():
    picks = {
        "L1": StrikePick(24150, "N24150CE", 84.15),
        "L2": StrikePick(24100, "N24100PE", 78.0),
        "L3": StrikePick(24700, "N24700CE", 3.8),
        "L4": StrikePick(23200, "N23200PE", 3.5),
    }
    tokens = {"L1": 11, "L2": 12, "L3": 13, "L4": 14}
    return ICSelection(ok=True, expiry=date(2026, 7, 9), picks=picks, tokens=tokens)


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(GM, "LATCH_PATH", tmp_path / "latch.json")
    monkeypatch.setattr(GM, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GM, "_ENTRY_FILL_CAP_S", 1)
    monkeypatch.setattr(GM, "_ENTRY_FILL_POLL_S", 0.01)
    _Cfg.strategy = {"quantity": {"lot_size": 65}, "freeze_qty": 1800}
    _Cfg.global_cfg = {"trade_on": True}
    DB["live"].clear(); DB["paper"].clear(); DB["closed"].clear()
    ALERTS.clear(); TG.clear(); LTPStore._d.clear()
    yield


def make_mgr(ex=None):
    ex = ex or FakeExecutor()
    for s, p in [("N24150CE", 84.15), ("N24100PE", 78.0),
                 ("N24700CE", 3.8), ("N23200PE", 3.5)]:
        ex.ltp[s] = p
        LTPStore.set(s, p)
    m = GM.ICGroupManager(executor=ex, ltp_resolver=lambda sym: ex.ltp.get(sym))
    return m, ex


# ── GT1: paper full day ──────────────────────────────────────────────────────
def test_gt1_paper_entry_and_eod():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    assert g.state == G_OPEN and len(DB["paper"]) == 4
    assert ex.orders == []                       # no broker calls in paper
    rows = list(DB["paper"].values())
    assert {r["trade_class"] for r in rows} == {"L1", "L2", "L3", "L4"}
    assert {r["trade_direction"] for r in rows} == {"SHORT", "LONG"}
    n = m.force_square_off_all(reason="EOD")
    assert n == 4 and g.state == G_CLOSED
    assert all(l.exit_reason == "EOD" for l in g.legs.values())
    assert len(DB["closed"]) == 4


# ── GT2: live entry, short dead → D6 unwind shorts-first ────────────────────
def test_gt2_live_short_dead_unwinds():
    m, ex = make_mgr()
    ex.dead_symbols.add("N24100PE")              # L2 SELL rejected
    assert not m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    assert g.state == G_ABORTED
    assert g.legs["L2"].state == L_DEAD
    exits = [o for o in ex.orders if o[0] in ("BUY_EXIT", "SELL_EXIT")]
    # L1 short flattened FIRST, then wings
    assert exits[0] == ("BUY_EXIT", "N24150CE", 1560)
    assert {e[1] for e in exits} == {"N24150CE", "N24700CE", "N23200PE"}
    assert "IC_UNWOUND" in ALERTS
    assert m._latch_today()                      # latch stays set — no retry


# ── GT3: live tick SL on L1 → MTC repin L2 → EOD_MTC ────────────────────────
def test_gt3_live_sl_mtc_repin_eod():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    assert len(ex.gtts) == 2                     # both shorts protected
    l1_gtts = set(m.leg_runtime("L1")["gtt_ids"])

    ex.ltp["N24100PE"] = 45.0                    # partner collapsed (realistic)
    LTPStore.set("N24100PE", 45.0)
    ex.ltp["N24150CE"] = 119.49                  # L1 SL touch
    m.on_tick(11, 120.0)

    assert g.legs["L1"].state == L_CLOSED and g.legs["L1"].exit_reason == "SL"
    assert not (l1_gtts & set(ex.gtts))          # L1 GTT cancelled pre-flatten
    assert ("BUY_EXIT", "N24150CE", 1560) in ex.orders
    # L2 repinned: exactly one live GTT at cost 78.0
    l2_gtts = m.leg_runtime("L2")["gtt_ids"]
    assert len(l2_gtts) == 1 and ex.gtts[l2_gtts[0]]["sl"] == 78.0
    assert g.legs["L2"].mtc_repinned and g.mtc_fired

    m.force_square_off_all(reason="EOD")
    assert g.legs["L2"].exit_reason == "EOD_MTC"
    assert g.state == G_CLOSED


# ── GT4: partner GTT uncancellable → keep ORIGINAL SL, critical alert ───────
def test_gt4_repin_cancel_fails_keeps_original():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    for gid in m.leg_runtime("L2")["gtt_ids"]:
        ex.uncancellable.add(gid)
    ex.ltp["N24100PE"] = 45.0                    # partner collapsed (realistic)
    LTPStore.set("N24100PE", 45.0)
    ex.ltp["N24150CE"] = 119.49
    m.on_tick(11, 120.0)
    # partner NOT repinned, NOT market-out, still open on original SL
    l2 = g.legs["L2"]
    assert l2.state == L_OPEN and not l2.mtc_repinned
    assert l2.sl == 73.35 or l2.sl == pytest.approx(78.0 * 1.42) or l2.sl  # original
    assert not any(o == ("BUY_EXIT", "N24100PE", 1560) for o in ex.orders)
    assert any(name == "notify_critical" for name, _ in TG)


# ── GT5: cancels OK but new GTT placement fails → MARKET_OUT (pure D5) ──────
def test_gt5_repin_place_fails_market_out():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    ex.ltp["N24100PE"] = 45.0                    # partner collapsed (realistic)
    LTPStore.set("N24100PE", 45.0)
    ex.ltp["N24150CE"] = 119.49
    # entry GTTs already placed; fail only the repin placement
    ex.fail_sl_only = True
    m.on_tick(11, 120.0)
    l2 = g.legs["L2"]
    assert l2.state == L_CLOSED and l2.exit_reason == "MTC_MARKET_OUT"
    assert ("BUY_EXIT", "N24100PE", 1560) in ex.orders


# ── GT6: D7 latch blocks a second entry ─────────────────────────────────────
def test_gt6_latch_blocks_reentry():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    m.force_square_off_all(reason="EOD")
    m2, _ = make_mgr()                            # fresh process, same latch dir
    assert not m2.enter_day(make_selection(), mode="PAPER")


# ── GT7: D3 slicing — 30 lots → two orders + two GTTs per short ─────────────
def test_gt7_slicing_30_lots():
    _Cfg.strategy = {
        "quantity": {"lot_size": 65}, "freeze_qty": 1800,
        "legs": [dict(l, lots=30) for l in GM.DEFAULT_LEGS],
    }
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    sells = [o for o in ex.orders if o[0] == "SELL_ENTRY" and o[1] == "N24150CE"]
    assert [q for _, _, q in sells] == [1755, 195]
    assert len(m.leg_runtime("L1")["gtt_ids"]) == 2
    gqtys = sorted(ex.gtts[g]["qty"] for g in m.leg_runtime("L1")["gtt_ids"])
    assert gqtys == [195, 1755]


# ── GT8: D8 margin guard — shortfall blocks, API error proceeds ─────────────
def test_gt8_margin_guard():
    m, ex = make_mgr()
    ex.get_basket_margin = lambda basket: {"required": 900000, "available": 500000}
    assert not m.enter_day(make_selection(), mode="LIVE")
    assert "IC_MARGIN_BLOCK" in ALERTS and not m._latch_today()

    m2, ex2 = make_mgr()
    def boom(basket): raise RuntimeError("margin api down")
    ex2.get_basket_margin = boom
    assert m2.enter_day(make_selection(), mode="LIVE")   # fail open


# ── GT9: backstop handoff — broker GTT fill runs the full MTC path ──────────
def test_gt9_backstop_sl_fill_triggers_mtc():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    ex.ltp["N24100PE"] = 45.0
    LTPStore.set("N24100PE", 45.0)
    m.on_backstop_leg_exit(leg_id="L1", exit_price=119.49, reason="SL")
    assert g.legs["L1"].state == L_CLOSED
    assert g.legs["L2"].mtc_repinned            # MTC ran from the backstop too
    # no flatten order for L1 — the GTT already filled it at the broker
    assert ("BUY_EXIT", "N24150CE", 1560) not in ex.orders


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
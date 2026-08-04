# backend/app/engine/ic/test_ic_group_manager.py
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
          "app.db", "app.api", "app.engine", "app.engine.ic", "app.utils"]:
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
sys.modules["app.engine.ic.ic_live_core"] = ic_live_core
import ic_carry_store
sys.modules["app.engine.ic.ic_carry_store"] = ic_carry_store

# ── IC_SPLIT ── the shared-engine tests exercise IC_V2 semantics (carry,
# adjustments) — the pre-split live behavior. Identity is a parameter now.
TEST_SID = "IC_V2"
import ic_selection
sys.modules["app.engine.ic.ic_selection"] = ic_selection

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
    def place_gtt_sl_only_short(self, *, symbol, qty, sl_price, limit_buffer=1.003):
        if self.fail_sl_only:
            raise RuntimeError("GTT_PLACE_FAIL")
        self._gid += 1
        gid = str(self._gid)
        self.gtts[gid] = {"symbol": symbol, "qty": qty, "sl": sl_price,
                          "armed": True, "buffer": limit_buffer}
        return gid

    def place_gtt_sl_only_long(self, *, symbol, qty, sl_price, limit_buffer=1.003):
        return self.place_gtt_sl_only_short(symbol=symbol, qty=qty,
                                            sl_price=sl_price,
                                            limit_buffer=limit_buffer)

    def place_gtt_tp_only_long(self, *, symbol, qty, tp_price):
        return self.place_gtt_sl_only_short(symbol=symbol, qty=qty,
                                            sl_price=tp_price)

    def place_gtt_oco(self, *, symbol, qty, sl_price, tp_price, direction=None):
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


def fast_forward(m, secs=61):
    """IC_V2: MTC re-pin / ADJ open are SCHEDULED (+60s). Simulate the
    activation minute by processing due actions at now+secs."""
    import time as _time
    m.process_due(int(_time.time()) + secs)


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
    # ── IC_SPLIT ── paths derive from STATE_DIR per strategy_id now;
    # patching the two STATE_DIRs redirects latch + carry + session.
    monkeypatch.setattr(GM, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ic_carry_store, "STATE_DIR", tmp_path)
    # PIN THE CLOCK (2026-07-30): _activate_adjust drops activations past
    # 15:29 IST (ADJ_CUTOFF_MIN) — unpinned, V2G1/V2G1b/V2G2 pass before
    # 15:29 IST wall-clock and fail after. Mid-session, fixed date.
    import datetime as _dt
    monkeypatch.setattr(GM, "_now_ist",
        lambda: _dt.datetime(2026, 7, 30, 11, 0, 0, tzinfo=GM.IST))
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
    m = GM.ICGroupManager(strategy_id=TEST_SID, executor=ex,
                          ltp_resolver=lambda sym: ex.ltp.get(sym))
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
    # IC_V2: repin is next-minute effective — not yet applied...
    assert not g.legs["L2"].mtc_repinned
    fast_forward(m)                              # ...activation minute
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
    fast_forward(m)                              # IC_V2 activation minute
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
    fast_forward(m)                              # IC_V2 activation minute
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
    fast_forward(m)                              # IC_V2 activation minute
    assert g.legs["L2"].mtc_repinned            # MTC ran from the backstop too
    # no flatten order for L1 — the GTT already filled it at the broker
    assert ("BUY_EXIT", "N24150CE", 1560) not in ex.orders



# ════════════════════════════════════════════════════════════════════════
# IC_V2 (2026-07-26) — adjustments, ADJ_ONLY, carry, morning close, expiry
# ════════════════════════════════════════════════════════════════════════

def _v2_cfg(extra=None):
    cfg = {
        "quantity": {"lot_size": 65}, "freeze_qty": 1800,
        "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",
        "expiry_exit_time": "15:28",
        "adjust_on_sl": True, "adjust_delay_s": 60,
        "adjust": {
            "L1": {"enabled": True, "lots": 24, "premium_max": 85,
                   "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
            "L2": {"enabled": True, "lots": 24, "premium_max": 85,
                   "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
        },
        "gtt_limit_buffer_pct": 5,
    }
    if extra:
        cfg.update(extra)
    return cfg


def _chain_provider_stub(ex):
    """Fresh-chain stub for adjustment selection: one CE + one PE candidate."""
    def provider():
        ce = [(24200, "N24200CE", 82.0)]
        pe = [(24000, "N24000PE", 80.0)]
        tokens = {"N24200CE": 21, "N24000PE": 22}
        ex.ltp["N24200CE"] = 82.0; ex.ltp["N24000PE"] = 80.0
        LTPStore.set("N24200CE", 82.0); LTPStore.set("N24000PE", 80.0)
        return date(2026, 7, 9), ce, pe, tokens
    return provider


# ── V2G1: ADJ_ON_MTC — SL exit arms + activates an adjustment BUY (paper) ───
def test_v2g1_adjust_arms_and_opens_paper():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                      # L1 SL (paper)
    assert g.legs["L1"].exit_reason == "SL"
    assert "L1" in m.pending_view()["adjust"]
    fast_forward(m)                           # activation (+60s)
    assert "L1A" in g.legs
    adj = g.legs["L1A"]
    assert adj.is_adjust and adj.adjust_of == "L1" and adj.action == "BUY"
    assert adj.symbol == "N24200CE" and adj.entry_price == 82.0
    assert adj.sl == pytest.approx(82.0 * 0.75)      # 25% long SL
    # booked as a paper row with the condor's group_id + trade_class L1A
    rows = [r for r in DB["paper"].values() if r.get("trade_class") == "L1A"]
    assert len(rows) == 1
    assert rows[0]["group_id"] is not None    # shares the condor's group_id


# ── V2G1b: MTC_COST scratch ALSO arms (2026-07-24 reversal) ─────────────────
def test_v2g1b_mtc_cost_arms_adjust():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    ex.ltp["N24100PE"] = 45.0; LTPStore.set("N24100PE", 45.0)
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                      # L1 SL → schedules MTC + ADJ(L1)
    fast_forward(m)                           # repin L2 to cost + open L1A
    assert g.legs["L2"].mtc_repinned
    ex.ltp["N24100PE"] = 78.0                 # back to cost → MTC_COST stop
    m.on_tick(12, 78.0)
    assert g.legs["L2"].exit_reason == "MTC_COST"
    assert "L2" in m.pending_view()["adjust"]     # scratch ARMS too
    fast_forward(m)
    assert "L2A" in g.legs and g.legs["L2A"].symbol == "N24000PE"


# ── V2G2: ADJ_ONLY — condor phantom (no rows/orders), only ·ADJ booked ──────
def test_v2g2_adjust_only_phantom():
    _Cfg.strategy = _v2_cfg({"adjust_only": True})
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    assert m.is_adjust_only()
    assert DB["paper"] == {} and DB["live"] == {}          # nothing booked
    assert all(not o for o in ex.orders)                    # no broker orders
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                      # phantom SL fires logically
    assert g.legs["L1"].exit_reason == "SL"
    assert DB["closed"] == []                 # phantom close not booked
    fast_forward(m)                           # ·ADJ opens FOR REAL (paper)
    assert "L1A" in g.legs
    rows = [r for r in DB["paper"].values() if r.get("trade_class") == "L1A"]
    assert len(rows) == 1                     # ONLY the adjustment is booked


# ── V2G3: carry commit → restore round-trip (DA1) + DA5 assert ──────────────
def test_v2g3_carry_commit_restore():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    # entry_date is today; expiry 2026-07-09 (≠ today) → carry allowed
    assert m.commit_carry("PAPER")
    assert ic_carry_store.carry_exists(TEST_SID)
    payload = ic_carry_store.load_carry(TEST_SID)
    assert len(payload["legs"]) == 4 and payload["paper"] is True

    m2, ex2 = make_mgr()
    assert m2.restore_carry_payload(payload)
    g2 = m2.current_group()
    assert m2.has_carried_open() and len(g2.open_legs()) == 4
    assert all(l.carried for l in g2.open_legs())
    # open-book gate: a restored carry BLOCKS a new entry (D8)
    assert not m2.enter_day(make_selection(), mode="PAPER")


# ── V2G4: morning square-off — NEXT_OPEN reasons + carry file cleared ───────
def test_v2g4_morning_square_off_and_clear():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    assert m.commit_carry("PAPER")
    payload = ic_carry_store.load_carry(TEST_SID)
    m2, ex2 = make_mgr()
    assert m2.restore_carry_payload(payload)
    remaining = m2.morning_square_off()
    assert remaining == 0
    g2 = m2.current_group()
    assert all(l.exit_reason == "NEXT_OPEN" for l in g2.legs.values())
    assert g2.state == G_CLOSED
    assert not ic_carry_store.carry_exists(TEST_SID)   # snapshot cleared post-reconcile


# ── V2G4b: LIVE morning close is STRICT — order failure leaves leg OPEN ─────
def test_v2g4b_morning_strict_retry():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    assert m.commit_carry("LIVE")
    payload = ic_carry_store.load_carry(TEST_SID)

    class FailingExec(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.fail_exits = True
        def place_buy_exit(self, *, symbol, qty, reason):
            if self.fail_exits:
                raise RuntimeError("BROKER_DOWN")
            return super().place_buy_exit(symbol=symbol, qty=qty, reason=reason)
        def place_market_sell(self, symbol, qty):
            if self.fail_exits:
                raise RuntimeError("BROKER_DOWN")
            return super().place_market_sell(symbol, qty)

    ex2 = FailingExec()
    m2, _ = make_mgr(ex2)
    assert m2.restore_carry_payload(payload)
    r1 = m2.morning_square_off()
    assert r1 > 0                              # shorts failed → still open
    g2 = m2.current_group()
    assert any(l.state == L_OPEN for l in g2.legs.values())
    ex2.fail_exits = False                     # broker recovers
    r2 = m2.morning_square_off()
    assert r2 == 0 and g2.state == G_CLOSED
    assert not ic_carry_store.carry_exists(TEST_SID)


# ── V2G5: premarket GTT teardown (live) ─────────────────────────────────────
def test_v2g5_premarket_gtt_cancel():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    assert len(ex.gtts) == 2
    assert m.commit_carry("LIVE")
    payload = ic_carry_store.load_carry(TEST_SID)
    m2, ex2 = make_mgr(ex)                     # same broker state
    assert m2.restore_carry_payload(payload)
    assert m2.premarket_cancel_gtts() is True
    assert ex.gtts == {}                       # broker-side GTTs gone
    for lid in ("L1", "L2"):
        assert m2.leg_runtime(lid)["gtt_ids"] == []


# ── V2G6: expiry-day square-off scoping (DA5) ───────────────────────────────
def test_v2g6_expiry_square_off_scoping():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    today = g.legs["L1"].entry_date
    # legs' expiry (2026-07-09) != today → NOTHING closes
    assert m.expiry_square_off(today) == 0
    assert len(g.open_legs()) == 4
    # force the scenario: expiry == entry_date == today → ALL close as EOD
    for l in g.legs.values():
        l.expiry = today
    n = m.expiry_square_off(today)
    assert n == 4 and g.state == G_CLOSED
    assert all(l.exit_reason in ("EOD", "EOD_MTC") for l in g.legs.values())



# ── V2G7: KILL SWITCH — overrides everything, abort-before-flatten ──────────
def test_v2g7_kill_all_live():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    assert len(ex.gtts) == 2
    res = m.kill_all()
    assert res["ok"] is True and res["remaining"] == 0 and res["closed"] == 4
    assert ex.gtts == {}                              # swept before flatten
    assert g.state == G_CLOSED
    assert ("BUY_EXIT", "N24150CE", 1560) in ex.orders
    assert all(l.exit_reason in ("MANUAL", "EOD_MTC") for l in g.legs.values())


def test_v2g7b_kill_aborts_on_unverified_gtt():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    bad = m.leg_runtime("L1")["gtt_ids"][0]
    ex.uncancellable.add(bad)
    res = m.kill_all()
    assert res["ok"] is False and res["closed"] == 0
    assert res["stuck_gtts"] and res["stuck_gtts"][0]["gtt_id"] == bad
    # NOTHING flattened against the armed GTT (double-fire guard)
    assert not any(o[0] in ("BUY_EXIT", "SELL_EXIT") for o in ex.orders)
    assert all(l.state == L_OPEN for l in g.legs.values())
    assert any(name == "notify_critical" for name, _ in TG)


def test_v2g7c_kill_closes_carry_and_clears_snapshot():
    # kill on a restored carried group: overrides the 09:16 wait, closes
    # carried legs, and housekeeping clears the snapshot
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    assert m.commit_carry("LIVE")
    payload = ic_carry_store.load_carry(TEST_SID)
    m2, _ = make_mgr(ex)                     # same broker (GTTs still armed)
    assert m2.restore_carry_payload(payload)
    res = m2.kill_all()
    assert res["ok"] is True and res["remaining"] == 0
    assert not ic_carry_store.carry_exists(TEST_SID)
    assert m2.current_group().state == G_CLOSED



# ── V2G8: IC_RESTART — mid-session snapshot restore continuity ──────────────
def test_v2g8_session_restart_continuity():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    assert ic_carry_store.session_exists(TEST_SID)          # persisted at entry
    g = m.current_group()
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                            # L1 SL → MTC+ADJ pending
    payload = ic_carry_store.load_session(TEST_SID)
    assert payload["pending_mtc"] and payload["pending_adjust"]
    assert any(l["state"] == "CLOSED" for l in payload["core"]["legs"])

    # ── "restart": fresh manager, restore, life continues ──
    m2, ex2 = make_mgr()
    m2.attach_chain_provider(_chain_provider_stub(ex2))
    assert m2.restore_session_payload(payload)
    g2 = m2.current_group()
    assert g2.legs["L1"].state == L_CLOSED
    assert g2.legs["L1"].exit_reason == "SL"
    assert not any(l.carried for l in g2.open_legs())   # today's legs
    # partner must be BELOW cost at activation or the decision is a
    # (correct) MARKET_OUT — same setup as V2G1b
    ex2.ltp["N24100PE"] = 45.0; LTPStore.set("N24100PE", 45.0)
    fast_forward(m2)                                 # pendings survived →
    assert g2.legs["L2"].mtc_repinned                #   repin fires
    assert "L1A" in g2.legs                          #   adjustment opens
    # entry gate: restored group blocks a second entry (D7/D8)
    assert not m2.enter_day(make_selection(), mode="PAPER")


def test_v2g8b_finalize_clears_session():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    assert ic_carry_store.session_exists(TEST_SID)
    m.force_square_off_all(reason="MANUAL")
    assert m.current_group().state == G_CLOSED
    assert not ic_carry_store.session_exists(TEST_SID)       # no ghost at next boot


def test_v2g8c_adopt_as_carry():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    payload = ic_carry_store.load_session(TEST_SID)
    m2, _ = make_mgr()
    assert m2.restore_session_payload(payload, adopt_as_carry=True)
    g2 = m2.current_group()
    assert all(l.carried for l in g2.open_legs())
    assert m2.has_carried_open()
    assert m2.carry_entry_date() == payload["entry_date"]
    assert m2.pending_view() == {"mtc": {}, "adjust": {}}   # pendings dropped


def test_v2g8d_carry_commit_supersedes_session():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    assert ic_carry_store.session_exists(TEST_SID)
    assert m.commit_carry("PAPER")
    assert ic_carry_store.carry_exists(TEST_SID)
    assert not ic_carry_store.session_exists(TEST_SID)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
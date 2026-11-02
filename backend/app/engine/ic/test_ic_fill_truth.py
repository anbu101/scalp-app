# backend/app/engine/ic/test_ic_fill_truth.py
#
# ── FILL_TRUTH_20261102 ── an exit is booked at the order that closed THAT leg.
# Run on its own (it reuses the stubbed harness of test_ic_group_manager):
#   cd backend/app/engine/ic && python3 -m pytest -q test_ic_fill_truth.py
import test_ic_group_manager as H
from test_ic_group_manager import GM, GMON, _entered, _px, fast_forward, L1S, L2S, clean  # noqa: F401  (clean = autouse fixture)
from ic_live_core import L_OPEN, L_CLOSED

FP = GM._fill_pick()


# ── 1. the real Kite order book of 2026-11-02 ────────────────────────────────
P = "NIFTY26N03"


def o(oid, t, side, sym, px, q=65):
    return {"order_id": oid, "order_timestamp": f"2026-11-02 {t}", "transaction_type": side,
            "tradingsymbol": P + sym, "status": "COMPLETE", "average_price": px,
            "filled_quantity": q, "quantity": q}


BOOK = [  # oldest → newest, as Kite returns it
    o("e3", "09:18:03", "BUY", "23750CE", 3.20), o("e4", "09:18:07", "BUY", "22400PE", 3.70),
    o("e1", "09:18:08", "SELL", "23300CE", 75.25), o("e2", "09:18:13", "SELL", "23200PE", 83.85),
    o("x1", "09:45:14", "BUY", "23300CE", 107.80), o("a1", "09:46:29", "BUY", "23350CE", 80.15),
    o("xa1", "11:48:07", "SELL", "23350CE", 55.85), o("x2", "13:09:07", "BUY", "23200PE", 87.45),
    o("a2", "13:10:24", "BUY", "23200PE", 88.30), o("xa2", "13:59:24", "SELL", "23200PE", 71.55),
    o("x3", "13:59:25", "SELL", "23750CE", 2.85), o("x4", "13:59:31", "SELL", "22400PE", 2.40),
]
ENTRY_IDS = {"e1", "e2", "e3", "e4", "a1", "a2"}
TS = FP.order_ts


def _exit(sym, short, entry_oid, exclude=ENTRY_IDS):
    entry_ts = TS(next(x for x in BOOK if x["order_id"] == entry_oid))
    return FP.pick_exit_fill(BOOK, symbol=P + sym, exit_side=FP.exit_side_for(short),
                             not_before_ts=entry_ts, exclude_ids=exclude, want_qty=65)


def test_old_rule_reproduces_the_incident():
    newest_buy = [x for x in BOOK if x["tradingsymbol"] == P + "23350CE"
                  and x["transaction_type"] == "BUY"][-1]["average_price"]
    assert newest_buy == 80.15                     # what the monitor booked for L1·ADJ


def test_every_leg_of_the_day_resolves_to_kites_fill():
    assert _exit("23300CE", True, "e1") == (107.80, ["x1"])        # L1
    assert _exit("23350CE", False, "a1") == (55.85, ["xa1"])       # L1·ADJ (was 80.15)
    assert _exit("23200PE", True, "e2") == (87.45, ["x2"])         # L2, despite the ADJ BUY after it
    assert _exit("23200PE", False, "a2") == (71.55, ["xa2"])       # L2·ADJ, not L2's 09:18 SELL
    assert _exit("23750CE", False, "e3") == (2.85, ["x3"])         # L3
    assert _exit("22400PE", False, "e4") == (2.40, ["x4"])         # L4


def test_same_strike_short_exit_holds_even_without_known_adj_id():
    # e.g. after a restart that lost the ADJ's entry id: earliest-after-entry still wins
    assert _exit("23200PE", True, "e2", exclude={"e2"}) == (87.45, ["x2"])


def test_gtt_named_order_is_the_only_candidate():
    px, ids = FP.pick_exit_fill(BOOK, symbol=P + "23350CE", exit_side="SELL", only_ids=["nope"])
    assert px is None and ids == []


def test_freeze_slices_vwap():
    book = [o("s1", "11:00:00", "SELL", "23350CE", 50.0, 1800), o("s2", "11:00:01", "SELL", "23350CE", 49.0, 700)]
    px, ids = FP.pick_exit_fill(book, symbol=P + "23350CE", exit_side="SELL", want_qty=2500)
    assert ids == ["s1", "s2"] and abs(px - (1800 * 50 + 700 * 49) / 2500) < 1e-9


def test_implausible_exit_flags_the_incident_only():
    why = FP.implausible_exit(80.15, entry=80.15, sl=56.14, tp=160.40, reason="SL")
    assert why and "(= entry)" in why
    assert FP.implausible_exit(55.85, entry=80.15, sl=56.14, tp=160.40, reason="SL") is None
    assert FP.implausible_exit(107.80, entry=75.25, sl=107.78, tp=None, reason="SL") is None
    assert FP.implausible_exit(87.45, entry=83.85, sl=83.85, tp=None, reason="MTC_COST") is None


# ── 2. end to end through the real router + Kite-shaped fake ─────────────────
def _adj_open(monkeypatch):
    m, kite, g = _entered(monkeypatch)
    _px(kite, L2S, 45.0)
    kite.px[L1S] = 118.0
    m.on_tick(11, 120.0)                     # L1 stopped
    fast_forward(m)                          # L1A bought (N24200CE)
    adj = g.legs["L1A"]
    assert adj.state == L_OPEN
    return m, kite, g, adj


def test_e2e_adj_gtt_race_books_the_real_sell(monkeypatch):
    m, kite, g, adj = _adj_open(monkeypatch)
    _px(kite, "N24200CE", round(adj.sl - 1.0, 2))                  # ADJ SL GTT fires at the broker
    trig = [x for x in kite.gtts.values() if x["symbol"] == "N24200CE" and x["status"] == "triggered"]
    assert trig
    sell = [x for x in kite.orders if x["tradingsymbol"] == "N24200CE" and x["transaction_type"] == "SELL"][-1]
    for x in trig:
        x["orders"][0]["result"] = None                             # Zerodha race
    GMON.ICGTTMonitor(m.executor, m)._sweep()
    assert adj.state == L_CLOSED
    assert adj.exit_price == sell["average_price"] != adj.entry_price
    closes = [k for book, k in H.DB["closed"] if book == "live"]
    assert closes[-1]["exit_order_id"] == sell["order_id"]          # exit order stored on the trade


def test_e2e_adj_gtt_normal_path_unchanged(monkeypatch):
    m, kite, g, adj = _adj_open(monkeypatch)
    _px(kite, "N24200CE", round(adj.sl - 1.0, 2))
    GMON.ICGTTMonitor(m.executor, m)._sweep()
    assert adj.state == L_CLOSED and adj.exit_price != adj.entry_price


def test_backstop_refuses_the_legs_own_entry_order(monkeypatch):
    m, kite, g, adj = _adj_open(monkeypatch)
    own = m.leg_runtime("L1A")["order_ids"][0]
    m.on_backstop_leg_exit(leg_id="L1A", exit_price=adj.entry_price, reason="SL", exit_order_id=own)
    assert adj.state == L_OPEN and "IC_EXIT_FILL_REFUSED" in H.ALERTS


def test_known_order_ids_and_restart_keep_scoping(monkeypatch):
    m, kite, g, adj = _adj_open(monkeypatch)
    rt = m.leg_runtime("L1A")
    assert rt.get("entry_ts") and rt.get("order_ids")
    ids = set(m.known_order_ids(except_exit_of="L2"))
    for lid in ("L1", "L2", "L3", "L4", "L1A"):
        assert set(m.leg_runtime(lid)["order_ids"]) <= ids
    payload = GM.ic_carry_store.load_session(H.TEST_SID)
    m2 = GM.ICGroupManager(strategy_id=H.TEST_SID, executor=m.executor,
                           ltp_resolver=lambda s: kite.px.get(s))
    assert m2.restore_session_payload(payload)
    r2 = m2.leg_runtime("L1A")
    assert r2["order_ids"] == rt["order_ids"] and r2["entry_ts"] == rt["entry_ts"]


def test_gtt_order_named_but_not_complete_is_not_guessed():
    class Leg:
        leg_id, symbol, is_short, qty = "L1A", "N24200CE", False, 65
        state, sl, tp, entry_price, is_adjust = L_OPEN, 61.5, None, 82.0, True

    class GMx:
        strategy_id = "IC_V2"
        def __init__(s): s.leg, s.handoffs = Leg(), []
        def leg_runtime(s, lid): return {"gtt_ids": ["G1"], "order_ids": ["E1"], "entry_ts": 1}
        def known_order_ids(s, except_exit_of=None): return ["E1"]

    class Ex:
        def get_order_fill(s, oid): return {"status": "OPEN", "avg_price": 0.0}
        def get_orders(s):
            return [{"order_id": "E1", "tradingsymbol": "N24200CE", "transaction_type": "BUY",
                     "status": "COMPLETE", "average_price": 82.0, "quantity": 65},
                    {"order_id": "OTHER", "tradingsymbol": "N24200CE", "transaction_type": "SELL",
                     "status": "COMPLETE", "average_price": 70.0, "quantity": 65}]

    mon = GMON.ICGTTMonitor(Ex(), GMx())
    gtt = {"id": "G1", "status": "triggered", "orders": [{"result": {"order_result": {"order_id": "G1O"}}}]}
    assert mon._resolve_fill(gtt, Leg()) == (None, None)          # retry, never someone else's fill
    gtt["orders"][0]["result"] = None                               # race: no id exposed
    assert mon._resolve_fill(gtt, Leg()) == (70.0, "OTHER")         # side-correct scan, entry excluded

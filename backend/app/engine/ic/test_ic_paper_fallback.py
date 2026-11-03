# backend/app/engine/ic/test_ic_paper_fallback.py
#
# ── IC_PAPER_FALLBACK_20261103 ── PAPER_LIVE: a LIVE entry refused on margin no
# longer silences the paper book. 2026-11-03 09:18: "IC_V2 entry blocked:
# margin shortfall" stopped BOTH books (paper is a twin of the live rows).
# Run on its own:  cd backend/app/engine/ic && python3 -m pytest -q test_ic_paper_fallback.py
import test_ic_group_manager as H
from test_ic_group_manager import (GM, _e2e, _px, fast_forward, make_selection,  # noqa: F401
                                   L1S, L2S, clean)                              # clean = autouse fixture
from ic_live_core import L_OPEN, G_OPEN

TWIN = 10            # paper_lots: the twin carries 10× a 1-lot live leg in this test


def _paper_live(monkeypatch, m, on=True):
    monkeypatch.setattr(m, "_paper_fallback_allowed", lambda: on)
    monkeypatch.setattr(m, "_twin_qty", lambda lid, q, ls, ot, sym: q * TWIN)


def test_paper_live_margin_block_runs_paper_only(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m)
    kite.margin_available, kite.margin_required = 100_000, 400_000
    assert m.enter_day(make_selection(), mode="LIVE")                  # the day goes ahead…
    g = m.current_group()
    assert g.state == G_OPEN and m.is_paper()                          # …as PAPER
    assert not kite.events                                             # no broker order, no GTT
    assert len(H.DB["paper"]) == 4 and not H.DB["live"]                # booked in the paper book
    assert "IC_MARGIN_BLOCK" in H.ALERTS
    assert all(r["qty"] % TWIN == 0 for r in H.DB["paper"].values())   # twin size
    assert all(lg.qty == 1560 * TWIN for lg in g.legs.values())        # 24 lots × 65 live → ×10


def test_alert_names_the_numbers(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m)
    kite.margin_available, kite.margin_required = 100_000, 400_000
    seen = []
    real = GM.record_alert
    monkeypatch.setattr(GM, "record_alert", lambda code, msg, **k: (seen.append(msg), real(code, msg, **k)))
    m.enter_day(make_selection(), mode="LIVE")
    msg = next(x for x in seen if "margin shortfall" in x)
    assert "required ₹400,000" in msg and "available ₹100,000" in msg and "paper book trades on" in msg


def test_live_only_still_hard_blocked(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m, on=False)                              # LIVE (not PAPER_LIVE)
    kite.margin_available, kite.margin_required = 100_000, 400_000
    assert not m.enter_day(make_selection(), mode="LIVE")
    assert not kite.events and not H.DB["paper"] and not H.DB["live"]


def test_enough_margin_unchanged(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m)
    assert m.enter_day(make_selection(), mode="LIVE")
    assert not m.is_paper() and len(H.DB["live"]) == 4 and not H.DB["paper"] and kite.events


def test_fallback_adjust_leg_is_paper_and_twin_sized(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m)
    kite.margin_available, kite.margin_required = 100_000, 400_000
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    _px(kite, L2S, 45.0)
    kite.px[L1S] = 118.0
    m.on_tick(11, 120.0)                                               # paper L1 stop
    fast_forward(m)
    adj = g.legs.get("L1A")
    assert adj is not None and adj.state == L_OPEN
    row = next(r for r in H.DB["paper"].values() if r.get("trade_class") == "L1A")
    assert row["qty"] % TWIN == 0 and not kite.events and not H.DB["live"]


def test_fallback_survives_restart(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _paper_live(monkeypatch, m)
    kite.margin_available, kite.margin_required = 100_000, 400_000
    assert m.enter_day(make_selection(), mode="LIVE")
    payload = GM.ic_carry_store.load_session(H.TEST_SID)
    assert payload and payload.get("paper") is True and payload.get("paper_fallback") is True
    m2 = GM.ICGroupManager(strategy_id=H.TEST_SID, executor=m.executor, ltp_resolver=lambda s: kite.px.get(s))
    assert m2.restore_session_payload(payload) and m2.is_paper() and m2._paper_fallback

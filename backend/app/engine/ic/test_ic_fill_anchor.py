# backend/app/engine/ic/test_ic_fill_anchor.py
#
# ── IC_FILL_ANCHOR_20261102 ── LIVE SL/TP are derived from the CONFIRMED FILL
# (the backtest's construction), not the selection LTP. Run on its own:
#   cd backend/app/engine/ic && python3 -m pytest -q test_ic_fill_anchor.py
from datetime import date

import test_ic_group_manager as H
from test_ic_group_manager import (GM, _e2e, _px, fast_forward, make_selection,  # noqa: F401
                                   L1S, L2S, clean)                              # clean = autouse fixture
from ic_live_core import L_OPEN, sl_price, tp_price


def _gtt(kite, sym):
    return [g for g in kite.gtts.values() if g["symbol"] == sym and g["status"] == "active"]


def test_main_short_sl_from_fill_not_pick(monkeypatch):
    m, kite = _e2e(monkeypatch)
    kite.set_px(L1S, 82.0)                       # market moved after the 84.15 pick
    assert m.enter_day(make_selection(), mode="LIVE")
    leg = m.current_group().legs["L1"]
    assert leg.entry_price == 82.0               # booked at the fill
    assert abs(leg.sl - 82.0 * 1.42) < 1e-6      # was 84.15 × 1.42 = 119.49
    g = _gtt(kite, L1S)
    assert g and g[0]["trigger"] == 116.45       # the broker level is the anchored one
    other = m.current_group().legs["L2"]         # filled at its pick → unchanged
    assert abs(other.sl - 78.0 * 1.42) < 1e-6


def test_main_legs_unchanged_when_fill_equals_pick(monkeypatch):
    m, kite = _e2e(monkeypatch)
    assert m.enter_day(make_selection(), mode="LIVE")
    leg = m.current_group().legs["L1"]
    assert leg.entry_price == 84.15 and abs(leg.sl - 84.15 * 1.42) < 1e-6
    assert _gtt(kite, L1S)[0]["trigger"] == 119.5


def test_adjust_sl_tp_from_fill_not_pick(monkeypatch):
    cfg = H._v2_cfg()
    cfg["adjust"]["L1"].update(tp_val=100, sl_val=30, premium_max=100)
    m, kite = _e2e(monkeypatch, cfg)
    oco = []                                     # the fake broker has no OCO GTT: record what is sent

    def place_gtt_oco(symbol, qty, sl_price, tp_price, *a, **k):
        oco.append((symbol, round(sl_price, 2), round(tp_price, 2)))
        return f"OCO{len(oco)}"
    kite.place_gtt_oco = place_gtt_oco
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    # chain snapshot says 91.20 (stale by the time the order goes in); the market is 88.30
    m.attach_chain_provider(lambda: (date(2026, 7, 9), [(24200, "N24200CE", 91.20)],
                                     [(24000, "N24000PE", kite.px["N24000PE"])],
                                     {"N24200CE": 21, "N24000PE": 22}))
    kite.set_px("N24200CE", 88.30)
    _px(kite, L2S, 45.0)
    kite.px[L1S] = 118.0
    m.on_tick(11, 120.0)                         # L1 stopped → L1A scheduled
    fast_forward(m)
    adj = g.legs["L1A"]
    assert adj.state == L_OPEN and adj.entry_price == 88.30
    assert abs(adj.sl - sl_price("BUY", 88.30, 30, "pct")) < 1e-6     # 61.81 (pick-anchored: 63.84)
    assert abs(adj.tp - tp_price("BUY", 88.30, 100, "pct")) < 1e-6    # 176.60 (pick-anchored: 182.40)
    assert oco == [("N24200CE", 61.81, 176.6)]   # the broker levels are the anchored ones


def test_paper_is_untouched(monkeypatch):
    m, kite = _e2e(monkeypatch)
    assert m.enter_day(make_selection(), mode="PAPER")
    leg = m.current_group().legs["L1"]
    assert leg.entry_price == 84.15 and abs(leg.sl - 84.15 * 1.42) < 1e-6
    assert not kite.events                       # paper places nothing

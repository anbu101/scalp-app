# backend/app/engine/ic/test_ic_margin_headroom.py
#
# ── IC_MARGIN_HEADROOM_20261105 ── the IC margin pre-check keeps headroom above
# Kite's basket estimate (default 5%, config margin_buffer_pct; 0 = old rule).
# 2026-11-04: basket ₹1,26,373 vs ₹1,36,993 available passed; the 4th leg then
# needed ₹1,38,009 (+9.2%) and was rejected.
# Run on its own:  cd backend/app/engine/ic && python3 -m pytest -q test_ic_margin_headroom.py
import test_ic_group_manager as H
from test_ic_group_manager import GM, _e2e, make_selection, clean   # noqa: F401  (clean = autouse)


def _arm(monkeypatch, m, paper_live):
    monkeypatch.setattr(m, "_paper_fallback_allowed", lambda: paper_live)
    monkeypatch.setattr(m, "_twin_qty", lambda lid, q, ls, ot, sym: q * 10)
    seen = []
    real = GM.record_alert
    monkeypatch.setattr(GM, "record_alert", lambda code, msg, **k: (seen.append(msg), real(code, msg, **k)))
    return seen


def test_default_5pct_blocks_before_any_order(monkeypatch):
    m, kite = _e2e(monkeypatch)
    seen = _arm(monkeypatch, m, paper_live=True)
    kite.margin_required, kite.margin_available = 131_000, 136_993           # 137,550 needed
    assert m.enter_day(make_selection(), mode="LIVE")
    assert m.is_paper() and not kite.events and len(H.DB["paper"]) == 4
    msg = next(x for x in seen if "margin shortfall" in x)
    assert "required ₹131,000 + 5% headroom = ₹137,550, available ₹136,993" in msg


def test_nov4_numbers_pass_at_5pct_but_block_at_10pct(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _arm(monkeypatch, m, paper_live=False)
    kite.margin_required, kite.margin_available = 126_373, 136_993           # 132,692 at 5%
    assert m.enter_day(make_selection(), mode="LIVE") and not m.is_paper()
    cfg = H._v2_cfg()
    cfg["margin_buffer_pct"] = 10
    m2, kite2 = _e2e(monkeypatch, cfg)
    _arm(monkeypatch, m2, paper_live=False)
    kite2.margin_required, kite2.margin_available = 126_373, 136_993         # 139,010 at 10%
    assert not m2.enter_day(make_selection(), mode="LIVE") and not kite2.events


def test_enough_room_still_trades_live(monkeypatch):
    m, kite = _e2e(monkeypatch)
    _arm(monkeypatch, m, paper_live=True)
    kite.margin_required, kite.margin_available = 120_000, 140_000
    assert m.enter_day(make_selection(), mode="LIVE")
    assert not m.is_paper() and len(H.DB["live"]) == 4


def test_buffer_zero_restores_the_old_rule(monkeypatch):
    cfg = H._v2_cfg()
    cfg["margin_buffer_pct"] = 0
    m, kite = _e2e(monkeypatch, cfg)
    _arm(monkeypatch, m, paper_live=False)
    kite.margin_required, kite.margin_available = 136_000, 136_993
    assert m.enter_day(make_selection(), mode="LIVE") and not m.is_paper()

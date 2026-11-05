# backend/app/engine/ic/test_ic_entry_fail_report.py
#
# ── IC_ENTRY_FAIL_REPORT_20261105 ── 2026-11-04 09:18: L3, L4, L1 filled, L2
# was rejected by Zerodha for funds, the three were unwound — the alert said
# only "entry FAILED at L2; all filled legs unwound" and the PAPER book got
# nothing. Now: one clear message (which leg, the broker's reason, each closed
# leg with prices, the ₹ result, what is still open, what happens next) and, in
# PAPER_LIVE with a clean unwind, the condor is re-entered PAPER-only.
# Run on its own:  cd backend/app/engine/ic && python3 -m pytest -q test_ic_entry_fail_report.py
import test_ic_group_manager as H
from test_ic_group_manager import GM, _e2e, make_selection, L1S, L2S, L3S, clean   # noqa: F401  (clean = autouse)
from ic_live_core import G_OPEN

TWIN = 10
RMS = "Insufficient funds. Required margin is 143210.50 but available margin is 137544.00."


def _arm(monkeypatch, m, paper_live=True):
    monkeypatch.setattr(m, "_paper_fallback_allowed", lambda: paper_live)
    monkeypatch.setattr(m, "_twin_qty", lambda lid, q, ls, ot, sym: q * TWIN)
    sent = []
    monkeypatch.setattr(GM, "notify_critical", lambda d: sent.append(d["message"]))
    shorts = []
    real = GM.record_alert
    monkeypatch.setattr(GM, "record_alert", lambda code, msg, **k: (shorts.append((code, msg)), real(code, msg, **k)))
    return sent, shorts


def test_paper_live_rejected_leg_unwinds_then_paper_trades(monkeypatch):
    m, kite = _e2e(monkeypatch)
    sent, shorts = _arm(monkeypatch, m)
    kite.reject[L2S] = RMS
    assert m.enter_day(make_selection(), mode="LIVE")              # the day goes ahead…
    g = m.current_group()
    assert g.state == G_OPEN and m.is_paper()                      # …as PAPER, twin-sized
    assert all(lg.qty == 1560 * TWIN for lg in g.legs.values())
    assert len(H.DB["paper"]) == 4 and not H.DB["live"]
    assert kite.flat()                                             # the 3 real legs were closed
    assert len(sent) == 1
    t = sent[0]
    assert "LIVE entry failed — L2 SELL " + L2S + " was REJECTED by Zerodha" in t
    assert RMS[:60] in t
    assert "The 3 leg(s) already filled were closed at once:" in t
    assert t.count(" → ") == 3 and "Unwind result ₹" in t
    assert "No live position is open." in t and "paper book on its own" in t
    code, short = next(x for x in shorts if x[0] == "IC_UNWOUND")
    assert "LIVE entry failed at L2 (REJECTED: Insufficient funds" in short and "paper book trades on" in short


def test_live_only_still_stops_with_a_clear_message(monkeypatch):
    m, kite = _e2e(monkeypatch)
    sent, _ = _arm(monkeypatch, m, paper_live=False)
    kite.reject[L2S] = RMS
    assert not m.enter_day(make_selection(), mode="LIVE")
    assert not H.DB["paper"] and not H.DB["live"] and kite.flat()
    assert len(sent) == 1 and "No further live entry today." in sent[0] and "REJECTED" in sent[0]


def test_unwind_not_confirmed_never_falls_back(monkeypatch):
    m, kite = _e2e(monkeypatch)
    sent, _ = _arm(monkeypatch, m)
    kite.reject[L2S] = RMS
    real = m._flatten_live
    monkeypatch.setattr(m, "_flatten_live",
                        lambda leg, **k: ("FAIL", None) if leg.symbol == L1S else real(leg, **k))
    assert not m.enter_day(make_selection(), mode="LIVE")          # a live leg may still be open
    assert not H.DB["paper"]
    t = sent[-1]
    assert "NOT confirmed closed — check Kite Positions NOW: L1 SELL " + L1S in t
    assert "No further live entry today." in t


def test_rejected_first_leg_reports_without_closing_anything(monkeypatch):
    m, kite = _e2e(monkeypatch)
    sent, _ = _arm(monkeypatch, m)
    kite.reject[L3S] = "Order price is out of the circuit limit"
    assert m.enter_day(make_selection(), mode="LIVE")              # PAPER_LIVE: paper still trades
    assert m.is_paper() and len(H.DB["paper"]) == 4 and kite.flat()
    t = sent[0]
    assert "L3 BUY " + L3S + " was REJECTED" in t and "circuit limit" in t
    assert "closed at once" not in t and "No live position is open." in t

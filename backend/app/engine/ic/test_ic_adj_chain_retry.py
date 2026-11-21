# backend/app/engine/ic/test_ic_adj_chain_retry.py — fence IC_ADJ_CHAIN_RETRY_20261121
#
# 13 Nov 2026, Icarus PAPER: L1 stopped at 10:45:28; at the +60 s activation
# (10:46:40) the fresh chain snapshot hit one kite.quote ReadTimeout (7 s),
# came back empty, and L1·ADJ was dropped for good ("[ADJ][CHAIN_EMPTY] →
# drop"). The backtest on the same day bought 25400CE. A failed snapshot is
# now retried through process_due (the engine loop never blocks).
#   TC-ICACR-0001  snapshot raises once (timeout) → retried, adjustment bought,
#                  no drop alert
#   TC-ICACR-0002  snapshot comes back empty twice → bought on the 3rd try
#   TC-ICACR-0003  while a retry is pending the wings are held (pending
#                  adjustment keeps L3/L4) and the retry is not run early
#   TC-ICACR-0004  snapshot never recovers → dropped after 3 retries with
#                  IC_ADJ_NO_STRIKE (fail closed), nothing left pending
# Run: cd backend/app/engine/ic && python3 -m pytest -q test_ic_adj_chain_retry.py
import time

from test_ic_group_manager import (       # noqa: F401  (clean = autouse fixture)
    clean, _Cfg, ALERTS, GM, LTPStore, make_mgr, make_selection, _v2_cfg,
    _chain_provider_stub, fast_forward,
)
from ic_live_core import L_OPEN


def _flaky(ex, fail_first, how="raise"):
    """Chain provider that fails `fail_first` times, then works."""
    good = _chain_provider_stub(ex)
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        if calls["n"] <= fail_first:
            if how == "raise":
                raise TimeoutError("Read timed out. (read timeout=7)")
            return None, [], [], {}
        return good()
    return provider, calls


def _l1_stopped(provider):
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    p, calls = provider(ex)
    m.attach_chain_provider(p)
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                      # L1 SL (paper) → ADJ armed
    assert "L1" in m.pending_view()["adjust"]
    return m, g, calls


def _retry_pass(m):
    m.process_due(int(time.time()) + 61 + GM.ADJ_CHAIN_RETRY_S + 1)


def test_tc_icacr_0001_timeout_once_then_bought():
    m, g, calls = _l1_stopped(lambda ex: _flaky(ex, 1, "raise"))
    fast_forward(m)                           # activation: snapshot times out
    assert "L1A" not in g.legs and "L1" in m.pending_view()["adjust"]
    _retry_pass(m)
    assert "L1A" in g.legs and g.legs["L1A"].state == L_OPEN
    assert g.legs["L1A"].symbol == "N24200CE"
    assert calls["n"] == 2
    assert "IC_ADJ_NO_STRIKE" not in ALERTS
    assert not m.pending_view()["adjust"]


def test_tc_icacr_0002_empty_twice_then_bought():
    m, g, calls = _l1_stopped(lambda ex: _flaky(ex, 2, "empty"))
    fast_forward(m)
    _retry_pass(m)
    assert "L1A" not in g.legs
    _retry_pass(m)
    assert "L1A" in g.legs and calls["n"] == 3
    assert "IC_ADJ_NO_STRIKE" not in ALERTS


def test_tc_icacr_0003_retry_pending_holds_wings_and_waits():
    m, g, calls = _l1_stopped(lambda ex: _flaky(ex, 1, "raise"))
    fast_forward(m)                           # first attempt fails
    m.process_due(int(time.time()))           # before the retry is due
    assert calls["n"] == 1, "retry ran early"
    assert all(g.legs[w].state == L_OPEN for w in ("L3", "L4"))
    _retry_pass(m)
    assert "L1A" in g.legs


def test_tc_icacr_0004_never_recovers_dropped_after_retries():
    m, g, calls = _l1_stopped(lambda ex: _flaky(ex, 99, "raise"))
    fast_forward(m)
    for _ in range(GM.ADJ_CHAIN_RETRIES):
        _retry_pass(m)
    assert calls["n"] == 1 + GM.ADJ_CHAIN_RETRIES
    assert "L1A" not in g.legs
    assert "IC_ADJ_NO_STRIKE" in ALERTS
    assert not m.pending_view()["adjust"]
    _retry_pass(m)                            # nothing more is tried
    assert calls["n"] == 1 + GM.ADJ_CHAIN_RETRIES

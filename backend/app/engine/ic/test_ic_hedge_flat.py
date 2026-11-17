# backend/app/engine/ic/test_ic_hedge_flat.py — fences IC2_HEDGE_FLAT_20261117 + IC2_HEDGE_FLAT2_20261117
#
# Paper/Live: once every short (L1/L2) and ·ADJ leg of the condor is closed and
# no adjustment is waiting, the L3/L4 hedge buys are sold (reason HEDGE_FLAT)
# on the next engine iteration (process_due). Setting hedge_close_when_flat,
# absent = ON for the carry model (IC_V2), OFF for EOD (IC_V1).
#   TC-ICHF-0001  paper: both shorts SL'd, no adjustment → wings sold, group CLOSED
#   TC-ICHF-0002  setting OFF → wings stay open (old behaviour)
#   TC-ICHF-0003  one short still open → wings kept
#   TC-ICHF-0004  adjustment pending → wings kept; sold as soon as the ·ADJ buys are
#                 open, which run on to their own SL/TP (FLAT2)
#   TC-ICHF-0005  outside market hours / carried book → never sold here
#   TC-ICHF-0006  live: wings sold with SELL orders; a failed sell is retried
#   TC-ICHF-0007  EOD exit mode (IC_V1) with the key absent → off
#   TC-ICHF-0008  IC_V2 loader default carries hedge_close_when_flat = True
#   TC-ICHF-0009  adjustment pending then dropped (no chain) → wings sold in that pass
# Uses the stubbed harness of test_ic_group_manager (same fixtures).
# Run: cd backend/app/engine/ic && python3 -m pytest -q test_ic_hedge_flat.py
import datetime as _dt
import pytest

from test_ic_group_manager import (       # noqa: F401  (clean = autouse fixture)
    clean, _Cfg, DB, ALERTS, GM, LTPStore, make_mgr, make_selection, _v2_cfg,
    _chain_provider_stub, fast_forward, ic_carry_store, TEST_SID,
)
from ic_live_core import G_OPEN, G_CLOSED, L_OPEN, L_CLOSED


def _stop_both_shorts(m, ex):
    """L1 then L2 hit their original SLs (paper or live)."""
    ex.ltp["N24150CE"] = 120.0; LTPStore.set("N24150CE", 120.0)
    m.on_tick(11, 120.0)
    ex.ltp["N24100PE"] = 111.0; LTPStore.set("N24100PE", 111.0)
    m.on_tick(12, 111.0)


def _wings(g):
    return g.legs["L3"], g.legs["L4"]


def test_tc_ichf_0001_paper_wings_sold_when_flat():
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    assert g.legs["L1"].state == L_CLOSED and g.legs["L2"].state == L_CLOSED
    assert all(w.state == L_OPEN for w in _wings(g))       # not yet — next iteration
    m.process_due()
    l3, l4 = _wings(g)
    assert l3.state == L_CLOSED and l4.state == L_CLOSED
    assert l3.exit_reason == "HEDGE_FLAT" and l4.exit_reason == "HEDGE_FLAT"
    assert l3.exit_price == 3.8 and l4.exit_price == 3.5   # sold at the market price
    assert g.state == G_CLOSED
    closed = {k["exit_reason"] for _, k in DB["closed"]}
    assert "HEDGE_FLAT" in closed
    assert "IC_HEDGE_FLAT" in ALERTS
    assert not ic_carry_store.carry_exists(TEST_SID)       # nothing left to carry


def test_tc_ichf_0002_setting_off_keeps_wings():
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False, "hedge_close_when_flat": False})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    m.process_due()
    assert all(w.state == L_OPEN for w in _wings(g)) and g.state == G_OPEN


def test_tc_ichf_0003_one_short_open_keeps_wings():
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    ex.ltp["N24100PE"] = 45.0; LTPStore.set("N24100PE", 45.0)   # partner in profit → re-pinned, stays open
    ex.ltp["N24150CE"] = 120.0
    m.on_tick(11, 120.0)                                   # only L1 stopped
    for _ in range(3):
        fast_forward(m)
    assert g.legs["L2"].state == L_OPEN
    assert all(w.state == L_OPEN for w in _wings(g))


def test_tc_ichf_0004_pending_and_open_adjustment_keep_wings():
    _Cfg.strategy = _v2_cfg()                              # adjust ON
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)                               # arms L1A + L2A
    m.process_due()                                        # not due yet
    assert m.pending_view()["adjust"]
    assert all(w.state == L_OPEN for w in _wings(g))       # adjustment pending → kept
    fast_forward(m)                                        # both ·ADJ open, same pass sells the wings
    assert g.legs["L1A"].state == L_OPEN and g.legs["L2A"].state == L_OPEN
    assert all(w.state == L_CLOSED and w.exit_reason == "HEDGE_FLAT" for w in _wings(g))
    assert g.state == G_OPEN                               # ·ADJ buys still running
    m.on_tick(21, 1.0)                                     # L1A SL (25 % long stop)
    m.on_tick(22, 1.0)                                     # L2A SL
    assert g.legs["L1A"].state == L_CLOSED and g.legs["L2A"].state == L_CLOSED
    assert g.state == G_CLOSED


def test_tc_ichf_0009_pending_adjustment_dropped_sells_wings():
    _Cfg.strategy = _v2_cfg()                              # adjust ON, but no chain provider
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    m.process_due()
    assert all(w.state == L_OPEN for w in _wings(g))       # pending → kept
    fast_forward(m)                                        # activation: no chain → dropped
    assert "IC_ADJ_NO_STRIKE" in ALERTS
    assert "L1A" not in g.legs and "L2A" not in g.legs
    assert all(w.exit_reason == "HEDGE_FLAT" for w in _wings(g)) and g.state == G_CLOSED


def test_tc_ichf_0005_outside_hours_and_carried(monkeypatch):
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    monkeypatch.setattr(GM, "_now_ist", lambda: _dt.datetime(2026, 7, 30, 15, 31, 0, tzinfo=GM.IST))
    m.process_due()
    assert all(w.state == L_OPEN for w in _wings(g))       # market closed → not sold
    monkeypatch.setattr(GM, "_now_ist", lambda: _dt.datetime(2026, 7, 30, 9, 14, 0, tzinfo=GM.IST))
    m.process_due()
    assert all(w.state == L_OPEN for w in _wings(g))
    # the evening carry commit then owns them: never sold by this rule
    monkeypatch.setattr(GM, "_now_ist", lambda: _dt.datetime(2026, 7, 30, 15, 41, 0, tzinfo=GM.IST))
    assert m.commit_carry("PAPER")
    monkeypatch.setattr(GM, "_now_ist", lambda: _dt.datetime(2026, 7, 31, 10, 0, 0, tzinfo=GM.IST))
    m.process_due()
    assert all(w.state == L_OPEN and w.carried for w in _wings(g))
    assert m.morning_square_off() == 0                     # morning close still works
    assert all(w.exit_reason == "NEXT_OPEN" for w in _wings(g))


def test_tc_ichf_0006_live_sell_and_retry(monkeypatch):
    monkeypatch.setattr(GM, "_EXIT_RETRY_BACKOFF_S", 0, raising=False)
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    assert g.legs["L1"].state == L_CLOSED and g.legs["L2"].state == L_CLOSED
    real_sell = ex.place_market_sell
    calls = {"n": 0}

    def flaky(symbol, qty):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("broker down")
        return real_sell(symbol, qty)
    ex.place_market_sell = flaky
    for _ in range(4):
        m.process_due()
        if g.state == G_CLOSED:
            break
    assert calls["n"] >= 2                                 # first sell failed, retried
    l3, l4 = _wings(g)
    assert l3.exit_reason == "HEDGE_FLAT" and l4.exit_reason == "HEDGE_FLAT"
    sells = [o for o in ex.orders if o[0] == "SELL_EXIT"]
    assert {o[1] for o in sells} == {"N24700CE", "N23200PE"}
    assert g.state == G_CLOSED


def test_tc_ichf_0007_eod_mode_key_absent_is_off():
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False, "exit_mode": "EOD"})
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    _stop_both_shorts(m, ex)
    m.process_due()
    assert all(w.state == L_OPEN for w in _wings(g))
    _Cfg.strategy = _v2_cfg({"adjust_on_sl": False, "exit_mode": "EOD", "hedge_close_when_flat": True})
    m.process_due()
    assert all(w.exit_reason == "HEDGE_FLAT" for w in _wings(g))


def test_tc_ichf_0008_loader_default():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "config" / "strategy_loader.py").read_text()
    i = src.index('"IC_V2": {')
    j = src.index('"legs":', i)
    assert '"hedge_close_when_flat": True' in src[i:j]
    ast.parse(src)


if __name__ == "__main__":
    import os
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(os.path.join(here, "..", "..", "..")), "."]))
    rc = 0
    for f in (os.path.basename(__file__), "test_ic_group_manager.py", "test_ic_paper_fallback.py",
              "test_ic_margin_headroom.py", "test_ic_live_core.py"):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f],
                           cwd=here, env=env)
        rc = rc or r.returncode
    sys.exit(rc)

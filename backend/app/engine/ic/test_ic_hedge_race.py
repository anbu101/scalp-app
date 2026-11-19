# backend/app/engine/ic/test_ic_hedge_race.py — fence IC2_HEDGE_RACE_20261119
#
# 19 Nov 2026, Icarus LIVE: L2 SL → L2·ADJ → L2·ADJ SL, then L1 (re-pinned to
# cost) stopped at the broker. The GTT monitor books that stop on ITS thread;
# between L1 turning CLOSED and its adjustment being armed (DB + Telegram in
# between) the engine's hedge-flat check saw "no short, nothing pending", sold
# L3/L4, the condor closed and L1·ADJ was never bought. Paper (stop booked on
# the engine thread) bought it.
#   TC-ICHF-0010  LIVE: last short's stop booked on another thread → hedges are
#                 NOT sold while it is in flight; its adjustment is armed and
#                 bought; hedges sold after that (·ADJ still open)
#   TC-ICHF-0011  the same with the stop path held open across several engine
#                 passes — no pass sells the hedges
#   TC-ICHF-0012  a stop booked after the condor already closed raises an
#                 IC_ADJ_DROPPED alert ("not placed") instead of arming silently
# Run: cd backend/app/engine/ic && python3 -m pytest -q test_ic_hedge_race.py
import threading

from test_ic_group_manager import (       # noqa: F401  (clean = autouse fixture)
    clean, _Cfg, DB, ALERTS, GM, LTPStore, make_mgr, make_selection, _v2_cfg,
    _chain_provider_stub, fast_forward,
)
from ic_live_core import G_OPEN, G_CLOSED, L_OPEN, L_CLOSED


def _live_to_last_short(m, ex):
    """L2 stop → L2·ADJ bought → L2·ADJ stopped. L1 (re-pinned) still open."""
    ex.ltp["N24150CE"] = 45.0; LTPStore.set("N24150CE", 45.0)   # L1 in profit → re-pinned, stays open
    m.on_backstop_leg_exit(leg_id="L2", exit_price=111.0, reason="SL")
    fast_forward(m)                                   # MTC on L1 + L2·ADJ bought
    g = m.current_group()
    assert g.legs["L2A"].state == L_OPEN
    m.on_backstop_leg_exit(leg_id="L2A", exit_price=50.0, reason="SL")
    assert g.legs["L2A"].state == L_CLOSED
    assert g.legs["L1"].state == L_OPEN
    assert all(g.legs[w].state == L_OPEN for w in ("L3", "L4"))
    return g


def _slow_close(m, leg_id):
    entered, go = threading.Event(), threading.Event()
    orig = m._finish_close

    def slow(leg):
        if leg.leg_id == leg_id:
            entered.set()
            go.wait(5)
        return orig(leg)
    m._finish_close = slow
    return entered, go


def _run_scenario(passes):
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="LIVE")
    g = _live_to_last_short(m, ex)
    entered, go = _slow_close(m, "L1")
    t = threading.Thread(target=lambda: m.on_backstop_leg_exit(
        leg_id="L1", exit_price=95.0, reason="SL"))
    t.start()                                         # GTT monitor thread
    assert entered.wait(5)
    assert g.legs["L1"].state == L_CLOSED             # L1 booked, adjustment not armed yet
    for _ in range(passes):
        m.process_due()                               # engine thread inside the window
        assert all(g.legs[w].state == L_OPEN for w in ("L3", "L4")), "hedges sold mid stop path"
    go.set()
    t.join(5)
    assert "L1" in m.pending_view()["adjust"]          # L1·ADJ armed
    fast_forward(m)                                   # activation: L1·ADJ bought
    assert "L1A" in g.legs and g.legs["L1A"].state == L_OPEN
    assert all(g.legs[w].state == L_CLOSED and g.legs[w].exit_reason == "HEDGE_FLAT"
               for w in ("L3", "L4"))                 # FLAT2: sold with ·ADJ open
    assert g.state == G_OPEN
    return m, g


def test_tc_ichf_0010_live_stop_on_monitor_thread_keeps_adjustment():
    _run_scenario(passes=1)


def test_tc_ichf_0011_many_passes_inside_window():
    _run_scenario(passes=5)


def test_tc_ichf_0012_stop_after_close_alerts():
    _Cfg.strategy = _v2_cfg({"hedge_close_when_flat": False})
    m, ex = make_mgr()
    m.attach_chain_provider(_chain_provider_stub(ex))
    assert m.enter_day(make_selection(), mode="PAPER")
    g = m.current_group()
    m.force_square_off_all("MANUAL")
    assert g.state == G_CLOSED
    ALERTS.clear()
    m._schedule_adjust("L1", 0)
    assert "IC_ADJ_DROPPED" in ALERTS and not m.pending_view()["adjust"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(os.path.join(here, "..", "..", "..")), "."]))
    rc = 0
    for f in (os.path.basename(__file__), "test_ic_hedge_flat.py", "test_ic_group_manager.py"):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f],
                           cwd=here, env=env)
        rc = rc or r.returncode
    sys.exit(rc)

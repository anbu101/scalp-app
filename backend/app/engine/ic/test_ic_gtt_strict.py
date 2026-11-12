# backend/app/engine/ic/test_ic_gtt_strict.py
#
# ── GTT_VERIFY_STRICT_20261112 ── the 11→12 Nov IC_V2 LIVE carry, replayed:
# L1 stopped out (GTT), L2 re-pinned to cost (new GTT), L1·ADJ bought with an
# OCO GTT, the condor carried overnight. Next morning the app ran its
# pre-market GTT teardown BEFORE the day's Kite login. The old executor
# reported every cancel "confirmed gone" off an unreadable GTT list; the
# app forgot the ids, squared off at 09:16 and left the GTTs armed.
#   * strict verify: pre-market keeps the ids until Kite is readable, then
#     cancels; nothing is left armed after the 09:16 close
#   * closing sweep: even if ids are lost, a closed group leaves no ACTIVE
#     GTT on a flat symbol it traded — but never touches a symbol still held
# Run:  cd backend/app/engine/ic && python3 test_ic_gtt_strict.py
import sys

import test_ic_group_manager as H
from test_ic_group_manager import (GM, _e2e, _px, fast_forward, make_selection,    # noqa: F401
                                   ic_carry_store, ALERTS, TEST_SID, L1S, L2S, clean)
from ic_live_core import G_CLOSED

FK = H._FK


class SessionKite(FK.FakeKite):
    """Kite whose session can be stale (before the day's login).
    strict=True  → the new contract: unreadable = NOT verified.
    strict=False → the old one: an unreadable list read as "gone"."""
    logged_in = True
    strict = True

    def cancel_gtt(self, gtt_id):
        if not self.logged_in:
            raise RuntimeError("TokenException: Incorrect access_token")
        return super().cancel_gtt(gtt_id)

    def get_gtts_or_none(self):
        return super().get_gtts() if self.logged_in else None

    def cancel_gtt_verified(self, gtt_id, retries=4):
        try:
            self.cancel_gtt(gtt_id)
        except Exception:
            pass
        lst = self.get_gtts_or_none()
        if lst is None:
            return not self.strict            # old: [] ⇒ "gone"
        st = next((g["status"] for g in lst if str(g["id"]) == str(gtt_id)), "missing")
        return st != "active"


def _carry_day(monkeypatch, strict=True):
    monkeypatch.setattr(FK, "FakeKite", SessionKite)
    m, kite = _e2e(monkeypatch)
    kite.strict = strict
    assert m.enter_day(make_selection(), mode="LIVE")
    g = m.current_group()
    _px(kite, L2S, 45.0)                       # partner collapses …
    kite.px[L1S] = 118.0
    m.on_tick(11, 120.0)                       # … L1 stops out
    fast_forward(m)                            # L2 re-pinned to cost + L1·ADJ bought (OCO GTT)
    assert g.legs["L2"].mtc_repinned and "L1A" in g.legs
    armed = {x["symbol"] for x in kite.active_gtts()}
    assert L2S in armed and "N24200CE" in armed            # cost-stop + adjustment GTTs
    assert m.commit_carry("LIVE")
    m2 = GM.ICGroupManager(strategy_id=TEST_SID, executor=m.executor,
                           ltp_resolver=lambda s: kite.px.get(s))
    m2._sleep, m2._clock = kite.clock.sleep, kite.clock
    assert m2.restore_carry_payload(ic_carry_store.load_carry(TEST_SID))
    return m2, kite


def _morning(monkeypatch, hh, mm):
    import datetime as _dt
    monkeypatch.setattr(GM, "_now_ist", lambda: _dt.datetime(2026, 11, 12, hh, mm, 0, tzinfo=GM.IST))


def test_premarket_before_login_keeps_ids_then_cancels(monkeypatch):
    m2, kite = _carry_day(monkeypatch, strict=True)
    ids_before = {l: list(m2.leg_runtime(l)["gtt_ids"]) for l in ("L2", "L1A")}
    kite.logged_in = False
    _morning(monkeypatch, 8, 40)
    assert m2.premarket_cancel_gtts() is False                       # not cleared …
    assert {l: list(m2.leg_runtime(l)["gtt_ids"]) for l in ("L2", "L1A")} == ids_before   # … ids kept
    assert "IC_PREMARKET_GTT" not in ALERTS                           # quiet before 09:10
    _morning(monkeypatch, 9, 11)
    assert m2.premarket_cancel_gtts() is False
    assert ALERTS.count("IC_PREMARKET_GTT") == 1
    m2.premarket_cancel_gtts()
    assert ALERTS.count("IC_PREMARKET_GTT") == 1                      # once a day, not every poll
    kite.logged_in = True                                             # the day's login lands
    assert m2.premarket_cancel_gtts() is True
    assert not kite.active_gtts()                                     # really gone at the broker
    _morning(monkeypatch, 9, 16)
    assert m2.morning_square_off() == 0
    assert kite.flat() and not kite.active_gtts() and m2.current_group().state == G_CLOSED


def _forget_ids(m2):
    """What the 12 Nov pre-market did: a false "confirmed gone" emptied the ids."""
    for l in ("L2", "L3", "L4", "L1A"):
        rt = m2.leg_runtime(l)
        if rt:
            rt["gtt_ids"].clear()


def test_lost_ids_left_gtts_armed_sweep_now_catches_them(monkeypatch):
    m2, kite = _carry_day(monkeypatch)
    _forget_ids(m2)
    assert {x["symbol"] for x in kite.active_gtts()} >= {L2S, "N24200CE"}   # still armed at Kite
    _morning(monkeypatch, 9, 16)
    assert m2.morning_square_off() == 0 and kite.flat()
    # without the sweep this is exactly 12 Nov: flat book, GTTs armed
    assert not kite.active_gtts(), kite.active_gtts()                 # the closing sweep cancelled them
    assert "IC_ORPHAN_GTT" in ALERTS


def test_sweep_leaves_a_symbol_another_strategy_still_holds(monkeypatch):
    m2, kite = _carry_day(monkeypatch)
    _forget_ids(m2)
    _morning(monkeypatch, 9, 16)
    sweep = m2._sweep_orphan_gtts
    m2._sweep_orphan_gtts = lambda: None
    assert m2.morning_square_off() == 0 and kite.flat()     # the condor closes as usual
    m2._sweep_orphan_gtts = sweep
    orig = kite.get_open_positions_or_none
    # …but another strategy is short the cost-stopped strike when the sweep runs
    kite.get_open_positions_or_none = lambda: (orig() or []) + [{"tradingsymbol": L2S, "quantity": -65}]
    m2._after_close_housekeeping()
    left = {x["symbol"] for x in kite.active_gtts()}
    assert L2S in left                     # held elsewhere → never touched
    assert "N24200CE" not in left          # the adjustment's orphan (flat) → cancelled


def test_sweep_waits_when_broker_unreadable(monkeypatch):
    m2, kite = _carry_day(monkeypatch)
    _forget_ids(m2)
    _morning(monkeypatch, 9, 16)
    kite.positions_readable = False
    m2.morning_square_off()
    assert kite.active_gtts()                                         # nothing guessed
    kite.positions_readable = True
    m2._after_close_housekeeping()                                    # next housekeeping retries
    assert not kite.active_gtts()


if __name__ == "__main__":
    import os
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(os.path.join(here, "..", "..", "..")), "."]))
    rc = 0
    for f in (os.path.basename(__file__), "test_ic_group_manager.py", "test_ic_engine_monitor_eod.py",
              "test_ic_fill_truth.py", "test_ic_orphan_reconcile.py"):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f],
                           cwd=here, env=env)
        rc = rc or r.returncode
    sys.exit(rc)

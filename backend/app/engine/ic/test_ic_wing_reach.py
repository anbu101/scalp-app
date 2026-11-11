# backend/app/engine/ic/test_ic_wing_reach.py
#
# ── WING_REACH_20261111 ── IC group manager: a cheapest-real fallback wing
# raises IC_WING_FALLBACK (it was a stored flag and nothing else); a missing
# wing is an error and reaches Telegram when LIVE. Same harness as
# test_ic_group_manager.py.
# Run:  cd backend/app/engine/ic && python3 test_ic_wing_reach.py
import sys

from test_ic_group_manager import GM, _e2e, make_selection, clean   # noqa: F401  (clean = autouse fixture)
from ic_live_core import G_OPEN, StrikePick


def _msgs(monkeypatch):
    seen = []
    real = GM.record_alert
    monkeypatch.setattr(GM, "record_alert",
                        lambda code, msg, **k: (seen.append((code, msg, k.get("severity"))),
                                                real(code, msg, **k)))
    return seen


def test_fallback_wing_alerts_and_still_enters(monkeypatch):
    m, kite = _e2e(monkeypatch)
    seen = _msgs(monkeypatch)
    sel = make_selection()
    sel.picks["L4"] = StrikePick(23200, "N23200PE", 3.5, fallback=True)   # flagged as above-cap
    sel.wing_fallback = True
    assert m.enter_day(sel, mode="LIVE")
    assert m.current_group().state == G_OPEN
    fb = [x for x in seen if x[0] == "IC_WING_FALLBACK"]
    assert fb and "L4 N23200PE" in fb[0][1] and fb[0][2] == "warning"


def test_normal_day_no_wing_alert(monkeypatch):
    m, kite = _e2e(monkeypatch)
    seen = _msgs(monkeypatch)
    assert m.enter_day(make_selection(), mode="LIVE")
    assert not any(c.startswith("IC_WING") for c, _, _ in seen)


def test_absent_wing_is_an_error_and_skips(monkeypatch):
    m, kite = _e2e(monkeypatch)
    seen = _msgs(monkeypatch)
    sel = make_selection()
    del sel.picks["L4"]
    sel.wing_absent = ["L4"]
    assert not m.enter_day(sel, mode="LIVE")
    assert any(c == "IC_WING_ABSENT" and s == "error" for c, _, s in seen)
    assert not kite.events


if __name__ == "__main__":
    # The harness stubs the app package, so pytest must run from this folder
    # with the IC folder first on the path (house pattern for Icarus suites).
    import os
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.abspath(os.path.join(here, "..", "..", "..")), "."]))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        os.path.basename(__file__)], cwd=here, env=env)
    sys.exit(r.returncode)

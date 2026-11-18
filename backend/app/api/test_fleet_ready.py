# backend/app/api/test_fleet_ready.py — ── READY_PANEL_20261118 ──
# GET /api/fleet/ready: feed state + carried stops at the broker, on fakes (no Kite).
#   cd backend && PYTHONPATH=$PWD python3 app/api/test_fleet_ready.py
import time

import app.api.fleet_ready as R
import app.api.fleet_today_routes as F

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Eng:
    def __init__(self, c, t):
        self._connected, self._last_tick_ts = c, t


class Leg:
    def __init__(self, lid, carried):
        self.leg_id, self.carried = lid, carried


class Core:
    def __init__(self, legs):
        self._l = legs

    def open_legs(self):
        return list(self._l)


class Ex:
    def __init__(self, gtts):
        self.gtts, self.calls = gtts, 0

    def get_gtts_or_none(self):
        self.calls += 1
        if isinstance(self.gtts, Exception):
            raise self.gtts
        return self.gtts

    # anything that could change the broker must never be called
    def place_gtt_oco(self, **k): raise AssertionError("placed")
    def cancel_gtt_verified(self, g): raise AssertionError("cancelled")


class GM:
    def __init__(self, paper, legs, rt, ex):
        self._paper, self._core, self._rt, self.executor = paper, Core(legs), rt, ex


def fresh():
    R._gtt_cache.clear()


# ── feed
R._ws_engines = lambda: []
check("TC-READY-0001 no websocket registered → not_started", R.feed_state()["state"] == "not_started")
R._ws_engines = lambda: [Eng(False, 0)]
check("TC-READY-0002 registered, not connected → connecting", R.feed_state()["state"] == "connecting")
R._ws_engines = lambda: [Eng(False, 0), Eng(True, time.time() - 3)]
f = R.feed_state()
check("TC-READY-0003 one connected → connected, tick age", f["state"] == "connected" and 2 <= f["last_tick_age_s"] <= 5, f)
R._ws_engines = lambda: (_ for _ in ()).throw(RuntimeError("x"))
check("TC-READY-0004 registry error → unknown, no raise", R.feed_state()["state"] == "unknown")

# ── carried stops
legs = [Leg("a", True), Leg("b", True), Leg("c", False)]
rt = {"a": {"gtt_ids": [101]}, "b": {"gtt_ids": [102]}, "c": {"gtt_ids": [103]}}
ex = Ex([{"id": 101, "status": "active"}, {"id": 102, "status": "active"}, {"id": 103, "status": "active"}])
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex), {})]
cs = R.carried_stops()
check("TC-READY-0005 live carried legs, both GTTs active → 2 of 2 armed (today's leg not counted)",
      cs == [{"id": "IC_V2", "legs": 2, "with_stop": 2, "armed": 2}], cs)
ex2 = Ex([{"id": 101, "status": "active"}, {"id": 102, "status": "triggered"}])
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex2), {})]
check("TC-READY-0006 one GTT triggered → 1 of 2 armed", R.carried_stops()[0]["armed"] == 1)
ex3 = Ex(None)
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex3), {})]
check("TC-READY-0007 Kite not readable (before login) → armed None", R.carried_stops()[0]["armed"] is None)
ex4 = Ex(RuntimeError("down"))
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex4), {})]
check("TC-READY-0008 Kite read raises → armed None, no raise", R.carried_stops()[0]["armed"] is None)
fresh(); R._instances = lambda: [("IC_V2", GM(True, legs, rt, ex), {})]
check("TC-READY-0009 paper instance → not listed (paper places no GTTs)", R.carried_stops() == [])
fresh(); R._instances = lambda: [("IC_V2", GM(False, [Leg("c", False)], rt, ex), {})]
check("TC-READY-0010 nothing carried → not listed", R.carried_stops() == [])
rt2 = {"a": {"gtt_ids": []}, "b": {"phantom": True, "gtt_ids": [9]}}
ex5 = Ex([])
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs[:2], rt2, ex5), {})]
cs = R.carried_stops()
check("TC-READY-0011 no GTT on record → 0 of 1, Kite not read; phantom leg ignored",
      cs == [{"id": "IC_V2", "legs": 1, "with_stop": 0, "armed": 0}] and ex5.calls == 0, cs)
ex6 = Ex([{"id": 101, "status": "active"}, {"id": 102, "status": "active"}])
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex6), {}), ("IC_V1", GM(False, legs, rt, ex6), {})]
R.carried_stops(); R.carried_stops()
check("TC-READY-0012 one Kite read per executor per minute (cached)", ex6.calls == 1, ex6.calls)


class Boom:
    _paper = False

    @property
    def _core(self):
        raise RuntimeError("bad")


fresh(); R._instances = lambda: [("IC_V1", Boom(), {}), ("IC_V2", GM(False, legs, rt, ex), {})]
cs = R.carried_stops()
check("TC-READY-0013 one broken instance never hides the other", len(cs) == 2 and cs[0].get("error") and cs[1]["armed"] == 2, cs)

# ── route
R._ws_engines = lambda: [Eng(True, time.time())]
fresh(); R._instances = lambda: [("IC_V2", GM(False, legs, rt, ex), {})]
out = F.get_fleet_ready()
check("TC-READY-0014 route returns ok + feed + carried_stops", out.get("ok") and out["feed"]["state"] == "connected"
      and out["carried_stops"][0]["armed"] == 2, out)
R.ready_payload = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
import importlib
out = F.get_fleet_ready()
check("TC-READY-0015 route never raises", out.get("ok") is False and out["carried_stops"] == [], out)

print(f"\n{'FAIL' if FAILS else 'OK'}: {15 - len(FAILS)}/15 checks")
raise SystemExit(1 if FAILS else 0)

"""
test_dash_calm_routes.py — DASH_CALM_20261117: GET /api/replay/meters (every card's meter
in one call; route function called directly, builders stubbed — no corpus, Kite or feed)
and the dashboard_layout values (modern = the redesigned page, classic = the previous one).

    cd backend && PYTHONPATH=. python3 app/api/test_dash_calm_routes.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import app.backtest.replay.live_replay as LR                          # noqa: E402
from app.api import live_replay_routes as R                           # noqa: E402
from app.api import app_settings_api as S                             # noqa: E402

fails, n = [], [0]


def check(tc, cond, msg):
    n[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


calls = []
CLOCK = [1000.0]
R._time.monotonic = lambda: CLOCK[0]
METER = lambda sid: {"side": "exit", "score": 40, "word": "calm", "reason": sid}   # noqa: E731
FULL = lambda sid: {"ok": True, "strategy_id": sid, "spot": {"bars": [1] * 3000}, "legs_bars": {"a": [1] * 3000},   # noqa: E731
                    "waiting": {"status": "holding", "headline": "h", "items": [], "meter": METER(sid)},
                    "live": {"active": True, "poll_ms": 2000, "now_ts": 1, "stale": False, "no_feed": [], "legs": [1]}}
KEYS = {"IC_V2": "t|ic:1", "TMA_V2": "a|tma:2", "EMC_V1": "w|EMC_V1", "BAD": None, "BOOM": "t|boom:1"}


def find_keys(strategy=None, **k):
    calls.append(("find", strategy))
    key = KEYS.get(strategy)
    return {"ok": True, "key": key, "strategy_id": strategy} if key else {"ok": False, "error": "no matching trade", "status": 404}


def build_live_replay(scope, key, fill=True, live=False):
    calls.append(("trade", scope, key, fill, live))
    if key == "boom:1":
        raise RuntimeError("corpus locked")
    return FULL({"ic:1": "IC_V2", "tma:2": "TMA_V2"}[key])


def build_watch(sid, fill=True, live=False):
    calls.append(("watch", sid, fill, live))
    return FULL(sid)


LR.find_keys, LR.build_live_replay, LR.build_watch = find_keys, build_live_replay, build_watch

out = R.strategy_meters(strategies="IC_V2,TMA_V2,EMC_V1,IC_V2, ,BAD,BOOM")
builds = [c for c in calls if c[0] != "find"]
check("TC-CALM-0024", set(out["meters"]) == {"IC_V2", "TMA_V2", "EMC_V1"} and out["meters"]["IC_V2"]["waiting"]["meter"]["reason"] == "IC_V2",
      f"one call answers each strategy once (IC_V2 asked twice) ({sorted(out['meters'])})")
check("TC-CALM-0025", out["errors"].get("BAD") == "no matching trade" and "corpus locked" in out["errors"].get("BOOM", ""),
      f"a failing strategy is reported alone, the others still answer ({out['errors']})")
m = out["meters"]["TMA_V2"]
check("TC-CALM-0026", not ({"spot", "legs_bars"} & set(m)) and set(m["live"]) == set(R.METER_LIVE_KEYS) and m["watch"] is False
      and out["meters"]["EMC_V1"]["watch"] is True,
      "slim: meter + checklist + live clock, same shape as /meter (watch flag for a waiting strategy)")
check("TC-CALM-0027", ("trade", "trades", "ic:1", True, True) in builds and ("trade", "analytics", "tma:2", True, True) in builds
      and ("watch", "EMC_V1", True, True) in builds, "first read of each strategy tops its bars up from Kite (fill on), live")
n0 = len(calls)
CLOCK[0] += 3
R.strategy_meters(strategies="IC_V2,TMA_V2,EMC_V1")
check("TC-CALM-0028", len(calls) == n0, f"read again within {R.METERS_TTL_S:.0f} s → served from the cache, no rebuild")
CLOCK[0] += 10
R.strategy_meters(strategies="IC_V2")
last = [c for c in calls if c[0] == "trade"][-1]
check("TC-CALM-0029", last[3] is False, f"after the cache, before {R.METERS_FILL_EVERY_S:.0f} s: rebuilt WITHOUT a Kite top-up ({last})")
CLOCK[0] += 61
R.strategy_meters(strategies="IC_V2")
last = [c for c in calls if c[0] == "trade"][-1]
check("TC-CALM-0030", last[3] is True, "a minute later: one Kite top-up again")
many = ",".join(f"S{i}" for i in range(40))
calls.clear()
R.strategy_meters(strategies=many)
check("TC-CALM-0031", len([c for c in calls if c[0] == "find"]) == R.METERS_MAX, f"at most {R.METERS_MAX} strategies a call")
paths = [r.path for r in R.router.routes]
src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)), "..", "api_server.py"), encoding="utf-8").read()
check("TC-CALM-0032", "/api/replay/meters" in paths
      and "include_router(live_replay_router, dependencies=[Depends(_require_admin_ui)])" in src,
      "on the replay router, mounted behind the admin wall")
check("TC-CALM-0033", S._merge_defaults({})["dashboard_layout"] == "modern"
      and S._merge_defaults({"dashboard_layout": "classic"})["dashboard_layout"] == "classic"
      and S._merge_defaults({"dashboard_layout": "legacy"})["dashboard_layout"] == "legacy"
      and S._merge_defaults({"dashboard_layout": "calm"})["dashboard_layout"] == "modern",
      "layouts: modern (default, the redesign), classic, legacy; anything else → modern")

print(f"\n{'FAIL' if fails else 'OK'}: {n[0] - len(fails)}/{n[0]} checks")
sys.exit(1 if fails else 0)

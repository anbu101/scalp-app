"""
test_replay_meter_route.py — PHONE_METER_20261117: GET /api/replay/meter (route function
called directly; the builders are stubbed, so this needs no corpus, Kite or feed).

    cd backend && PYTHONPATH=. python3 app/api/test_replay_meter_route.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from fastapi import HTTPException                                     # noqa: E402

import app.backtest.replay.live_replay as LR                          # noqa: E402
from app.api import live_replay_routes as R                           # noqa: E402

fails, n = [], [0]


def check(tc, cond, msg):
    n[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


calls = []
METER = {"side": "exit", "score": 70, "level": 3, "word": "imminent", "reason": "x"}
FULL = lambda sid: {"ok": True, "strategy_id": sid, "spot": {"bars": [1] * 5000}, "legs_bars": {"a": [1] * 5000},   # noqa: E731
                    "mtm": [1] * 900, "waiting": {"status": "holding", "headline": "h", "items": [], "meter": METER},
                    "live": {"active": True, "poll_ms": 2000, "now_ts": 1, "stale": False, "no_feed": [], "spot_ltp": 23300.0,
                             "spot_age_s": 0.0, "legs": [{"sym": "X"}], "forming_ts": 9}}
FIND = {}
LR.find_keys = lambda strategy=None, **k: (calls.append(("find", strategy)), FIND[strategy])[1]
LR.build_live_replay = lambda scope, key, fill=True, live=False: (calls.append(("trade", scope, key, fill, live)), FULL("TMA_V2"))[1]
LR.build_watch = lambda sid, fill=True, live=False: (calls.append(("watch", sid, fill, live)), FULL(sid))[1]

FIND["TMA_V2"] = {"ok": True, "key": "t|tma2_trades:2", "strategy_id": "TMA_V2", "nav": []}
out = R.strategy_meter(strategy="TMA_V2")
check("TC-MMTR-0019", calls[-1] == ("trade", "trades", "tma2_trades:2", True, True) and out["watch"] is False
      and out["waiting"]["meter"] == METER, "open position → the trade builder, live, same key as the Live chart")
check("TC-MMTR-0020", not ({"spot", "legs_bars", "mtm"} & set(out)) and set(out["live"]) == set(R.METER_LIVE_KEYS),
      f"slim: meter + checklist + live clock only ({sorted(out)})")
FIND["IC_V2"] = {"ok": True, "key": "a|ic2:7", "strategy_id": "IC_V2", "nav": []}
R.strategy_meter(strategy="IC_V2")
check("TC-MMTR-0021", calls[-1][:3] == ("trade", "analytics", "ic2:7"), "Analytics row → analytics scope")
FIND["VET_V1"] = {"ok": True, "key": "w|VET_V1", "strategy_id": "VET_V1", "watch": True, "nav": []}
out = R.strategy_meter(strategy="VET_V1")
check("TC-MMTR-0022", calls[-1] == ("watch", "VET_V1", True, True) and out["watch"] is True, "no open position → watch builder")
FIND["NOPE"] = {"ok": False, "error": "no matching trade", "status": 404}
try:
    R.strategy_meter(strategy="NOPE")
    code = None
except HTTPException as e:
    code = e.status_code
check("TC-MMTR-0023", code == 404, f"unknown strategy → 404 ({code})")
LR.build_watch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    R.strategy_meter(strategy="VET_V1")
    code = None
except HTTPException as e:
    code = e.status_code
check("TC-MMTR-0024", code == 500, f"builder error → 500, logged ({code})")
paths = [r.path for r in R.router.routes]
src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)), "..", "api_server.py"), encoding="utf-8").read()
check("TC-MMTR-0025", "/api/replay/meter" in paths
      and "include_router(live_replay_router, dependencies=[Depends(_require_admin_ui)])" in src,
      "on the replay router, which api_server mounts behind the admin wall")

print(f"\n{'FAIL' if fails else 'OK'}: {n[0] - len(fails)}/{n[0]} checks")
sys.exit(1 if fails else 0)

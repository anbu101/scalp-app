"""
test_strategy_visibility.py — OFF_HIDDEN_20261117 (TD-009) + CRYPTO_ADMIN_20261117

GET /api/strategies/visibility: hidden = mode OFF AND flat AND no trade today;
fail-open on a degraded config read or an unreadable fleet feed.
Crypto Lab backend routes are admin-gated.

Run:  cd backend && PYTHONPATH=. python3 app/api/test_strategy_visibility.py
Route function called directly (no TestClient). Seams: the config read
(execution_modes.resolve_execution_plan) and the fleet feed (get_fleet_today).
"""
import ast
import os
import sys

import app.api.strategy_visibility_routes as V
import app.api.fleet_today_routes as F
from app.risk import execution_modes as EM

fails = []
N = [0]


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


PLAN = {}            # sid -> (mode, degraded)
FEED = {"ok": True, "strategies": []}
EM.resolve_execution_plan = lambda sid: PLAN.get(sid, ("PAPER", False))
F.get_fleet_today = lambda: FEED


def rows(*spec):
    return [{"id": s, "state": st} for s, st in spec]


def vis():
    return V.get_strategy_visibility()


# TC-OFFH-0001 OFF, flat, no trade today → hidden
PLAN.clear(); PLAN.update({"SCALP_V1": ("OFF", False)})
FEED.update(ok=True, strategies=[])
r = vis()
check("TC-OFFH-0001", r["hidden"] == ["SCALP_V1"] and r["strategies"]["SCALP_V1"]["hidden"],
      "OFF + flat + no trade today → hidden")

# TC-OFFH-0002 OFF but holds an open (carried) position → visible
FEED.update(strategies=rows(("SCALP_V1", "OPEN")))
r = vis()
check("TC-OFFH-0002", r["hidden"] == [] and r["strategies"]["SCALP_V1"]["open"],
      "OFF with an open position → visible (never hide a live position)")

# TC-OFFH-0003 OFF, flat, but traded today → visible
FEED.update(strategies=rows(("SCALP_V1", "CLOSED")))
r = vis()
check("TC-OFFH-0003", r["hidden"] == [] and r["strategies"]["SCALP_V1"]["traded_today"],
      "OFF, flat, traded today → visible until tomorrow")

# TC-OFFH-0004 PAPER / LIVE / PAPER_LIVE idle → visible
PLAN.clear(); PLAN.update({"TMA_V2": ("LIVE", False), "VET_V1": ("PAPER_LIVE", False), "TSG_V1": ("PAPER", False)})
FEED.update(strategies=[])
r = vis()
check("TC-OFFH-0004", r["hidden"] == [], "PAPER / LIVE / PAPER+LIVE strategies with nothing today → visible")

# TC-OFFH-0005 degraded config read is never OFF (fail-open)
PLAN.clear(); PLAN.update({"IC_V2": ("PAPER", True)})
r = vis()
check("TC-OFFH-0005", r["hidden"] == [] and r["strategies"]["IC_V2"]["mode"] == "PAPER",
      "unreadable config → treated as PAPER, visible")

# TC-OFFH-0006 fleet feed fails, or one strategy unreadable → nothing hidden
PLAN.clear(); PLAN.update({"SCALP_V1": ("OFF", False), "BB_V1": ("OFF", False)})
FEED.update(ok=False, strategies=[])
r1 = vis()
FEED.update(ok=True, strategies=[{"id": "IC_V2", "state": "IDLE", "error": True}])
r2 = vis()
def _boom():
    raise RuntimeError("db locked")
F.get_fleet_today = _boom
r3 = vis()
F.get_fleet_today = lambda: FEED
check("TC-OFFH-0006", r1["hidden"] == [] and r2["hidden"] == [] and r3["hidden"] == [] and r1["ok"] is False,
      "fleet feed down / a strategy unreadable / feed throws → nothing hidden (fail-open)")

# TC-OFFH-0007 covers every fleet strategy incl. LVB_V1 (the Levels page)
FEED.update(ok=True, strategies=[])
PLAN.clear(); PLAN.update({"LVB_V1": ("OFF", False)})
r = vis()
check("TC-OFFH-0007", set(r["strategies"]) == set(F.FLEET_IDS) and r["hidden"] == ["LVB_V1"],
      "answers for every fleet strategy; LVB_V1 OFF → hidden (drives the Levels page)")

# TC-CRYPTO-0005 Crypto Lab backend routes are admin-gated
src = open(os.path.join(os.path.dirname(__file__), "..", "api_server.py"), encoding="utf-8").read()
tree = ast.parse(src)
flag = [n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "CRYPTO_LAB_OPEN_TO_ALL" for t in n.targets)
        and isinstance(n.value, ast.Constant)]
fe = open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend", "src", "hooks", "usePageVisibility.js"),
          encoding="utf-8").read() if os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend")) else None
check("TC-CRYPTO-0005", flag == [False] and (fe is None or "export const CRYPTO_LAB_OPEN_TO_ALL = false;" in fe),
      f"CRYPTO_LAB_OPEN_TO_ALL is False in api_server.py (crypto routes mounted behind the admin gate) "
      f"and in the frontend ({flag})")

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)

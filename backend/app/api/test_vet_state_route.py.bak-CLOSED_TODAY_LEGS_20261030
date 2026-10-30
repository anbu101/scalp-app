# backend/app/api/test_vet_state_route.py
#
# ── VET_PANEL_V2_20260921 ── behavioural suite for GET /api/vet/state.
# Calls the route function directly (no TestClient — build-Mac httpx pin).
# Every timestamp is on the REAL epoch clock (TSG_BANK_FIX1 scar): the
# "today" boundary and the LTP freshness window are compared against
# time.time(), so toy integers would prove nothing.
#
#   cd backend && PYTHONPATH=$PWD python3 app/api/test_vet_state_route.py

import os
import sys
import tempfile
import time

import app.api.vet_state_routes as R
import app.engine.vet.vet_selection_loop as L
from app.engine.vet.vet_common import VetRepo
from app.license import license_state
from app.marketdata.ltp_store import LTPStore

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = int(time.time())
DAY0 = R._ist_day_start(NOW)
SYM, WSYM = "NIFTYTEST23400PE", "NIFTYTEST22900PE"

tmp = tempfile.mkdtemp(prefix="vet_state_")
repo = VetRepo(os.path.join(tmp, "app.db"))
repo.ensure_schema()
R._repo = lambda: repo                      # route reads THIS db
_real_level = license_state.ui_level


def set_admin(on):
    license_state.ui_level = (lambda: "admin") if on else (lambda: "standard")


def leg(gid, role, sym, direction, entry_ts, px, **kw):
    row = {"group_id": gid, "leg_role": role, "mode": "PAPER", "direction": direction,
           "tradingsymbol": sym, "token": 1, "instrument_type": "PE", "strike": 23400.0,
           "expiry": "2026-09-22", "qty": 650, "lots": 10, "lot_size": 65,
           "entry_ts": entry_ts, "entry_price": px, "entry_order_id": "P1",
           "signal_bar_ts": entry_ts - 300, "condition": 1, "leg_action": "SELL"}
    row.update(kw)
    row["id"] = repo.insert_leg(row)
    return row


class FakeMgr:
    mode = "PAPER"; frozen = False; freeze_reason = None; wants_wing = False
    _day_entries = 1

    def __init__(self, pos, quote_fn):
        self.cfg = {"leg_action": "SELL", "eod_square": False, "_spot": 23364.85,
                    "quantity": {"lots": 10, "lot_size": 65}, "max_trades_per_day": 3}
        self.pos, self.quote_fn = pos, quote_fn


class FakeEng:
    def status(self):
        return {"frozen": False, "freeze_reason": None, "warmup_sessions": 10,
                "warmup_required": 10, "warmup_ok": True, "bars_today": 80,
                "last_bar_ts": NOW - NOW % 300 - 300}

    def latest_signal(self):
        return {"bar_ts": NOW - 600, "condition": 1, "in_range": False,
                "dir_trend": 1, "spot": 23360.0, "last1m_ts": NOW - 360, "warmup_ok": True}


def clear_ltp():
    with LTPStore._lock:
        LTPStore._prices.pop(SYM, None); LTPStore._timestamps.pop(SYM, None)


print("── 1. IST day boundary on the real clock")
check("day start <= now < day start + 24h", DAY0 <= NOW < DAY0 + 86400, f"{DAY0} {NOW}")
check("day start is 00:00 IST", (DAY0 + 5 * 3600 + 1800) % 86400 == 0)

print("── 2. loop NOT up, carried SHORT row in the DB (this morning's screenshot)")
L._manager = None; L._engine = None
set_admin(True); clear_ltp()
main = leg("g_open", "MAIN", SYM, "SHORT", DAY0 - 3 * 86400 + 36000, 144.6)
s = R.vet_state()
p = s["position"]
check("payload ok, running False", s["ok"] and s["running"] is False)
check("position surfaced from DB", p is not None and s["position_source"] == "DB")
check("symbol / entry / qty / lots", p and (p["symbol"], p["entry"], p["qty"], p["lots"]) == (SYM, 144.6, 650, 10))
check("carried flag true for a prior-day entry", p and p["carried"] is True)
check("expiry normalised to ISO date", p and p["expiry"] == "2026-09-22")
check("no publisher, no quote_fn -> mark None (never invented)", p and p["mark"] is None and p["mark_src"] is None)

print("── 3. manager up: mark source ladder")
mgr = FakeMgr({"group_id": "g_open", "side": "PE", "main": main, "wing": None}, lambda sym: 89.85)
L._manager, L._engine = mgr, FakeEng()
s = R.vet_state(); p = s["position"]
check("running True, source MANAGER", s["running"] and s["position_source"] == "MANAGER")
check("QUOTE_1M when LTPStore is empty", p["mark"] == 89.85 and p["mark_src"] == "QUOTE_1M")
LTPStore.update(SYM, 88.10)
p = R.vet_state()["position"]
check("fresh LTPStore print wins -> LTP", p["mark"] == 88.10 and p["mark_src"] == "LTP")
with LTPStore._lock:
    LTPStore._timestamps[SYM] = time.time() - 3600          # an hour-old print
p = R.vet_state()["position"]
check("STALE LTPStore print never outranks VET's own quote", p["mark"] == 89.85 and p["mark_src"] == "QUOTE_1M")
mgr.quote_fn = lambda sym: None
p = R.vet_state()["position"]
check("stale print is the last resort, labelled", p["mark"] == 88.10 and p["mark_src"] == "LTP_STALE")
mgr.quote_fn = lambda sym: (_ for _ in ()).throw(RuntimeError("boom"))
check("quote_fn raising never breaks the route", R.vet_state()["ok"] is True)
mgr.quote_fn = lambda sym: 89.85
mtm = (p["entry"] - 89.85) * p["qty"]
check("screenshot arithmetic: (144.6-89.85)*650 = 35,587.5", abs(mtm - 35587.5) < 1e-6, str(mtm))

print("── 4. masking (Phase 2b): the signal is admin-only, fails closed")
s = R.vet_state()
check("admin sees regime + entry condition", s["signal"] and s["signal"]["condition"] == 1 and s["position"].get("condition") == 1)
set_admin(False)
s = R.vet_state()
check("standard: no signal block", s["signal"] is None)
check("standard: no condition on the position", "condition" not in s["position"])
check("standard still gets position + mark", s["position"]["mark"] == 89.85)

print("── 5. closed groups: exit-timestamp 'today', wing folded in")
y = leg("g_yday", "MAIN", "NIFTYTESTY", "SHORT", DAY0 - 90000, 100.0)
repo.close_leg(y["id"], exit_ts=DAY0 - 1, exit_price=60.0, exit_reason="FLIP")
t_main = leg("g_today", "MAIN", "NIFTYTESTT", "SHORT", DAY0 - 90000, 120.0)
t_wing = leg("g_today", "WING", WSYM, "LONG", DAY0 - 90000, 8.0, leg_action="BUY")
repo.close_leg(t_main["id"], exit_ts=DAY0 + 1, exit_price=100.0, exit_reason="SIGNAL_EXIT")
repo.close_leg(t_wing["id"], exit_ts=DAY0 + 1, exit_price=5.0, exit_reason="SIGNAL_EXIT")
import sqlite3
with sqlite3.connect(repo.db_path) as c:                    # independent of close_leg's own pnl math
    c.execute("UPDATE vet_trades SET pnl=26000, charges=400, net_pnl=25600 WHERE id=?", (y["id"],))
    c.execute("UPDATE vet_trades SET pnl=13000, charges=300, net_pnl=12700 WHERE id=?", (t_main["id"],))
    c.execute("UPDATE vet_trades SET pnl=-1950, charges=100, net_pnl=-2050 WHERE id=?", (t_wing["id"],))
s = R.vet_state()
by = {g["group_id"]: g for g in s["closed"]}
check("both closed groups listed, open one excluded", set(by) == {"g_yday", "g_today"})
check("exit 1s BEFORE IST midnight is not today", by["g_yday"]["today"] is False)
check("exit 1s AFTER IST midnight is today (entered yesterday)", by["g_today"]["today"] is True)
check("wing folded into the position net", by["g_today"]["net"] == 10650.0 and by["g_today"]["has_wing"])
check("today_net counts today's exits only", s["today_net"] == 10650.0, str(s["today_net"]))
check("closed_today counts positions, not legs", s["day"]["closed_today"] == 1)
check("standard licence: reasons masked", all(g["reason"] == "CLOSED" for g in s["closed"]))
set_admin(True)
check("admin: real reasons", {g["reason"] for g in R.vet_state()["closed"]} == {"FLIP", "SIGNAL_EXIT"})

print("── 6. flat + frozen engine")
mgr.pos = None
with sqlite3.connect(repo.db_path) as c:
    c.execute("UPDATE vet_trades SET status='CLOSED', exit_ts=?, exit_price=90, net_pnl=1 WHERE id=?", (NOW, main["id"]))
s = R.vet_state()
check("manager up + flat -> no DB fallback, position None", s["position"] is None and s["position_source"] is None)
FakeEng.status = lambda self: {"frozen": True, "freeze_reason": "prefix guard", "warmup_ok": True}
s = R.vet_state()
check("engine freeze surfaces with its reason", s["frozen"] and s["freeze_reason"] == "prefix guard")

print("── 7. v1 endpoints untouched")
check("/status still answers", "manager" in R.vet_status())
check("/trades still answers", "trades" in R.vet_trades_route(limit=10, status="all"))

license_state.ui_level = _real_level
L._manager = None; L._engine = None
print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")

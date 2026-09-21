# backend/app/api/test_ic_closed_route.py
#
# ── CLOSED_RECENT_20260921 ── behavioural suite for GET /api/ic/{sid}/closed.
# Calls the route function directly (no TestClient — build-Mac httpx pin).
# Timestamps are on the REAL epoch clock: "today" is compared to time.time().
#
#   cd backend && PYTHONPATH=$PWD python3 app/api/test_ic_closed_route.py

import os
import sqlite3
import sys
import tempfile
import time

import app.api.ic_state_routes as R
from app.license import license_state

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = int(time.time())
DAY0 = R._ic_day_start(NOW)
db = os.path.join(tempfile.mkdtemp(prefix="ic_closed_"), "app.db")
R._ic_db_path = lambda: db
_real_level = license_state.ui_level


def set_admin(on):
    license_state.ui_level = (lambda: "admin") if on else (lambda: "standard")


with sqlite3.connect(db) as c:
    c.executescript("""
    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, symbol TEXT,
      trade_direction TEXT, qty INTEGER, lots INTEGER, entry_price REAL, exit_price REAL,
      exit_reason TEXT, entry_time INTEGER, exit_time INTEGER, state TEXT,
      pnl_value REAL, total_charges REAL, net_pnl REAL, group_id TEXT, trade_class TEXT);
    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, symbol TEXT, trade_direction TEXT,
      qty INTEGER, entry_price REAL, exit_price REAL, exit_reason TEXT, entry_time INTEGER,
      exit_time INTEGER, state TEXT, group_id TEXT, trade_class TEXT);
    """)


def paper(sid, gid, cls, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650):
    state = "OPEN" if ext_ts is None else "CLOSED"
    g = c_ = n = None
    if ext is not None:
        g = (ent - ext) * qty if direction == "SHORT" else (ext - ent) * qty
        c_ = 50.0
        n = g - c_
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{gid}{cls}", sid, sym, direction, qty, qty // 65, ent, ext, reason,
                   ent_ts, ext_ts, state, g, c_, n, gid, cls))


def live(sid, gid, cls, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{gid}{cls}", sid, sym, direction, qty, ent, ext, reason, ent_ts, ext_ts,
                   "PROTECTED" if ext_ts is None else "CLOSED", gid, cls))


def condor(book, sid, gid, ent_ts, exits, prices):
    """prices: {cls: (sym, dir, entry, exit)}, exits: {cls: (reason, ts)}"""
    for cls, (sym, d, ent, ext) in prices.items():
        reason, ts = exits.get(cls, (None, None))
        book(sid, gid, cls, sym, d, ent, ext if ts else None, reason, ent_ts, ts)


LEGS = {"L1": ("NIFTY2692223400CE", "SHORT", 63.5, 40.0), "L2": ("NIFTY2692223400PE", "SHORT", 85.0, 60.0),
        "L3": ("NIFTY2692223750CE", "LONG", 2.8, 1.0),    "L4": ("NIFTY2692222850PE", "LONG", 3.75, 1.5)}
ALL_NEXT = lambda ts: {k: ("NEXT_OPEN", ts) for k in LEGS}

print("── 1. helpers")
check("IST day start on the real clock", DAY0 <= NOW < DAY0 + 86400 and (DAY0 + 19800) % 86400 == 0)
check("weekly symbol shortens", R._ic_short("NIFTY2692223400CE") == "23400CE")
check("monthly symbol shortens", R._ic_short("NIFTY26SEP23400PE") == "23400PE")
check("unparseable symbol comes back whole", R._ic_short("WEIRD") == "WEIRD")

print("── 2. paper condor entered yesterday, NEXT_OPEN today → counts TODAY")
set_admin(True)
condor(paper, "IC_V2", "gA", DAY0 - 50000, ALL_NEXT(DAY0 + 60), LEGS)
s = R.get_ic_closed("IC_V2")
g = s["groups"][0] if s["groups"] else {}
check("one group, not four legs", s["ok"] and len(s["groups"]) == 1 and g.get("legs") == 4)
check("label = shorts, sub = wings", g.get("label") == "23400CE / 23400PE" and g.get("sub") == "wings 23750CE / 22850PE", str(g))
check("credit = 63.5+85-2.8-3.75 = 141.95", g.get("entry") == 141.95, str(g.get("entry")))
check("debit = 40+60-1-1.5 = 97.5", g.get("exit") == 97.5, str(g.get("exit")))
exp_gross = (141.95 - 97.5) * 650
check("gross = (credit-debit)*qty", abs(g.get("gross", 0) - exp_gross) < 0.01, f"{g.get('gross')} vs {exp_gross}")
check("net = gross - booked charges (4×50)", abs(g.get("net", 0) - (exp_gross - 200)) < 0.01)
check("today by EXIT ts, single reason collapsed", g.get("today") is True and g.get("reason") == "NEXT_OPEN")
check("today_net / closed_today", abs(s["today_net"] - (exp_gross - 200)) < 0.01 and s["closed_today"] == 1)
check("lots from the short legs", g.get("lots") == 10)

print("── 3. partially closed condor is NOT a closed position")
condor(paper, "IC_V2", "gB", DAY0 + 100, {"L1": ("SL", DAY0 + 500)}, LEGS)
s = R.get_ic_closed("IC_V2")
check("group with open legs excluded", [x["group_id"] for x in s["groups"]] == ["PAPER:gA"])

print("── 4. mixed reasons, exit 1s before IST midnight → not today")
ex = ALL_NEXT(DAY0 - 1); ex["L1"] = ("SL", DAY0 - 4000)
condor(paper, "IC_V2", "gC", DAY0 - 90000, ex, LEGS)
s = R.get_ic_closed("IC_V2")
gc = next(x for x in s["groups"] if x["group_id"] == "PAPER:gC")
check("group exit = LAST leg exit; 1s before midnight is not today", gc["exit_ts"] == DAY0 - 1 and gc["today"] is False)
check("reason summary counts legs", gc["reason"] == "NEXT_OPEN×3 · SL×1", gc["reason"])
check("newest exit first", [x["group_id"] for x in s["groups"]] == ["PAPER:gA", "PAPER:gC"])
check("yesterday's net not in today_net", s["closed_today"] == 1)

print("── 5. sid isolation + unknown sid fails closed")
condor(paper, "IC_V1", "gV1", DAY0 + 10, ALL_NEXT(DAY0 + 20), LEGS)
check("IC_V1 sees only its own group", [x["group_id"] for x in R.get_ic_closed("IC_V1")["groups"]] == ["PAPER:gV1"])
check("IC_V2 unchanged by IC_V1 rows", len(R.get_ic_closed("IC_V2")["groups"]) == 2)
try:
    R.get_ic_closed("IC_V9"); ok = False
except Exception as e:
    ok = getattr(e, "status_code", None) == 404
check("unknown sid → 404", ok)

print("── 6. LIVE book: P&L derived, charges modelled, missing exit price → approx")
condor(live, "IC_V1", "gL", DAY0 + 30, ALL_NEXT(DAY0 + 90), LEGS)
s = R.get_ic_closed("IC_V1")
gl = next(x for x in s["groups"] if x["group_id"] == "LIVE:gL")
check("live gross is direction-aware", abs(gl["gross"] - exp_gross) < 0.01, str(gl["gross"]))
check("live charges modelled (>0) and net = gross - charges", gl["charges"] > 0 and abs(gl["net"] - (gl["gross"] - gl["charges"])) < 0.01)
check("exact live group not flagged approx", gl["approx"] is False and gl["book"] == "LIVE")
lp = dict(LEGS); lp["L1"] = ("NIFTY2692223400CE", "SHORT", 63.5, None)
for cls, (sym, d, ent, ext) in lp.items():
    live("IC_V1", "gM", cls, sym, d, ent, ext, "BROKER_EXIT" if ext is None else "NEXT_OPEN", DAY0 + 40, DAY0 + 95)
gm = next(x for x in R.get_ic_closed("IC_V1")["groups"] if x["group_id"] == "LIVE:gM")
check("leg without exit price → approx, debit withheld", gm["approx"] is True and gm["exit"] is None)

print("── 7. adjustment legs")
adj = dict(LEGS); adj["L1A"] = ("NIFTY2692223500CE", "LONG", 20.0, 30.0)
condor(paper, "IC_V2", "gD", DAY0 + 200, {k: ("EOD", DAY0 + 300) for k in adj}, adj)
gd = next(x for x in R.get_ic_closed("IC_V2")["groups"] if x["group_id"] == "PAPER:gD")
check("adj leg in sub + folded into net, not into the label", gd["sub"].endswith("+1 adj") and gd["label"] == "23400CE / 23400PE" and gd["legs"] == 5)

print("── 8. masking + limit + resilience")
set_admin(False)
check("standard licence: reasons masked", all(x["reason"] == "CLOSED" for x in R.get_ic_closed("IC_V2")["groups"]))
set_admin(True)
check("limit honoured", len(R.get_ic_closed("IC_V2", limit=1)["groups"]) == 1)
check("today_net independent of limit", R.get_ic_closed("IC_V2", limit=1)["today_net"] == R.get_ic_closed("IC_V2")["today_net"])
R._ic_db_path = lambda: os.path.join(os.path.dirname(db), "missing", "nope.db")
r = R.get_ic_closed("IC_V2")
check("unreadable DB never raises", r["groups"] == [] and "today_net" in r)
R._ic_db_path = lambda: db

with sqlite3.connect(db) as c:
    c.execute("ALTER TABLE trades RENAME COLUMN trade_class TO leg_tag")
r = R.get_ic_closed("IC_V2")
check("schema drift is SURFACED, paper book still served", r.get("warnings") and len(r["groups"]) == 3, str(r.get("warnings")))

print("── 9. existing endpoints untouched")
st = R.get_ic_state("IC_V2")
check("/state still answers with its keys", {"mode", "engine_up", "group", "latched_today"} <= set(st))

license_state.ui_level = _real_level
print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")

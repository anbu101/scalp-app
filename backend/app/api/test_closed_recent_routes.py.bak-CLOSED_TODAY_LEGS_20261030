# backend/app/api/test_closed_recent_routes.py
#
# ── CLOSED_RECENT_FLEET_20260921 ── behavioural suite for
# GET /api/closed/{strategy_id}. Route function called directly (no
# TestClient — build-Mac httpx pin). Timestamps on the REAL epoch clock.
#
#   cd backend && PYTHONPATH=$PWD python3 app/api/test_closed_recent_routes.py

import os
import sqlite3
import sys
import tempfile
import time

import app.api.closed_recent_routes as R
import app.api.trade_history_routes as H
from app.license import license_state

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = int(time.time())
DAY0 = R._day_start(NOW)
db = os.path.join(tempfile.mkdtemp(prefix="closed_fleet_"), "app.db")
R._db_path = lambda: db
H.DB_PATH = db                                  # the V3/V5 live mappers open their own conn
R.CACHE_TTL_S = 0                               # every call re-reads in this suite
_real_level = license_state.ui_level
license_state.ui_level = lambda: "admin"

with sqlite3.connect(db) as c:
    c.executescript("""
    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, trade_mode TEXT, symbol TEXT,
      trade_direction TEXT, qty INTEGER, lots INTEGER, entry_price REAL, exit_price REAL,
      exit_reason TEXT, entry_time INTEGER, exit_time INTEGER, state TEXT,
      pnl_value REAL, total_charges REAL, net_pnl REAL, group_id TEXT, trade_class TEXT);
    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, slot TEXT, symbol TEXT, trade_direction TEXT,
      qty INTEGER, entry_price REAL, exit_price REAL, exit_reason TEXT, entry_time INTEGER,
      exit_time INTEGER, state TEXT);
    CREATE TABLE scalp_v3_trades (v3_trade_id TEXT, strategy_name TEXT, paper INTEGER, hedge_symbol TEXT,
      hedge_side TEXT, hedge_qty INTEGER, hedge_entry_price REAL, hedge_sl REAL, hedge_gtt_id TEXT,
      entry_time INTEGER, exit_time INTEGER, exit_price REAL, exit_reason TEXT, realized_pnl REAL, state TEXT);
    """)
_n = [0]


def paper(sid, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650, mode="PAPER", cls=None, booked=True):
    _n[0] += 1
    g = ch = n = None
    if ext is not None and ext_ts and booked:
        g = (ent - ext) * qty if direction == "SHORT" else (ext - ent) * qty
        ch, n = 50.0, g - 50.0
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"p{_n[0]}", sid, mode, sym, direction, qty, qty // 65, ent, ext, reason, ent_ts, ext_ts,
                   "OPEN" if not ext_ts else "CLOSED", g, ch, n, None, cls))


def live(sid, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=65):
    _n[0] += 1
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"t{_n[0]}", sid, "CE_1", sym, direction, qty, ent, ext, reason, ent_ts, ext_ts,
                   "CLOSED" if ext_ts else "PROTECTED"))


def ids(sid, **kw):
    return [g["label"] for g in R.get_closed_recent(sid, **kw)["groups"]]


print("── 1. clock + ids")
check("IST day start on the real clock", DAY0 <= NOW < DAY0 + 86400 and (DAY0 + 19800) % 86400 == 0)
check("bad strategy id refused, never queried", R.get_closed_recent("x'; DROP")["ok"] is False)
check("unknown-but-valid id → empty, ok", R.get_closed_recent("NEW_V9")["groups"] == [])

print("── 2. single-leg LONG (ORB): booked charges, today by EXIT ts")
paper("ORB_V1", "NIFTY2692223400CE", "LONG", 180.0, 270.0, "TP", DAY0 + 100, DAY0 + 900)
paper("ORB_V1", "NIFTY2691523300PE", "LONG", 170.0, 150.0, "SL", DAY0 - 90000, DAY0 - 1)
paper("ORB_V1", "NIFTY2692223500CE", "LONG", 160.0, None, None, DAY0 + 1000, None)          # open
s = R.get_closed_recent("ORB_V1")
g0 = s["groups"][0]
check("open row excluded; newest exit first", [g["label"] for g in s["groups"]] == ["NIFTY2692223400CE", "NIFTY2691523300PE"])
check("net = booked net_pnl", g0["net"] == 90 * 650 - 50 and g0["charges"] == 50 and g0["approx"] is False)
check("kind L, reason, entry/exit", (g0["kind"], g0["reason"], g0["entry"], g0["exit"]) == ("L", "TP", 180.0, 270.0))
check("exit 1s before IST midnight is NOT today", s["groups"][1]["today"] is False and g0["today"] is True)
check("today_net / closed_today", s["today_net"] == 90 * 650 - 50 and s["closed_today"] == 1)

print("── 3. SHORT sign + LIVE book from `trades` (gross only → modelled charges)")
live("SCALP_V1", "NIFTY2692223600CE", "SHORT", 100.0, 60.0, "TP", DAY0 + 50, DAY0 + 500)
paper("SCALP_V1", "NIFTY2692223200PE", "SHORT", 100.0, 130.0, "SL", DAY0 + 60, DAY0 + 600)
s = R.get_closed_recent("SCALP_V1")
lv = next(g for g in s["groups"] if g["book"] == "LIVE")
pp = next(g for g in s["groups"] if g["book"] == "PAPER")
check("live short: gross = (entry-exit)*qty", lv["gross"] == 40 * 65 and lv["kind"] == "S", str(lv["gross"]))
check("live charges modelled > 0, net = gross - charges, exact", lv["charges"] > 0 and abs(lv["net"] - (lv["gross"] - lv["charges"])) < 0.01 and not lv["approx"])
check("paper short loss is negative", pp["net"] == -30 * 650 - 50)
check("both books merged into one today_net", abs(s["today_net"] - (lv["net"] + pp["net"])) < 0.01)

print("── 4. paper_trades rows with trade_mode=LIVE (BRK/ORB/TSG live) tagged LIVE")
paper("BRK_V1", "NIFTY2692223400PE", "LONG", 180.0, 200.0, "TP", DAY0 + 10, DAY0 + 20, mode="LIVE")
check("book = LIVE", R.get_closed_recent("BRK_V1")["groups"][0]["book"] == "LIVE")

print("── 5. TSG basket: legs cluster by entry time; closed only when ALL legs are")
T0 = DAY0 - 86400 + 33360                      # yesterday 09:16 IST
for cls, sym, d, ent, ext in (("L1", "NIFTY2692223600CE", "SHORT", 85.0, 40.0), ("L2", "NIFTY2692223100PE", "SHORT", 85.0, 50.0),
                              ("L3", "NIFTY2692224000CE", "LONG", 5.0, 1.0), ("L4", "NIFTY2692222700PE", "LONG", 5.0, 2.0)):
    paper("TSG_V1", sym, d, ent, ext, "EOD", T0 + (0 if d == "LONG" else 20), T0 + 22200, cls=cls)
T1 = DAY0 + 33360                              # today 09:16 IST (may be in the future — irrelevant to grouping)
paper("TSG_V1", "NIFTY2692223650CE", "SHORT", 80.0, 90.0, "MTM_SL", T1, T1 + 3000, cls="L1")
paper("TSG_V1", "NIFTY2692223050PE", "SHORT", 80.0, None, None, T1 + 5, None, cls="L2")     # still open
s = R.get_closed_recent("TSG_V1")
check("yesterday's 4 legs = ONE row; today's half-open basket absent", len(s["groups"]) == 1 and s["groups"][0]["legs"] == 4, str(s["groups"]))
b = s["groups"][0]
check("label shorts / sub wings", b["label"] == "23600CE / 23100PE" and b["sub"] == "wings 24000CE / 22700PE", f"{b['label']} | {b['sub']}")
check("credit 85+85-5-5=160, debit 40+50-1-2=87", b["entry"] == 160.0 and b["exit"] == 87.0, f"{b['entry']} {b['exit']}")
check("net = (160-87)*650 - 4*50", b["net"] == 73 * 650 - 200 and b["reason"] == "EOD")
check("two baskets on different days never merge", b["today"] is False)

print("── 6. SCALP_V3 through the Trades-page mappers (hedge leg, gross-only store)")
with sqlite3.connect(db) as c:
    c.execute("INSERT INTO scalp_v3_trades VALUES ('v1','SCALP_V3',1,'NIFTY2692223400CE','CE',65,100,80,NULL,?,?,130,'TP',1950,'CLOSED')", (DAY0 + 5, DAY0 + 50))
    c.execute("INSERT INTO scalp_v3_trades VALUES ('v2','SCALP_V3',0,'NIFTY2692223400PE','PE',65,100,80,'g1',?,?,90,'SL',-650,'CLOSED')", (DAY0 + 6, DAY0 + 60))
    c.execute("INSERT INTO scalp_v3_trades VALUES ('v3','SCALP_V3',1,'NIFTY2692223300PE','PE',65,100,80,NULL,?,NULL,NULL,NULL,NULL,'OPEN')", (DAY0 + 7,))
s = R.get_closed_recent("SCALP_V3")
bk = {g["book"]: g for g in s["groups"]}
check("paper + live rows, open excluded", set(bk) == {"PAPER", "LIVE"} and len(s["groups"]) == 2, str(s))
check("symbol is the HEDGE; gross = realized_pnl", bk["PAPER"]["label"] == "NIFTY2692223400CE" and bk["PAPER"]["gross"] == 1950)
check("mapper's net==gross is NOT trusted: charges modelled", bk["PAPER"]["charges"] > 0 and bk["PAPER"]["net"] < 1950)
check("no mapper warnings", "warnings" not in s, str(s.get("warnings")))

print("── 7. honesty: missing exit price → approx; schema drift surfaced")
paper("HA_V1", "NIFTY2692223400CE", "LONG", 100.0, None, "BROKER_EXIT", DAY0 + 1, DAY0 + 2)
with sqlite3.connect(db) as c:
    c.execute("UPDATE paper_trades SET state='CLOSED' WHERE strategy_name='HA_V1'")
h = R.get_closed_recent("HA_V1")["groups"][0]
check("closed without exit price: net 0, flagged approx", h["approx"] is True and h["net"] == 0)
license_state.ui_level = lambda: "standard"
check("standard licence: reasons masked", all(g["reason"] == "CLOSED" for g in R.get_closed_recent("ORB_V1")["groups"]))
license_state.ui_level = lambda: "admin"
check("limit honoured; today_net independent of it", len(R.get_closed_recent("ORB_V1", limit=1)["groups"]) == 1
      and R.get_closed_recent("ORB_V1", limit=1)["today_net"] == R.get_closed_recent("ORB_V1")["today_net"])
with sqlite3.connect(db) as c:
    c.execute("ALTER TABLE trades RENAME COLUMN slot TO slot_x")
r = R.get_closed_recent("SCALP_V1")
check("renamed column is SURFACED; the other book still served", r.get("warnings") and len(r["groups"]) == 1, str(r.get("warnings")))
R._db_path = lambda: os.path.join(os.path.dirname(db), "missing", "nope.db")
r = R.get_closed_recent("ORB_V1")
check("unreadable DB never raises", r["groups"] == [] and r["ok"] is False)
R._db_path = lambda: db

print("── 8. cache: same day reuses, never across the masking boundary")
R.CACHE_TTL_S = 60
R._cache.clear()
a = R.get_closed_recent("ORB_V1")
paper("ORB_V1", "NIFTY2692223450CE", "LONG", 100.0, 110.0, "TP", DAY0 + 3000, DAY0 + 3100)
check("within TTL the cached read is served", len(R.get_closed_recent("ORB_V1")["groups"]) == len(a["groups"]))
license_state.ui_level = lambda: "standard"
check("masking applied AFTER the cache (admin read must not leak)", all(g["reason"] == "CLOSED" for g in R.get_closed_recent("ORB_V1")["groups"]))
license_state.ui_level = lambda: "admin"
check("...and the cached copy itself was not mutated", R.get_closed_recent("ORB_V1")["groups"][0]["reason"] == "TP")

license_state.ui_level = _real_level
print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")

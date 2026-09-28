# backend/app/api/test_fleet_today_routes.py
#
# ── DASH_MODERN_20260925 ── behavioural suite for GET /api/fleet/today and
# the MTM sampler. Route function called directly (no TestClient — build-Mac
# httpx pin). Timestamps on the REAL epoch clock; LTPStore, config mode,
# licence and the TSG stop are stubbed at the module's seams.
#
#   cd backend && PYTHONPATH=$PWD python3 app/api/test_fleet_today_routes.py

import os
import sqlite3
import sys
import tempfile
import time

import app.api.closed_recent_routes as CR
import app.api.trade_history_routes as H
import app.api.fleet_today_routes as F
import app.api.app_settings_api as S
from app.license import license_state

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = int(time.time())
DAY0 = CR._day_start(NOW)
db = os.path.join(tempfile.mkdtemp(prefix="fleet_today_"), "app.db")
CR._db_path = lambda: db
H.DB_PATH = db
CR.CACHE_TTL_S = 0
F.CACHE_TTL_S = 0
MODES = {}
LTPS = {}
DENY = set()
F._mode = lambda sid: MODES.get(sid, "PAPER")
F._ltp = lambda sym: LTPS.get(sym)
F._allowed = lambda sid: sid not in DENY
F._stop_for = lambda sid, book: (-35000.0 if sid == "TSG_V1" else None)
F._market_open = lambda: False
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
    CREATE TABLE tma2_trades (id INTEGER PRIMARY KEY, mode TEXT, status TEXT, group_id TEXT, direction TEXT,
      tradingsymbol TEXT, instrument_type TEXT, token INTEGER, qty INTEGER, entry_ts INTEGER, entry_price REAL,
      sl REAL, tp REAL, exit_ts INTEGER, exit_price REAL, exit_reason TEXT, pnl REAL, charges REAL, net_pnl REAL,
      sell_gtt_id TEXT);
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


def tma2(direction, sym, ent, ent_ts, qty=650, status="OPEN"):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO tma2_trades (mode,status,group_id,direction,tradingsymbol,instrument_type,qty,entry_ts,entry_price)"
                  " VALUES ('PAPER',?,?,?,?,?,?,?,?)", (status, "g1", direction, sym, sym[-2:], qty, ent_ts, ent))


def feed():
    F.CACHE.clear()
    return F.get_fleet_today()


def row(out, sid, book="PAPER"):
    return next((r for r in out["strategies"] if r["id"] == sid and r["book"] == book), None)


print("── 1. clock + empty fleet")
check("minute-of-day on the real clock", 0 <= F._now_min(NOW) < 1440 and F._hhmm(NOW).count(":") == 1)
g = time.gmtime(NOW + F.IST_OFF)
check("day key is the IST date", F._day_key(NOW) == f"{g.tm_year:04d}-{g.tm_mon:02d}-{g.tm_mday:02d}")
o = feed()
check("ok, nothing traded → no strategy rows", o["ok"] and o["strategies"] == [], o.get("error"))
check("every allowed strategy listed idle once", sorted(x["id"] for x in o["idle"]) == sorted(F.FLEET_IDS))
check("fleet 14, active 0", o["fleet"] == 14 and o["active"] == 0)
check("per-book totals present and zero", o["totals"]["LIVE"]["gross"] == 0 and o["totals"]["PAPER"]["gross"] == 0)
check("session bounds 09:15 → 15:40", o["session"]["open_min"] == 555 and o["session"]["close_min"] == 940)

print("── 2. TSG_V1 paper strangle: 4 open legs, live marks")
E = DAY0 + 9 * 3600 + 16 * 60                         # 09:16 today
for sym, d, ent, ltp in [("NIFTY26SEP23150CE", "SHORT", 110.10, 97.90), ("NIFTY26SEP23150PE", "SHORT", 117.95, 117.75),
                         ("NIFTY26SEP23650CE", "LONG", 7.40, 5.35), ("NIFTY26SEP22500PE", "LONG", 7.70, 7.60)]:
    paper("TSG_V1", sym, d, ent, None, None, E, None, cls="L")
    LTPS[sym] = ltp
o = feed()
t = row(o, "TSG_V1")
check("row present, OPEN, 4 legs, not carried", t and t["state"] == "OPEN" and t["open_legs"] == 4 and not t["carried"])
check("unrealised = Σ (entry−ltp)×qty on shorts, (ltp−entry)×qty on longs = 6662.5",
      t and abs(t["unrealised"] - 6662.5) < 1e-6 and t["realised"] == 0 and abs(t["gross"] - 6662.5) < 1e-6, t and t["unrealised"])
check("stop from the seam, risk_to_stops = gross − stop", t and t["stop"] == -35000.0 and abs(o["totals"]["PAPER"]["risk_to_stops"] - 41662.5) < 1e-6)
check("last event: entered 4 legs at 09:16", t and t["last_event"]["kind"] == "entry" and t["last_event"]["text"] == "Entered 4 legs at 09:16", t and t["last_event"])
check("no marks missing, not approx", t and t["ltp_missing"] == 0 and t["approx"] is False)
check("TSG no longer idle, active 1", "TSG_V1" not in [x["id"] for x in o["idle"]] and o["active"] == 1)
check("PAPER totals carry it, LIVE totals untouched", abs(o["totals"]["PAPER"]["gross"] - 6662.5) < 1e-6 and o["totals"]["LIVE"]["gross"] == 0)
check("peak = gross when no samples yet", t and t["peak"] == t["gross"] and t["path"] == [])

print("── 3. missing mark → counts 0, flagged approx")
del LTPS["NIFTY26SEP22500PE"]
t = row(feed(), "TSG_V1")
check("one leg unmarked → +65 (its −65 dropped), approx", t and abs(t["unrealised"] - 6727.5) < 1e-6 and t["ltp_missing"] == 1 and t["approx"] is True)
LTPS["NIFTY26SEP22500PE"] = 7.60

print("── 4. ORB_V1: two closed today (booked charges)")
paper("ORB_V1", "NIFTY26SEP23100CE", "LONG", 118.20, 117.62, "SL", DAY0 + 9 * 3600 + 31 * 60, DAY0 + 9 * 3600 + 52 * 60)
paper("ORB_V1", "NIFTY26SEP23100PE", "LONG", 104.30, 106.79, "TARGET", DAY0 + 9 * 3600 + 58 * 60, DAY0 + 10 * 3600 + 42 * 60)
o = feed()
r = row(o, "ORB_V1")
check("CLOSED, 2 trades, 0 open", r and r["state"] == "CLOSED" and r["closed_trades"] == 2 and r["open_legs"] == 0)
check("realised gross 1241.5 (−377 + 1618.5), charges 100, net 1141.5", r and abs(r["realised"] - 1241.5) < 1e-6 and r["charges"] == 100 and abs(r["net"] - 1141.5) < 1e-6, r and (r["realised"], r["charges"]))
check("last event = the TARGET exit at 10:42", r and r["last_event"]["text"] == "Exited 23100PE TARGET at 10:42", r and r["last_event"])
check("fleet PAPER gross = TSG unrealised + ORB realised", abs(o["totals"]["PAPER"]["gross"] - (6662.5 + 1241.5)) < 1e-6)
check("fleet PAPER closed_trades 2, open_legs 4", o["totals"]["PAPER"]["closed_trades"] == 2 and o["totals"]["PAPER"]["open_legs"] == 4)

print("── 5. yesterday's close is not today")
paper("BB_V1", "BANKNIFTY26SEP55000PE", "LONG", 121.40, 118.09, "SL", DAY0 - 6 * 3600, DAY0 - 3600)
o = feed()
check("BB_V1 stays idle", row(o, "BB_V1") is None and "BB_V1" in [x["id"] for x in o["idle"]])

print("── 6. TMA_V2 carried spread through the private-table mapper")
tma2("SELL", "NIFTY26SEP23300PE", 92.40, DAY0 - 86400 + 10 * 3600)
tma2("BUY", "NIFTY26SEP22900PE", 4.10, DAY0 - 86400 + 10 * 3600)
LTPS["NIFTY26SEP23300PE"] = 94.65
LTPS["NIFTY26SEP22900PE"] = 4.85
r = row(feed(), "TMA_V2")
check("OPEN, 2 legs, carried", r and r["state"] == "OPEN" and r["open_legs"] == 2 and r["carried"] is True)
check("since-entry P&L: short −1462.5 + hedge +487.5 = −975", r and abs(r["unrealised"] + 975) < 1e-6, r and r["unrealised"])
check("no stop for TMA (—)", r and r["stop"] is None)

print("── 7. LIVE book is its own row and its own total")
paper("TSG_V1", "NIFTY26SEP23200CE", "SHORT", 100.0, None, None, E, None, qty=65, mode="LIVE")
LTPS["NIFTY26SEP23200CE"] = 90.0
live("HA_V1", "NIFTY26SEP23000CE", "LONG", 50.0, 56.0, "TP", DAY0 + 10 * 3600, DAY0 + 10 * 3600 + 600)
MODES["TSG_V1"] = "PAPER_LIVE"
o = feed()
tl = row(o, "TSG_V1", "LIVE")
check("TSG_V1 has a LIVE row and a PAPER row", tl is not None and row(o, "TSG_V1", "PAPER") is not None)
check("LIVE row unrealised (100−90)×65 = 650, mode PAPER_LIVE", tl and abs(tl["unrealised"] - 650) < 1e-6 and tl["mode"] == "PAPER_LIVE")
hl = row(o, "HA_V1", "LIVE")
check("trades-table LIVE close counted today, gross 390", hl and hl["state"] == "CLOSED" and abs(hl["realised"] - 390) < 1e-6, hl and hl["realised"])
check("LIVE total = 650 + 390, PAPER total unchanged",
      abs(o["totals"]["LIVE"]["gross"] - 1040) < 1e-6 and abs(o["totals"]["PAPER"]["gross"] - (6662.5 + 1241.5 - 975)) < 1e-6,
      (o["totals"]["LIVE"]["gross"], o["totals"]["PAPER"]["gross"]))
check("active counts strategies, not rows (TSG once)", o["active"] == 4 and o["fleet"] == 14)

print("── 8. masking for standard licences, applied after the cache")
F.CACHE_TTL_S = 60
o_admin = feed()
license_state.ui_level = lambda: "standard"
o_std = F.get_fleet_today()                             # served from the cache
check("admin sees the reason", row(o_admin, "ORB_V1")["last_event"]["reason"] == "TARGET")
check("standard sees CLOSED in reason and text", row(o_std, "ORB_V1")["last_event"]["reason"] == "CLOSED"
      and "TARGET" not in row(o_std, "ORB_V1")["last_event"]["text"])
check("cached rows were not mutated", F.CACHE["rows"] and next(r for r in F.CACHE["rows"] if r["key"] == "ORB_V1:PAPER")["last_event"]["reason"] == "TARGET")
license_state.ui_level = lambda: "admin"
F.CACHE_TTL_S = 0

print("── 9. licence filter")
DENY.add("ORB_V1")
o = feed()
check("a strategy the licence excludes is absent from rows AND idle", row(o, "ORB_V1") is None and "ORB_V1" not in [x["id"] for x in o["idle"]] and o["fleet"] == 13)
DENY.clear()

print("── 10. sampler")
r1 = F.sample_now(now=NOW, force=False)
check("gated on the session when not forced", r1["sampled"] is False and r1["reason"] == "market closed")
r1 = F.sample_now(now=NOW, force=True)
check("forced sample writes every active row + both fleet keys", r1["sampled"] and r1["keys"] == 5 + 2, r1)
r2 = F.sample_now(now=NOW, force=True)
with sqlite3.connect(db) as c:
    n_rows = c.execute("SELECT COUNT(*) FROM fleet_mtm_samples").fetchone()[0]
    keys = sorted(k for (k,) in c.execute("SELECT DISTINCT key FROM fleet_mtm_samples"))
check("same minute twice → replaced, not duplicated", n_rows == 7, n_rows)
check("keys are <ID>:<BOOK> and __LIVE__/__PAPER__", keys == sorted(["TSG_V1:PAPER", "TSG_V1:LIVE", "ORB_V1:PAPER", "TMA_V2:PAPER", "HA_V1:LIVE", "__LIVE__", "__PAPER__"]), keys)
o = feed()
t = row(o, "TSG_V1")
check("route returns the row's path [[minute, mtm]]", t["path"] == [[F._now_min(NOW), round(t["gross"], 2)]], t["path"])
check("fleet paths per book", o["paths"]["PAPER"] == [[F._now_min(NOW), round(o["totals"]["PAPER"]["gross"], 2)]] and len(o["paths"]["LIVE"]) == 1)
check("peak from samples + now", t["peak"] == t["gross"] and o["totals"]["PAPER"]["peak"] == o["totals"]["PAPER"]["gross"])
with sqlite3.connect(db) as c:
    c.execute("INSERT INTO fleet_mtm_samples VALUES (?,?,?,?)", (F._day_key(NOW - 20 * 86400), "X:PAPER", 600, 1.0))
    c.execute("INSERT INTO fleet_mtm_samples VALUES (?,?,?,?)", (F._day_key(NOW - 2 * 86400), "X:PAPER", 600, 1.0))
    c.commit()
F.sample_now(now=NOW, force=True)
with sqlite3.connect(db) as c:
    old = c.execute("SELECT COUNT(*) FROM fleet_mtm_samples WHERE key='X:PAPER'").fetchone()[0]
check("retention: 20-day-old sample dropped, 2-day-old kept", old == 1, old)
F._market_open = lambda: True
F._last_sampled[0] = None
with sqlite3.connect(db) as c:
    c.execute("DELETE FROM fleet_mtm_samples")
    c.commit()
o = feed()
with sqlite3.connect(db) as c:
    n_rows = c.execute("SELECT COUNT(*) FROM fleet_mtm_samples").fetchone()[0]
check("route self-samples once per minute while the market is open", n_rows == 7 and o["session"]["market_open"] is True, n_rows)
feed()
with sqlite3.connect(db) as c:
    n2 = c.execute("SELECT COUNT(*) FROM fleet_mtm_samples").fetchone()[0]
check("…and not again in the same minute", n2 == 7, n2)
F._market_open = lambda: False
check("job entry point never raises", F.fleet_mtm_sample_job() is None)

print("── 11. one bad strategy never blanks the fleet")
_real = F._legs_for
F._legs_for = lambda sid, w: (_ for _ in ()).throw(RuntimeError("boom")) if sid == "HA_V1" else _real(sid, w)
o = feed()
check("HA_V1 reported in warnings, others intact", o["ok"] and any("HA_V1" in w for w in o.get("warnings", [])) and row(o, "TSG_V1") is not None)
F._legs_for = _real

print("── 12. app settings: dashboard_layout")
check("default modern", S._merge_defaults({})["dashboard_layout"] == "modern")
check("legacy honoured", S._merge_defaults({"dashboard_layout": "legacy"})["dashboard_layout"] == "legacy")
check("garbage → modern", S._merge_defaults({"dashboard_layout": "x"})["dashboard_layout"] == "modern")
check("model default modern", S.AppSettings().dashboard_layout == "modern")
check("theme untouched", S._merge_defaults({"theme": "arcade"})["theme"] == "arcade")

print("── 13. week / month P/L per book (FLEET_KPIS_PERIOD_20260928)")
check("week window = 7 calendar days ending today", F._week_from(DAY0) == DAY0 - 6 * 86400)
mf = F._month_from(DAY0)
gm, g0 = time.gmtime(mf + F.IST_OFF), time.gmtime(DAY0 + F.IST_OFF)
check("month window starts on the same calendar day last month at 00:00 IST",
      (gm.tm_hour, gm.tm_min) == (0, 0) and ((g0.tm_mon - gm.tm_mon) % 12 == 1) and gm.tm_mday <= g0.tm_mday, (gm.tm_mon, gm.tm_mday, g0.tm_mon, g0.tm_mday))
import calendar as _cal
_mar31 = _cal.timegm((2026, 3, 31, 0, 0, 0)) - F.IST_OFF
_g = time.gmtime(F._month_from(_mar31) + F.IST_OFF)
check("month clamp: 31 Mar → 28 Feb", (_g.tm_mon, _g.tm_mday) == (2, 28), (_g.tm_mon, _g.tm_mday))
o0 = feed()
w0, m0 = o0["totals"]["PAPER"]["week_net"], o0["totals"]["PAPER"]["month_net"]
check("week includes yesterday's BB_V1 close (not only today)", abs(w0 - (1141.5 - 2201.5)) < 1e-6, w0)
check("live book has its own week figure (HA_V1: +390 gross less modelled charges)", o0["totals"]["LIVE"]["week_trades"] == 1 and 0 < o0["totals"]["LIVE"]["week_net"] < 390, o0["totals"]["LIVE"])
paper("ORB_V1", "NIFTY26SEP23000CE", "LONG", 100.0, 100.5, "TARGET", DAY0 - 3 * 86400 + 36000, DAY0 - 3 * 86400 + 37800)   # +325 gross, +275 net, 3 days ago
paper("ORB_V1", "NIFTY26SEP23000PE", "LONG", 100.0, 101.0, "TARGET", DAY0 - 20 * 86400 + 36000, DAY0 - 20 * 86400 + 37800)  # +650 gross, +600 net, 20 days ago
paper("ORB_V1", "NIFTY26AUG23000PE", "LONG", 100.0, 102.0, "TARGET", DAY0 - 40 * 86400 + 36000, DAY0 - 40 * 86400 + 37800)  # 40 days ago: outside
o1 = feed()
w1, m1 = o1["totals"]["PAPER"]["week_net"], o1["totals"]["PAPER"]["month_net"]
check("3-day-old trade counts in week and month", abs(w1 - w0 - 275) < 1e-6, (w0, w1))
check("20-day-old trade counts in month only", abs(m1 - m0 - 275 - 600) < 1e-6, (m0, m1))
check("40-day-old trade counts in neither", o1["totals"]["PAPER"]["month_trades"] == o0["totals"]["PAPER"]["month_trades"] + 2)
check("window day keys present", o1["totals"]["PAPER"]["week_from"] == F._day_key(DAY0 - 6 * 86400) and o1["totals"]["PAPER"]["month_from"] == F._day_key(mf))
r = row(o1, "ORB_V1")
check("row carries its own week/month", r["week"]["trades"] == 3 and r["month"]["trades"] == 4)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for x in FAILS:
        print("  -", x)
    sys.exit(1)
print("ALL FLEET_TODAY CHECKS PASSED")

# backend/app/recon/test_recon.py
#
# ── BROKER_RECON_20261029 ── behavioural suite for the fleet broker check.
# Throw-away HOME, real migrations, a fake Kite that only answers positions()
# and orders() (anything else = the check tried to trade → failure). Clock
# values are REAL epochs built from IST wall-clock times.
#
#   cd backend && PYTHONPATH=$PWD python3 app/recon/test_recon.py

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

FAILS = []
PASSES = [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


TMP = tempfile.mkdtemp(prefix="recon_")
os.environ["HOME"] = TMP
IST = timezone(timedelta(hours=5, minutes=30))
D = date(2026, 11, 2)                                           # a Monday


def ts_of(d, hh, mm, ss=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST).timestamp())


from app.event_bus.audit_logger import audit_muted             # noqa: E402
from app.db.sqlite import get_conn                             # noqa: E402
from app.db.migrations.runner import run_migrations            # noqa: E402
from app.recon import recon_core as RC                         # noqa: E402
from app.api import fleet_today_routes as F                    # noqa: E402
import app.config.account_bindings as AB                       # noqa: E402

APP = get_conn()
with audit_muted():
    run_migrations(APP)
check("throw-away HOME in force", str(RC.db_path()).startswith(TMP), RC.db_path())


class FakeKite:
    def __init__(self):
        self.pos, self.ords, self.calls = [], [], []

    def positions(self):
        self.calls.append("positions")
        return {"net": list(self.pos), "day": []}

    def orders(self):
        self.calls.append("orders")
        return list(self.ords)

    def __getattr__(self, name):                                # place_order, cancel_order, … → loud
        raise AssertionError(f"reconciliation called kite.{name}")


K = FakeKite()


def bpos(sym, q, exch="NFO", product="NRML"):
    return {"tradingsymbol": sym, "exchange": exch, "product": product, "quantity": q}


LEGS = {}                                                       # sid → list of normalised legs
MODES = {}
WARN = {}


def leg(sym, q, short, book="LIVE", open_=True):
    return {"symbol": sym, "qty": q, "short": short, "book": book, "open": open_}


def fake_legs(sid, warns):
    if sid in WARN:
        warns.append(WARN[sid])
    return list(LEGS.get(sid, []))


F.FLEET_IDS = ["TSG_V1", "IC_V2", "VET_V1", "HA_V1"]
F._allowed = lambda sid: True
F._mode = lambda sid: MODES.get(sid, "OFF")
_real_legs_for = F._legs_for
F._legs_for = fake_legs
AB.load_bindings = lambda: {"HA_V1": "ANGELONE"}
RC.trading_day = lambda d: d.weekday() < 5
ALERTS = []
RC._alert = lambda text, severity="error": ALERTS.append((severity, text))


def poll(hh, mm, ss=0, d=D, **kw):
    RC._STATE["last"] = None
    with audit_muted():
        return RC.run_once(ts_of(d, hh, mm, ss), kite=kw.pop("kite", K), **kw)


def issues(open_only=True):
    c = RC.connect()
    q = "SELECT symbol, kind, broker_qty, app_qty, ignored_qty FROM recon_issues"
    rows = c.execute(q + (" WHERE resolved_at IS NULL" if open_only else "") + " ORDER BY id").fetchall()
    c.close()
    return rows


A, B, C2, E = "NIFTY26N0424000CE", "NIFTY26N0424500PE", "NIFTY26N0423000CE", "NIFTY26N0425000CE"

print("── 1. gates (real clock)")
MODES.update({"TSG_V1": "LIVE"})
r = poll(9, 5)
check("09:05 → outside the window, no Kite call", not r["ran"] and not K.calls, r)
r = poll(9, 16)
check("09:16 → open burst, no Kite call", not r["ran"] and "burst" in r["reason"] and not K.calls, r)
r = poll(15, 41)
check("15:41 → after the NFO close, no Kite call", not r["ran"] and not K.calls, r)
r = poll(11, 0, d=date(2026, 11, 7))
check("Saturday → no poll", not r["ran"] and not K.calls, r)
MODES.clear()
r = poll(11, 0)
check("no strategy LIVE on Zerodha and no live legs → nothing to check", not r["ran"] and "no strategy" in r["reason"], r)
MODES.update({"TSG_V1": "LIVE", "IC_V2": "PAPER_LIVE", "VET_V1": "LIVE"})
r = poll(9, 12)
check("09:12 carried-position check runs (before any strategy acts)", r["ran"] and r["ok"], r)
r = poll(20, 0, force=True)
check("force (the /run route) bypasses the window", r["ran"] and r["ok"], r)

print("── 2. a clean book")
LEGS["TSG_V1"] = [leg(A, 65, True), leg(A, 65, True), leg(B, 65, True, book="PAPER")]
K.pos = [bpos(A, -100, product="NRML"), bpos(A, -30, product="MIS"), bpos("RELIANCE", 10, exch="NSE")]
r = poll(10, 0)
check("legs summed per symbol (−130), MIS + NRML netted, PAPER legs and equity ignored", r["ok"] and r["mismatches"] == []
      and r["app"] == {A: -130} and r["broker"] == {A: -130}, r)
check("only positions() and orders() were called", set(K.calls) == {"positions", "orders"}, K.calls)
check("no alert on a clean book", ALERTS == [])

print("── 3. ORPHAN: broker holds what no strategy does")
K.pos = [bpos(A, -130), bpos(B, -65)]
r = poll(10, 1)
check("first sighting → pending, no alert", [m["kind"] for m in r["mismatches"]] == ["ORPHAN"] and ALERTS == [], r)
r = poll(10, 2)
check("second consecutive poll → one alert", len(ALERTS) == 1 and "ORPHAN" in ALERTS[0][1] and B in ALERTS[0][1], ALERTS)
check("…saying how to clear it", "mark it ignored" in ALERTS[0][1])
r = poll(10, 3)
check("unchanged → not alerted again", len(ALERTS) == 1)
check("issue stored open", issues() == [(B, "ORPHAN", -65, 0, 0)], issues())

print("── 4. marking a manual trade")
import app.recon.recon_routes as RR                             # noqa: E402
RR.add_ignore(RR.IgnoreReq(tradingsymbol=B.lower(), qty=-65, note="my hedge"))
r = poll(10, 4)
check("ignored manual qty → matches; issue resolved with a note", r["mismatches"] == [] and issues() == []
      and ALERTS[-1][0] == "info" and "resolved" in ALERTS[-1][1], ALERTS[-1:])
K.pos = [bpos(A, -130)]
r = poll(10, 5)
st = RR.status()
check("manual position closed at the broker → its ignore lapses by itself", st["ignores"] == [] and r["mismatches"] == [], st["ignores"])

print("── 5. GHOST and QTY, with strategy names")
LEGS["VET_V1"] = [leg(C2, 65, False)]
poll(10, 6)
poll(10, 7)
check("GHOST: app holds, broker flat → alert after two polls", ALERTS[-1][1].startswith("🔎 Broker check — GHOST") and C2 in ALERTS[-1][1],
      ALERTS[-1:])
check("…names the strategy by codename, never its id", "VET_V1" not in ALERTS[-1][1], ALERTS[-1][1])
LEGS["VET_V1"] = []
K.pos = [bpos(A, -65)]
poll(10, 8)
poll(10, 9)
check("QTY: broker −65 vs app −130 → alert", "QTY" in ALERTS[-1][1] and "-65" in ALERTS[-1][1] and "-130" in ALERTS[-1][1],
      ALERTS[-1:])
check("the GHOST resolved when VET's leg closed", any("resolved" in a[1] and C2 in a[1] for a in ALERTS))
K.pos = []
poll(10, 10)
n = len(ALERTS)
poll(10, 11)
check("shape change (QTY → GHOST) closes the old issue and alerts the new one", len(ALERTS) == n + 1
      and "GHOST" in ALERTS[-1][1] and [i[1] for i in issues()] == ["GHOST"], (ALERTS[-2:], issues()))
K.pos = [bpos(A, -130)]
poll(10, 12)

print("── 6. settling orders are judged next poll")
LEGS["IC_V2"] = [leg(E, 65, True)]
K.ords = [{"tradingsymbol": E, "status": "OPEN", "order_timestamp": datetime.fromtimestamp(ts_of(D, 10, 13), IST).replace(tzinfo=None)}]
r = poll(10, 13, 10)
check("an order in flight → the symbol is skipped", E in r["settling"] and r["mismatches"] == [], r)
K.ords = [{"tradingsymbol": E, "status": "COMPLETE", "order_timestamp": datetime.fromtimestamp(ts_of(D, 10, 14), IST).replace(tzinfo=None),
           "exchange_update_timestamp": (datetime.fromtimestamp(ts_of(D, 10, 14, 5), IST)).strftime("%Y-%m-%d %H:%M:%S")}]
r = poll(10, 14, 15)
check("a fill 10 s ago (naive IST timestamps, real clock) → still settling", E in r["settling"], r)
r = poll(10, 14, 40)
check("35 s later → judged", E not in r["settling"] and [m["kind"] for m in r["mismatches"]] == ["GHOST"], r)
n = len(ALERTS)
K.pos = [bpos(A, -130), bpos(E, -65)]
r = poll(10, 15)
check("a one-poll flicker never alerts", len(ALERTS) == n and r["mismatches"] == [], r)
K.ords = []

print("── 7. restart keeps the memory")
K.pos = [bpos(A, -130)]
LEGS["IC_V2"] = []
K.pos = [bpos(A, -130), bpos(B, 50)]
poll(11, 0)
poll(11, 1)
n = len(ALERTS)
RC._STATE["pending"] = {}
poll(11, 2)
poll(11, 3)
check("after a restart an already-alerted issue is not re-alerted", len(ALERTS) == n, ALERTS[n:])
K.pos = [bpos(A, -130)]
poll(11, 4)

print("── 8. fail safe")
WARN["TSG_V1"] = "TSG_V1 live mapper: OperationalError('database is locked')"
n = len(ALERTS)
K.pos = []
r = poll(11, 5)
r2 = poll(11, 6)
check("a strategy's legs unreadable → no verdicts at all (no false GHOST)", not r["ok"] and not r2["ok"] and len(ALERTS) == n
      and "unreadable" in r, r)
del WARN["TSG_V1"]
K.pos = [bpos(A, -130)]
r = poll(11, 7, kite=None)
RCK = RC.trade_kite
RC.trade_kite = lambda: None
r = poll(11, 8, kite=None)
RC.trade_kite = RCK
check("no Kite session → recorded as an error, no alert", not r["ok"] and r["error"] == "no Kite session" and len(ALERTS) == n, r)


class Boom(FakeKite):
    def positions(self):
        raise RuntimeError("Kite 503")


r = poll(11, 9, kite=Boom())
check("a Kite error → recorded, never raised", not r["ok"] and "Kite 503" in r["error"], r)
LEGS["HA_V1"] = [leg("NIFTY26N0424200CE", 65, False)]
MODES["HA_V1"] = "LIVE"
r = poll(11, 10)
check("a strategy bound to Angel One is not compared with Zerodha", "NIFTY26N0424200CE" not in r["app"] and r["mismatches"] == [], r)

print("── 9. the real leg reader feeds it (paper_trades LIVE row)")
F._legs_for = _real_legs_for
APP.execute("INSERT INTO paper_trades (paper_trade_id, strategy_name, trade_mode, symbol, token, side, entry_time, entry_price, "
            "candle_ts, sl_price, tp_price, rr, lots, lot_size, qty, state, created_at, trade_direction) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (9001, "TSG_V1", "LIVE", A, 1, "CE", ts_of(D, 9, 16), 100.0, ts_of(D, 9, 16), 0, 0, 0, 2, 65, 130, "OPEN",
             ts_of(D, 9, 16), "SHORT"))
APP.execute("INSERT INTO paper_trades (paper_trade_id, strategy_name, trade_mode, symbol, token, side, entry_time, entry_price, "
            "candle_ts, sl_price, tp_price, rr, lots, lot_size, qty, state, created_at, trade_direction) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (9002, "TSG_V1", "PAPER", B, 2, "PE", ts_of(D, 9, 16), 100.0, ts_of(D, 9, 16), 0, 0, 0, 1, 65, 65, "OPEN",
             ts_of(D, 9, 16), "SHORT"))
APP.commit()
qty, who, bad = RC.app_book(["TSG_V1"])
check("the dashboard reader's LIVE row → −130 of the symbol; the PAPER row is not in the broker book",
      qty == {A: -130} and who == {A: {"TSG_V1": -130}} and not bad, (qty, who, bad))

print("── 10. routes on the REAL api_server app (admin gate) + schedule")
import app.api_server as API                                    # noqa: E402
from app.license import license_state as LS                     # noqa: E402


def call(path, level, method="GET", body=b""):
    LS.ui_level = lambda: level
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m_):
        sent.append(m_)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"content-type", b"application/json")] if body else [],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "root_path": ""}
    with audit_muted():
        asyncio.run(API.app(scope, receive, send))
    st_ = next(m_["status"] for m_ in sent if m_["type"] == "http.response.start")
    return st_, b"".join(m_.get("body", b"") for m_ in sent if m_["type"] == "http.response.body")


s_, b_ = call("/api/recon/status", "admin")
js = json.loads(b_) if s_ == 200 else {}
check("status: admin → 200 with last run, open issues, ignores", s_ == 200 and "open_issues" in js and js.get("last_run"), (s_, b_[:200]))
s_, _ = call("/api/recon/status", "standard")
check("status: non-admin → 403", s_ == 403, s_)
s_, _ = call("/api/recon/ignore", "admin", "POST", json.dumps({"tradingsymbol": B, "qty": 0}).encode())
check("ignore with qty 0 → 400", s_ == 400, s_)
s_, b_ = call("/api/recon/ignore", "admin", "POST", json.dumps({"tradingsymbol": B, "qty": -65, "note": "x"}).encode())
iid = json.loads(b_).get("id") if s_ == 200 else None
s_, _ = call(f"/api/recon/ignore/{iid}", "admin", "DELETE")
s2, _ = call(f"/api/recon/ignore/{iid}", "admin", "DELETE")
check("ignore add → delete → delete again is 404", iid and s_ == 200 and s2 == 404, (iid, s_, s2))
src = open(API.__file__).read()
check("api_server schedules the poll every POLL_S under its own id",
      'id="broker_recon_poll"' in src and "recon_job" in src and RC.POLL_S == 45)
_lvl = LS.ui_level
LS.ui_level = lambda: "standard"
ran = []
_ro = RC.run_once
RC.run_once = lambda *a, **k: ran.append(1)
RC.recon_job()
check("standard licence: the job does nothing", ran == [])
LS.ui_level = lambda: "admin"
RC.recon_job()
check("admin licence: the job polls", ran == [1])
RC.run_once = _ro
LS.ui_level = _lvl

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL BROKER_RECON CHECKS PASSED")
shutil.rmtree(TMP, ignore_errors=True)

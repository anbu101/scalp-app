# backend/app/recon/test_recon_value.py
#
# ── RECON_VALUE_20261102 ── the broker check compares MONEY, not just qty.
# Replays Monday 2026-11-02 (IC_V2 LIVE, 1 lot) with Kite's real fills and the
# app's real (wrong) booking of L1·ADJ, then the repaired booking.
#
#   cd backend && PYTHONPATH=$PWD python3 app/recon/test_recon_value.py

import os
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


TMP = tempfile.mkdtemp(prefix="recon_value_")
os.environ["HOME"] = TMP
IST = timezone(timedelta(hours=5, minutes=30))
D = date(2026, 11, 2)


def ts_of(d, hh, mm, ss=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST).timestamp())


from app.event_bus.audit_logger import audit_muted             # noqa: E402
from app.db.sqlite import get_conn                             # noqa: E402
from app.db.migrations.runner import run_migrations            # noqa: E402
from app.recon import recon_core as RC                         # noqa: E402
from app.recon import recon_routes as RR                       # noqa: E402
from app.api import fleet_today_routes as F                    # noqa: E402
import app.config.account_bindings as AB                       # noqa: E402

with audit_muted():
    run_migrations(get_conn())
check("throw-away HOME in force", str(RC.db_path()).startswith(TMP), RC.db_path())

P = "NIFTY26N03"
S22400PE, S23750CE, S23300CE, S23200PE, S23350CE = (P + x for x in
    ("22400PE", "23750CE", "23300CE", "23200PE", "23350CE"))


def dpos(sym, bq, bv, sq, sv):
    return {"tradingsymbol": sym, "exchange": "NFO", "product": "NRML",
            "quantity": bq - sq, "buy_quantity": bq, "buy_value": bv,
            "sell_quantity": sq, "sell_value": sv}


# Kite's day book after the 13:59 square-off (the 12 executed orders)
KITE_DAY = [
    dpos(S22400PE, 65, 65 * 3.70, 65, 65 * 2.40),
    dpos(S23750CE, 65, 65 * 3.20, 65, 65 * 2.85),
    dpos(S23300CE, 65, 65 * 107.80, 65, 65 * 75.25),
    dpos(S23200PE, 130, 65 * 87.45 + 65 * 88.30, 130, 65 * 83.85 + 65 * 71.55),
    dpos(S23350CE, 65, 65 * 80.15, 65, 65 * 55.85),
]


class FakeKite:
    def __init__(self):
        self.net, self.day, self.ords, self.calls = [], [], [], []

    def positions(self):
        self.calls.append("positions")
        return {"net": list(self.net), "day": list(self.day)}

    def orders(self):
        return list(self.ords)

    def __getattr__(self, name):
        raise AssertionError(f"reconciliation called kite.{name}")


K = FakeKite()
K.day = list(KITE_DAY)
K.net = [dict(p, quantity=0) for p in KITE_DAY]


def leg(lbl, sym, short, ent, ext, ets, xts, q=65, book="LIVE"):
    return {"symbol": sym, "qty": q, "short": short, "book": book, "open": xts is None,
            "entry": ent, "exit": ext, "entry_ts": ets, "exit_ts": xts, "leg": lbl}


T = lambda hh, mm, ss=0: ts_of(D, hh, mm, ss)                 # noqa: E731
APP_LEGS = [
    leg("L1", S23300CE, True, 75.25, 107.80, T(9, 18), T(9, 45, 14)),
    leg("L1A", S23350CE, False, 80.15, 80.15, T(9, 46), T(11, 48, 7)),     # ← the bad booking
    leg("L2", S23200PE, True, 83.85, 87.45, T(9, 18), T(13, 9, 7)),
    leg("L2A", S23200PE, False, 88.30, 71.55, T(13, 10), T(13, 59, 24)),
    leg("L3", S23750CE, False, 3.20, 2.85, T(9, 18), T(13, 59, 25)),
    leg("L4", S22400PE, False, 3.70, 2.40, T(9, 18), T(13, 59, 31)),
]
LEGS = {"IC_V2": list(APP_LEGS)}
F.FLEET_IDS = ["IC_V2", "TSG_V1"]
F._allowed = lambda sid: True
F._mode = lambda sid: "PAPER_LIVE" if sid == "IC_V2" else "OFF"
F._legs_for = lambda sid, warns: list(LEGS.get(sid, []))
AB.load_bindings = lambda: {}
RC.trading_day = lambda d: d.weekday() < 5
ALERTS = []
RC._alert = lambda text, severity="error": ALERTS.append((severity, text))


def poll(hh, mm, ss=0, d=D):
    with audit_muted():
        return RC.run_once(ts_of(d, hh, mm, ss), kite=K)


def vopen():
    c = RC.connect()
    rows = c.execute("SELECT symbol, kind, delta, detail FROM recon_value_issues "
                     "WHERE resolved_at IS NULL ORDER BY id").fetchall()
    c.close()
    return rows


print("── 1. pure comparison on the real day")
app, who, notes = RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 0)))
brk = RC.broker_day_flows(K.positions())
vm = RC.compare_values(app, brk, set())
check("exactly one symbol differs", [x["symbol"] for x in vm] == [S23350CE], vm)
x = vm[0]
check("kind PRICE (quantities agree)", x["kind"] == "PRICE", x)
check("delta is +₹1,579.50 (app better than broker)", abs(x["delta"] - 1579.50) < 0.01, x["delta"])
check("detail names the SELL side, 80.15 vs 55.85", "SELL avg app 80.15 vs Kite 55.85" in x["detail"], x["detail"])
check("23200PE (short + same-strike ADJ) nets clean", S23200PE not in {y["symbol"] for y in vm})
app_total = sum(f["sv"] - f["bv"] for f in app.values())
brk_total = sum(f["sv"] - f["bv"] for f in brk.values())
check("app gross −₹3,545.75 vs Kite −₹5,125.25",
      abs(app_total + 3545.75) < 0.01 and abs(brk_total + 5125.25) < 0.01, (app_total, brk_total))

print("── 2. net rows' day_* fields when the 'day' list is absent")
net_only = {"net": [{"tradingsymbol": p["tradingsymbol"], "exchange": "NFO", "quantity": 0,
                     "day_buy_quantity": p["buy_quantity"], "day_buy_value": p["buy_value"],
                     "day_sell_quantity": p["sell_quantity"], "day_sell_value": p["sell_value"]}
                    for p in KITE_DAY]}
vm2 = RC.compare_values(app, RC.broker_day_flows(net_only), set())
check("same verdict from net day_* fields", [(y["symbol"], y["kind"]) for y in vm2] == [(S23350CE, "PRICE")], vm2)

print("── 3. live polls: two-poll confirmation, one alert, badge data")
r1 = poll(14, 30)
check("poll 1 ran ok", r1.get("ok") and r1.get("value_error") is None, r1)
check("poll 1: seen, not yet confirmed", r1["value_mismatches"] and not r1["value_confirmed"] and not vopen())
r2 = poll(14, 31)
check("poll 2: confirmed", r2["value_confirmed"] == [S23350CE], r2.get("value_confirmed"))
rows = vopen()
check("one open PRICE issue stored", len(rows) == 1 and rows[0][1] == "PRICE", rows)
price_alerts = [t for s, t in ALERTS if "PRICE" in t]
check("one PRICE alert, names strategy and leg", len(price_alerts) == 1 and "L1A" in price_alerts[0], ALERTS)
check("alert states the rupee effect", "₹1,579.50 better" in price_alerts[0], price_alerts)
poll(14, 32)
check("no repeat alert on the next poll", len([t for s, t in ALERTS if "PRICE" in t]) == 1, ALERTS)
st = RR.status()
check("status route exposes value_issues", len(st.get("value_issues") or []) == 1, st.get("value_issues"))
check("qty check still clean (open_issues empty)", st.get("open_issues") == [], st.get("open_issues"))
check("never traded", set(K.calls) == {"positions"}, K.calls)

print("── 4. the repair resolves it")
LEGS["IC_V2"][1] = dict(LEGS["IC_V2"][1], exit=55.85)
poll(14, 33)
check("issue resolved", vopen() == [], vopen())
check("resolved note sent", any(s == "info" and S23350CE in t and "match Kite again" in t for s, t in ALERTS), ALERTS[-1:])

print("── 5. FILLS: a fill the app never booked")
K.day = KITE_DAY + []
K.day[0] = dpos(S22400PE, 130, 65 * 3.70 + 65 * 3.0, 130, 65 * 2.40 + 65 * 2.9)   # extra manual round trip
vm3 = RC.compare_values(*RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 40)))[:1],
                        RC.broker_day_flows(K.positions()), set())
check("FILLS on the symbol with the extra round trip",
      [(y["symbol"], y["kind"]) for y in vm3] == [(S22400PE, "FILLS")], vm3)
check("…but a manual mark skips it", RC.compare_values(
    RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 40)))[0], RC.broker_day_flows(K.positions()),
    {S22400PE}) == [])
K.day = list(KITE_DAY)

print("── 6. tolerance, settling, missing prices, other books")
app1 = {S23750CE: {"bq": 65, "bv": 65 * 3.20, "sq": 65, "sv": 65 * 2.90}}       # 0.05 off → ₹3.25
check("a tick of rounding is not a mismatch", RC.compare_values(app1, RC.broker_day_flows(K.positions()), set()) == [])
check("settling symbol skipped", RC.compare_values(app, RC.broker_day_flows(K.positions()), {S23350CE}) == [])
LEGS["IC_V2"].append(leg("LX", P + "24000CE", True, 50.0, None, T(9, 18), T(12, 0)))
fl, _, _ = RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 0)))
check("a leg closed without a price makes its symbol unverifiable", P + "24000CE" not in fl, fl.keys())
LEGS["IC_V2"].pop()
LEGS["IC_V2"].append(leg("P1", P + "24100CE", True, 50.0, 40.0, T(9, 18), T(12, 0), book="PAPER"))
fl, _, _ = RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 0)))
check("PAPER legs never counted", P + "24100CE" not in fl)
LEGS["IC_V2"].pop()
carried = [leg("C1", S23300CE, True, 70.0, 107.80, ts_of(date(2026, 10, 30), 9, 18), T(9, 45, 14))]
LEGS["TSG_V1"] = []
LEGS["IC_V2"] = carried
fl, _, _ = RC.app_day_flows(["IC_V2"], RC.day_start_ts(T(14, 0)))
check("carried leg: only today's exit counted", fl[S23300CE] == {"bq": 65, "bv": 65 * 107.80, "sq": 0, "sv": 0.0}, fl)
LEGS["IC_V2"] = list(APP_LEGS)

print("── 7. yesterday's open issue is closed on a new day")
LEGS["IC_V2"][1] = dict(APP_LEGS[1])                         # bad booking again
poll(14, 50); poll(14, 51)
check("re-opened", len(vopen()) == 1)
LEGS["IC_V2"] = []
K.day, K.net = [], []
D2 = date(2026, 11, 3)
with audit_muted():
    RC.run_once(ts_of(D2, 9, 30), kite=K)
check("previous day's issue resolved on the new day", vopen() == [], vopen())

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL RECON_VALUE CHECKS PASSED")

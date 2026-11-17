"""
test_index_path.py — INDEX_PATH_20261117: today's NIFTY / BANKNIFTY line for the Dashboard
(GET /api/fleet/indices). Kite, the index state and the clocks are stubbed: no network.

    cd backend && PYTHONPATH=. python3 app/api/test_index_path.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.api import index_path as IP                                  # noqa: E402
from app.api import fleet_today_routes as FR                          # noqa: E402

fails, n = [], [0]


def check(tc, cond, msg):
    n[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def at(h, m, day=17):
    return datetime(2026, 11, day, h, m, 20, tzinfo=IP.IST)


MONO = [1000.0]
IP._mono = lambda: MONO[0]
SNAP = {"NIFTY": {"ltp": 25492.7, "prev_close": 25291.1}, "BANKNIFTY": {"ltp": 61982.4, "prev_close": 61575.85}}
IP._snapshot = lambda: SNAP
calls = []


class Kite:
    fail = False

    def historical_data(self, instrument_token, from_date, to_date, interval, **k):
        calls.append((instrument_token, from_date, to_date, interval))
        if self.fail:
            raise RuntimeError("Too many requests")
        out = [{"date": datetime(2026, 11, 16, 15, 29, tzinfo=IP.IST), "close": 1.0}]      # yesterday: dropped
        for m in range(555, 600):                                                         # 09:15 … 09:59
            out.append({"date": datetime(2026, 11, 17, m // 60, m % 60, tzinfo=IP.IST), "close": 25300.0 + (m - 555)})
        return out


KITE = [Kite()]
IP._kite = lambda: KITE[0]


def reset():
    IP._KITE_CACHE.clear()
    IP._SAMPLES.clear()
    calls.clear()


# ── during the session
reset()
out = IP.build(at(10, 30))
nf = out["indices"]["NIFTY"]
check("TC-IDX-0012", nf["path"][0] == [555, 25300.0] and [599, 25344.0] in nf["path"] and nf["path"][-1] == [630, 25492.7]
      and all(p[0] >= 555 for p in nf["path"]),
      "Kite's 1-minute closes for TODAY (yesterday dropped) + the live price as the forming minute")
check("TC-IDX-0013", nf["prev_close"] == 25291.1 and nf["ltp"] == 25492.7 and nf["source"] == "kite+live"
      and {c[0] for c in calls} == {256265, 260105} and calls[0][3] == "minute" and calls[0][1].endswith("09:15:00"),
      "prev close and price from the index state (same as the badges); one 1-minute call per index from 09:15")
n0 = len(calls)
MONO[0] += 30
IP.build(at(10, 30))
check("TC-IDX-0014", len(calls) == n0, "read again within a minute → no new Kite call (cached)")
MONO[0] += 31
IP.build(at(10, 31))
check("TC-IDX-0015", len(calls) == n0 + 2, "a minute later → one call per index again")
# own samples fill minutes Kite has not closed
IP.sample(at(10, 40))
SNAP["NIFTY"]["ltp"] = 25500.0
MONO[0] += 10
out = IP.build(at(10, 41))
pts = dict((p[0], p[1]) for p in out["indices"]["NIFTY"]["path"])
check("TC-IDX-0016", pts.get(640) == 25492.7 and pts.get(641) == 25500.0, "minutes after Kite's last close come from the app's own samples")
# Kite fails → last good rows kept, no error shown while there is a line
KITE[0].fail = True
MONO[0] += 61
out = IP.build(at(10, 42))
check("TC-IDX-0017", [599, 25344.0] in out["indices"]["NIFTY"]["path"] and out["indices"]["NIFTY"]["note"] is None,
      "a failed Kite call keeps the last good line")
KITE[0].fail = False

# ── no Kite session: the app's own samples only
reset()
KITE[0] = None
for m in (0, 1, 2):
    IP.sample(at(11, m))
out = IP.build(at(11, 3))
nf = out["indices"]["NIFTY"]
check("TC-IDX-0018", nf["source"] == "live samples" and [p[0] for p in nf["path"]] == [660, 661, 662, 663] and nf["note"] == "no Kite session",
      "no Kite session → the line from the app's own minute samples, with a note")
KITE[0] = Kite()

# ── before the open / weekend
reset()
out = IP.build(at(8, 53))
check("TC-IDX-0019", not calls and out["indices"]["NIFTY"]["path"] == [] and out["indices"]["NIFTY"]["ltp"] == 25500.0,
      "before 09:15: no Kite call, no line; the price is still there for the chip")
IP.sample(datetime(2026, 11, 21, 11, 0, tzinfo=IP.IST))   # Saturday
check("TC-IDX-0020", not IP._SAMPLES, "no samples on a weekend or outside 09:15–15:30")

# ── after the close: the line is final, Kite asked rarely
reset()
IP.build(at(16, 0))
n0 = len(calls)
MONO[0] += 120
IP.build(at(16, 2))
check("TC-IDX-0021", n0 == 2 and len(calls) == n0, "after the close the cached line is kept for 15 minutes")

# ── route + per-minute job
src = open(FR.__file__, encoding="utf-8").read()
paths = [r.path for r in FR.router.routes]
check("TC-IDX-0022", "/api/fleet/indices" in paths and "_ip.sample()" in src.split("def fleet_mtm_sample_job")[1].split("def ")[0],
      "GET /api/fleet/indices on the fleet router; the per-minute fleet job also samples the indices")
IP.build, orig = (lambda: (_ for _ in ()).throw(RuntimeError("boom"))), IP.build
r = FR.fleet_indices()
IP.build = orig
check("TC-IDX-0023", r.get("ok") is False and "boom" in r.get("error", ""), "the route never raises: an error is reported, the Dashboard keeps its last line")

print(f"\n{'FAIL' if fails else 'OK'}: {n[0] - len(fails)}/{n[0]} checks")
sys.exit(1 if fails else 0)

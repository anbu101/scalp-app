# backend/app/backtest/repo/test_trading_calendar_fast.py
#
# ── BT_CALENDAR_FAST_20260929 ── the trading-day calendar (sessions-to-expiry
# for the results page and Trade Replay) must produce EXACTLY the dates of the
# old full DISTINCT scan, in milliseconds, and the run-detail route must
# return the same JSON it always did. Real epoch timestamps throughout.
# No TestClient (build-Mac httpx pin): the route function is called directly.
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/repo/test_trading_calendar_fast.py

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


IST = timezone(timedelta(hours=5, minutes=30))


def ds_of(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


TMP = tempfile.mkdtemp(prefix="bt_cal_fast_")
DB = os.path.join(TMP, "backtest.db")
SCHEMA = (Path(__file__).resolve().parent / "schema.sql").read_text()

import app.backtest.repo.backtest_repo as BR          # noqa: E402
BR._db_path = lambda: Path(DB)
import app.backtest.repo.trading_calendar as TC        # noqa: E402

c = sqlite3.connect(DB)
c.executescript(SCHEMA)
c.execute("PRAGMA journal_mode=WAL")

print("── 1. corpus with the awkward cases")
rows = []


def put(sym, typ, und, ts, strike=23000.0):
    rows.append((abs(hash(sym)) % 10 ** 8, ts, und, sym, typ, strike, "2099-01-01", 1, 1, 1, 1, 0, 0))


d, n = date(2024, 1, 1), 0
weekdays = []
while n < 80:
    if d.weekday() < 5:
        weekdays.append(d)
        ds = ds_of(d)
        for k in range(21):
            for typ in ("CE", "PE"):
                sym = f"NIFTY{d:%y%m%d}{22000 + 50 * k}{typ}"
                for m in range(375):
                    put(sym, typ, "NIFTY", ds + (555 + m) * 60)
        n += 1
    d += timedelta(days=1)
sat = date(2024, 1, 20)                                  # special Saturday session: CE only
for m in range(0, 375, 5):
    put("NIFTY24SATCE", "CE", "NIFTY", ds_of(sat) + (555 + m) * 60)
pe_only = date(2024, 5, 6)                               # PE prints only
put("NIFTY24PEONLY", "PE", "NIFTY", ds_of(pe_only) + 600 * 60)
spot_only = date(2024, 5, 7)                             # SPOT / FUT only → not a trading day
put("NIFTY 50", "SPOT", "NIFTY", ds_of(spot_only) + 600 * 60)
put("NIFTYFUT", "FUT", "NIFTY", ds_of(spot_only) + 601 * 60)
bnf_only = date(2024, 5, 8)                              # another underlying's options only
put("BANKNIFTY24MAYCE", "CE", "BANKNIFTY", ds_of(bnf_only) + 600 * 60)
stray = date(2024, 5, 12)                                # Sunday, one 02:00 IST print
put("NIFTY24STRAYCE", "CE", "NIFTY", ds_of(stray) + 120 * 60)
edge = date(2024, 5, 13)                                 # print at 23:59:59 IST (day edge)
put("NIFTY24EDGEPE", "PE", "NIFTY", ds_of(edge) + 86399)
c.executemany("INSERT OR IGNORE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
c.commit()
check(f"corpus built ({len(rows):,} rows)", len(rows) > 1_000_000)

print("── 2. identical to the old full scan")
t = time.time()
old = TC._scan_days(c, "NIFTY")
t_old = time.time() - t
t = time.time()
new = TC._probe_days(c, "NIFTY")
t_new = time.time() - t
check(f"probe == scan ({len(new)} dates)", new == old, (len(new), len(old), sorted(set(old) ^ set(new))[:6]))
check("Saturday special session counted", sat.isoformat() in new)
check("PE-only day counted", pe_only.isoformat() in new)
check("SPOT/FUT-only day excluded", spot_only.isoformat() not in new)
check("other underlying's day excluded", bnf_only.isoformat() not in new)
check("02:00 IST stray print counted (same as the scan)", stray.isoformat() in new and stray.isoformat() in old)
check("23:59:59 IST print lands on its IST date", edge.isoformat() in new and (edge + timedelta(days=1)).isoformat() not in new)
check("BANKNIFTY calendar identical too", TC._probe_days(c, "BANKNIFTY") == TC._scan_days(c, "BANKNIFTY") == [bnf_only.isoformat()])
check("unknown underlying → []", TC._probe_days(c, "FINNIFTY") == [] == TC._scan_days(c, "FINNIFTY"))
check(f"probe much faster than the scan ({t_new * 1000:.1f} ms vs {t_old * 1000:.0f} ms)",
      t_new < 0.2 and t_new * 10 < t_old, (t_new, t_old))
plans = [" ".join(str(x) for x in r) for q in (TC._PROBE_SQL,
         "SELECT MIN(ts) FROM backtest_candles_1m WHERE underlying = ? AND instrument_type = ?")
         for r in c.execute("EXPLAIN QUERY PLAN " + q, (("NIFTY", "CE", 0, 1) if "LIMIT" in q else ("NIFTY", "CE")))]
check("probe + min/max are covering-index seeks", all("idx_bt1m_under_type_ts" in p for p in plans), plans)
_saved = TC._MAX_PROBE_DAYS
TC._MAX_PROBE_DAYS = 10
check("absurd span falls back to the scan (same answer)", TC._probe_days(c, "NIFTY") == old)
TC._MAX_PROBE_DAYS = _saved

print("── 3. public trading_dates: cache, invalidation, DTE")
TC._cache.clear()
calls = [0]
_real_connect = TC.sqlite3.connect


def _counting_connect(*a, **k):
    calls[0] += 1
    return _real_connect(*a, **k)


TC.sqlite3.connect = _counting_connect
t = time.time()
first = TC.trading_dates("NIFTY")
t_first = time.time() - t
second = TC.trading_dates("NIFTY")
check(f"trading_dates == scan, cold call {t_first * 1000:.1f} ms", first == old and t_first < 0.2, t_first)
check("second call served from the cache (no SQL)", second == first and calls[0] == 1, calls[0])
# a run persisted into the same file bumps its mtime — the case that used to cost 15–20 s
time.sleep(1.1)
c.execute("INSERT INTO backtest_runs (run_id, strategy_id, underlying, date_from, date_to, config_json, fill_model, status, created_at) "
          "VALUES ('r-mtime', 'ORB_V1', 'NIFTY', '2024-01-01', '2024-05-01', '{}', 'pessimistic', 'done', 0)")
c.commit()
c.execute("PRAGMA wal_checkpoint(FULL)")
t = time.time()
third = TC.trading_dates("NIFTY")
t_third = time.time() - t
check(f"after a run write: recomputed ({calls[0]} reads) in {t_third * 1000:.1f} ms, same dates",
      third == old and calls[0] == 2 and t_third < 0.2, (calls[0], t_third))
TC.sqlite3.connect = _real_connect

trades = []
for i, dd in enumerate(weekdays[:40]):
    exp = weekdays[min(len(weekdays) - 1, i + (i % 5))]
    trades.append({"id": i, "entry_ts": ds_of(dd) + 36000, "expiry": exp.isoformat()})
run = TC.attach_dte({"underlying": "NIFTY", "trades": [dict(t) for t in trades]})
want = [TC.sessions_to_expiry(old, dd.isoformat(), t["expiry"]) for dd, t in zip(weekdays[:40], trades)]
check("attach_dte identical to the scan calendar", [t.get("dte") for t in run["trades"]] == want,
      ([t.get("dte") for t in run["trades"]][:6], want[:6]))
e = tempfile.mkdtemp(prefix="bt_cal_empty_")
ec = sqlite3.connect(os.path.join(e, "backtest.db"))
ec.executescript(SCHEMA)
ec.close()
check("empty corpus → [] (DTE simply absent)", TC.trading_dates("NIFTY", db_path=os.path.join(e, "backtest.db")) == [])

print("── 4. run detail route: same JSON, direct render")
try:
    from fastapi.encoders import jsonable_encoder
    from app.api.backtest_routes import run_detail
    c.execute("INSERT INTO backtest_runs (run_id, strategy_id, underlying, date_from, date_to, config_json, fill_model, status, "
              "created_at, summary_json) VALUES ('r-json', 'ORB_V1', 'NIFTY', '2024-01-01', '2024-05-01', ?, 'pessimistic', 'done', 0, ?)",
              (json.dumps({"lots": 10, "nested": {"a": [1, 2]}}), json.dumps({"net": 12.5, "trades": 2})))
    for i, dd in enumerate(weekdays[:6]):
        c.execute("INSERT INTO backtest_trades (run_id, tradingsymbol, instrument_type, strike, expiry, direction, entry_ts, entry_price, "
                  "sl, tp, exit_ts, exit_price, exit_reason, pnl, qty, charges, net_pnl) VALUES "
                  "('r-json', ?, 'CE', 23000, ?, 'BUY', ?, 100.5, NULL, 140, ?, 120.25, 'TP', 1283.75, 65, 40.1, 1243.65)",
                  (f"NIFTY{i}CE", weekdays[i + 2].isoformat(), ds_of(dd) + 36000, ds_of(dd) + 39600))
    c.commit()
    resp = run_detail("r-json")
    body = json.loads(resp.body)
    ref = jsonable_encoder(TC.attach_dte(BR.get_run("r-json")))
    check("route returns a rendered JSONResponse", type(resp).__name__ == "JSONResponse" and resp.media_type == "application/json")
    check("body identical to the old jsonable_encoder output", body == json.loads(json.dumps(ref)), (list(body)[:5], list(ref)[:5]))
    check("DTE present in the detail", all("dte" in t for t in body["trades"]))
    import app.api.backtest_routes as RT
    _g = BR.get_run
    BR.get_run = lambda rid: {"run_id": rid, "trades": [], "odd": {1, 2}}     # not JSON-native → fallback
    out = run_detail("r-odd")
    BR.get_run = _g
    check("non-JSON content falls back to FastAPI's encoder path", isinstance(out, dict) and out["run_id"] == "r-odd")
    try:
        run_detail("missing")
        check("missing run → 404", False)
    except Exception as ex:
        check("missing run → 404", getattr(ex, "status_code", None) == 404, ex)
except ImportError as ex:
    print(f"  (route checks skipped here: {ex})")
c.close()

print()
print(f"{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL BT_CALENDAR_FAST CHECKS PASSED")

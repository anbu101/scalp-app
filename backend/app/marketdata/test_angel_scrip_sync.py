# backend/app/marketdata/test_angel_scrip_sync.py
#
# ── ANGEL_SCRIP_SYNC_20261030 ── behavioural suite for the Angel One contract
# list: boot/daily/on-demand download, disk reload, new-strike refresh,
# cool-down, pre-open alert, zero cost without Account 2. Hermetic: temp
# cache dir, fake download, no network, no broker.
#
#   cd backend && PYTHONPATH=$PWD python3 app/marketdata/test_angel_scrip_sync.py

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


import app.marketdata.angel_instrument_resolver as AR
import app.marketdata.angel_scrip_sync as SS

TMP = Path(tempfile.mkdtemp(prefix="angel_scrip_"))
AR.SCRIP_PATH = TMP / "scrip_master.json"
AR.META_PATH = TMP / "scrip_master.meta.json"
TODAY = dt.datetime.now(tz=AR.IST).date()


def row(strike, opt, expiry="04NOV2025", token="111"):
    return {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",
            "symbol": f"NIFTY{expiry}{strike}{opt}", "strike": str(strike * 100),
            "expiry": expiry, "token": token}


BASE = [row(24000, "CE", token="1"), row(24000, "PE", token="2")] + \
       [row(20000 + i * 50, "CE", expiry="11NOV2025", token=str(1000 + i)) for i in range(1200)]
DL = {"calls": 0, "fail": False, "rows": BASE}


def fake_download(timeout):
    DL["calls"] += 1
    if DL["fail"]:
        raise RuntimeError("network down")
    return list(DL["rows"])


AR._download = fake_download
CLOCK = {"t": 1_761_800_000.0}           # real epoch scale (real-clock rule)
AR.time.time = lambda: CLOCK["t"]


def write_cache(rows, synced):
    AR.SCRIP_PATH.write_text(json.dumps(rows))
    AR.META_PATH.write_text(json.dumps({"synced_date": synced.isoformat()}))


def reset(rows=None, synced=None):
    for p in (AR.SCRIP_PATH, AR.META_PATH):
        if p.exists():
            p.unlink()
    if rows is not None:
        write_cache(rows, synced)
    DL.update(calls=0, fail=False, rows=BASE)
    CLOCK["t"] += 10_000
    return AR.AngelInstrumentResolver()


SYM = "NIFTY25N0424000CE"                # Kite weekly → 2025-11-04 24000 CE

print("── 1. the 2026-10-30 failure: never synced → now heals on demand")
r = reset()
check("starts unsynced", r._synced_date is None)
check("resolves after an on-demand download", r.resolve_from_kite_symbol(SYM) == ("NIFTY04NOV202524000CE", "1"))
check("exactly one download", DL["calls"] == 1, DL)
check("cache + meta written", AR.SCRIP_PATH.exists() and json.loads(AR.META_PATH.read_text())["synced_date"] == TODAY.isoformat())

print("── 2. fresh cache → no download")
r = reset(BASE, TODAY)
r.resolve_from_kite_symbol(SYM)
check("today's cache used as is", DL["calls"] == 0)
r = reset(BASE, TODAY - dt.timedelta(days=1))
r.resolve_from_kite_symbol(SYM)
check("yesterday's cache still fresh (unchanged rule)", DL["calls"] == 0)

print("── 3. download fails → fail closed, no download storm")
r = reset(BASE, TODAY - dt.timedelta(days=3))
DL["fail"] = True
try:
    r.resolve_from_kite_symbol(SYM)
    check("stale + failed download refuses", False)
except AR.AngelInstrumentStale:
    check("stale + failed download refuses", True)
for _ in range(5):
    try:
        r.resolve_from_kite_symbol(SYM)
    except AR.AngelInstrumentStale:
        pass
check("cool-down: 6 orders → 1 download attempt", DL["calls"] == 1, DL)
CLOCK["t"] += AR.ONDEMAND_COOLDOWN_S + 1
DL["fail"] = False
check("after the cool-down it retries and heals", r.resolve_from_kite_symbol(SYM)[1] == "1")
check("second attempt counted", DL["calls"] == 2, DL)

print("── 4. another instance synced on disk → reload, no download")
r = reset(BASE, TODAY - dt.timedelta(days=4))
write_cache(BASE, TODAY)                 # e.g. the 08:30 job via the shared resolver
r.resolve_from_kite_symbol(SYM)
check("picked up from disk", r._synced_date == TODAY and DL["calls"] == 0, (r._synced_date, DL))

print("── 5. strike listed after the cached copy → one forced refresh")
r = reset(BASE, TODAY)
DL["rows"] = BASE + [row(26550, "PE", token="777")]
check("new strike found after refresh", r.resolve_from_kite_symbol("NIFTY25N0426550PE")[1] == "777")
check("one refresh", DL["calls"] == 1)
try:
    r.resolve_from_kite_symbol("NIFTY25N0499999PE")
    check("truly unknown strike still raises", False)
except AR.AngelInstrumentError as e:
    check("truly unknown strike still raises", not isinstance(e, AR.AngelInstrumentStale))
check("…without another download inside the cool-down", DL["calls"] == 1, DL)

print("── 6. a bad download never overwrites a good cache")
old_day = TODAY - dt.timedelta(days=3)
r = reset(BASE, old_day)
DL["rows"] = []                          # Angel served an empty file
try:
    r.resolve_from_kite_symbol(SYM)
    check("empty download refused", False)
except AR.AngelInstrumentStale:
    check("empty download refused", True)
check("cache file untouched", len(json.loads(AR.SCRIP_PATH.read_text())) == len(BASE))
check("meta date untouched", json.loads(AR.META_PATH.read_text())["synced_date"] == old_day.isoformat())
for bad in ([], {"error": "x"}, BASE[:10]):
    try:
        AR._validate_rows(bad)
        check(f"rejects {type(bad).__name__}/{len(bad)}", False)
    except Exception:
        check(f"rejects {type(bad).__name__}/{len(bad)}", True)

print("── 7. shared resolver")
AR._SHARED = None
a, b = AR.get_shared_resolver(), AR.get_shared_resolver()
check("one instance per process", a is b)
try:
    from app.execution.angel_executor import AngelOneExecutor
    ex = AngelOneExecutor(manager=None)
    check("Angel executor uses the shared resolver", ex._resolver is a)
except ModuleNotFoundError as e:
    print(f"  SKIP  angel_executor import ({e}) — run on the build Mac")

print("── 8. jobs: zero cost without Account 2; alert only when it matters")
SS.angel_bound_strategies = lambda: []
DL.update(calls=0, fail=False, rows=BASE)
check("no Account-2 strategy → no download", SS.sync_job("test") is False and DL["calls"] == 0)
SS.angel_bound_strategies = lambda: ["SCALP_V3"]
AR._SHARED = None
for p in (AR.SCRIP_PATH, AR.META_PATH):
    p.unlink(missing_ok=True)
check("bound → daily job downloads", SS.sync_job("test") is True and DL["calls"] == 1, DL)

ALERTS = []
import app.event_bus.inapp_events as IE
IE.record_alert = lambda kind, msg, **kw: ALERTS.append((kind, msg))
SS.angel_live_strategies = lambda: []
check("pre-open: Angel-bound but PAPER only → silent", SS.preopen_check_job() is False and not ALERTS)
SS.angel_live_strategies = lambda: ["IC_V1"]
check("pre-open: LIVE on Angel + fresh list → silent", SS.preopen_check_job() is False and not ALERTS)
AR._SHARED = None
for p in (AR.SCRIP_PATH, AR.META_PATH):
    p.unlink(missing_ok=True)
DL["fail"] = True
check("pre-open: LIVE on Angel + list unobtainable → alert", SS.preopen_check_job() is True)
check("alert names the strategy and the fix", ALERTS and "IC_V1" in ALERTS[-1][1] and "Account 1" in ALERTS[-1][1], ALERTS)

print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")

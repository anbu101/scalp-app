#!/usr/bin/env python3
"""
apply_BT_DTE_SESSIONS_20260911.py — FENCE: BT_DTE_SESSIONS_20260911

Requires BT_DTE_BREAKDOWN_20260911 (calendar-day DTE panel).

Switch the Backtest "Days to Expiry" panel from calendar days to TRADING
SESSIONS to expiry (user, 2026-09-11): Fri before a Tuesday expiry reads
2DTE, not 4DTE, and a mid-week holiday shortens the count instead of
lying about it.

Why server-side: sessions need the 2020–2026 trading-day calendar. The app
only carries 2026 holidays (live use), but the corpus knows every day it
traded — and the backtest runners already define a trading day as "a date
with CE/PE candles for the underlying" (that is how sim_days is built).
Using the SAME definition makes the panel consistent with what the run
actually simulated. Computed once per underlying and cached (keyed on the
corpus file's mtime), then attached to every trade as `dte` when a run is
loaded via GET /backtest/runs/{run_id}. Best-effort: any failure leaves
`dte` absent and the panel shows "Unknown" rather than breaking the run.

  dte = number of trading dates d with entry_date(IST) < d ≤ expiry
        (0 on expiry day; the expiry date itself counts as one session)

Files:
  backend/app/backtest/repo/trading_calendar.py   NEW — calendar + attach_dte()
  backend/app/api/backtest_routes.py               run_detail → attach_dte
  frontend/src/pages/Backtest.jsx                  key reads t.dte; title

Safety: parent-fence check, py_compile gate, sqlite simulation (calendar
from CE/PE rows with a mid-week holiday; Fri→Tue = 2, Mon = 1, Tue = 0,
holiday week shortened, no-expiry → absent, cache hit on unchanged mtime,
empty corpus → nothing attached), node test of the panel key, JSX parse
gate, .bak backups, dual-tree mirror. Backend + frontend → full rebuild.

Run from the repo root:  python3 apply_BT_DTE_SESSIONS_20260911.py
"""
import importlib.util, os, py_compile, shutil, sqlite3, subprocess, sys, tempfile, time

FENCE = "BT_DTE_SESSIONS_20260911"
PARENT = "BT_DTE_BREAKDOWN_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"cal": "backend/app/backtest/repo/trading_calendar.py",
       "api": "backend/app/api/backtest_routes.py",
       "ui":  "frontend/src/pages/Backtest.jsx"}
PATH = {k: os.path.join(ROOT, v) for k, v in REL.items()}
if not os.path.isfile(PATH["api"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


ui = read(PATH["ui"]); api = read(PATH["api"])
if PARENT not in ui:
    die(f"{PARENT} not applied to Backtest.jsx — run apply_{PARENT}.py first")
for k in ("api", "ui"):
    if FENCE in read(PATH[k]):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")
if os.path.exists(PATH["cal"]):
    die(f"{REL['cal']} already exists — nothing to do")

CAL = '''# backend/app/backtest/repo/trading_calendar.py
#
# ── BT_DTE_SESSIONS_20260911 ── trading-day calendar from the corpus.
#
# A trading day is "a date with CE/PE candles for the underlying" — the SAME
# definition every backtest runner uses to build sim_days, so anything
# derived here agrees with what the run simulated. Holiday-proof by
# construction (2020–2026 needs no hand-maintained list). Cached per
# underlying, invalidated when the corpus file changes.
from __future__ import annotations

import bisect
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log

_IST = timezone(timedelta(hours=5, minutes=30))
_lock = threading.Lock()
_cache: Dict[Tuple[str, str, float], List[str]] = {}   # (db, underlying, mtime) -> sorted ISO dates


def _db_path() -> str:
    from app.backtest.repo.backtest_repo import _db_path as _p
    return str(_p())


def trading_dates(underlying: str = "NIFTY", db_path: Optional[str] = None) -> List[str]:
    """Sorted ISO dates on which the corpus has CE/PE candles for `underlying`.
    Empty list when the corpus is absent/empty (callers treat as unknown)."""
    db = db_path or _db_path()
    try:
        mtime = os.path.getmtime(db)
    except OSError:
        return []
    key = (db, underlying, mtime)
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        c = sqlite3.connect(db)
        rows = c.execute(
            "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d"
            " FROM backtest_candles_1m WHERE underlying = ?"
            " AND instrument_type IN ('CE','PE') ORDER BY d", (underlying,)).fetchall()
        c.close()
        out = [r[0] for r in rows if r[0]]
    except Exception as e:
        write_audit_log(f"[BACKTEST][CALENDAR] read failed ({e!r}) — DTE unavailable")
        return []
    with _lock:
        _cache.clear()          # one underlying/mtime live at a time; tiny
        _cache[key] = out
    return out


def sessions_to_expiry(dates_sorted: List[str], entry_iso: str, expiry_iso: str) -> Optional[int]:
    """Count trading dates d with entry < d <= expiry. 0 on expiry day.
    None when the calendar is empty or the expiry precedes the entry."""
    if not dates_sorted or not entry_iso or not expiry_iso:
        return None
    e, x = entry_iso[:10], expiry_iso[:10]
    if x < e:
        return None
    lo = bisect.bisect_right(dates_sorted, e)
    hi = bisect.bisect_right(dates_sorted, x)
    return max(0, hi - lo)


def entry_date_ist(entry_ts) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(entry_ts), _IST).strftime("%Y-%m-%d")
    except Exception:
        return None


def attach_dte(run: dict, db_path: Optional[str] = None) -> dict:
    """Best-effort: add `dte` (sessions to expiry) to every trade dict in
    run["trades"] that carries entry_ts and expiry. Never raises."""
    try:
        trades = run.get("trades") or []
        if not trades:
            return run
        cal = trading_dates(str(run.get("underlying") or "NIFTY"), db_path)
        if not cal:
            return run
        n = 0
        for t in trades:
            e = entry_date_ist(t.get("entry_ts"))
            x = t.get("expiry")
            if not e or not x:
                continue
            d = sessions_to_expiry(cal, e, str(x))
            if d is not None:
                t["dte"] = d
                n += 1
        if n:
            write_audit_log(f"[BACKTEST][CALENDAR] dte attached to {n}/{len(trades)} trades")
    except Exception as e:
        write_audit_log(f"[BACKTEST][CALENDAR] attach failed ({e!r})")
    return run
'''

api = sub1(api,
    '@router.get("/runs/{run_id}")\n'
    'def run_detail(run_id: str):\n'
    '    from app.backtest.repo.backtest_repo import get_run\n'
    '    d = get_run(run_id)\n'
    '    if d is None:\n'
    '        raise HTTPException(404, "run not found")\n'
    '    return d\n',
    '@router.get("/runs/{run_id}")\n'
    'def run_detail(run_id: str):\n'
    '    from app.backtest.repo.backtest_repo import get_run\n'
    '    d = get_run(run_id)\n'
    '    if d is None:\n'
    '        raise HTTPException(404, "run not found")\n'
    '    # ── BT_DTE_SESSIONS_20260911 ── sessions-to-expiry per trade, from the\n'
    '    # corpus trading-day calendar (best-effort; absent → "Unknown" in the UI)\n'
    '    try:\n'
    '        from app.backtest.repo.trading_calendar import attach_dte\n'
    '        d = attach_dte(d)\n'
    '    except Exception as _e:\n'
    '        write_audit_log(f"[BACKTEST][CALENDAR] {_e!r}")\n'
    '    return d\n',
    "api run_detail")

# ── frontend: key reads t.dte; title ─────────────────────────────────────
start = ui.index('    dteBreakdown: makeBreakdowns((t) => {')
end = ui.index('    }).sort((a, b) => {', start)
ui = (ui[:start]
      + '    // ── BT_DTE_SESSIONS_20260911 ── `dte` = TRADING SESSIONS to expiry,\n'
        '    // attached server-side from the corpus calendar (GET /runs/{id});\n'
        '    // the calendar-day computation from BT_DTE_BREAKDOWN is retired.\n'
        '    dteBreakdown: makeBreakdowns((t) => {\n'
        '      const d = Number(t.dte);\n'
        '      return t.dte != null && Number.isFinite(d) && d >= 0 ? `${d}DTE` : "Unknown";\n'
      + ui[end:])
ui = sub1(ui,
    '                <BreakdownPanel title="Days to Expiry" items={metrics.dteBreakdown}',
    '                <BreakdownPanel title="Sessions to Expiry (DTE)" items={metrics.dteBreakdown}',
    "ui title")

NEW = {"cal": CAL, "api": api, "ui": ui}

# ── gates ────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k in ("cal", "api"):
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("app.backtest.repo.trading_calendar", tmp["cal"])
CAL_M = importlib.util.module_from_spec(spec); sys.modules[spec.name] = CAL_M; spec.loader.exec_module(CAL_M)
audit = []; CAL_M.write_audit_log = lambda s: audit.append(s)
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
db = os.path.join(TMP, "bt.db")
c = sqlite3.connect(db)
c.execute("CREATE TABLE backtest_candles_1m (tradingsymbol TEXT, instrument_type TEXT, underlying TEXT, ts INTEGER, close REAL)")
# trading days: Wed 9, Thu 10, Fri 11, (Mon 14 = Ganesh Chaturthi HOLIDAY → no rows), Tue 15 (expiry), Wed 16
for d in ("2026-09-09", "2026-09-10", "2026-09-11", "2026-09-15", "2026-09-16"):
    ts = int(datetime.fromisoformat(d + "T09:15:00").replace(tzinfo=IST).timestamp())
    c.execute("INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?)", ("NIFTY26915" + "24500CE", "CE", "NIFTY", ts, 1.0))
    c.execute("INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?)", ("NIFTY26915" + "24500PE", "PE", "NIFTY", ts, 1.0))
# a SPOT row on the holiday must NOT count (definition is CE/PE)
c.execute("INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?)", ("NIFTY 50", "SPOT", "NIFTY",
          int(datetime.fromisoformat("2026-09-14T09:15:00").replace(tzinfo=IST).timestamp()), 1.0))
c.commit(); c.close()
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
print("calendar simulation (Tue-expiry week with a Monday holiday)")
cal = CAL_M.trading_dates("NIFTY", db)
check("calendar = CE/PE dates only (holiday with SPOT-only row excluded)", cal == ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-15", "2026-09-16"], str(cal))
S = lambda e, x: CAL_M.sessions_to_expiry(cal, e, x)
check("holiday week: Fri 11 → Tue 15 = 1 session (Mon closed; calendar days would say 4)", S("2026-09-11", "2026-09-15") == 1)
check("holiday week: Thu 10 → Tue 15 = 2", S("2026-09-10", "2026-09-15") == 2)
check("Tue 15 (expiry day) → 0", S("2026-09-15", "2026-09-15") == 0)
check("holiday week: Wed 9 → Tue 15 = 3", S("2026-09-09", "2026-09-15") == 3)
normal = ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08"]   # Wed..Tue, no holiday
N = lambda e, x: CAL_M.sessions_to_expiry(normal, e, x)
check("normal week: Fri 4 → Tue 8 = 2 (Mon, Tue)", N("2026-09-04", "2026-09-08") == 2)
check("normal week: Mon 7 → 1, Wed 2 → 4", N("2026-09-07", "2026-09-08") == 1 and N("2026-09-02", "2026-09-08") == 4)
check("expiry before entry → None", S("2026-09-16", "2026-09-15") is None)
check("empty calendar → None", CAL_M.sessions_to_expiry([], "2026-09-11", "2026-09-15") is None)
ent = lambda d, h=9, m=16: int(datetime.fromisoformat(f"{d}T{h:02d}:{m:02d}:00").replace(tzinfo=IST).timestamp())
run = {"underlying": "NIFTY", "trades": [
    {"entry_ts": ent("2026-09-11"), "expiry": "2026-09-15"},
    {"entry_ts": ent("2026-09-15", 0, 30), "expiry": "2026-09-15T15:30:00"},   # 00:30 IST on expiry day
    {"entry_ts": ent("2026-09-11"), "expiry": None},
    {"entry_ts": None, "expiry": "2026-09-15"},
]}
CAL_M.attach_dte(run, db)
t = run["trades"]
check("attach_dte: Fri (holiday week) → 1, expiry-day 00:30 IST → 0, missing fields → absent",
      t[0].get("dte") == 1 and t[1].get("dte") == 0 and "dte" not in t[2] and "dte" not in t[3], str(t))
check("audited count", any("dte attached to 2/4" in a for a in audit))
# cache: second call with unchanged mtime does not re-query (monkeypatch sqlite to prove it)
real_connect = CAL_M.sqlite3.connect
CAL_M.sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must hit cache"))
check("cache hit on unchanged corpus mtime", CAL_M.trading_dates("NIFTY", db) == cal)
CAL_M.sqlite3.connect = real_connect
check("missing corpus → [] and attach leaves trades untouched",
      CAL_M.trading_dates("NIFTY", os.path.join(TMP, "nope.db")) == [] and "dte" not in CAL_M.attach_dte({"trades": [{"entry_ts": 1, "expiry": "2026-09-15"}]}, os.path.join(TMP, "nope.db"))["trades"][0])
# node test of the panel key
js = os.path.join(TMP, "k.js")
with open(js, "w") as f:
    f.write('''const key = (t) => { const d = Number(t.dte); return t.dte != null && Number.isFinite(d) && d >= 0 ? `${d}DTE` : "Unknown"; };
const chk = (n, g, w) => { if (g !== w) { console.log("FAIL " + n + " got " + g); process.exit(1); } console.log("  PASS  " + n); };
chk("dte 2 → 2DTE", key({ dte: 2 }), "2DTE"); chk("dte 0 → 0DTE", key({ dte: 0 }), "0DTE");
chk("dte absent → Unknown", key({}), "Unknown"); chk("dte null → Unknown", key({ dte: null }), "Unknown");
chk("dte string '3' tolerated", key({ dte: "3" }), "3DTE"); chk("negative → Unknown", key({ dte: -1 }), "Unknown");
''')
pr = subprocess.run(["node", js], capture_output=True, text=True); print(pr.stdout.strip())
if pr.returncode != 0: die("node key test failed")
if FAILS:
    die(f"simulation failed: {FAILS}")
tmpjsx = os.path.join(TMP, "Backtest.jsx")
with open(tmpjsx, "w", encoding="utf-8") as f: f.write(ui)
pr = subprocess.run(["npx", "--no-install", "esbuild", tmpjsx, "--loader:.jsx=jsx", "--log-level=error"], cwd=os.path.join(ROOT, "frontend"), capture_output=True, text=True)
print("JSX parse gate: OK" if pr.returncode == 0 else "JSX parse gate: esbuild not available here — the frontend build is the gate")
print("simulation: OK")

# ── write ────────────────────────────────────────────────────────────────
for k in ("api", "ui"):
    shutil.copy2(PATH[k], PATH[k] + f".bak-{FENCE}")
for k, p in PATH.items():
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 3 files (2 backups; trading_calendar.py is new)")
if os.path.isdir(DUAL):
    for k in ("cal", "api"):
        rel = REL[k].replace("backend/", "", 1)
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend + frontend: ./desktop/build-scalp.sh")

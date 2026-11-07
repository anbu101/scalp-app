# backend/app/parity/test_parity_stale_leg.py
# ── PARITY_STALE_20261107 ── a row the app never closed and later marked
# STALE (no exit time, not OPEN) is not a position. Before: parity read
# "no exit time" as open → a friend's 2026-09-09 Velvet intraday row showed
# as "Extra in app · still open" on every day and dragged every replay back
# to d−7 (replayed from 2026-10-30 instead of 2026-11-04).
# Run: cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_stale_leg.py
import os, sys, tempfile
from datetime import date, datetime, timedelta, timezone
os.environ["HOME"] = tempfile.mkdtemp(prefix="parity_stale_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.parity import parity_core as PC          # noqa: E402
from app.api import fleet_today_routes as F       # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


def T(y, mo, dd, h, mi):
    return int(datetime(y, mo, dd, h, mi, tzinfo=IST).timestamp())


def leg(sym, short, et, open_, xt=None, ext=None, reason=None):
    return {"symbol": sym, "short": short, "qty": 650, "entry": 100.0, "exit": ext, "entry_ts": et,
            "exit_ts": xt, "reason": reason, "open": open_, "book": "PAPER"}


D = date(2026, 11, 6)
STALE_SEP = leg("NIFTY2691523600PE", False, T(2026, 9, 9, 9, 19), False, reason="STALE:intraday row from prior day")
STALE_NOV = leg("NIFTY26N0322300PE", False, T(2026, 11, 3, 9, 19), False, reason="STALE:intraday row from prior day")
CARRIED = leg("NIFTY26N0923900PE", True, T(2026, 11, 4, 13, 14), True)
CLOSED_TODAY = leg("NIFTY26N0924450CE", True, T(2026, 11, 6, 9, 29), False, T(2026, 11, 6, 9, 39), 206.0, "FLIP")
LIVE_OPEN = dict(leg("NIFTY26N0924600PE", True, T(2026, 11, 6, 9, 39), True), book="PAPER")
F._legs_for = lambda sid, warns: [STALE_SEP, STALE_NOV, CARRIED, CLOSED_TODAY, LIVE_OPEN]

legs, notes = PC.app_legs("VET_V1", D)
syms = {l["symbol"]: l for l in legs}
check("the STALE 09-09 row is not a leg (was: 'Extra in app · still open')", "NIFTY2691523600PE" not in syms, syms)
check("…nor a recent STALE row", "NIFTY26N0322300PE" not in syms, syms)
check("the carried open short is still open", syms.get("NIFTY26N0923900PE", {}).get("open") is True, syms)
check("a leg closed today is still a closed leg", syms.get("NIFTY26N0924450CE", {}).get("open") is False
      and syms["NIFTY26N0924450CE"]["exit_ts"] == T(2026, 11, 6, 9, 39))
check("an open leg entered today stays open", syms.get("NIFTY26N0924600PE", {}).get("open") is True)
check("a recent STALE row is named in the notes, with its reason",
      any("ignored 1 leg" in n and "NIFTY26N0322300PE" in n and "STALE:intraday" in n for n in notes), notes)
check("…an old one (09-09) is not, or the note would repeat forever", not any("2691523600" in n for n in notes), notes)
ent = [datetime.fromtimestamp(l["entry_ts"], IST).date() for l in legs if l["entry_ts"]]
check("replay start is driven by real positions only (11-04, not d−7)", min(ent + [D]) == date(2026, 11, 4), ent)

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL PARITY STALE-LEG CHECKS PASSED")

# backend/app/api/test_fleet_fresh_marks.py — fence FLEET_TODAY_STRIP_20261015
#
# The Traded-today table priced open legs from LTPStore with NO age check.
# A print can be hours old when the symbol's last publisher was ANOTHER
# strategy: 15 Oct, Velvet's wing 21400PE was valued at 2.50 all day — Icarus'
# 09:16 exit print — while the live price was 4.55 (row ≈ ₹1,330 below the
# panel). During market hours a print older than MARK_FRESH_S is no longer
# "live": the newest of (that print, the feed's last seen mark, a batched REST
# quote) is used, and the row says "marks as of".
#
# Run: cd backend && python3 app/api/test_fleet_fresh_marks.py

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())

import app.api.fleet_today_routes as F  # noqa: E402
from app.marketdata.ltp_store import LTPStore  # noqa: E402

FAILS: list = []
OK = [0]
IST = 19800
DAY0 = ((int(time.time()) + IST) // 86400) * 86400 - IST
T11 = DAY0 + 11 * 3600                  # 11:00 IST today (outside the 09:15–09:20 REST quiet window)
WING, MAIN, NEVER = "NIFTY26O1921400PE", "NIFTY26O1922800PE", "NIFTY26O1924000CE"
REST = {"calls": [], "answer": {}}


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


def reset(market=True):
    F._seen_marks.clear(); F._db_marks.clear(); F._marks_dirty.clear()
    F._rest_last[0] = 0.0
    F._market_open = lambda: market
    F._load_db_marks = lambda: None
    F._persist_marks = lambda now, force=False: 0
    REST["calls"].clear(); REST["answer"] = {}
    F._rest_quote = lambda syms: (REST["calls"].append(sorted(syms)), dict(REST["answer"]))[1]
    with LTPStore._lock:
        for s in (WING, MAIN, NEVER):
            LTPStore._prices.pop(s, None); LTPStore._timestamps.pop(s, None)


def put(sym, px, ts):
    with LTPStore._lock:
        LTPStore._prices[sym] = px
        LTPStore._timestamps[sym] = ts


def leg(sym, entry, short, qty=650):
    return {"open": True, "symbol": sym, "entry": entry, "short": short, "qty": qty, "gross": 0.0,
            "charges": 0.0, "approx": False, "entry_ts": DAY0 + 9 * 3600 + 1440, "exit_ts": None,
            "reason": None, "book": "PAPER"}


if __name__ == "__main__":
    print("── fresh print during market hours ──")
    reset()
    put(MAIN, 80.35, T11 - 5)
    px, ts, stale = F._mark(MAIN, T11)
    check("fresh print → live mark, not stale", (px, stale) == (80.35, False))
    F._rest_fill([leg(MAIN, 182.40, True)], T11)
    check("a freshly published symbol is never REST-quoted", REST["calls"] == [])

    print("── the Velvet wing: another strategy's hours-old print ──")
    reset()
    put(MAIN, 80.35, T11 - 5)
    put(WING, 2.50, DAY0 + 9 * 3600 + 960)          # Icarus' 09:16 exit print
    REST["answer"] = {WING: (4.55, T11 - 3)}
    legs = [leg(MAIN, 182.40, True), leg(WING, 2.85, False)]
    n = F._rest_fill(legs, T11)
    check("stale print → batched REST quote for that symbol only", REST["calls"] == [[WING]] and n == 1, REST["calls"])
    px, ts, stale = F._mark(WING, T11)
    check("wing valued at the REST price 4.55, not Icarus' 2.50", px == 4.55 and stale is False, (px, ts, stale))
    row = F._row("VET_V1", "PAPER", "PAPER", legs, T11, DAY0)
    want = (182.40 - 80.35) * 650 + (4.55 - 2.85) * 650
    check("row unrealised = panel's spread (main + wing at live prices)", abs(row["unrealised"] - want) < 0.01,
          (row["unrealised"], want))
    check("…and the row is not flagged stale", row["marks_stale"] == 0)

    print("── REST down: honest fallback ──")
    reset()
    put(WING, 2.50, DAY0 + 9 * 3600 + 960)
    F._rest_quote = lambda syms: {}
    F._rest_fill([leg(WING, 2.85, False)], T11)
    px, ts, stale = F._mark(WING, T11)
    check("no fresher source → old print used but FLAGGED stale with its time",
          px == 2.50 and stale is True and ts == DAY0 + 9 * 3600 + 960, (px, ts, stale))

    print("── throttle / quiet window / old rules ──")
    reset()
    put(WING, 2.50, DAY0 + 9 * 3600 + 960)
    F._rest_fill([leg(WING, 2.85, False)], T11)
    F._rest_fill([leg(WING, 2.85, False)], T11 + 30)
    check("REST at most once per REST_EVERY_S", len(REST["calls"]) == 1, REST["calls"])
    reset()
    put(WING, 2.50, DAY0 + 9 * 3600 + 960)
    F._rest_fill([leg(WING, 2.85, False)], DAY0 + 9 * 3600 + 17 * 60)
    check("no REST inside the 09:15–09:20 quiet window", REST["calls"] == [])
    reset()
    F._rest_fill([leg(NEVER, 5.0, False)], T11)
    check("never-seen symbol still priced (FLEET_MARKS_REST rule kept)", REST["calls"] == [[NEVER]])

    print("── after the close: unchanged behaviour ──")
    reset(market=False)
    put(WING, 2.50, DAY0 + 9 * 3600 + 960)
    px, ts, stale = F._mark(WING, DAY0 + 16 * 3600)
    check("market closed: last print used as before (no stale flag, no REST)", (px, stale) == (2.50, False))
    F._rest_fill([leg(WING, 2.85, False)], DAY0 + 16 * 3600)
    check("…and no REST after the close for a symbol that has a print", REST["calls"] == [])

    reset()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} FLEET FRESH-MARK CHECKS PASSED")

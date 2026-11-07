# backend/app/parity/test_parity_polish.py
# ── PARITY_POLISH_20261107 ── entries / exits counted apart on the summary
# line; the settings note gives each date once.
# Run: cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_polish.py
import json, os, sys, tempfile
from datetime import date, datetime, timedelta, timezone
os.environ["HOME"] = tempfile.mkdtemp(prefix="parity_polish_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.parity import parity_core as PC          # noqa: E402
from app.recorder import rec_db as RD             # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


def T(y, mo, dd, h, mi, s=0):
    return int(datetime(y, mo, dd, h, mi, s, tzinfo=IST).timestamp())


def row(entry_d, exit_d, open_side=None):
    return {"status": "MATCHED", "bt": {"entry_ts": 1}, "app": {"entry_ts": 1, "reason": "FLIP"},
            "entry_delta_min": entry_d, "exit_delta_min": exit_d, "open_side": open_side,
            "bt_pnl": 0, "app_pnl": 0}


# your 6 Nov: one pair, entry 0 min, the app still holds it
s = PC.summarise([row(0, None, "app")])
check("6 Nov (mine): 1/1 entered same minute — was '0/1 same minute'",
      s["entry_same_minute"] == 1 and s["paired"] == 1, s)
check("…no exit to compare, so no exit count", s["exit_compared"] == 0, s)
check("…'match' still needs both ends (unchanged)", s["same_minute"] == 0, s)
# the friend's 6 Nov: three pairs, one exit 0 min apart (₹3.20), one open on both
s2 = PC.summarise([row(0, 0), row(0, 0), row(0, None)])
check("friend: 3/3 entered · 2/2 exited same minute",
      (s2["entry_same_minute"], s2["exit_compared"], s2["exit_same_minute"]) == (3, 2, 2), s2)
check("…and still a match", s2["verdict"] == "match", s2)
s3 = PC.summarise([row(0, 96), row(2, 0)])
check("exits apart are counted apart", (s3["entry_same_minute"], s3["exit_same_minute"]) == (1, 1), s3)

c = RD.connect()
c.execute("INSERT INTO session_config (day, strategy_id, mode, degraded, config_json, config_hash, captured_at) "
          "VALUES (?,?,?,?,?,?,?)", ("2026-11-04", "VET_V1", "PAPER", 0, json.dumps({"trend_len": 36}), "h",
                                     T(2026, 11, 4, 9, 14)))
for ts in (T(2026, 11, 4, 9, 20, 58), T(2026, 11, 4, 9, 21, 26), T(2026, 11, 5, 10, 12)):
    c.execute("INSERT INTO config_changes (ts, strategy_id, mode, config_json, config_hash, source) "
              "VALUES (?,?,?,?,?,?)", (ts, "VET_V1", "PAPER", "{}", "x", "save"))
c.commit(); c.close()
_, notes = PC.config_for(date(2026, 11, 4), "VET_V1", until=date(2026, 11, 6))
nn = " ".join(notes)
check("note: 'on 2026-11-04 at 09:20, 09:21; on 2026-11-05 at 10:12'",
      "(on 2026-11-04 at 09:20, 09:21; on 2026-11-05 at 10:12)" in nn, notes)
check("…the date no longer repeats per time", "2026-11-04 09:2" not in nn, notes)

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL PARITY POLISH CHECKS PASSED")

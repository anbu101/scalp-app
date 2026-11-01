# backend/app/backtest/test_spot_stamp_always.py — ── SPOT_STAMP_ALWAYS_20261031 ── ── LOG_RETENTION_60_20261031 ──
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/test_spot_stamp_always.py
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


TMP = tempfile.mkdtemp(prefix="stamp_")
os.environ["HOME"] = TMP
IST = timezone(timedelta(hours=5, minutes=30))
from app.backtest.replay.replay_synth import build          # noqa: E402
from app.backtest import spot_stamp_repair as SSR           # noqa: E402

DB = os.path.join(TMP, "backtest.db")
build(DB, date(2025, 2, 17), date(2025, 2, 28))
c = sqlite3.connect(DB)
c.execute("UPDATE backtest_candles_1m SET underlying='NIFTY'")


def ds(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


BAD = [date(2025, 2, 24), date(2025, 2, 26)]
for d in BAD:
    rows = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?", (ds(d), ds(d) + 86400)).fetchall()
    c.execute("DELETE FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?", (ds(d), ds(d) + 86400))
    c.executemany("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(rows[0])),
                  [(r[0], r[1] - 60) + tuple(r[2:]) for r in rows])
stray = ds(BAD[0]) + 5 * 3600 + 38 * 60
tmpl = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type='SPOT' LIMIT 1").fetchone()
c.execute("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(tmpl)), (tmpl[0], stray) + tuple(tmpl[2:]))
c.execute("CREATE TABLE IF NOT EXISTS bt_meta (key TEXT PRIMARY KEY, value TEXT)")
c.execute("INSERT OR REPLACE INTO bt_meta (key, value) VALUES ('spot_stamp_repaired', '1')")   # the old one-time marker
c.commit()
before = c.execute("SELECT COUNT(*), SUM(close) FROM backtest_candles_1m WHERE instrument_type='SPOT'").fetchone()
good_before = c.execute("SELECT ts, close FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts",
                        (ds(date(2025, 2, 25)), ds(date(2025, 2, 25)) + 86400)).fetchall()
c.close()

print("── 1. repair ignores the one-time marker")
LOG = []
r = SSR.ensure_spot_stamps(DB, log=LOG.append)
check("marker already set, yet the two 09:14 days are repaired", r["days_repaired"] == 2 and r["rows_moved"] == 2 * 375, r)
c = sqlite3.connect(DB)
firsts = {d: datetime.fromtimestamp(c.execute("SELECT MIN(ts) FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                                               (ds(d) + 9 * 3600, ds(d) + 9 * 3600 + 1800)).fetchone()[0], IST).strftime("%H:%M") for d in BAD}
check("…both now open at 09:15", set(firsts.values()) == {"09:15"}, firsts)
check("the out-of-session stray print (05:38) is not moved", c.execute(
    "SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts=?", (stray,)).fetchone()[0] == 1)
check("correct days are untouched", c.execute("SELECT ts, close FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts",
                                               (ds(date(2025, 2, 25)), ds(date(2025, 2, 25)) + 86400)).fetchall() == good_before)
check("no row lost or duplicated", c.execute("SELECT COUNT(*), SUM(close) FROM backtest_candles_1m WHERE instrument_type='SPOT'").fetchone() == before)
c.close()
check("each repaired day is logged", sum("moved one minute later" in x for x in LOG) == 2, LOG)
LOG.clear()
r2 = SSR.ensure_spot_stamps(DB, log=LOG.append)
check("next boot: nothing to do, and nothing logged (quiet when clean)", r2["days_repaired"] == 0 and LOG == [], (r2, LOG))
c = sqlite3.connect(DB)
rows = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?", (ds(date(2025, 2, 27)), ds(date(2025, 2, 27)) + 86400)).fetchall()
c.execute("DELETE FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?", (ds(date(2025, 2, 27)), ds(date(2025, 2, 27)) + 86400))
c.executemany("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(rows[0])), [(x[0], x[1] - 60) + tuple(x[2:]) for x in rows])
c.commit()
c.close()
r3 = SSR.ensure_spot_stamps(DB, log=lambda *a: None)
check("a day written early LATER (a new backfill) is repaired on the next boot", r3["days_repaired"] == 1, r3)

print("── 2. audit logs kept 60 days")
from app.utils import housekeeping as HK                    # noqa: E402
check("retention is 60 days", HK.LOG_RETENTION_DAYS == 60)
ld = Path(TMP) / "logs"
ld.mkdir()
old, mid = ld / "2026-08-20.log", ld / "2026-10-01.log"
for f, age in ((old, 61), (mid, 30)):
    f.write_text("x")
    t = time.time() - age * 86400
    os.utime(f, (t, t))
HK._cleanup_dir(ld, "*.log", HK.LOG_RETENTION_DAYS)
check("a 61-day-old log is removed; a 30-day-old log is kept", not old.exists() and mid.exists())

print("── 3. boot runs it on its own thread")
src = (Path(SSR.__file__).resolve().parents[1] / "api_server.py").read_text()
check("api_server starts the check on a background thread", "spot-stamp-check" in src and "_spot_stamp_bg" in src)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL SPOT_STAMP_ALWAYS CHECKS PASSED")

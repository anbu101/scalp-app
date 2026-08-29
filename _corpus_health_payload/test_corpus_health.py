# backend/app/backtest/util/test_corpus_health.py
# ── CORPUS_FRAME_REPAIR_20260828 ── builds a synthetic corpus with the exact
# HDFCBANK pathology (adjusted spot + dual option frames + junk) and verifies
# the repair collapses it onto the as-traded frame without losing good rows.
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_health as CH  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
EX = date(2025, 8, 26)
fails = []


def ck(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        fails.append(name)


def ts_for(d, minute=0):
    return int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp()) + minute * 60


def build(db):
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE backtest_candles_1m (
        instrument_token INTEGER NOT NULL, ts INTEGER NOT NULL,
        underlying TEXT NOT NULL, tradingsymbol TEXT NOT NULL,
        instrument_type TEXT NOT NULL, strike REAL NOT NULL, expiry TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume INTEGER DEFAULT 0, oi INTEGER DEFAULT 0,
        PRIMARY KEY (instrument_token, ts))""")
    tok = [0]

    def ins(d, sym, typ, strike, px, m=0):
        tok[0] += 1
        m = tok[0] % 300          # keep every stamp inside the same IST day
        conn.execute("INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
                     (tok[0], ts_for(d, m), "X", sym, typ, strike, "2025-09-30",
                      px, px, px, px))

    days = []
    d = date(2025, 8, 18)
    while d <= date(2025, 9, 5):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    for d in days:
        # SPOT is back-adjusted end to end -> ~800 on BOTH sides of the ex-date
        ins(d, "X-SPOT", "SPOT", 0, 800.0)
        if d < EX:
            # as-traded frame: dense, real, near spot*2 = 1600
            for k in range(1500, 1701, 20):
                ins(d, f"X{k}CE", "CE", float(k), 20.0, m=k)
            # adjusted duplicate frame: sparse, near spot = 800
            for k in (700, 780, 800, 900):
                ins(d, f"X{k}CE", "CE", float(k), 20.0, m=k)
            # junk: far outside either frame
            ins(d, "X122CE", "CE", 122.0, 90.0, m=1)
            ins(d, "X3620CE", "CE", 3620.0, 0.1, m=2)
        else:
            # post ex-date: single clean frame near spot
            for k in range(750, 851, 10):
                ins(d, f"X{k}CE", "CE", float(k), 20.0, m=k)
            ins(d, "X175CE", "CE", 175.0, 50.0, m=3)     # post-side junk
    conn.commit()
    conn.close()
    return days


with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "X.db")
    days = build(db)
    pre_days = [d for d in days if d < EX]

    census = CH.frame_scan(db, "X", factor=2.0)
    ck("scan bands are DISJOINT (regression: junk went negative)",
       all(r["native"] + r["scaled"] + r["junk"] == r["total"] for r in census)
       and all(r["junk"] >= 0 for r in census),
       str([(r["month"], r["junk"]) for r in census]))
    s = CH.summarize_scan(census)
    ck("scan flags the corpus as not clean", not s["clean"])
    ck("scan finds dual-frame months", s["dual_frame_months"] >= 1,
       f"got {s['dual_frame_months']}")
    ck("scan counts junk rows", s["junk_rows"] == len(pre_days) * 2 + (len(days) - len(pre_days)),
       f"got {s['junk_rows']}")
    ck("no orphans in a well-formed corpus", s["orphan_rows"] == 0,
       f"got {s['orphan_rows']}")

    plan = CH.repair_frame_split(db, "X", ex_date=EX.isoformat(), factor=2.0,
                                 dry_run=True)
    ck("dry run writes nothing", plan.get("dry_run") is True)
    ck("dry run keeps the dense as-traded rows",
       plan["pre_options_keep"] == len(pre_days) * 11,
       f"got {plan['pre_options_keep']}")
    ck("dry run targets duplicate+junk for deletion",
       plan["pre_options_delete"] == len(pre_days) * 6,
       f"got {plan['pre_options_delete']}")
    ck("dry run reports the keep share", plan["pre_keep_share"] > 0.6,
       str(plan["pre_keep_share"]))
    conn = sqlite3.connect(db)
    ck("db untouched by dry run",
       conn.execute("SELECT count(*) FROM backtest_candles_1m").fetchone()[0]
       == len(pre_days) * 18 + (len(days) - len(pre_days)) * 13)  # +1 SPOT/day
    conn.close()

    r = CH.repair_frame_split(db, "X", ex_date=EX.isoformat(), factor=2.0,
                              dry_run=False, backup=False)
    ck("repair applied", r.get("applied") is True)

    conn = sqlite3.connect(db)
    # spot now in the as-traded frame pre-ex-date, untouched post
    pre_spot = conn.execute(
        "SELECT DISTINCT close FROM backtest_candles_1m WHERE instrument_type="
        "'SPOT' AND date(ts,'unixepoch','+330 minutes') < ?",
        (EX.isoformat(),)).fetchall()
    post_spot = conn.execute(
        "SELECT DISTINCT close FROM backtest_candles_1m WHERE instrument_type="
        "'SPOT' AND date(ts,'unixepoch','+330 minutes') >= ?",
        (EX.isoformat(),)).fetchall()
    ck("pre-ex-date spot rescaled to as-traded", pre_spot == [(1600.0,)], f"{pre_spot}")
    ck("post-ex-date spot untouched", post_spot == [(800.0,)], f"{post_spot}")

    strikes = [r[0] for r in conn.execute(
        "SELECT DISTINCT strike FROM backtest_candles_1m WHERE instrument_type="
        "'CE' AND date(ts,'unixepoch','+330 minutes') < ? ORDER BY strike",
        (EX.isoformat(),)).fetchall()]
    ck("duplicate adjusted frame removed", all(k >= 1500 for k in strikes),
       f"min {min(strikes)}")
    ck("as-traded ladder fully preserved", strikes == [float(k) for k in range(1500, 1701, 20)])
    ck("pre-side junk removed", 122.0 not in strikes and 3620.0 not in strikes)

    post_strikes = [r[0] for r in conn.execute(
        "SELECT DISTINCT strike FROM backtest_candles_1m WHERE instrument_type="
        "'CE' AND date(ts,'unixepoch','+330 minutes') >= ? ORDER BY strike",
        (EX.isoformat(),)).fetchall()]
    ck("post-side junk removed", 175.0 not in post_strikes)
    ck("post-side good rows preserved",
       post_strikes == [float(k) for k in range(750, 851, 10)])

    # spot vs strikes now agree pre-ex-date: ATM is findable
    ck("spot and strikes share one frame after repair",
       min(strikes) <= 1600.0 <= max(strikes))
    conn.close()

    again = CH.repair_frame_split(db, "X", ex_date=EX.isoformat(), factor=2.0,
                                  dry_run=False, backup=False)
    ck("re-run refused (spot cannot be doubled twice)",
       again.get("skipped") is True, str(again.get("reason")))
    conn = sqlite3.connect(db)
    ck("spot still 1600 after refused re-run",
       conn.execute("SELECT DISTINCT close FROM backtest_candles_1m WHERE "
                    "instrument_type='SPOT' AND date(ts,'unixepoch',"
                    "'+330 minutes') < ?", (EX.isoformat(),)).fetchall()
       == [(1600.0,)])
    ck("frame break stamped for a future runner guard",
       conn.execute("SELECT value FROM corpus_meta WHERE key='frame_break_dates'"
                    ).fetchone()[0] == EX.isoformat())
    conn.close()

    # a day with option rows but NO spot row must be caught, not silently kept
    db3 = str(Path(td) / "Z.db")
    build(db3)
    c3 = sqlite3.connect(db3)
    c3.execute("INSERT INTO backtest_candles_1m VALUES "
               "(999999,?, 'X','XORPH','CE',1600,'2025-09-30',5,5,5,5,0,0)",
               (ts_for(date(2025, 8, 30), 5),))       # Saturday, no spot
    c3.commit(); c3.close()
    ck("scan flags orphan option rows",
       CH.summarize_scan(CH.frame_scan(db3, "X"))["orphan_rows"] == 1)
    p3 = CH.repair_frame_split(db3, "X", ex_date=EX.isoformat(), factor=2.0,
                               dry_run=True)
    ck("dry run reports orphans", p3["orphan_options_delete"] == 1)
    CH.repair_frame_split(db3, "X", ex_date=EX.isoformat(), factor=2.0,
                          dry_run=False, backup=False)
    c3 = sqlite3.connect(db3)
    ck("repair deletes orphan rows",
       c3.execute("SELECT count(*) FROM backtest_candles_1m WHERE "
                  "tradingsymbol='XORPH'").fetchone()[0] == 0)
    c3.close()
    ck("orphan corpus reports clean after repair",
       CH.summarize_scan(CH.frame_scan(db3, "X"))["clean"])

    census2 = CH.frame_scan(db, "X", factor=2.0)
    ck("post-repair scan reports clean", CH.summarize_scan(census2)["clean"],
       str(CH.summarize_scan(census2)))

    # guard: a wrong factor must abort rather than delete everything
    db2 = str(Path(td) / "Y.db")
    build(db2)
    for bad in (17.0, 0.5, 5.0):
        try:
            CH.repair_frame_split(db2, "X", ex_date=EX.isoformat(), factor=bad,
                                  dry_run=True)
            ck(f"absurd factor {bad:g} aborts", False)
        except SystemExit:
            ck(f"absurd factor {bad:g} aborts", True)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

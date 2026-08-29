# backend/app/backtest/dhan/test_frame_guard.py
# ── STOCK_FRAME_GUARD_20260828 ── drives the PATCHED _write_stock_series with
# a synthetic Dhan response carrying both price frames, and proves the
# duplicate frame never reaches the corpus.
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))     # backend/
from app.backtest.dhan import stock_backfill as SB     # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
fails = []


def ck(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        fails.append(name)


class FakeSeries:
    """Minimal stand-in for RollingSeries."""
    def __init__(self, rows):
        self.timestamp = [r[0] for r in rows]
        self.strike = [r[1] for r in rows]
        self.close = [r[2] for r in rows]
        self.open = list(self.close)
        self.high = list(self.close)
        self.low = list(self.close)
        self.volume = [0] * len(rows)
        self.oi = [0] * len(rows)

    def __len__(self):
        return len(self.timestamp)


D = date(2023, 6, 15)
SPOT = 796.0                    # back-adjusted, as Dhan serves it
TS = int(datetime(D.year, D.month, D.day, 10, 0, tzinfo=IST).timestamp())

# Dhan hands back BOTH frames plus junk, exactly as observed on HDFCBANK
ROWS = (
    [(TS + i * 60, 1500.0 + i * 20, 20.0) for i in range(11)]   # as-traded
    + [(TS + 900 + i * 60, 700.0 + i * 50, 20.0) for i in range(4)]  # duplicate
    + [(TS + 1500, 122.0, 90.0), (TS + 1560, 3620.0, 0.1)]           # junk
)


def fresh_db(with_spot=True):
    p = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(p)
    conn.executescript(SB._CANDLES_DDL)
    if with_spot:
        conn.execute(
            "INSERT INTO backtest_candles_1m VALUES "
            "(1,?, 'HDFCBANK','HDFCBANK-SPOT','SPOT',0,'',?,?,?,?,0,0)",
            (TS, SPOT, SPOT, SPOT, SPOT))
    conn.commit()
    return p, conn


from app.backtest.util import corpus_health as CH        # noqa: E402
ck("guard band is IMPORTED from corpus_health, never re-declared",
   (SB._FRAME_LO, SB._FRAME_HI) == (CH.FRAME_LO, CH.FRAME_HI),
   f"guard {SB._FRAME_LO}/{SB._FRAME_HI} vs scan {CH.FRAME_LO}/{CH.FRAME_HI}")
ck("the shared band leaves a gap between frames at factor 2",
   CH.bands_disjoint(2.0), str(CH.frame_band(2.0)))

# ── guard ON: only the frame matching spot survives ──
p, conn = fresh_db()
reject = {}
spot_by_day = {D.isoformat(): SPOT}
written = SB._write_stock_series(
    conn.cursor(), FakeSeries(ROWS), underlying="HDFCBANK", type_code="CE",
    days_seen=set(), expiries_seen=set(), spot_by_day=spot_by_day,
    reject=reject)
conn.commit()

strikes = sorted(r[0] for r in conn.execute(
    "SELECT DISTINCT strike FROM backtest_candles_1m WHERE instrument_type='CE'"))
ck("only the spot-matching frame is written", strikes == [700.0, 750.0, 800.0, 850.0],
   f"got {strikes}")
ck("as-traded duplicate frame rejected", all(s < 1000 for s in strikes))
ck("junk rejected", 122.0 not in strikes and 3620.0 not in strikes)
ck("written count matches survivors", written == 4, f"got {written}")
ck("rejects counted", reject.get("out_of_frame") == 13,
   f"got {reject.get('out_of_frame')}")
ck("sample ratios captured for the operator", len(reject.get("_ratios", [])) > 0)
conn.close()

# ── the real-world orientation: spot in the AS-TRADED frame ──
p2 = tempfile.mktemp(suffix=".db")
c2 = sqlite3.connect(p2)
c2.executescript(SB._CANDLES_DDL)
reject2 = {}
w2 = SB._write_stock_series(
    c2.cursor(), FakeSeries(ROWS), underlying="HDFCBANK", type_code="CE",
    days_seen=set(), expiries_seen=set(),
    spot_by_day={D.isoformat(): SPOT * 2}, reject=reject2)
c2.commit()
s2 = sorted(r[0] for r in c2.execute(
    "SELECT DISTINCT strike FROM backtest_candles_1m WHERE instrument_type='CE'"))
ck("with as-traded spot, the as-traded ladder survives instead",
   s2 == [float(k) for k in range(1500, 1701, 20)], f"got {s2[:3]}...")
ck("adjusted duplicate rejected in that orientation", w2 == 11, f"got {w2}")
c2.close()

# ── no spot for the day: fail-closed, nothing written ──
p3 = tempfile.mktemp(suffix=".db")
c3 = sqlite3.connect(p3)
c3.executescript(SB._CANDLES_DDL)
reject3 = {}
w3 = SB._write_stock_series(
    c3.cursor(), FakeSeries(ROWS), underlying="HDFCBANK", type_code="CE",
    days_seen=set(), expiries_seen=set(), spot_by_day={}, reject=reject3)
c3.commit()
ck("day with no spot reference writes nothing", w3 == 0)
ck("no-spot rows counted separately", reject3.get("no_spot_ref") == len(ROWS))
c3.close()

# ── guard OFF (spot_by_day=None): legacy behaviour preserved ──
p4 = tempfile.mktemp(suffix=".db")
c4 = sqlite3.connect(p4)
c4.executescript(SB._CANDLES_DDL)
w4 = SB._write_stock_series(
    c4.cursor(), FakeSeries(ROWS), underlying="HDFCBANK", type_code="CE",
    days_seen=set(), expiries_seen=set(), spot_by_day=None, reject=None)
c4.commit()
ck("guard off writes every row (back-compat)", w4 == len(ROWS), f"got {w4}")
c4.close()

# ── the composed symbols really are distinct, i.e. dedupe could never help ──
syms = set()
for _, k, _ in ROWS:
    syms.add(SB.build_stock_symbol("HDFCBANK", date(2023, 6, 29), k, "CE"))
ck("both frames compose DISTINCT symbols (why dedupe never caught this)",
   len(syms) == len(ROWS), f"{len(syms)} symbols for {len(ROWS)} rows")

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

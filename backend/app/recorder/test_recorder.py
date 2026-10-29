# backend/app/recorder/test_recorder.py
#
# ── DATA_RECORDER_20261029 ── behavioural suite for the own-data recorder.
# Throw-away HOME; a fake Kite serves a synthetic NIFTY corpus (replay_synth).
# Every clock value is a REAL epoch built from an IST wall-clock time (real-
# clock rule), and every fail-closed gate is also shown opening.
#
#   cd backend && PYTHONPATH=$PWD python3 app/recorder/test_recorder.py

# Behave like the macOS Python build (SQLite without SQLITE_USE_URI): URI
# handling is off unless a connection asks for it. Best-effort — only takes
# effect where this process loads the same libsqlite3 (Linux).
try:
    import ctypes
    import ctypes.util
    ctypes.CDLL(ctypes.util.find_library("sqlite3") or "libsqlite3.so.0").sqlite3_config(17, ctypes.c_int(0))   # before sqlite3 is imported
except Exception:
    pass

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

FAILS = []
PASSES = [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


TMP = tempfile.mkdtemp(prefix="recorder_")
os.environ["HOME"] = TMP
IST = timezone(timedelta(hours=5, minutes=30))


def ts_of(d, hh, mm, ss=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST).timestamp())


from pathlib import Path                                   # noqa: E402
from app.event_bus.audit_logger import audit_muted         # noqa: E402
import app.backtest.backfill.kite_backfill as KB           # noqa: E402
KB.THROTTLE_S = 0.0
from app.recorder import rec_db as RD                      # noqa: E402
from app.recorder import rec_pull as RP                    # noqa: E402
from app.recorder import rec_jobs as RJ                    # noqa: E402
from app.backtest.replay.replay_synth import build         # noqa: E402

check("throw-away HOME in force", str(RD.app_home()).startswith(TMP), RD.app_home())
SRC = os.path.join(TMP, "src.db")
SYN_DAYS = build(SRC, date(2025, 1, 1), date(2025, 2, 28))
src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
D = date(2025, 2, 24)                                       # Monday; front expiry Thu 27 Feb
EXP_DAY = date(2025, 2, 27)


class FakeKite:
    def __init__(self):
        self.calls = []
        self.fail = set()                                   # tokens that raise

    def historical_data(self, instrument_token, from_date, to_date, interval, oi=False):
        self.calls.append((int(instrument_token), from_date, to_date))
        if int(instrument_token) in self.fail:
            raise RuntimeError("Kite says no")
        tok = 1 if int(instrument_token) == RD.SPOT_TOKEN else int(instrument_token)
        lo = int(datetime.fromisoformat(from_date).replace(tzinfo=IST).timestamp())
        hi = int(datetime.fromisoformat(to_date).replace(tzinfo=IST).timestamp())
        rows = src.execute("SELECT ts, open, high, low, close, volume, oi FROM backtest_candles_1m "
                           "WHERE instrument_token=? AND ts>=? AND ts<=? ORDER BY ts", (tok, lo, hi)).fetchall()
        return [{"date": datetime.fromtimestamp(r["ts"], IST), "open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "volume": r["volume"], "oi": r["oi"]} for r in rows]


KITE = FakeKite()
INSTR = [{"token": int(r[0]), "tradingsymbol": r[1], "instrument_type": r[2], "strike": float(r[3]), "expiry": r[4]}
         for r in src.execute("SELECT DISTINCT instrument_token, tradingsymbol, instrument_type, strike, expiry "
                              "FROM backtest_candles_1m WHERE instrument_type IN ('CE','PE')")]
NOW = [ts_of(D, 10, 7, 30)]
RP.now_ts = lambda: NOW[0]
RP.load_current_instruments = lambda: [r for r in INSTR if r["expiry"] >= RP.ist_date(NOW[0]).isoformat()]
RP.load_snapshot_instruments = lambda d: [r for r in INSTR if r["expiry"] >= d.isoformat()]
SYN = set(SYN_DAYS)
RJ.is_trading_day = lambda d: d in SYN
REAL_IS_ADMIN = RJ.is_admin
RJ.is_admin = lambda: True


def rec():
    return sqlite3.connect(str(RD.db_path()))


print("── 1. paths + schema")
check("recorder pairs with the real corpus path", RD.db_path_for(RD.main_db_path()) == RD.db_path())
check("…and lives beside backtest.db: ~/.scalp-app/backtest/recorder/NIFTY.db",
      RD.db_path() == RD.app_home() / "backtest" / "recorder" / "NIFTY.db")   # ── CHECKS_LAYOUT_20261029 ──
other = Path(TMP) / "x" / "backtest.db"
check("any other corpus path pairs with a recorder BESIDE it (suites never touch the real one)",
      RD.db_path_for(other) == Path(TMP) / "x" / "recorder" / "NIFTY.db")
c = RD.connect()
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("schema: candles + band + day_status + pull_log + session_config + meta",
      tables >= {"backtest_candles_1m", "band", "day_status", "pull_log", "session_config", "meta"}, tables)
cols = [r[1] for r in c.execute("PRAGMA table_info(backtest_candles_1m)")]
check("candle table has exactly the corpus columns, in order", ", ".join(cols) == RD.COLS, cols)
check("meta: schema version + cutover", RD.get_meta(c, "cutover") == "2026-09-01" and RD.get_meta(c, "schema_version") == "1")
idx = {r[1] for r in c.execute("PRAGMA index_list(backtest_candles_1m)")}
check("corpus indexes present", {"idx_bt1m_sym_ts", "idx_bt1m_under_exp_ts", "idx_bt1m_under_type_ts"} <= idx, idx)
c.close()

print("── 2. band rules")
check("strike range = low − 10 steps … high + 10 steps", RP.strike_range(24012.4, 24188.0) == (23500.0, 24700.0),
      RP.strike_range(24012.4, 24188.0))
check("an exact-strike range still pads 10 steps", RP.strike_range(24000, 24000) == (23500.0, 24500.0))
R = [{"token": 1, "tradingsymbol": "NIFTY2611324000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-03"},
     {"token": 2, "tradingsymbol": "NIFTY26N1024000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-10"},
     {"token": 3, "tradingsymbol": "NIFTY2611724000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-17"},
     {"token": 4, "tradingsymbol": "NIFTY26NOV24000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-24"},
     {"token": 5, "tradingsymbol": "NIFTY26DEC24000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-12-29"}]
k = RP.expiry_kinds(R, date(2026, 10, 30))
check("kinds: week / next / month (monthly by symbol)", k == {"2026-11-03": "week", "2026-11-10": "next", "2026-11-24": "month"}, k)
k = RP.expiry_kinds(R, date(2026, 11, 24))
check("monthly-expiry week: week = the monthly; next = following; no third", k == {"2026-11-24": "week", "2026-12-29": "next"}, k)
b = RP.build_band(R + [dict(R[0], token=9, tradingsymbol="NIFTY2611326000CE", strike=26000.0)], date(2026, 10, 30), 24010, 24090)
check("band = spot + in-range strikes of the 3 expiries only",
      sorted(x["token"] for x in b) == [1, 2, 4, RD.SPOT_TOKEN] and b[0]["kind"] == "spot", [x["token"] for x in b])

print("── 3. intraday pass: closed minutes only (real clock)")
c = RD.connect()
with audit_muted():
    st = RP.pull_day(c, KITE, D, final=False, reason="open", now=ts_of(D, 10, 7, 30))
last = c.execute("SELECT MAX(ts) FROM backtest_candles_1m WHERE instrument_token=?", (RD.SPOT_TOKEN,)).fetchone()[0]
check("10:07:30 pass stores through the 10:05 bar (closed ≥ 90 s)", last == ts_of(D, 10, 5), datetime.fromtimestamp(last or 0, IST))
check("…nothing at/after 10:06 (still closing)", not c.execute(
    "SELECT 1 FROM backtest_candles_1m WHERE ts>=?", (ts_of(D, 10, 6),)).fetchone())
n_spot = c.execute("SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_token=?", (RD.SPOT_TOKEN,)).fetchone()[0]
check("spot 09:15–10:05 = 51 bars, under the corpus spot identity",
      n_spot == 51 and c.execute("SELECT DISTINCT tradingsymbol, instrument_type, expiry FROM backtest_candles_1m "
                                 "WHERE instrument_token=?", (RD.SPOT_TOKEN,)).fetchall() == [("NIFTYSPOT", "SPOT", "")], n_spot)
band = c.execute("SELECT kind, COUNT(*) FROM band WHERE day=? GROUP BY kind", (D.isoformat(),)).fetchall()
check("band written for the day (spot + week contracts)", dict(band).get("spot") == 1 and dict(band).get("week", 0) >= 10, band)
check("day_status = recording, not final", st.get("quality") == "recording" and not st.get("final_at"), st)
opt_last = c.execute("SELECT MAX(ts) FROM backtest_candles_1m WHERE instrument_type IN ('CE','PE')").fetchone()[0]
check("options stored through 10:05 too", opt_last == ts_of(D, 10, 5))
n0 = len(KITE.calls)
with audit_muted():
    RP.pull_day(c, KITE, D, final=False, reason="open", now=ts_of(D, 10, 40, 0))
froms = {t: f for t, f, _ in KITE.calls[n0:]}
check("next pass resumes each instrument at 10:06 (incremental)", froms.get(RD.SPOT_TOKEN) == f"{D} 10:06:00", froms.get(RD.SPOT_TOKEN))
check("no minute stored twice (PK)", c.execute("SELECT COUNT(*) FROM (SELECT instrument_token, ts FROM backtest_candles_1m "
                                               "GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0] == 0)

print("── 4. final pass + quality gate")
with audit_muted():
    st = RP.pull_day(c, KITE, D, final=True, reason="open", now=ts_of(D, 15, 41))
check("final: spot has all 375 bars (09:15–15:29)", st.get("spot_bars") == 375, st)
check("final: graded clean, final_at set, completeness reported", st.get("quality") == "clean" and st.get("final_at")
      and st.get("completeness") is not None, st)
check("nothing outside 09:15–15:29", not c.execute("SELECT 1 FROM backtest_candles_1m WHERE "
                                                   "(ts - ?) % 86400 < 33300 OR (ts - ?) % 86400 >= 55800",
                                                   (ts_of(D, 0, 0), ts_of(D, 0, 0))).fetchone())
check("recorded bars equal Kite's bars", [tuple(r) for r in c.execute(
    "SELECT ts, close FROM backtest_candles_1m WHERE instrument_token=? ORDER BY ts", (RD.SPOT_TOKEN,))] ==
      [tuple(r) for r in src.execute("SELECT ts, close FROM backtest_candles_1m WHERE instrument_token=1 AND ts>=? "
                                     "AND ts<?", (ts_of(D, 0, 0), ts_of(D, 23, 0)))])

print("── 5. errors retry, then settle")
D2 = date(2025, 2, 25)
bad = next(r["token"] for r in INSTR if r["expiry"] == "2025-02-27")
KITE.fail = {bad}
NOW[0] = ts_of(D2, 15, 41)
with audit_muted():
    st = RP.pull_day(c, KITE, D2, final=True, reason="open", now=NOW[0])
check("a Kite error → partial, error counted, NOT final yet (retry)", st.get("quality") == "partial" and st.get("error_n") == 1
      and not st.get("final_at") and st.get("attempts") == 1, st)
with audit_muted():
    RP.pull_day(c, KITE, D2, final=True, reason="open", now=NOW[0] + 60)
    st = RP.pull_day(c, KITE, D2, final=True, reason="open", now=NOW[0] + 120)
check("after 3 attempts the day is final (partial), not retried forever", st.get("final_at") and st.get("attempts") == 3, st)
KITE.fail = set()
check("the error is in pull_log", c.execute("SELECT COUNT(*) FROM pull_log WHERE day=? AND status='error'",
                                            (D2.isoformat(),)).fetchone()[0] == 3)

print("── 6. catch-up of a past day: expired contracts are lost, never invented")
D3 = date(2025, 2, 18)                                      # front expiry Thu 20 Feb, expired by 3 Mar
NOW[0] = ts_of(date(2025, 2, 21), 20, 0)
with audit_muted():
    st = RP.pull_day(c, KITE, D3, final=True, reason="catchup", now=ts_of(date(2025, 2, 21), 20, 0))
lost = c.execute("SELECT COUNT(DISTINCT instrument_token) FROM pull_log WHERE day=? AND status='expired'",
                 (D3.isoformat(),)).fetchone()[0]
check("expired contracts logged as expired", lost > 0 and st.get("lost_n") == lost, (lost, st))
check("no bars stored for an expired contract", not c.execute(
    "SELECT 1 FROM backtest_candles_1m b JOIN band ON band.instrument_token=b.instrument_token AND band.day=? "
    "WHERE band.kind<>'spot' AND band.expiry < ? AND b.ts>=? AND b.ts<?", (D3.isoformat(), "2025-02-21", ts_of(D3, 0, 0), ts_of(D3, 23, 0))).fetchone())
check("spot still recorded for that day (spot never expires)", st.get("spot_bars") == 375, st)
check("graded partial (contracts lost)", st.get("quality") == "partial", st)
n_exp = c.execute("SELECT COUNT(*) FROM pull_log WHERE day=? AND status='expired'", (D3.isoformat(),)).fetchone()[0]
with audit_muted():
    RP.pull_day(c, KITE, D3, final=True, reason="catchup", now=NOW[0] + 60)
check("expired contracts are logged once, not on every pass", c.execute(
    "SELECT COUNT(*) FROM pull_log WHERE day=? AND status='expired'", (D3.isoformat(),)).fetchone()[0] == n_exp)
c.close()

print("── 7. union view: corpus ∪ recorder, the corpus wins")
MAIN = Path(TMP) / "u" / "backtest.db"
MAIN.parent.mkdir(parents=True)
m = sqlite3.connect(str(MAIN))
m.executescript((Path(RD.__file__).resolve().parents[1] / "backtest" / "repo" / "schema.sql").read_text())
row = (RD.SPOT_TOKEN, ts_of(D, 9, 15), "NIFTY", "NIFTYSPOT", "SPOT", 0.0, "", 1.0, 1.0, 1.0, 11111.0, 0, 0)
m.execute(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
m.commit()
m.close()
UREC = RD.db_path_for(MAIN)
shutil.copy(str(RD.db_path()), str(UREC.parent / "NIFTY.db") if UREC.parent.mkdir(parents=True) is None else "")
u = RD.open_union_ro(MAIN, "NIFTY")
v = u.execute("SELECT close FROM backtest_candles_1m WHERE tradingsymbol='NIFTYSPOT' AND ts=?", (ts_of(D, 9, 15),)).fetchall()
check("a minute in both → exactly one row, the corpus's", v == [(11111.0,)], v)
n = u.execute("SELECT COUNT(*) FROM backtest_candles_1m WHERE tradingsymbol='NIFTYSPOT' AND ts>=? AND ts<?",
              (ts_of(D, 0, 0), ts_of(D, 23, 0))).fetchone()[0]
check("recorder-only minutes are visible through the view", n == 375, n)
u.close()
p = RD.open_union_ro(MAIN, "HDFCBANK")
check("non-NIFTY → plain corpus (no recorder attached)", "rec" not in RD.attached(p))
p.close()
NOREC = Path(TMP) / "v" / "backtest.db"
NOREC.parent.mkdir(parents=True)
shutil.copy(str(MAIN), str(NOREC))
p = RD.open_union_ro(NOREC, "NIFTY")
check("no recorder file → plain corpus, and none is created", "rec" not in RD.attached(p) and not RD.db_path_for(NOREC).exists())
p.close()
try:
    RD.open_union_ro(MAIN, "NIFTY").execute("DELETE FROM main.backtest_candles_1m")
    ro = False
except sqlite3.OperationalError:
    ro = True
check("the union connection is read-only", ro)

f_ = RD.open_fill(MAIN)
check("open_fill: recorder writable, corpus attached read-only as bt (works without SQLITE_USE_URI)",
      "bt" in RD.attached(f_) and RD.corpus_minutes(f_, "NIFTYSPOT", ts_of(D, 0, 0), ts_of(D, 23, 0)) == {ts_of(D, 9, 15)})
try:
    f_.execute("DELETE FROM bt.backtest_candles_1m")
    bt_ro = False
except sqlite3.OperationalError:
    bt_ro = True
check("…and the corpus cannot be written through it", bt_ro)
f_.close()
BAD = Path(TMP) / "bad" / "backtest.db"
BAD.parent.mkdir(parents=True)
BAD.write_text("not a database")
try:
    RD.open_fill(BAD).execute("SELECT 1 FROM bt.backtest_candles_1m")
    loud = False
except Exception:
    loud = True
check("open_fill fails closed when the corpus cannot be read (no blind copy)", loud)

print("── 8. one-time move of post-cutover replay-filled option rows")
BT = Path(TMP) / "mv" / "backtest.db"
BT.parent.mkdir(parents=True)
m = sqlite3.connect(str(BT))
m.executescript((Path(RD.__file__).resolve().parents[1] / "backtest" / "repo" / "schema.sql").read_text())
cut = RD.cutover_epoch()
rows = []
for i in range(5):                                          # Kite-token options after cutover → MOVE
    rows.append((12345 + i, cut + 86400 * 30 + 33300 + 60 * i, "NIFTY", f"NIFTY26O27{24000 + i}CE", "CE", 24000.0 + i, "2026-10-27", 10, 11, 9, 10.5, 1, 1))
rows.append((12399, cut + 86400 * 30 + 33300, "NIFTY", "NIFTY26O2724500PE", "PE", 24500.0, "2026-10-27", 10, 11, 9, 10.5, 1, 1))
rows.append((RD.DHAN_TOKEN_FLOOR + 7, cut + 86400 * 5 + 33300, "NIFTY", "NIFTY2690824000CE", "CE", 24000.0, "2026-09-08", 5, 6, 4, 5.5, 1, 1))   # Dhan → stay
rows.append((RD.SPOT_TOKEN, cut + 86400 * 30 + 33300, "NIFTY", "NIFTYSPOT", "SPOT", 0.0, "", 1, 1, 1, 1, 0, 0))                         # spot → stay
rows.append((55555, cut - 86400 * 90 + 33300, "NIFTY", "NIFTY2660924000CE", "CE", 24000.0, "2026-06-09", 5, 6, 4, 5.5, 1, 1))             # June Kite → stay
m.executemany(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
m.commit()
m.close()
MREC = RD.db_path_for(BT)
r1 = RD.move_post_cutover_options(BT)
m = sqlite3.connect(str(BT))
left = sorted(r[0] for r in m.execute("SELECT instrument_token FROM backtest_candles_1m"))
m.close()
check("moved exactly the 6 Kite-token CE/PE rows dated after the cutover", r1.get("ok") and r1.get("moved") == 6, r1)
check("Dhan rows, spot and the June Kite rows stay in backtest.db",
      left == sorted([RD.DHAN_TOKEN_FLOOR + 7, RD.SPOT_TOKEN, 55555]), left)
mr = sqlite3.connect(str(MREC))
check("the recorder now holds those 6 rows, identical", sorted(tuple(x) for x in mr.execute(
    f"SELECT {RD.COLS} FROM backtest_candles_1m")) == sorted(tuple(map(lambda v: v, x)) for x in rows[:6]))
check("the move is recorded in recorder meta", json.loads(RD.get_meta(mr, "moved_post_cutover_options"))["moved"] == 6)
mr.close()
r2 = RD.move_post_cutover_options(BT)
check("re-run is a no-op (idempotent)", r2.get("ok") and r2.get("moved") == 0, r2)
# a conflicting copy already in the recorder → verify fails → nothing deleted
m = sqlite3.connect(str(BT))
m.execute(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (77777, cut + 86400 * 31 + 33300, "NIFTY", "NIFTY26O2724700CE", "CE", 24700.0, "2026-10-27", 10, 11, 9, 10.5, 1, 1))
m.commit()
m.close()
mr = sqlite3.connect(str(MREC))
mr.execute(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (77777, cut + 86400 * 31 + 33300, "NIFTY", "NIFTY26O2724700CE", "CE", 24700.0, "2026-10-27", 10, 11, 9, 99.9, 1, 1))
mr.commit()
mr.close()
r3 = RD.move_post_cutover_options(BT)
m = sqlite3.connect(str(BT))
still = m.execute("SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_token=77777").fetchone()[0]
m.close()
check("verify fails on a mismatching copy → nothing deleted from backtest.db", (not r3.get("ok")) and still == 1, r3)

print("── 9. the schedule (one driver; real-clock windows)")
for p_ in (RD.db_path(), Path(str(RD.db_path()) + "-wal"), Path(str(RD.db_path()) + "-shm")):
    if p_.exists():
        p_.unlink()
from app.license import license_state                     # noqa: E402
license_state.license_allows_strategy = lambda sid: True
KF = [0]


def kf():
    KF[0] += 1
    return KITE


def pas(d, hh, mm, ss=0, **kw):
    NOW[0] = ts_of(d, hh, mm, ss)
    with audit_muted():
        return RJ.run_pass(NOW[0], kite_factory=kw.get("kf", kf))


def steps(r):
    return [s[0] for s in r["steps"]]


r = pas(D, 9, 10)
check("09:10: nothing (before the 09:14 snapshot, no Kite)", steps(r) == [] and KF[0] == 0, r)
r = pas(D, 9, 14, 5)
cfg_n = rec().execute("SELECT COUNT(*) FROM session_config WHERE day=?", (D.isoformat(),)).fetchone()[0]
check("09:14: session_config captured for every licensed strategy", "session_config" in steps(r) and cfg_n >= 10, (steps(r), cfg_n))
one = rec().execute("SELECT mode, config_hash, config_json FROM session_config WHERE day=? AND strategy_id='TSG_V1'", (D.isoformat(),)).fetchone()
check("…with mode, a config hash and the full config", one and one[1] and json.loads(one[2]) is not None, one)
cap0 = rec().execute("SELECT MAX(captured_at) FROM session_config").fetchone()[0]
r = pas(D, 9, 15, 30)
check("09:15: no Kite call inside the 09:13–09:18 burst window", KF[0] == 0 and "session_config" not in steps(r), r)
check("session_config captured once per day", rec().execute("SELECT MAX(captured_at) FROM session_config").fetchone()[0] == cap0)
r = pas(D, 9, 44)
check("09:44: no pull yet (first at 09:45)", "incremental" not in steps(r) and KF[0] == 0, r)
r = pas(D, 9, 46)
check("09:46: first incremental pull", "incremental" in steps(r), r)
r = pas(D, 10, 0)
check("10:00: none (30-minute cadence)", "incremental" not in steps(r), r)
r = pas(D, 10, 16, 30)
check("10:16: next incremental", "incremental" in steps(r), r)
r = pas(D, 15, 35)
check("15:35 on a non-expiry day: no final yet, no catch-up/backup in session", not set(steps(r)) & {"final", "catchup", "backup"}, r)
r = pas(D, 15, 40, 10)
check("15:40: final pull", "final" in steps(r), r)
check("…day graded clean", RP.day_status(rec(), D).get("quality") == "clean", RP.day_status(rec(), D))
r = pas(D, 15, 41)
check("15:41: final not repeated", "final" not in steps(r), r)
r = pas(D, 15, 46)
bk = [s for s in r["steps"] if s[0] == "backup"]
check("15:46: one backup copy (default folder on this Mac, flagged)", bk and bk[0][1] and bk[0][1]["off_machine"] is False
      and Path(bk[0][1]["path"]).exists(), r)
chk = sqlite3.connect(bk[0][1]["path"]) if bk and bk[0][1] else None
check("the backup is a consistent, readable copy", chk is not None and chk.execute(
    "SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0] == rec().execute("SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0])
r = pas(D, 15, 47)
check("one backup per day", "backup" not in steps(r), r)
# expiry day: final at 15:32
NOW[0] = ts_of(EXP_DAY, 9, 50)
pas(EXP_DAY, 9, 50)
r = pas(EXP_DAY, 15, 31)
check("expiry day 15:31: not yet", "final" not in steps(r), r)
r = pas(EXP_DAY, 15, 32, 30)
check("expiry day 15:32: final pull (that week's contracts leave Kite at expiry)", "final" in steps(r), r)
# catch-up outside the session
RD.CUTOVER = date(2025, 2, 1)
r = pas(date(2025, 3, 1), 11, 0)                            # Saturday
cu = [s[1] for s in r["steps"] if s[0] == "catchup"]
check("weekend: catch-up of past sessions without a final pull, newest first, ≤ 3 per pass", 0 < len(cu) <= 3
      and cu == sorted(cu, reverse=True) and EXP_DAY.isoformat() not in cu and D.isoformat() not in cu, cu)
RD.CUTOVER = date(2025, 2, 26)
check("never before the cutover", all(d >= "2025-02-26" for d in [x.isoformat() for x in RJ.catchup_days(rec(), date(2025, 3, 1))]),
      RJ.catchup_days(rec(), date(2025, 3, 1)))
RD.CUTOVER = date(2026, 9, 1)
# no Kite → error + 10-minute back-off
RJ.STATE.kite_backoff_until = 0
r = pas(date(2025, 2, 20), 10, 20, kf=lambda: None)
check("no Kite session → the pass stops with a clear error", "no Kite session" in (r.get("error") or ""), r)
KF[0] = 0
r = pas(date(2025, 2, 20), 10, 25)
check("…and Kite is not asked again for 10 minutes", "backoff" in steps(r) and KF[0] == 0, r)
r = pas(date(2025, 2, 20), 10, 31)
check("…then it resumes", "incremental" in steps(r), r)

print("── 10. gates")
RJ.is_admin = lambda: False
RJ.STATE.worker = None
RJ.recorder_driver_job()
check("non-admin: the driver starts nothing", RJ.STATE.worker is None)
RJ.is_admin = lambda: True
from app.license import license_state as _ls               # noqa: E402
_lvl = _ls.ui_level
_ls.ui_level = lambda: "standard"
check("is_admin: a standard licence → closed", REAL_IS_ADMIN() is False)
_ls.ui_level = lambda: "admin"
check("is_admin: an admin licence → open", REAL_IS_ADMIN() is True)
_ls.ui_level = lambda: (_ for _ in ()).throw(RuntimeError("licence not loaded"))
check("is_admin: licence not loaded (raises) → closed", REAL_IS_ADMIN() is False)
_ls.ui_level = _lvl
RJ.is_admin = lambda: True
spawned = []
RJ._spawn = (lambda f, *a, **k: spawned.append(f) or True)
RJ.recorder_driver_job()
check("admin: the driver hands the pass to the worker", spawned == [RJ.run_pass], spawned)
# caffeinate (macOS path, faked)
started = []


class _P:
    def __init__(self, args, **kw):
        started.append(args)

    def poll(self):
        return None


RJ.sys = type("S", (), {"platform": "darwin"})()
RJ.subprocess = type("SP", (), {"Popen": _P, "DEVNULL": None})()
RJ.STATE.awake = None
ok1 = RJ.ensure_awake(ts_of(D, 9, 5))
ok2 = RJ.ensure_awake(ts_of(D, 9, 6))
check("macOS: one `caffeinate -i -t <until 15:45>` kept alive", ok1 and ok2 and len(started) == 1
      and started[0][:3] == ["caffeinate", "-i", "-t"] and int(started[0][3]) == (ts_of(D, 15, 45) - ts_of(D, 9, 5)), started)
RJ.STATE.awake = None
check("after 15:45 nothing is started", RJ.ensure_awake(ts_of(D, 15, 50)) is False and len(started) == 1)
import sys as _sys                                          # noqa: E402
RJ.sys = _sys

print("── 11. routes on the REAL api_server app (admin gate)")
import app.api_server as API                               # noqa: E402
from app.license import license_state as LS                # noqa: E402


def call(path, level, method="GET", body=b""):
    LS.ui_level = lambda: level
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m_):
        sent.append(m_)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"content-type", b"application/json")] if body else [],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "root_path": ""}
    with audit_muted():
        asyncio.run(API.app(scope, receive, send))
    status = next(m_["status"] for m_ in sent if m_["type"] == "http.response.start")
    return status, b"".join(m_.get("body", b"") for m_ in sent if m_["type"] == "http.response.body")


st, body = call("/api/recorder/status", "admin")
js = json.loads(body) if st == 200 else {}
check("status: admin → 200 with recorded days", st == 200 and js.get("exists") and len(js.get("days") or []) >= 3, (st, body[:200]))
check("status reports the backup folder as on-this-Mac", js.get("backup_off_machine") is False)
st, _ = call("/api/recorder/status", "standard")
check("status: non-admin → 403", st == 403, st)
BK = os.path.join(TMP, "offsite")
st, body = call("/api/recorder/settings", "admin", "POST", json.dumps({"backup_dir": BK, "keep": 2}).encode())
check("settings: backup_dir saved and probed writable", st == 200 and os.path.isdir(BK), (st, body[:200]))
check("…now reported off-machine", json.loads(call("/api/recorder/status", "admin")[1])["backup_off_machine"] is True)
st, _ = call("/api/recorder/settings", "admin", "POST", json.dumps({"keep": 99}).encode())
check("settings: keep out of range → 400", st == 400, st)
st, _ = call("/api/recorder/pull", "admin", "POST", json.dumps({"day": "2099-01-01"}).encode())
check("pull: a future day → 400", st == 400, st)
src_api = Path(API.__file__).read_text()
check("api_server schedules the driver every minute under its own id",
      'id="data_recorder_driver"' in src_api and "recorder_driver_job" in src_api and 'minute="*"' in src_api)

print("── 12. Backfill inventory + Backtest coverage")
import app.api.backtest_routes as BRT                       # noqa: E402
bt_dir = RD.app_home() / "backtest"
bt_dir.mkdir(parents=True, exist_ok=True)
shutil.copy(str(BT), str(bt_dir / "backtest.db"))
inv = BRT.corpus_inventory(refresh=True)
names = {i["db_name"] for i in inv["items"]}
check("inventory lists the recorder as its own rows", "recorder/NIFTY.db" in names and "backtest.db" in names, names)
rrow = [i for i in inv["items"] if i["db_name"] == "recorder/NIFTY.db" and i["kind"] == "SPOT"]
check("…with its own date range", rrow and rrow[0]["date_from"] <= rrow[0]["date_to"], rrow)
FC = Path(TMP) / "fc.db"
f = sqlite3.connect(str(FC))
f.executescript((Path(RD.__file__).resolve().parents[1] / "backtest" / "repo" / "schema.sql").read_text())
aug31 = ts_of(date(2026, 8, 31), 15, 29)
f.executemany(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
    (RD.DHAN_TOKEN_FLOOR + 1, aug31, "NIFTY", "NIFTY2690124000CE", "CE", 24000.0, "2026-09-01", 1, 1, 1, 1, 0, 0),
    (RD.DHAN_TOKEN_FLOOR + 2, aug31 - 86400 * 7, "NIFTY", "NIFTY2682524000CE", "CE", 24000.0, "2026-08-25", 1, 1, 1, 1, 0, 0),
    (4242, ts_of(date(2026, 10, 20), 10, 0), "NIFTY", "NIFTY26O2724000CE", "CE", 24000.0, "2026-10-27", 1, 1, 1, 1, 0, 0),
    (RD.SPOT_TOKEN, ts_of(date(2026, 10, 29), 10, 0), "NIFTY", "NIFTYSPOT", "SPOT", 0.0, "", 1, 1, 1, 1, 0, 0)])
f.commit()
fc = BRT._full_chain_to(f, FC, "NIFTY")
check("full_chain_to = last minute of the Dhan chain (skips a later Kite-only expiry and spot)", fc == aug31,
      datetime.fromtimestamp(fc or 0, IST))
f.close()

print("── 12b. PARITY_CAPTURE_20261029: held contracts + config changes")
import app.api.fleet_today_routes as FT                     # noqa: E402
FAR = {"token": 999001, "tradingsymbol": "NIFTY25FEB30000CE", "instrument_type": "CE", "strike": 30000.0, "expiry": "2025-02-27"}
INSTR.append(FAR)
_donor = src.execute("SELECT instrument_token FROM backtest_candles_1m WHERE instrument_type='CE' AND ts>=? AND ts<? "
                     "GROUP BY instrument_token HAVING COUNT(*)=375 LIMIT 1",
                     (ts_of(date(2025, 2, 26), 0, 0), ts_of(date(2025, 2, 26), 23, 0))).fetchone()[0]
_orig_hist = KITE.historical_data


def _hist(instrument_token, from_date, to_date, interval, oi=False):
    return _orig_hist(_donor if int(instrument_token) == 999001 else instrument_token, from_date, to_date, interval, oi)


KITE.historical_data = _hist
D5 = date(2025, 2, 26)
_fleet_ids, _legs_for = FT.FLEET_IDS, FT._legs_for
FT.FLEET_IDS = ["VET_V1", "TSG_V1"]
LEGS5 = {"VET_V1": [{"symbol": FAR["tradingsymbol"], "entry_ts": ts_of(date(2025, 2, 24), 10, 0), "exit_ts": None, "open": True, "book": "PAPER"}],
         "TSG_V1": [{"symbol": "NIFTY25FEB31000PE", "entry_ts": ts_of(D5, 9, 16), "exit_ts": ts_of(D5, 11, 0), "open": False, "book": "LIVE"},
                    {"symbol": "NIFTY25FEB29000CE", "entry_ts": ts_of(date(2025, 2, 20), 9, 16), "exit_ts": ts_of(date(2025, 2, 21), 15, 0),
                     "open": False, "book": "PAPER"},
                    {"symbol": "NIFTYNXT5025FEB1000CE", "entry_ts": ts_of(D5, 9, 16), "exit_ts": None, "open": True, "book": "PAPER"}]}
FT._legs_for = lambda sid, w: list(LEGS5.get(sid, []))
held = RP.app_contracts(D5)
check("held = contracts open at any time that day (carried, or opened and closed); not other days, not NIFTYNXT50",
      held == {FAR["tradingsymbol"], "NIFTY25FEB31000PE"}, held)
c = RD.connect()
with audit_muted():
    st = RP.pull_day(c, KITE, D5, final=True, reason="catchup", now=ts_of(D5, 15, 45))
hb = c.execute("SELECT kind, reason FROM band WHERE day=? AND instrument_token=999001", (D5.isoformat(),)).fetchone()
check("a far-OTM held contract (30000 CE, miles outside ±10 steps) joins the band as 'held'", hb == ("held", "held"), hb)
nb = c.execute("SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_token=999001 AND ts>=? AND ts<?",
               (ts_of(D5, 0, 0), ts_of(D5, 23, 0))).fetchone()[0]
check("…and its candles are recorded for the whole day", nb == 375, nb)
check("a held contract missing from the instrument list is noted, not invented",
      "NIFTY25FEB31000PE" in (st.get("notes") or ""), st.get("notes"))
c.close()
FT._legs_for = lambda sid, w: (_ for _ in ()).throw(RuntimeError("db locked"))
c = RD.connect()
with audit_muted():
    st2 = RP.pull_day(c, KITE, date(2025, 2, 25), final=False, reason="open", now=ts_of(date(2025, 2, 25), 11, 0))
check("leg reader failing never stops the recording", st2.get("spot_bars", 0) > 0, st2)
c.close()
FT.FLEET_IDS, FT._legs_for = _fleet_ids, _legs_for
KITE.historical_data = _orig_hist

def _changes(sid):
    return rec().execute("SELECT mode, config_hash, source FROM config_changes WHERE strategy_id=? ORDER BY id", (sid,)).fetchall()


RJ.is_admin = lambda: True
check("config change: first save of a new config is logged", RJ.record_config_change("ZZ_TEST", {"a": 1, "trade_execution_mode": "PAPER_LIVE"},
                                                                                      now=ts_of(D5, 11, 5)) and len(_changes("ZZ_TEST")) == 1)
check("…the same config again is not logged twice (hash)", not RJ.record_config_change("ZZ_TEST", {"trade_execution_mode": "PAPER_LIVE", "a": 1})
      and len(_changes("ZZ_TEST")) == 1)
RJ.record_config_change("ZZ_TEST", {"a": 2, "trade_execution_mode": "LIVE"}, now=ts_of(D5, 12, 0))
check("…a changed config is logged with its mode", [r[0] for r in _changes("ZZ_TEST")] == ["PAPER_LIVE", "LIVE"], _changes("ZZ_TEST"))
RJ.is_admin = lambda: False
check("standard licence: nothing logged", not RJ.record_config_change("ZZ_TEST", {"a": 3}) and len(_changes("ZZ_TEST")) == 2)
RJ.is_admin = lambda: True
check("an unserialisable value never raises", RJ.record_config_change("ZZ_TEST", {"a": object()}) in (True, False))
from app.config import strategy_loader as SLD               # noqa: E402
n0 = len(_changes("VET_V1"))
SLD.save_strategy_config("VET_V1", {"trend_len": 36, "trade_execution_mode": "PAPER_LIVE"})
RJ.wait_for_config_logs()
check("save_strategy_config logs the change through the hook (on its own thread)", len(_changes("VET_V1")) == n0 + 1 and _changes("VET_V1")[-1][2] == "save")
_lk = sqlite3.connect(str(RD.db_path()), timeout=1)
_lk.execute("BEGIN IMMEDIATE")                              # a pass holding the recorder's write lock
import time as _t                                           # noqa: E402
_t0 = _t.monotonic()
SLD.save_strategy_config("VET_V1", {"trend_len": 37})
check("a Settings save never waits on a locked recorder", _t.monotonic() - _t0 < 1.0, _t.monotonic() - _t0)
_lk.rollback()
_lk.close()
RJ.wait_for_config_logs()
check("…and the change is logged once the lock clears", '"trend_len": 37' in (rec().execute(
    "SELECT config_json FROM config_changes WHERE strategy_id='VET_V1' ORDER BY id DESC LIMIT 1").fetchone() or [""])[0])
RJ.record_config_change_async = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
SLD.save_strategy_config("VET_V1", {"trend_len": 40})
check("a failing hook never breaks a Settings save", SLD.load_strategy_config("VET_V1").get("trend_len") == 40)

print("── 13. one-time move from ~/.scalp-app/recorder (CHECKS_LAYOUT_20261029)")
import shutil as _sh                                        # noqa: E402
_new = RD.db_path().parent
_tmpkeep = Path(TMP) / "keep_recorder"
_sh.move(str(_new), str(_tmpkeep))                          # pretend the new place is empty
_old = RD.old_db_dir()
_sh.copytree(str(_tmpkeep), str(_old))
_c = sqlite3.connect(str(_old / "NIFTY.db"))
_n0 = _c.execute("SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0]
_c.close()
_r = RD.relocate_from_old()
check("relocate: rows identical, old folder gone, settings + backups travel with it",
      _r.get("ok") and _r.get("rows") == _n0 and not _old.exists() and RD.db_path().exists()
      and (RD.db_path().parent / "settings.json").exists(), _r)
check("relocate again: nothing at the old place → no-op", RD.relocate_from_old() == {"ok": True, "moved": False, "reason": "nothing at the old place"})
_sh.move(str(RD.db_path().parent), str(_old))               # back to the old place, new place absent
_c2 = RD.connect()                                          # first touch after the upgrade
_c2.close()
check("self-heal: the first connect() moves the old folder instead of creating an empty DB",
      not _old.exists() and RD.db_path().exists() and sqlite3.connect(str(RD.db_path())).execute(
          "SELECT COUNT(*) FROM backtest_candles_1m").fetchone()[0] == _n0)
_old.mkdir()
(_old / "NIFTY.db").write_text("x")
check("both places hold data → refuses to merge", RD.relocate_from_old().get("ok") is False)
_sh.rmtree(str(_old))

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL DATA_RECORDER CHECKS PASSED")
shutil.rmtree(TMP, ignore_errors=True)

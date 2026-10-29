# backend/app/backtest/replay/test_replay_stale_rows.py
#
# ── REPLAY_STALE_OPEN_20261028 ── an orphaned intraday row that the engine
# marked STALE on a later boot (status='STALE', no exit time) is NOT an open
# position. Before this fix the Dashboard's live chart (find_keys) treated
# "no exit time" as open, so a strategy with one old orphan always opened
# that weeks-old trade (and streamed it as open from its entry day to today)
# instead of today's open trade or the watch chart.
#
# Real app.db migrations in a throw-away HOME; rows go into the REAL
# vet_trades / tma2_trades tables and are read back through the Trades
# page's OWN loader (get_paper_trades), exactly as the Dashboard does.
# Only the chart builder (replay_core.build_replay_for) is replaced, by a
# recorder of the run it is handed.
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/replay/test_replay_stale_rows.py
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


TMP = tempfile.mkdtemp(prefix="replay_stale_")
os.environ["HOME"] = TMP
os.environ.setdefault("SCALP_TEST_MODE", "1")
IST = timezone(timedelta(hours=5, minutes=30))


def ts_of(d, hh, mm):
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST).timestamp())


from pathlib import Path                                   # noqa: E402
import app.backtest.replay.replay_core as RC               # noqa: E402
RC.runs_db_path = lambda: os.path.join(TMP, "backtest.db")
import app.backtest.replay.live_replay as LR               # noqa: E402
from app.db.sqlite import get_conn                         # noqa: E402
from app.db.migrations.runner import run_migrations        # noqa: E402
from app.event_bus.audit_logger import audit_muted         # noqa: E402

APP = get_conn()
with audit_muted():
    run_migrations(APP)
check("app.db migrated in the throw-away HOME", str(TMP) in str(Path.home()))

TODAY = date(2026, 10, 28)
SEP9 = date(2026, 9, 9)
LR._now_ts = lambda: ts_of(TODAY, 16, 44)                  # the screenshot: 16:44 IST, market closed

COLS = ("group_id, leg_role, mode, direction, tradingsymbol, token, instrument_type, strike, expiry, qty, lots, "
        "lot_size, entry_ts, entry_price, status, exit_ts, exit_price, exit_reason, pnl, charges, net_pnl")


def vet(gid, sym, entry_ts, status, exit_ts=None, exit_px=None, reason=None, role="MAIN", dirn="SHORT"):
    APP.execute(f"INSERT INTO vet_trades ({COLS}) VALUES ({','.join('?' * 21)})",
                (gid, role, "PAPER", dirn, sym, 0, sym[-2:], 0, "", 65, 1, 65, entry_ts, 150.0, status,
                 exit_ts, exit_px, reason, None if exit_ts is None else -500.0,
                 None if exit_ts is None else 40.0, None if exit_ts is None else -540.0))
    APP.commit()
    return APP.execute("SELECT max(id) FROM vet_trades").fetchone()[0]


# the friend's VET_V1: a 9 Sep intraday row the engine later marked STALE
# (no exit), plenty of closed history, and two trades closed today (flat now)
stale_id = vet("VG_SEP9", "NIFTY2691523600PE", ts_of(SEP9, 9, 19), "STALE",
               reason="STALE:intraday row from prior day")
for i in range(6):
    d = SEP9 + timedelta(days=7 * (i + 1))
    vet(f"VG_H{i}", "NIFTY26O1323500PE", ts_of(d, 10, 0), "CLOSED", ts_of(d, 14, 0), 120.0, "XOVER")
today_ids = [vet("VG_T1", "NIFTY26N0322500CE", ts_of(TODAY, 9, 40), "CLOSED", ts_of(TODAY, 11, 59), 160.0, "FLIP"),
             vet("VG_T2", "NIFTY26N0322400PE", ts_of(TODAY, 12, 5), "CLOSED", ts_of(TODAY, 14, 10), 140.0, "SL")]

# ── 1) the Trades page loader: what find_keys actually sees ───────────────
rows = LR.load_rows("trades")
srow = next(r for r in rows if r["paper_trade_id"] == f"vet_trades:{stale_id}")
check("loader: STALE row reads state CLOSED with no exit time",
      srow["state"] == "CLOSED" and srow["exit_time"] is None, srow)
check("_row_open: STALE/no-exit row is not open", LR._row_open(srow) is False)

# ── 2) Dashboard click on VET (Traded today): no open position → watch ────
f = LR.find_keys(strategy="VET_V1", book="PAPER")
check("VET flat today + one STALE orphan → the live watch chart, not the orphan",
      f.get("watch") is True and f["key"] == "w|VET_V1", f)
check("watch nav still lists the orphan and today's trades (Prev reaches them)",
      f"t|vet_trades:{stale_id}" in f["nav"] and all(f"t|vet_trades:{i}" in f["nav"] for i in today_ids)
      and f["nav"][-1] == "w|VET_V1", f.get("nav"))
f2 = LR.find_keys(strategy="VET_V1")
check("same without a book filter", f2.get("watch") is True, f2)

# ── 3) a genuinely open position still wins ───────────────────────────────
open_id = vet("VG_OPEN", "NIFTY26N0322300PE", ts_of(TODAY, 14, 35), "OPEN")
f = LR.find_keys(strategy="VET_V1", book="PAPER")
check("an OPEN row today is the one opened", f["key"] == f"t|vet_trades:{open_id}", f)
APP.execute("DELETE FROM vet_trades WHERE id=?", (open_id,))
APP.commit()

carry_id = vet("VG_CARRY", "NIFTY26N0322100PE", ts_of(TODAY - timedelta(days=1), 10, 0), "OPEN")
f = LR.find_keys(strategy="VET_V1", book="PAPER")
check("a carried (prior-day) OPEN position is still treated as open",
      f["key"] == f"t|vet_trades:{carry_id}", f)
APP.execute("DELETE FROM vet_trades WHERE id=?", (carry_id,))
APP.commit()

# ── 4) the same rule for the other strategies that mark STALE (TMA_V2) ────
try:
    APP.execute("INSERT INTO tma2_trades (group_id, mode, direction, tradingsymbol, instrument_type, qty, entry_ts, "
                "entry_price, status, exit_ts, exit_price, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TG_SEP9", "PAPER", "SELL", "NIFTY26S2223500CE", "CE", 65, ts_of(SEP9, 9, 30), 90.0, "STALE",
                 None, None, "STALE:prior day"))
    APP.execute("INSERT INTO tma2_trades (group_id, mode, direction, tradingsymbol, instrument_type, qty, entry_ts, "
                "entry_price, status, exit_ts, exit_price, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TG_T1", "PAPER", "SELL", "NIFTY26N0323600CE", "CE", 65, ts_of(TODAY, 10, 0), 80.0, "CLOSED",
                 ts_of(TODAY, 14, 12), 95.0, "SL"))
    APP.commit()
    f = LR.find_keys(strategy="TMA_V2", book="PAPER")
    check("TMA_V2 with a STALE orphan → watch chart", f.get("watch") is True, f)
except Exception as e:                                 # schema drift: say so, don't hide it
    check("TMA_V2 rows inserted", False, repr(e))

# ── 5) opening the orphan itself (Prev): drawn as a closed-out day, not live ─
SEEN = {}


def rec_build(run, trade_id):
    SEEN["run"] = run
    legs = run["groups"][0]
    return {"ok": True, "group": {"ids": [t["id"] for t in legs], "legs": [{"id": t["id"]} for t in legs]},
            "parity": {"tier": "none"}, "notes": []}


RC.build_replay_for = rec_build
out = LR.build_live_replay("trades", f"vet_trades:{stale_id}", fill=False, live=True)
check("orphan replay builds", out.get("ok") is True, out)
check("orphan: chart window is its own day (not 9 Sep → today)",
      SEEN["run"]["window_until_ts"] is None, SEEN["run"].get("window_until_ts"))
check("orphan: not reported open / not streamed live",
      out["live"]["open"] is False and out["live"].get("active") is False and out["group"]["open_legs"] == 0,
      out.get("live"))
check("orphan: the leg carries stale=True", SEEN["run"]["trades"][0].get("stale") is True)
check("orphan: a note says why it has no exit",
      any("STALE" in n and "no exit" in n for n in out["notes"]), out["notes"])
check("orphan: no 'still open' note",
      not any("still open" in n for n in out["notes"]), out["notes"])

# a real open leg is still live-capable and runs to now
open_id = vet("VG_OPEN2", "NIFTY26N0322300PE", ts_of(TODAY, 14, 35), "OPEN")
out = LR.build_live_replay("trades", f"vet_trades:{open_id}", fill=False, live=False)
check("open leg: window runs to now", SEEN["run"]["window_until_ts"] == LR._now_ts())
check("open leg: reported open", out["live"]["open"] is True and out["group"]["open_legs"] == 1)
check("open leg: stale=False", SEEN["run"]["trades"][0].get("stale") is False)

# ── 6) Analytics rows (no state field) keep the exit-time rule ─────────────
check("_row_open: analytics-style row, no exit → open", LR._row_open({"exit_time": None}) is True)
check("_row_open: analytics-style row, exited → closed", LR._row_open({"exit_time": 1761600000}) is False)
check("_row_open: state OPEN wins", LR._row_open({"state": "OPEN", "exit_time": 1761600000}) is True)
check("_row_open: status STALE → not open", LR._row_open({"status": "STALE", "exit_time": None}) is False)

total = PASSES[0] + len(FAILS)
print()
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")

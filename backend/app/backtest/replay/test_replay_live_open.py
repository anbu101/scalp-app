# backend/app/backtest/replay/test_replay_live_open.py
#
# ── REPLAY_LIVE_OPEN_20261111 ── 11 Nov: the friend's LIVE Icarus condor was
# open (4 legs + 1 closed by SL), but his Dashboard's live chart said
# "watching for an entry · no open position". Live legs are booked in the
# shared `trades` table through the REAL insert_trade(state="PROTECTED"),
# and the replay's open rule only accepted state == "OPEN". Paper rows
# (state OPEN) — and Velvet's own table (status OPEN) — were fine.
#
# Real app.db migrations in a throw-away HOME, rows written by the real
# trades_repo, read back through the Analytics page's own loader.
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/replay/test_replay_live_open.py
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


TMP = tempfile.mkdtemp(prefix="replay_live_open_")
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
import app.db.trades_repo as TR                            # noqa: E402

APP = get_conn()
with audit_muted():
    run_migrations(APP)
check("app.db migrated in the throw-away HOME", str(TMP) in str(Path.home()))

TODAY = date(2026, 11, 11)
LR._now_ts = lambda: ts_of(TODAY, 13, 40)
GID = "IC-20261111-228ab988"


def leg(tid, slot, sym, ts, px, short):
    TR.insert_trade(trade_id=tid, strategy_id="IC_V2", slot=f"IC_V2_{slot}", symbol=sym, token=0,
                    entry_price=px, qty=195, buy_order_id="OID", sl_price=0.0, tp_price=0.0,
                    tp_mode="GTT", state="PROTECTED", trade_direction="SHORT" if short else "LONG",
                    group_id=GID, trade_class=slot)
    APP.execute("UPDATE trades SET entry_time=? WHERE trade_id=?", (ts, tid))
    APP.commit()


# the friend's live condor exactly as booked at 09:18, then L2 SL + adjustment
leg("ic-l1", "L1", "NIFTY26N1725750CE", ts_of(TODAY, 9, 18), 93.50, True)
leg("ic-l2", "L2", "NIFTY26N1725000PE", ts_of(TODAY, 9, 18), 93.48, True)
leg("ic-l3", "L3", "NIFTY26N1726800CE", ts_of(TODAY, 9, 18), 7.50, False)
leg("ic-l4", "L4", "NIFTY26N1723850PE", ts_of(TODAY, 9, 18), 7.85, False)
APP.execute("UPDATE trades SET exit_time=?, exit_price=?, state='CLOSED' WHERE trade_id='ic-l2'",
            (ts_of(TODAY, 13, 30), 132.95))
leg("ic-adj", "L2-ADJ", "NIFTY26N1724850PE", ts_of(TODAY, 13, 33), 90.35, False)
# yesterday's fully closed condor (history)
y = TODAY - timedelta(days=1)
TR.insert_trade(trade_id="ic-old", strategy_id="IC_V2", slot="IC_V2_L1x", symbol="NIFTY26N1725500CE",
                token=0, entry_price=80.0, qty=195, buy_order_id="O", sl_price=0.0, tp_price=0.0,
                tp_mode="GTT", state="PROTECTED", trade_direction="SHORT", group_id="IC-OLD", trade_class="L1")
APP.execute("UPDATE trades SET entry_time=?, exit_time=?, exit_price=?, state='CLOSED' WHERE trade_id='ic-old'",
            (ts_of(y, 9, 18), ts_of(y, 15, 20), 60.0))
APP.commit()

rows = LR.load_rows("analytics")
r1 = next((r for r in rows if r.get("trade_id") == "ic-l1"), None)
check("Analytics loader returns the live leg with its state machine value",
      r1 is not None and str(r1.get("state")).upper() == "PROTECTED", r1)
check("_row_open: PROTECTED with no exit time → open (was: not open)", LR._row_open(r1) is True)
check("_row_open: BUY_PLACED with no exit time → open", LR._row_open({"state": "BUY_PLACED", "exit_time": None}))
check("_row_open: CLOSED → not open", LR._row_open({"state": "CLOSED", "exit_time": None}) is False)
check("_row_open: STALE → not open (28 Oct orphans stay out)",
      LR._row_open({"status": "STALE", "exit_time": None}) is False)
check("_row_open: any state with an exit time → not open",
      LR._row_open({"state": "PROTECTED", "exit_time": 1762800000}) is False)
check("_row_open: explicit OPEN still wins", LR._row_open({"state": "OPEN", "exit_time": 1762800000}) is True)

f = LR.find_keys(strategy="IC_V2")
check("Dashboard live chart: the live condor is an OPEN position, not 'watching for an entry'",
      not f.get("watch") and f.get("key", "").startswith("a|"), f)
check("…the newest open leg is the one opened (the 13:33 adjustment)",
      f.get("key") == "a|" + LR.row_key(next(r for r in rows if r.get("trade_id") == "ic-adj"), "analytics"), f)
check("…with Next leading to the watch chart", f.get("nav", [])[-1:] == ["w|IC_V2"], f.get("nav"))

# the replay of an open live leg is drawn as open (not stale), whole condor grouped
SEEN = {}


def rec_build(run, trade_id):
    SEEN["run"] = run
    legs = run["groups"][0]
    return {"ok": True, "group": {"ids": [t["id"] for t in legs], "legs": [{"id": t["id"]} for t in legs]},
            "parity": {"tier": "none"}, "notes": []}


RC.build_replay_for = rec_build
key = LR.row_key(next(r for r in LR.load_rows("analytics") if r.get("trade_id") == "ic-adj"), "analytics")
out = LR.build_live_replay("analytics", key, fill=False, live=False)
check("open live replay builds", out.get("ok") is True, out)
legs = SEEN.get("run", {}).get("trades", [])
check("all five condor legs drawn (4 open + the SL'd one)", len(legs) == 5, len(legs))
check("no open live leg is marked stale",
      not any(t.get("stale") for t in legs), [(t.get("symbol"), t.get("stale")) for t in legs])
check("reported open, 4 open legs", out["live"]["open"] is True and out["group"]["open_legs"] == 4, out.get("live"))

APP.execute("UPDATE trades SET exit_time=?, exit_price=?, state='CLOSED' WHERE trade_id IN "
            "('ic-l1','ic-l3','ic-l4','ic-adj')", (ts_of(TODAY, 15, 28), 50.0))
APP.commit()
f = LR.find_keys(strategy="IC_V2")
check("once every leg is closed → back to the watch chart", f.get("watch") is True, f)

print(f"\n{PASSES[0]}/{PASSES[0] + len(FAILS)} checks passed")
sys.exit(1 if FAILS else 0)

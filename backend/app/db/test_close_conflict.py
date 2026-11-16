# test_close_conflict.py — CLOSE_CONFLICT_ALERT_20261116 (DEF-032)
# A live close that changes no row because the row is ALREADY closed at a
# different exit must raise a CRITICAL alert naming both exits (16 Nov:
# Icarus L2 bought back at 19.30, row still held the false 14 Nov close at
# 96.90). A repeat close at the same price stays quiet; close_trade never
# raises because of the check.
#   PYTHONPATH=backend python3 backend/app/db/test_close_conflict.py
import os
import re
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SCALP_TEST_MODE", "1")

SENT = []
tg = types.ModuleType("app.api.telegram_api")
tg.notify_critical = lambda d: SENT.append(d)
sys.modules["app.api.telegram_api"] = tg

import app.db.trades_repo as TR                                      # noqa: E402

MIG = (ROOT / "app/db/migrations/020_relax_exit_reason_for_ic.sql").read_text()
TABLE = re.search(r"CREATE TABLE IF NOT EXISTS trades_v3 \(.*?\n\);", MIG, re.S).group(0) \
    .replace("trades_v3", "trades")
TRIGS = re.findall(r"(CREATE TRIGGER (?:IF NOT EXISTS )?\w+\s+BEFORE UPDATE ON trades.*?END;)", MIG, re.S)
CON = sqlite3.connect(":memory:", check_same_thread=False)
CON.executescript(TABLE + "\n" + "\n".join(TRIGS))
TR.get_conn = lambda: CON

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


def row(tid, *, closed_at=None, exit_px=None, reason=None):
    CON.execute(
        "INSERT INTO trades(trade_id,strategy_id,slot,symbol,token,entry_time,entry_price,qty,"
        "buy_order_id,sl_price,tp_price,tp_mode,state,exit_time,exit_price,exit_reason,trade_class) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "IC_V2", "IC_V2_" + tid, "NIFTY26N1724950PE", 1, 1794541694, 96.9, 195, "b", 0, 0, "GTT",
         "CLOSED" if closed_at else "PROTECTED", closed_at, exit_px, reason, "L2"))
    CON.commit()


def db(tid):
    return CON.execute("SELECT exit_price, exit_reason, exit_time FROM trades WHERE trade_id=?",
                       (tid,)).fetchone()


# C1 open row → closed, True, no alert
row("t1")
SENT.clear()
r = TR.close_trade(trade_id="t1", exit_price=19.3, exit_order_id="o1", exit_reason="NEXT_OPEN")
check("C1 open row: closed, returns True, no alert", r is True and db("t1")[:2] == (19.3, "NEXT_OPEN")
      and not SENT, f"{r} {db('t1')} {SENT}")

# C2 same close again (same price) → False, quiet
SENT.clear()
r = TR.close_trade(trade_id="t1", exit_price=19.3, exit_order_id="o1", exit_reason="NEXT_OPEN")
check("C2 repeat close at the same price: returns False, no alert", r is False and not SENT, f"{r} {SENT}")
r = TR.close_trade(trade_id="t1", exit_price=19.35, exit_order_id="o1", exit_reason="NEXT_OPEN")
check("C2b repeat close within 0.5%: no alert", not SENT, str(SENT))

# C3 the 16 Nov case: row closed 14 Nov at 96.90 BROKER_EXIT, real close 19.30
row("t3", closed_at=1794612610, exit_px=96.9, reason="BROKER_EXIT")
SENT.clear()
r = TR.close_trade(trade_id="t3", exit_price=19.3, exit_order_id="o3", exit_reason="NEXT_OPEN")
msg = (SENT[0].get("message") if SENT else "") or ""
check("C3 16 Nov case: returns False, row unchanged", r is False and db("t3") == (96.9, "BROKER_EXIT", 1794612610),
      f"{r} {db('t3')}")
check("C3 16 Nov case: ONE critical alert naming both exits, the contract and the trade id",
      len(SENT) == 1 and SENT[0].get("severity") == "error" and SENT[0].get("strategy_id") == "IC_V2"
      and "19.3" in msg and "96.9" in msg and "BROKER_EXIT" in msg and "NIFTY26N1724950PE" in msg
      and "t3" in msg, str(SENT))

# C4 unknown trade id → False, quiet
SENT.clear()
r = TR.close_trade(trade_id="nope", exit_price=10.0, exit_order_id=None, exit_reason="SL")
check("C4 unknown trade id: returns False, no alert", r is False and not SENT, f"{r} {SENT}")

# C5 the alert itself fails → close_trade still returns, never raises
row("t5", closed_at=1794612610, exit_px=50.0, reason="BROKER_EXIT")
tg.notify_critical = lambda d: (_ for _ in ()).throw(RuntimeError("telegram down"))
try:
    r = TR.close_trade(trade_id="t5", exit_price=20.0, exit_order_id=None, exit_reason="SL")
    ok = r is False
except Exception as e:
    ok, r = False, repr(e)
check("C5 alert fails: close_trade does not raise", ok, str(r))
tg.notify_critical = lambda d: SENT.append(d)

# C6 exit price None on an already-closed row → quiet (nothing to compare)
SENT.clear()
TR.close_trade(trade_id="t3", exit_price=None, exit_order_id=None, exit_reason="BROKER_EXIT")
check("C6 repeat close with no price: no alert", not SENT, str(SENT))

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL CLOSE-CONFLICT CHECKS PASSED")

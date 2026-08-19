# backend/app/engine/tma2/test_tma2_entry_paths.py
#
# ── TMA_V2 ENTRY / UNWIND PATH PROOF ──
# Regression guard for the 2026-08-19 NameError pair (the v10.2.9 bug
# class: a name that only resolves on a path nobody exercises until real
# money is on it). _persist_entry had no `sig` in scope and _unwind_hedge
# had no `pend` — both would have raised on the FIRST live entry and the
# FIRST hedge unwind respectively. pyflakes caught them; this keeps them
# caught. Also pins the E1/E2 condition stamping and the sold-side rule
# (the SELL leg is always OPPOSITE the trend side).
# Run standalone:  python3 test_tma2_entry_paths.py
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.config import strategy_loader as SL                     # noqa: E402
from app.engine.tma2 import tma2_trade_manager as TM             # noqa: E402
from app.engine.tma2.tma2_common import TMA2Repo                 # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class _FakeExec:
    def place_market_sell(self, sym, qty):
        return "oid-1"

    def order_status(self, oid):
        return {"status": "COMPLETE", "average_price": 3.5}


def _manager(db):
    SL.load_strategy_config_ex = lambda sid: ({
        "s1": {"main": {"premium_max": 200, "lots": 10, "sl_pct": 12,
                        "tp_pct": 10, "sl_unit": "PCT", "tp_unit": "ABS"},
               "hedge": {"premium_max": 5, "lots": 10},
               "max_trades_per_day": 0},
        "trade_mode": "POSITIONAL", "trade_execution_mode": "PAPER"}, False)
    return TM.TMA2TradeManager({}, TMA2Repo(db_path=db))


def test_entry_and_unwind_paths():
    db = tempfile.mktemp(suffix=".db")
    m = _manager(db)
    snap = m._cfg_snapshot()
    for cond, trend in (("E1", "PE"), ("E2", "CE")):
        sig = {"ts": 1_700_000_000, "side": trend, "cond": cond,
               "spot": 25000.0}
        g = m._build_group(
            sig=sig, snap=snap, mode="PAPER", sell_sym="NIFTYX_S",
            hedge_sym="NIFTYX_H", sell_qty=650, buy_qty=650,
            smeta={"token": 1, "strike": 25000, "expiry": "2026-08-27"},
            hmeta={"token": 2, "strike": 24000, "expiry": "2026-08-27"},
            sell_entry=180.0, hedge_entry=4.0, hedge_fb=False)
        check(f"{cond}: group carries cond", g.get("cond") == cond)
        check(f"{cond}: sold side is OPPOSITE the trend",
              g["sell"]["side"] == ("PE" if trend == "CE" else "CE"))
        m._persist_entry(g)          # NameError before the fix
    rows = list(sqlite3.connect(db).execute(
        "SELECT condition,direction,instrument_type FROM tma2_trades "
        "ORDER BY id"))
    check("_persist_entry writes both legs of both groups", len(rows) == 4,
          str(rows))
    check("E1 rows stamped E1, E2 rows stamped E2",
          [r[0] for r in rows] == ["E1", "E1", "E2", "E2"])
    check("each group is one SELL + one BUY",
          sorted(r[1] for r in rows[:2]) == ["BUY", "SELL"])
    check("E1 (PE trend) sells CE; E2 (CE trend) sells PE",
          rows[0][2] == "CE" and rows[2][2] == "PE")

    m.executor = _FakeExec()
    m._unwind_hedge("NIFTYX_H", 650, 4.0, cond="E2")   # NameError before
    last = list(sqlite3.connect(db).execute(
        "SELECT condition,direction FROM tma2_trades ORDER BY id DESC "
        "LIMIT 1"))
    check("_unwind_hedge records a BUY row with the signal's cond",
          last and last[0] == ("E2", "BUY"), str(last))
    m._unwind_hedge("NIFTYX_H", 650, 4.0)
    d = list(sqlite3.connect(db).execute(
        "SELECT condition FROM tma2_trades ORDER BY id DESC LIMIT 1"))
    check("unwind cond defaults to E1 when unknown", d[0][0] == "E1")


if __name__ == "__main__":
    test_entry_and_unwind_paths()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print("\nALL TMA_V2 ENTRY/UNWIND PATH TESTS PASSED")
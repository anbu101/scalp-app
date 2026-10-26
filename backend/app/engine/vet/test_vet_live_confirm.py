# backend/app/engine/vet/test_vet_live_confirm.py
#
# ── VET_LIVE_CONFIRM_20261026 ── VET_V1 LIVE order handling against a
# Kite-shaped fake broker (real tuple returns, RMS rejection, margin
# rejection of a hedge sell while a short is open, resting limits that keep
# working until cancelled). Every scenario below FAILS on the pre-fix code.
# Run standalone:  python3 test_vet_live_confirm.py
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.engine.tma2 import test_tma2_exit_paths as T            # noqa: E402
from app.engine.vet import vet_manager as VM                     # noqa: E402
from app.engine.vet.vet_common import VetRepo                    # noqa: E402

FAILS = []
SPOT = 24000
PAGES = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def chain(side, ts):
    out = []
    for k in range(23000, 25050, 50):
        px = max(0.05, 180 - abs(k - SPOT) * 0.35)
        out.append({"tradingsymbol": f"NIFTY26OCT{k}{side}", "token": 1000 + k,
                    "strike": float(k), "expiry": "2026-10-27",
                    "instrument_type": side, "ltp": round(px, 2)})
    return out


def px_of(sym, e):
    k = int(sym[10:15])
    return round(max(0.05, 180 - abs(k - SPOT) * 0.35), 2)


def mk(px_fn=px_of):
    db = os.path.join(tempfile.mkdtemp(prefix="vetlive_"), "v.db")
    repo = VetRepo(db)
    repo.ensure_schema()
    m = VM.VetManager({"leg_action": "SELL", "hedge_enabled": True,
                       "hedge_max_premium": 3.0, "atm_offset": 0,
                       "eod_square": True, "_spot": SPOT,
                       "quantity": {"lots": 1, "lot_size": 65}},
                      repo=repo, chain_fn=chain, quote_fn=lambda s: 150.0,
                      executor=None, mode="LIVE")
    clock = T.Clock()
    br = T.FakeBroker(clock, px_fn)
    br.stale_ltp = {}
    clock.broker = br
    m.executor, m._sleep, m._clock = br, clock.sleep, clock
    m._page = lambda msg, code="": PAGES.append(msg)
    return m, br, db


def rows(db):
    return list(sqlite3.connect(db).execute(
        "SELECT leg_role,status,entry_order_id,entry_price,exit_price "
        "FROM vet_trades ORDER BY id"))


def test_entry_persists_and_uses_fills():
    m, br, db = mk()
    p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    r = rows(db)
    check("V1: LIVE entry opens", p is not None)
    check("V1: BOTH legs persisted (tuple order id no longer breaks insert)",
          len(r) == 2 and all(isinstance(x[2], str) for x in r), str(r))
    check("V1: wing bought BEFORE the short, both confirmed filled",
          [o["transaction_type"] for o in br.orders] == ["BUY", "SELL"]
          and all(o["status"] == "COMPLETE" for o in br.orders))


def test_wing_unfilled_no_short():
    m, br, db = mk()
    br.modify_ok = False
    br.stale_ltp = {s: 0.5 for s in [f"NIFTY26OCT{k}PE" for k in range(23000, 25050, 50)]}
    real_buy = br.place_buy
    br.place_buy = lambda s, t, q: (br._place(s, "BUY", q, 0.0), 0.0, q)   # limit below any trade
    p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    check("V2: wing never filled → no position", p is None and m.pos is None)
    check("V2: NO short was sent (LD5 needs a FILL, not an order)",
          not [o for o in br.orders if o["transaction_type"] == "SELL"])
    check("V2: the resting wing order was CANCELLED", not br.open_orders())
    br.place_buy = real_buy


def test_short_rejected_unwinds_wing():
    m, br, db = mk()
    br.reject_sell_entry = True
    p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    check("V3: RMS-rejected short → no position held",
          p is None and m.pos is None)
    check("V3: wing sold back and confirmed flat",
          all(q == 0 for q in br.net.values()), str(br.net))


def test_stuck_exit_keeps_wing_then_recovers():
    m, br, db = mk()
    m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
    short = m.pos["main"]["tradingsymbol"]
    wing = m.pos["wing"]["tradingsymbol"]
    br.modify_ok = False
    base = br.px_fn
    br.px_fn = lambda s, e: 260.0 if s == short else base(s, e)   # gap up
    br.stale_ltp = {short: 180.0}                                    # buy-back @181.8 rests
    out = m.close_position("SL", ts=2000)
    check("V4: stuck buy-back → nothing booked, position kept",
          out is None and m.pos is not None and m.exit_pending)
    check("V4: wing NOT sold while the short is open",
          br.net.get(wing) == 65, str(br.net))
    check("V4: the unfilled buy-back was cancelled (no stray fill later)",
          not br.open_orders())
    check("V4: rows still OPEN", [x[1] for x in rows(db)] == ["OPEN", "OPEN"])
    check("V4: page names the open short and the manual order",
          PAGES and "FIRST" in PAGES[-1], PAGES[-1:] if PAGES else "")
    fl = m.on_decision({"action": VM.FLIP, "side": "CE", "bar_ts": 3000,
                        "condition": 1}, ts=3000)
    check("V5: FLIP while the close is stuck does NOT open a new position",
          fl and fl.get("opened") is False and m.pos["main"]["tradingsymbol"] == short)
    br.modify_ok = True                          # re-peg works again
    res = m.retry_exit(4000)
    check("V4: next-minute retry closes short THEN wing, books broker fills",
          res is not None and m.pos is None
          and all(q == 0 for q in br.net.values())
          and rows(db)[1][4] >= 260.0, str(rows(db)))


if __name__ == "__main__":
    for t in (test_entry_persists_and_uses_fills, test_wing_unfilled_no_short,
              test_short_rejected_unwinds_wing,
              test_stuck_exit_keeps_wing_then_recovers):
        t()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print("\nALL VET_V1 LIVE CONFIRM TESTS PASSED")

# test_exit_once_restart.py — D12 for Icarus, Timberwolf and Velvet
# The app is killed while an exit order is being sent (it reaches Kite, its id
# is never saved). After the restart the exit must be completed with exactly
# the strategy's own quantity — never sent a second time (on a shared strike
# that buys back the OTHER strategy's short) and never lost.
#   PYTHONPATH=backend python3 backend/app/engine/test_exit_once_restart.py
import os
import sys
import io
import sqlite3
import contextlib
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/app/engine")
os.environ.setdefault("SCALP_TEST_MODE", "1")

import test_fleet_live as F                                    # noqa: E402
from app.execution import broker_truth as BT                    # noqa: E402
from app.engine.ic import ic_carry_store                        # noqa: E402

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class Died(BaseException):
    pass


def filled(k, s, t):
    return sum(int(o["filled_quantity"]) for o in k.orders
               if o["tradingsymbol"] == s and o["transaction_type"] == t)


def crash_on(ex, sym, reach=True, nth=1):
    """The app is killed during the nth exit send on `sym`."""
    real = ex.place_buy_exit
    seen = [0]

    def dies(symbol=None, qty=None, reason=None, *a, **kw):
        if symbol == sym:
            seen[0] += 1
            if seen[0] == nth:
                if reach:
                    real(symbol, qty, reason)
                raise Died()
        return real(symbol, qty, reason)
    ex.place_buy_exit = dies


def restart(f):
    f2 = F.Fleet(kite=f.kite, tmp=f.tmp, restore=True)
    f2.now = f.now
    c = sqlite3.connect(f.t2_db)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM tma2_trades WHERE status='OPEN'")]
    if rows:
        quiet(f2.t2.adopt_rows, rows, kite=f.kite)
    quiet(f2.vet.resume_from_db)
    f2.ic.restore_session_payload(ic_carry_store.load_session("IC_V2"))
    return f2


def net(k):
    return {s[-7:]: q for s, q in k.net.items() if q}


# ── per strategy: enter (optionally beside a sharer), exit, retry after restart
def vet_case(shared, reach=True, unreadable=False, wait=0):
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    if shared:
        quiet(f.t2_enter)
    quiet(f.vet_enter)
    s = F.S_VT
    crash_on(f.vet.executor, s, reach)
    try:
        quiet(f.vet_exit)
    except Died:
        pass
    k.clock.sleep(wait)
    f2 = restart(f)
    if unreadable:
        real = k.get_orders
        k.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("orders: timeout"))
        n0 = filled(k, s, "BUY") + len(k.orders)
        quiet(f2.vet_exit, ts=4000)
        sent = len(k.orders) + filled(k, s, "BUY") - n0
        k.get_orders = real
    for i in range(4):
        k.clock.sleep(61)
        if f2.vet.pos is not None:
            quiet(f2.vet_exit, ts=4100 + 60 * i)
    other = -130 if shared else 0
    return dict(bought=filled(k, s, "BUY"), want=130, net=k.net.get(s, 0), want_net=other,
                closed=f2.vet.pos is None, sent_while_unreadable=sent if unreadable else 0)


def t2_case(shared, reach=True, unreadable=False, wait=0):
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    if shared:
        quiet(f.vet_enter)
    quiet(f.t2_enter)
    s = F.S_VT
    crash_on(f.t2.executor, s, reach)
    try:
        quiet(f.t2.kill_close, f.ts_of(14, 30))
    except Died:
        pass
    k.clock.sleep(wait)
    f2 = restart(f)
    sent = 0
    if unreadable:
        real = k.get_orders
        k.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("orders: timeout"))
        n0 = len(k.orders)
        if f2.t2.group:
            quiet(f2.t2.kill_close, f.ts_of(14, 31))
        sent = len([o for o in k.orders[n0:] if o["tradingsymbol"] == s])
        k.get_orders = real
    for m in range(2, 6):
        k.clock.sleep(61)
        if f2.t2.group:
            quiet(f2.t2.kill_close, f.ts_of(14, 30) + 60 * m)
    other = -130 if shared else 0
    return dict(bought=filled(k, s, "BUY"), want=130, net=k.net.get(s, 0), want_net=other,
                closed=f2.t2.group is None, sent_while_unreadable=sent)


def ic_case(shared, reach=True, unreadable=False, wait=0, slices=None, nth=1):
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.ic_enter)
    if shared:
        quiet(f.ts_enter)
    s = F.S_IC_CE
    lid = next(l.leg_id for l in f.ic_legs().values() if l.symbol == s)
    if slices:
        f.ic._rt[lid]["slices"] = list(slices)
        f.ic._persist_session()
    crash_on(f.ic.executor, s, reach, nth)
    try:
        quiet(f.ic.force_square_off_all, reason="KILL")
    except Died:
        pass
    k.clock.sleep(wait)
    f2 = restart(f)
    if slices:
        f2.ic._rt[lid]["slices"] = list(slices)
    sent = 0
    if unreadable:
        real = k.get_orders
        k.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("orders: timeout"))
        n0 = len(k.orders)
        quiet(f2.ic.force_square_off_all, reason="KILL")
        sent = len([o for o in k.orders[n0:] if o["tradingsymbol"] == s])
        k.get_orders = real
    for _ in range(3):
        k.clock.sleep(61)
        quiet(f2.ic.force_square_off_all, reason="KILL")
    other = -130 if shared else 0
    leg = f2.ic_legs().get(lid)
    return dict(bought=filled(k, s, "BUY"), want=195, net=k.net.get(s, 0), want_net=other,
                closed=leg is not None and leg.state != F.L_OPEN, sent_while_unreadable=sent)


def verdict(tag, what, r, need_closed=True):
    ok = r["bought"] == r["want"] and r["net"] == r["want_net"] and (r["closed"] or not need_closed)
    check(f"{tag}: {what}", ok,
          f"bought={r['bought']} (want {r['want']}) net={r['net']} (want {r['want_net']}) closed={r['closed']}")


for name, case in (("Velvet", vet_case), ("Timberwolf", t2_case), ("Icarus", ic_case)):
    sharer = {"Velvet": "Timberwolf", "Timberwolf": "Velvet", "Icarus": "Tigris"}[name]
    verdict(name, f"killed while sending its buy-back on a strike shared with {sharer}: after the "
                  f"restart its own quantity is bought ONCE, {sharer} untouched", case(True))
    verdict(name, "same on a strike it holds alone: bought once, flat", case(False))
    verdict(name, "killed BEFORE the buy-back reached Kite (shared): after the restart it is sent once",
            case(True, reach=False, wait=90))
    r = case(True, unreadable=True)
    check(f"{name}: restarted with the order book unreadable: nothing sent for that leg",
          r["sent_while_unreadable"] == 0, f"sent={r['sent_while_unreadable']}")
    verdict(name, "…then readable: bought once, sharer untouched", r)

# Icarus closes a leg in slices: the first filled, killed while sending the second
verdict("Icarus", "slices 130+65, killed while sending the 2nd (alone): 195 bought in total, flat",
        ic_case(False, slices=(130, 65), nth=2))
verdict("Icarus", "slices 130+65, killed while sending the 2nd (shared with Tigris): 195 in total",
        ic_case(True, slices=(130, 65), nth=2))


# ── in one run nothing changes: an exit that did not fill is simply sent again
def same_run_retry():
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.t2_enter); quiet(f.vet_enter)
    s = F.S_VT
    k.stale_ltp[s] = k.px[s] * 0.5
    k.modify_ok = False
    quiet(f.vet_exit)
    first = len([o for o in k.orders if o["tradingsymbol"] == s and o["transaction_type"] == "BUY"])
    del k.stale_ltp[s]
    k.modify_ok = True
    quiet(f.vet_exit, ts=4000)
    check("same run: Velvet's unfilled buy-back is cancelled and sent again next attempt (its own note does not block it)",
          first == 1 and filled(k, s, "BUY") == 130 and f.vet.pos is None and k.net.get(s) == -130,
          f"first attempt orders={first} bought={filled(k, s, 'BUY')} closed={f.vet.pos is None}")


same_run_retry()

def notes_broken_never_block():
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.t2_enter); quiet(f.vet_enter)
    real = BT._intent_load
    BT._intent_load = lambda sid: (_ for _ in ()).throw(OSError("disk full"))
    try:
        quiet(f.vet_exit)
        quiet(f.t2.kill_close, f.ts_of(14, 30))
    finally:
        BT._intent_load = real
    check("the note file cannot be read or written (disk full): exits go out exactly as before",
          f.vet.pos is None and f.t2.group is None and k.net.get(F.S_VT, 0) == 0
          and filled(k, F.S_VT, "BUY") == 260,
          f"velvet closed={f.vet.pos is None} timberwolf closed={f.t2.group is None} net={net(k)}")


notes_broken_never_block()


print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL EXIT-ONCE RESTART CHECKS PASSED")

# test_tigris_exit_restart.py — tracker T2
# Tigris live exits that do not go through cleanly: the buy-back does not
# fill, cannot be cancelled, fills in part, or the app restarts in the middle.
# The oracle is always the account: every short bought back EXACTLY once,
# hedges sold only after their short, nothing left working, nothing booked
# that did not happen.
#   PYTHONPATH=backend python3 backend/app/engine/test_tigris_exit_restart.py
import os
import sys
import io
import contextlib
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/app/engine")
os.environ.setdefault("SCALP_TEST_MODE", "1")

import test_fleet_live as F                                    # noqa: E402
from app.execution import broker_truth as BT                    # noqa: E402
from app.engine.tsg.tsg_live_core import L_OPEN                     # noqa: E402

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


def fleet():
    BT.reset_app_orders()
    f = F.Fleet()
    quiet(f.ts_enter)
    return f


def restart(f):
    f2 = F.Fleet(kite=f.kite, tmp=f.tmp, restore=True)
    f2.now = f.now
    return f2


def minute(f, n=1):
    for _ in range(n):
        f.kite.clock.sleep(60)
        quiet(f.ts.on_minute, f.now)


def legs(f):
    return f.ts_legs()


def short_syms(f):
    return [l.symbol for l in legs(f).values() if l.is_short]


def wing_syms(f):
    return [l.symbol for l in legs(f).values() if not l.is_short]


def filled(k, s, txn):
    return sum(int(o["filled_quantity"]) for o in k.orders
               if o["tradingsymbol"] == s and o["transaction_type"] == txn)


def bought_once(f, k):
    """Every Tigris contract: bought == sold (and nothing else on it)."""
    over = {s[-7:]: (filled(k, s, "BUY"), filled(k, s, "SELL")) for s in short_syms(f) + wing_syms(f)
            if filled(k, s, "BUY") != filled(k, s, "SELL")}
    return not over, over


def working(k):
    return [(o["order_id"], o["tradingsymbol"][-7:], o["transaction_type"]) for o in k.open_orders()]


def hedge_sold_before_short(k, f):
    """Order in the book: no wing SELL before the BUY of the short it covers."""
    seq = [(o["transaction_type"], o["tradingsymbol"]) for o in k.orders
           if o.get("src", "APP") == "APP" and o["status"] == "COMPLETE"]
    for ce_pe in ("CE", "PE"):
        sh = [i for i, (t, s) in enumerate(seq) if t == "BUY" and s in short_syms(f) and s.endswith(ce_pe)]
        wg = [i for i, (t, s) in enumerate(seq) if t == "SELL" and s in wing_syms(f) and s.endswith(ce_pe)]
        if wg and (not sh or min(wg) < max(sh)):
            return False
    return True


# ═══ X1. the buy-back does not fill (a stale price), then the market reaches it
def x1_stuck_then_fills():
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.stale_ltp[s] = k.px[s] * 0.5                       # buy-back limit far below the market
    k.modify_ok = False                                  # and it cannot be re-priced
    quiet(f.ts.kill_all)
    l = next(l for l in legs(f).values() if l.symbol == s)
    hedge = f.ts._core.hedge_of(l.leg_id)
    check("X1: a buy-back that does not fill: the short stays OPEN, nothing booked, the order is not left working",
          l.state == L_OPEN and not [c for c in f.closes if c[1] == f.ts._live_row_ids.get(l.leg_id)]
          and not [w for w in working(k) if w[1] == s[-7:]],
          f"state={l.state} working={working(k)}")
    check("X1: …its hedge is KEPT while the short is open",
          legs(f)[hedge].state == L_OPEN, legs(f)[hedge].state)
    check("X1: …and the owner is paged", any("NOT CONFIRMED" in c for c in f.crit), f.crit[-2:])
    del k.stale_ltp[s]
    k.modify_ok = True
    minute(f, 2)
    ok, over = bought_once(f, k)
    check("X1: next minute it closes: account flat, every leg traded exactly once, hedge after its short",
          k.flat() and ok and not working(k) and hedge_sold_before_short(k, f)
          and not f.ts._core.open_ids(),
          f"net={ {x[-7:]: q for x, q in k.net.items() if q} } over={over} working={working(k)} "
          f"open={f.ts._core.open_ids()}")


# ═══ X2. the buy-back can be neither filled nor cancelled (relay stuck)
def x2_unresolved_never_doubled():
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.stale_ltp[s] = k.px[s] * 0.5
    k.modify_ok = False
    k.cancel_ok = False
    quiet(f.ts.kill_all)
    del k.stale_ltp[s]
    peak = 0
    for _ in range(4):
        minute(f)
        peak = max(peak, len([w for w in working(k) if w[1] == s[-7:]]))
    check("X2: an order that can be neither filled nor cancelled is re-driven each minute, never doubled",
          peak == 1 and filled(k, s, "BUY") == 0, f"working on the short at most={peak} filled={filled(k, s, 'BUY')}")
    k.cancel_ok = True
    k.modify_ok = True
    k.set_px(s, k.px[s] * 0.4)                           # the market comes to the old order
    minute(f, 2)
    ok, over = bought_once(f, k)
    check("X2: once it fills, it is counted: account flat, nothing traded twice",
          k.flat() and ok and not working(k) and not f.ts._core.open_ids(),
          f"net={ {x[-7:]: q for x, q in k.net.items() if q} } over={over} working={working(k)}")


# ═══ X3. restart while an exit order is working
def x3_restart_with_working_order():
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.stale_ltp[s] = k.px[s] * 0.5
    k.modify_ok = False
    k.cancel_ok = False
    quiet(f.ts.kill_all)
    f2 = restart(f)
    del k.stale_ltp[s]
    k.cancel_ok = True
    k.modify_ok = True
    check("X3: after a restart Tigris remembers its working exit order",
          bool(f2.ts._exit_working) and f2.ts._core is not None and f2.ts._core.open_ids(),
          f"exit_working={f2.ts._exit_working}")
    minute(f2, 2)
    ok, over = bought_once(f2, k)
    check("X3: …drives THAT order (no second buy-back) and closes the day flat",
          k.flat() and ok and not working(k) and not f2.ts._core.open_ids() and hedge_sold_before_short(k, f2),
          f"net={ {x[-7:]: q for x, q in k.net.items() if q} } over={over} working={working(k)}")


# ═══ X4. the order filled while the app was down
def x4_filled_while_down():
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.stale_ltp[s] = k.px[s] * 0.5
    k.modify_ok = False
    k.cancel_ok = False
    quiet(f.ts.kill_all)
    oid = dict(f.ts._exit_working)
    k.set_px(s, k.px[s] * 0.4)                           # fills while nobody watches
    f2 = restart(f)
    del k.stale_ltp[s]
    k.cancel_ok = k.modify_ok = True
    minute(f2, 2)
    ok, over = bought_once(f2, k)
    check("X4: an exit that filled while the app was down is booked from that fill, not bought again",
          k.flat() and ok and not f2.ts._core.open_ids(),
          f"working before restart={oid} net={ {x[-7:]: q for x, q in k.net.items() if q} } over={over}")


# ═══ X5. part filled, then a restart
def x5_partial_then_restart():
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.partial_once[s] = 65                               # 65 of 130 fills, the rest rests
    k.modify_ok = False                                  # out of reach; it is cancelled at the deadline
    quiet(f.ts.kill_all)
    k.modify_ok = True
    l = next(l for l in legs(f).values() if l.symbol == s)
    check("X5: a part fill keeps the short OPEN (nothing booked on half)",
          l.state == L_OPEN, f"state={l.state} fills={f.ts._exit_fills}")
    f2 = restart(f)
    minute(f2, 2)
    ok, over = bought_once(f2, k)
    check("X5: after a restart only the REST is bought: 130 in total, account flat",
          k.flat() and ok and filled(k, s, "BUY") == 130 and not f2.ts._core.open_ids(),
          f"bought={filled(k, s, 'BUY')} over={over} net={ {x[-7:]: q for x, q in k.net.items() if q} }")


# ═══ X6. the app dies while the buy-back is being placed (sent, id not saved)
def x6_crash_while_placing(shared):
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    if shared:
        quiet(f.ic_enter)                                 # Icarus shorts the same call
    quiet(f.ts_enter)
    s = next(l.symbol for l in legs(f).values() if l.is_short and l.symbol.endswith("CE"))
    if shared:
        check("X6s: setup — Tigris and Icarus are short the same call",
              BT.LEDGER.shared(s, "TSG_V1"), BT.LEDGER.holders(s, "TSG_V1"))
    real = f.ts.executor.place_buy_exit

    class Died(BaseException):
        pass

    def dies(symbol, qty, reason):
        oid = real(symbol, qty, reason)
        if symbol == s:
            raise Died()                                  # the process is killed here
        return oid
    f.ts.executor.place_buy_exit = dies
    try:
        quiet(f.ts.kill_all)
    except Died:
        pass
    other = -195 if shared else 0
    f2 = restart(f)
    for _ in range(3):
        minute(f2)
        quiet(f2.ts.kill_all)
        k.clock.sleep(60)
    tag = "X6s" if shared else "X6"
    what = "on a strike shared with Icarus" if shared else "on its own strike"
    check(f"{tag}: killed while sending the buy-back {what}: after the restart it is NOT bought again",
          filled(k, s, "BUY") == 130 and k.net.get(s, 0) == other,
          f"bought={filled(k, s, 'BUY')} (want 130) net={k.net.get(s, 0)} (want {other}) "
          f"open={f2.ts._core.open_ids() if f2.ts._core else None}")


def x7_restart_unreadable_broker():
    """Restart mid-exit while the broker cannot be read: nothing booked or sent
    until it can be."""
    f = fleet(); k = f.kite
    s = short_syms(f)[0]
    k.stale_ltp[s] = k.px[s] * 0.5
    k.modify_ok = k.cancel_ok = False
    quiet(f.ts.kill_all)
    f2 = restart(f)
    del k.stale_ltp[s]
    k.positions_readable = False
    n0 = len(k.orders)
    minute(f2, 2)
    check("X7: restarted mid-exit with the broker unreadable: nothing booked, no new order",
          f2.ts._core.open_ids() and len(k.orders) == n0, f"orders sent={len(k.orders) - n0}")
    k.positions_readable = True
    k.cancel_ok = k.modify_ok = True
    minute(f2, 2)
    ok, over = bought_once(f2, k)
    check("X7: …once readable it closes flat, nothing twice", k.flat() and ok and not working(k), over)


def _crash_on_send(f, s, reach=True):
    real = f.ts.executor.place_buy_exit

    class Died(BaseException):
        pass

    def dies(symbol, qty, reason):
        if symbol == s:
            if reach:
                real(symbol, qty, reason)                 # reaches Kite …
            raise Died()                                  # … and the app is killed
        return real(symbol, qty, reason)
    f.ts.executor.place_buy_exit = dies
    try:
        quiet(f.ts.kill_all)
    except Died:
        pass


def x8_crash_then_book_unreadable():
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.ic_enter); quiet(f.ts_enter)
    s = next(l.symbol for l in legs(f).values() if l.is_short and l.symbol.endswith("CE"))
    _crash_on_send(f, s)
    f2 = restart(f)
    real_orders = k.get_orders
    k.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("orders: timeout"))
    n0 = len(k.orders)
    minute(f2, 2)
    check("X8: restarted after a kill mid-send, order book unreadable: that leg sends NOTHING",
          len([o for o in k.orders[n0:] if o["tradingsymbol"] == s]) == 0,
          f"new orders on the short={[o['order_id'] for o in k.orders[n0:] if o['tradingsymbol'] == s]}")
    k.get_orders = real_orders
    for _ in range(3):
        minute(f2)
        quiet(f2.ts.kill_all)
    check("X8: …once readable, the order sent before the kill is found: bought once, Icarus untouched",
          filled(k, s, "BUY") == 130 and k.net.get(s, 0) == -195,
          f"bought={filled(k, s, 'BUY')} net={k.net.get(s, 0)}")


def x9_crash_before_it_reached():
    """Killed while sending, and the order never reached Kite: after the
    restart the exit is sent once (the kill's exit request is not lost)."""
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.ic_enter); quiet(f.ts_enter)
    s = next(l.symbol for l in legs(f).values() if l.is_short and l.symbol.endswith("CE"))
    _crash_on_send(f, s, reach=False)
    k.clock.sleep(90)
    f2 = restart(f)
    for _ in range(3):
        minute(f2)
    lid = next(l.leg_id for l in legs(f2).values() if l.symbol == s)
    check("X9: killed before the buy-back reached Kite: after the restart that exit is sent ONCE and booked",
          filled(k, s, "BUY") == 130 and k.net.get(s, 0) == -195 and lid not in f2.ts._core.open_ids(),
          f"bought={filled(k, s, 'BUY')} net={k.net.get(s, 0)} open={f2.ts._core.open_ids()}")



def x10_note_costs_nothing():
    import time as _t
    BT.INTENT_DIR = Path(__import__("tempfile").mkdtemp())
    t0 = _t.perf_counter()
    for i in range(200):
        BT.exit_intent_note("TSG_V1", f"L{i % 4}", "X", "BUY", 130, "T")
    per = (_t.perf_counter() - t0) / 200 * 1000
    check(f"X10: writing the exit note costs {per:.2f} ms (limit 2 ms; the order itself takes 100+ ms)",
          per < 2.0, f"{per:.3f} ms")


def x11_notes_broken_never_block():
    f = fleet(); k = f.kite
    real = BT._intent_load
    BT._intent_load = lambda sid: (_ for _ in ()).throw(OSError("disk full"))
    try:
        quiet(f.ts.kill_all)
        minute(f, 2)
    finally:
        BT._intent_load = real
    ok, over = bought_once(f, k)
    check("X11: the note file cannot be read or written (disk full): Tigris exits exactly as before",
          k.flat() and ok and not f.ts._core.open_ids(), f"over={over} open={f.ts._core.open_ids()}")


TESTS = [x1_stuck_then_fills, x2_unresolved_never_doubled, x3_restart_with_working_order,
         x4_filled_while_down, x5_partial_then_restart, lambda: x6_crash_while_placing(False),
         lambda: x6_crash_while_placing(True), x7_restart_unreadable_broker,
         x8_crash_then_book_unreadable, x9_crash_before_it_reached,
         x10_note_costs_nothing, x11_notes_broken_never_block]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception as e:
            import traceback
            FAILS.append(getattr(t, "__name__", "x6"))
            print(f"[ERR ] {getattr(t, '__name__', 'x6')}: {e!r}")
            traceback.print_exc()
    print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
    if FAILS:
        sys.exit(1)
    print("ALL TIGRIS EXIT/RESTART CHECKS PASSED")

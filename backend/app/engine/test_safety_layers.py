# test_safety_layers.py — tracker T4
# Three layers stop the 13/14 Nov false booking (an open short booked closed
# because Kite could not be read, or answered empty):
#   L1 strict reads       — an unreadable answer is "unknown", never "empty"
#   L2 session gate       — outside the F&O session nothing is inferred
#   L3 no order, no book  — "gone" without an exit order in today's book is
#                           paged, never booked (GTT monitors and exit paths)
# The fleet suite proves only the three together. Here each layer is proven
# ALONE (the other two switched back to the old behaviour), and the case with
# all three off must reproduce the incident — otherwise the test proves
# nothing.
#   PYTHONPATH=backend python3 backend/app/engine/test_safety_layers.py
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

PASSED, FAILS, MATRIX = [0], [], []


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


REAL_SESSION = None
REAL_IC_FILL = F.ICMON.ICGTTMonitor._fill_from_orders
REAL_T2_FILL = F.T2MON.TMA2GTTMonitor._fill_from_orders
REAL_FLAT = BT.flat_proven


def layers(f, on):
    """Switch layers OFF by restoring what the code did before them."""
    k = f.kite
    if "L1" not in on:                     # old executor: a failed read came back as []
        k.get_open_positions_or_none = lambda: [{"tradingsymbol": s, "quantity": q}
                                                for s, q in k.net.items() if q] if k.logged_in else []
        k.get_gtts_or_none = lambda: k._gtt_rows() if k.logged_in else []
    if "L2" not in on:                     # no session gate
        BT.session_open = lambda: True
    if "L3" not in on:                     # "gone" → booked at a model price, no order needed
        def ic_fill(self, leg, only_ids=None, px_cap=None):
            px, oid = REAL_IC_FILL(self, leg, only_ids=only_ids, px_cap=px_cap)
            return (px, oid) if oid else (self._price_fallback(leg), "NO-ORDER")
        def t2_fill(self, sell, only_ids=None, px_cap=None):
            px, oid = REAL_T2_FILL(self, sell, only_ids=only_ids, px_cap=px_cap)
            return (px, oid) if oid else (self._price_fallback(sell), "NO-ORDER")
        F.ICMON.ICGTTMonitor._fill_from_orders = ic_fill
        F.T2MON.TMA2GTTMonitor._fill_from_orders = t2_fill
        BT.flat_proven = lambda *a, **kw: True


def restore_layers():
    F.ICMON.ICGTTMonitor._fill_from_orders = REAL_IC_FILL
    F.T2MON.TMA2GTTMonitor._fill_from_orders = REAL_T2_FILL
    BT.flat_proven = REAL_FLAT


SCENARIOS = {
    # 13 Nov 07:43: the app starts before the Kite login — nothing can be read
    "before login (unreadable)": dict(market_open=False, logged_in=False, empty=False),
    # 14 Nov 05:00 / pre-open: logged in, Kite answers EMPTY outside the session
    "logged in, empty answers before the open": dict(market_open=False, logged_in=True, empty=True),
    # in the session, a successful but EMPTY answer
    "in session, empty answers": dict(market_open=True, logged_in=True, empty=True),
}


def run(scn, on):
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.ic_enter)
    quiet(f.t2_enter)
    real_net, real_gtts = dict(k.net), dict(k.gtts)
    k.market_open, k.logged_in = scn["market_open"], scn["logged_in"]
    if scn["empty"]:
        k.net, k.gtts = {}, {}
    layers(f, on)
    try:
        quiet(f.ic_sweep, 8)
        quiet(f.t2_sweep, 8)
    finally:
        restore_layers()
    k.net, k.gtts = real_net, real_gtts
    booked_ic = [l for l, v in f.ic_legs().items() if v.state != F.L_OPEN]
    booked_t2 = f.t2.group is None
    return not booked_ic and not booked_t2, f"Icarus booked={booked_ic} Timberwolf booked={booked_t2}"


def exit_path_run(on):
    """Velvet's exit when the positions read EMPTY in the session (D5)."""
    BT.reset_app_orders()
    f = F.Fleet(); k = f.kite
    quiet(f.vet_enter)
    real = dict(k.net)
    k.net = {}
    layers(f, on)
    try:
        n0 = len(k.orders)
        quiet(f.vet_exit)
    finally:
        restore_layers()
    k.net = real
    booked_blind = f.vet.pos is None and len(k.orders) == n0
    return not booked_blind, f"Velvet booked with no order={booked_blind}"


ALL = {"L1", "L2", "L3"}
for name, scn in SCENARIOS.items():
    ok, d = run(scn, set())
    check(f"[{name}] all three layers OFF: the false booking reproduces (the test can see it)", not ok, d)
    ok, d = run(scn, ALL)
    check(f"[{name}] all three ON: nothing booked", ok, d)
    for layer in sorted(ALL):
        ok, d = run(scn, {layer})
        MATRIX.append((name, layer, ok))

need = {   # which layer must hold ALONE in which situation
    ("before login (unreadable)", "L1"), ("before login (unreadable)", "L2"),
    ("before login (unreadable)", "L3"),
    ("logged in, empty answers before the open", "L2"),
    ("logged in, empty answers before the open", "L3"),
    ("in session, empty answers", "L3"),
}
for name, layer, ok in MATRIX:
    if (name, layer) in need:
        check(f"[{name}] {layer} ALONE stops the false booking", ok)
print("\nlayer alone holds?  " + "  ".join(sorted(ALL)))
for name in SCENARIOS:
    row = {l: ok for n, l, ok in MATRIX if n == name}
    print(f"  {name:<42}" + "  ".join(("yes" if row[l] else "no ") for l in sorted(ALL)))

ok, d = exit_path_run(set())
check("[exit path, in session, empty positions] L3 OFF: Velvet books its exit with no order (reproduces D5)",
      not ok, d)
ok, d = exit_path_run({"L3"})
check("[exit path, in session, empty positions] L3 ALONE: nothing booked without an order", ok, d)

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL SAFETY-LAYER CHECKS PASSED")

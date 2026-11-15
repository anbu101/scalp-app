# test_entry_reservation.py — tracker T3 (RESERVE_SIGNED_20261115)
# While a strategy's entry orders are in flight (before its own state shows
# the legs), every other strategy must see those contracts as held — on the
# right SIDE and with the right QUANTITY — so that
#   * exits on a shared contract use own fills only,
#   * opposite-side avoidance works for entries that overlap.
# A hook in the fake broker runs the checks at the moment each entry order
# is placed. Removing the reservations makes these checks fail.
#   PYTHONPATH=backend python3 backend/app/engine/test_entry_reservation.py
import os
import sys
import io
import contextlib
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = str(_HERE.parents[2])
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/app/engine")
os.environ.setdefault("SCALP_TEST_MODE", "1")

import test_fleet_live as F                                    # noqa: E402
from app.execution import broker_truth as BT                    # noqa: E402

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class HookKite(F.FleetKite):
    """Runs `on_order(symbol, txn)` right after each APP order is created."""

    def __init__(self, px):
        super().__init__(px)
        self.on_order = None
        self.fail_symbol = None          # an entry order on this contract is rejected

    def _new_order(self, s, txn, qty, price, **k):
        if self.fail_symbol and s == self.fail_symbol and k.get("src", "APP") == "APP":
            raise RuntimeError("InputException: insufficient funds (test)")
        oid = super()._new_order(s, txn, qty, price, **k)
        if self.on_order and k.get("src", "APP") == "APP":
            self.on_order(s, txn)
        return oid


def fleet():
    BT.reset_app_orders()
    return F.Fleet(kite=HookKite(F.prices()))


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def seen_during_entry(name, enter, me_other, plan):
    """plan: {symbol: signed qty} the entry will hold. At EVERY order of the
    entry, each planned contract must read as held by the entering strategy,
    on its side, with its quantity (as another strategy sees it)."""
    f = fleet(); k = f.kite
    bad = []

    def hook(sym, txn):
        for s, q in plan.items():
            h = BT.LEDGER.holders(s, me_other) or {}
            got = sum(h.values())
            if got != q:
                bad.append(f"at {txn} {sym[-7:]}: {s[-7:]} reads {got}, want {q}")
    k.on_order = hook
    quiet(enter, f)
    k.on_order = None
    check(f"{name}: while its entry is in flight, every leg reads as held on its side with its quantity",
          not bad and k.orders, "; ".join(bad[:4]) or "no order placed")
    return f


S, SIC_CE, SIC_PE = F.S_VT, F.S_IC_CE, F.S_IC_PE


def plan_of(f, sid):
    return {s: q for s, q in (BT.LEDGER.snapshot().get(sid) or {}).items() if q}


# what each strategy holds once entered (read from a normal entry)
def _holdings(enter, sid):
    f = fleet()
    quiet(enter, f)
    return plan_of(f, sid)


PLANS = {
    "Icarus": (lambda f: f.ic_enter(), "IC_V2"),
    "Timberwolf": (lambda f: f.t2_enter(), "TMA_V2"),
    "Tigris": (lambda f: f.ts_enter(), "TSG_V1"),
    "Velvet": (lambda f: f.vet_enter(), "VET_V1"),
}

for name, (enter, sid) in PLANS.items():
    plan = _holdings(enter, sid)
    check(f"{name}: a normal entry holds {len(plan)} contracts (setup)", len(plan) >= 2, plan)
    other = "VET_V1" if sid != "VET_V1" else "TSG_V1"
    seen_during_entry(name, enter, other, plan)

# ── opposite-side avoidance is answered correctly while Icarus is entering
f = fleet(); k = f.kite
ans = []
k.on_order = lambda s, t: ans.append((BT.LEDGER.opposite(SIC_CE, "VET_V1", "BUY"),
                                      BT.LEDGER.opposite(SIC_CE, "VET_V1", "SELL")))
quiet(f.ic_enter)
k.on_order = None
check("Icarus entering: another strategy may NOT buy its short strike (opposite side)",
      ans and all(a[0] == "IC_V2" for a in ans), ans[:3])
check("…and MAY sell it (same side adds up)", ans and all(a[1] is None for a in ans), ans[:3])

# ── nothing stays reserved after the entry
for name, (enter, sid) in PLANS.items():
    f = fleet()
    quiet(enter, f)
    check(f"{name}: no reservation left after the entry", sid not in BT.LEDGER._reserved,
          BT.LEDGER._reserved.get(sid))

# ── a failed entry (a leg rejected, the rest unwound) leaves nothing reserved
f = fleet(); k = f.kite
k.fail_symbol = SIC_PE
quiet(f.ic_enter)
k.fail_symbol = None
check("Icarus: a failed entry leaves nothing reserved and nothing held",
      "IC_V2" not in BT.LEDGER._reserved and not plan_of(f, "IC_V2"),
      (BT.LEDGER._reserved.get("IC_V2"), plan_of(f, "IC_V2")))
f = fleet(); k = f.kite
k.fail_symbol = S
quiet(f.t2_enter)
k.fail_symbol = None
check("Timberwolf: a failed entry leaves nothing reserved",
      "TMA_V2" not in BT.LEDGER._reserved, BT.LEDGER._reserved.get("TMA_V2"))

# ── an exception inside the entry still releases (finally)
f = fleet()
real = f.ic._enter_day_impl
f.ic._enter_day_impl = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    quiet(f.ic_enter)
except Exception:
    pass
f.ic._enter_day_impl = real
check("Icarus: an exception inside the entry still releases the reservation",
      "IC_V2" not in BT.LEDGER._reserved, BT.LEDGER._reserved.get("IC_V2"))

# ── an Icarus adjustment (a LONG) in flight reads as long, with its quantity
BT.reset_app_orders()
f = F.Fleet(kite=HookKite(F.prices()), adj_cap=205); k = f.kite
seen = []
k.on_order = lambda s, t: seen.append(BT.LEDGER.holders(S, "VET_V1")) if (s == S and t == "BUY") else None
adj = quiet(F._icarus_l1_stopped, f)
k.on_order = None
check("Icarus adjustment in flight: its contract reads long with the adjustment's quantity",
      adj is not None and adj.symbol == S and seen and all((h or {}).get("IC_V2") == int(adj.qty) for h in seen),
      (getattr(adj, "symbol", None), seen[:2]))
check("…and after it, the adjustment's reservation is gone", not (BT.LEDGER._reserved.get("IC_V2") or {}),
      BT.LEDGER._reserved.get("IC_V2"))

# ── one strategy's reservation never hides another's
BT.LEDGER.reserve("TSG_V1", {"X": -130})
BT.LEDGER.reserve("VET_V1", {"X": -130})
BT.LEDGER.release("VET_V1")
check("releasing one strategy keeps the other's reservation",
      BT.LEDGER.holders("X", "IC_V2") == {"TSG_V1": -130}, BT.LEDGER.holders("X", "IC_V2"))
BT.LEDGER.release("TSG_V1")

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL ENTRY-RESERVATION CHECKS PASSED")

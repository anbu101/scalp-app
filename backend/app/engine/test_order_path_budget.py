# test_order_path_budget.py — EXIT_LATENCY_20261115
# A fix must never make an entry or an exit slower. Every broker round trip
# costs network time, so this counts them and holds each strategy to the
# number it needed in v11.7.9 (14 Nov 2026, before the assurance fixes).
#   PYTHONPATH=backend python3 backend/app/engine/test_order_path_budget.py
"""Count broker round trips on the order path.
A 'round trip' is any call that reaches the broker object (the fake Kite here; a
network call in production). Counted from the moment a strategy decides to enter
or exit until it returns — the plain case, one strategy alone on the account."""
import sys, os, io, json, contextlib
from pathlib import Path
ROOT = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/app/engine")
os.environ["SCALP_TEST_MODE"] = "1"
import test_fleet_live as F

class Counting(F.FleetKite):
    def __init__(self, px):
        super().__init__(px)
        object.__setattr__(self, "_n", {})
        object.__setattr__(self, "_depth", 0)

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        import types
        if name.startswith("_") or not isinstance(attr, types.MethodType) or name in ("set_px", "manual", "active_gtts", "open_orders", "flat", "gtt_last_state"):
            return attr
        def wrapped(*a, **k):
            d = object.__getattribute__(self, "_depth")
            if d == 0:                                   # only calls coming from the app, not internal ones
                n = object.__getattribute__(self, "_n")
                n[name] = n.get(name, 0) + 1
            object.__setattr__(self, "_depth", d + 1)
            try:
                return attr(*a, **k)
            finally:
                object.__setattr__(self, "_depth", d)
        return wrapped

def measure(enter, leave):
    out = {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f = F.Fleet(kite=Counting(F.prices()))
        k = f.kite
        for label, fn in (("entry", enter), ("exit", leave)):
            object.__getattribute__(k, "_n").clear()
            fn(f)
            n = dict(object.__getattribute__(k, "_n"))
            out[label] = {"total": sum(n.values()), "calls": dict(sorted(n.items()))}
    return out

res = {
  "Icarus":     measure(lambda f: f.ic_enter(),  lambda f: f.ic.force_square_off_all(reason="EOD")),
  "Timberwolf": measure(lambda f: f.t2_enter(),  lambda f: f.t2.kill_close(f.ts_of(15, 20))),
  "Tigris":     measure(lambda f: f.ts_enter(),  lambda f: f.ts.square_off_all("EOD")),
  "Velvet":     measure(lambda f: f.vet_enter(), lambda f: f.vet_exit()),
}
# round trips in v11.7.9 — the ceiling. Lower is fine; higher needs a reason and Anbu's yes.
BUDGET = {"Icarus": (11, 18), "Timberwolf": (7, 8), "Tigris": (8, 12), "Velvet": (4, 6)}
bad = []
for name, (b_in, b_out) in BUDGET.items():
    n_in, n_out = res[name]["entry"]["total"], res[name]["exit"]["total"]
    ok = n_in <= b_in and n_out <= b_out
    print(f"[{'ok  ' if ok else 'MISS'}] {name:11s} entry {n_in:2d} (budget {b_in:2d})   exit {n_out:2d} (budget {b_out:2d})")
    if not ok:
        bad.append(name)
        print("         -> entry", res[name]["entry"]["calls"], "\n         -> exit ", res[name]["exit"]["calls"])
if "--json" in sys.argv:
    print(json.dumps(res))
if bad:
    print(f"\n{len(bad)} strategy path(s) OVER BUDGET")
    sys.exit(1)
print("ALL ORDER-PATH BUDGETS HELD")

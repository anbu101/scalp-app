# backend/app/engine/vet/test_vet_manager.py
#
# ── VET_V1 MANAGER INTEGRATION SMOKE ──
# ============================================================================
# Drives the REAL manager with a stubbed chain/quotes/executor through
# entry → MID-DAY RESTART → each exit path → flat, which is the gauntlet the
# checklist mandates (Part 5 item 4). The restart leg is not optional: it is
# the leg that caught TSG's unpersisted chain meta.
#
# The live-only hazards are asserted explicitly, because none of them can be
# caught by a backtest:
#   * wing is BOUGHT BEFORE the short is sold (order recorded and checked)
#   * a failed wing buy means NO TRADE and NO orders left behind
#   * a failed main leg AFTER the wing filled sells the wing back
#   * exits close the SHORT first, then the wing
#   * no REAL wing under the cap → entry skipped, never bare
#   * a restart rebuilds the position instead of opening a second one
#
# Runs standalone:  python3 test_vet_manager.py
# ============================================================================

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))

try:
    from app.engine.vet.vet_common import VetRepo
    from app.engine.vet.vet_live_core import ENTER, EXIT, FLIP
    from app.engine.vet.vet_manager import VetManager
except ImportError:                                        # pragma: no cover
    from vet_common import VetRepo                          # type: ignore
    from vet_live_core import ENTER, EXIT, FLIP             # type: ignore
    from vet_manager import VetManager                      # type: ignore

F = 0


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


SPOT = 24000.0


def chain(side, ts, cheap=True):
    """A ladder around SPOT wide enough that the far strikes actually reach a
    Rs 3 wing cap — a narrow ladder silently makes every hedged entry "skip",
    which looks exactly like a manager bug and is not one.
      cheap=True  -> far strikes fall to ~Rs 0.05 (real wings exist)
      cheap=False -> nothing below Rs 12 (the no-real-wing scenario)
    """
    out = []
    for k in range(23000, 25050, 50):
        dist = abs(k - SPOT)
        raw = 180 - dist * 0.35
        px = max(0.05, raw) if cheap else max(12.0, raw)
        out.append({"tradingsymbol": f"NIFTY26AUG{k}{side}",
                    "token": 1000 + k, "strike": float(k),
                    "expiry": "2026-08-27", "instrument_type": side,
                    "ltp": round(px, 2)})
    return out


class Exec:
    """Records the ORDER of calls — the whole point of the sequencing tests."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def place_buy(self, symbol, token, qty):
        self.calls.append(("BUY", symbol))
        return None if "BUY" in self.fail_on else f"OB-{len(self.calls)}"

    def place_sell_entry(self, symbol, token, qty):
        self.calls.append(("SELL", symbol))
        return None if "SELL" in self.fail_on else f"OS-{len(self.calls)}"

    def place_market_sell(self, symbol, qty):
        self.calls.append(("CLOSE_LONG", symbol))
        return f"OC-{len(self.calls)}"

    def place_buy_exit(self, symbol, qty, reason):
        self.calls.append(("CLOSE_SHORT", symbol))
        return f"OX-{len(self.calls)}"


def mk(cfg_extra=None, db=None, execu=None, cheap=True):
    cfg = {"leg_action": "BUY", "atm_offset": -1, "eod_square": True,
           "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT}
    cfg.update(cfg_extra or {})
    repo = VetRepo(db)
    repo.ensure_schema()
    return VetManager(cfg, repo=repo,
                      chain_fn=lambda s, t: chain(s, t, cheap),
                      quote_fn=lambda sym: 150.0,
                      executor=execu, mode="LIVE" if execu else "PAPER")


tmpdir = tempfile.mkdtemp(prefix="vet_mgr_")
db1 = os.path.join(tmpdir, "a.db")

print("── 1. BUY mode, plain lifecycle ──")
m = mk(db=db1)
p = m.open_position("CE", ts=1000, bar_ts=1000, condition=1)
chk("opens a long CE", p is not None and p["main"]["direction"] == "LONG")
chk("no wing in BUY mode", p and p["wing"] is None)
chk("ATM offset -1 picks one strike BELOW spot for a CE",
    p and p["main"]["strike"] == SPOT - 50, p and p["main"]["strike"])
chk("a second open is refused while one is live",
    m.open_position("CE", ts=1100, bar_ts=1100, condition=1) is None)
r = m.close_position("FLIP", ts=2000)
chk("closes and goes flat", r is not None and m.pos is None)

print("\n── 2. SELL + wing: the live-only ordering rule ──")
ex = Exec()
db2 = os.path.join(tmpdir, "b.db")
m2 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=db2, execu=ex)
p2 = m2.open_position("PE", ts=1000, bar_ts=1000, condition=1)
chk("opens a short PE with a wing", p2 is not None and p2["wing"] is not None)
chk("main leg is SHORT, wing is LONG",
    p2 and p2["main"]["direction"] == "SHORT"
    and p2["wing"]["direction"] == "LONG")
kinds = [c[0] for c in ex.calls]
chk("WING IS BOUGHT BEFORE THE SHORT IS SOLD",
    kinds[:2] == ["BUY", "SELL"], ex.calls)
chk("the wing costs no more than the cap",
    p2 and p2["wing"]["entry_price"] <= 3.0, p2 and p2["wing"]["entry_price"])
chk("the wing is a different contract from the short",
    p2 and p2["wing"]["tradingsymbol"] != p2["main"]["tradingsymbol"])
ex.calls.clear()
m2.close_position("FLIP", ts=2000)
kinds = [c[0] for c in ex.calls]
chk("on exit the SHORT is closed FIRST, then the wing",
    kinds == ["CLOSE_SHORT", "CLOSE_LONG"], ex.calls)

print("\n── 3. failure paths leave nothing dangling ──")
exf = Exec(fail_on={"BUY"})
m3 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=os.path.join(tmpdir, "c.db"), execu=exf)
p3 = m3.open_position("PE", ts=1000, bar_ts=1000, condition=1)
chk("wing BUY fails -> no position at all", p3 is None and m3.pos is None)
chk("...and no short was ever sent",
    [c[0] for c in exf.calls] == ["BUY"], exf.calls)

exs = Exec(fail_on={"SELL"})
m4 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=os.path.join(tmpdir, "d.db"), execu=exs)
p4 = m4.open_position("PE", ts=1000, bar_ts=1000, condition=1)
chk("main SELL fails after the wing filled -> no position",
    p4 is None and m4.pos is None)
chk("...and the wing is SOLD BACK, not left long",
    [c[0] for c in exs.calls] == ["BUY", "SELL", "CLOSE_LONG"], exs.calls)

print("\n── 4. no real wing under the cap -> skip, never bare ──")
m5 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=os.path.join(tmpdir, "e.db"),
        cheap=False)                       # cheapest listed option is Rs 12
chk("entry SKIPPED when no real wing exists",
    m5.open_position("PE", ts=1000, bar_ts=1000, condition=1) is None
    and m5.pos is None)
m5b = mk({"leg_action": "SELL"}, db=os.path.join(tmpdir, "f.db"), cheap=False)
chk("unhedged SELL still trades (the skip is wing-specific)",
    m5b.open_position("PE", ts=1000, bar_ts=1000, condition=1) is not None)

print("\n── 5. MID-DAY RESTART (mandatory) ──")
db6 = os.path.join(tmpdir, "g.db")
m6 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=db6)
opened = m6.open_position("PE", ts=1000, bar_ts=1000, condition=1)
gid = opened["group_id"]
del m6                                      # backend dies here

m7 = mk({"leg_action": "SELL", "hedge_enabled": True,
         "hedge_max_premium": 3.0}, db=db6)
chk("a fresh manager starts with no in-memory position", m7.pos is None)
resumed = m7.resume_from_db()
chk("restart REBUILDS the open position", resumed is not None
    and resumed["group_id"] == gid, resumed and resumed["group_id"])
chk("both legs come back", resumed and resumed["main"] and resumed["wing"])
chk("it does NOT open a second position",
    m7.open_position("PE", ts=3000, bar_ts=3000, condition=1) is None)
out = m7.close_position("EOD", ts=4000)
chk("the resumed position closes cleanly", out is not None and m7.pos is None)
chk("nothing is left OPEN in the DB",
    m7.repo.open_group("PAPER") is None, m7.repo.open_group("PAPER"))

print("\n── 6. decisions, boundaries and the kill path ──")
m8 = mk(db=os.path.join(tmpdir, "h.db"))
m8.on_decision({"action": ENTER, "side": "CE", "bar_ts": 10,
                "condition": 1}, ts=10)
chk("on_decision ENTER opens", m8.pos is not None)
res = m8.on_decision({"action": FLIP, "side": "PE", "reason": "FLIP",
                      "bar_ts": 20, "condition": -1}, ts=20)
chk("FLIP closes and reopens on the other side",
    res and res["flip"] and res["opened"] and m8.pos["side"] == "PE")
m8.on_decision({"action": EXIT, "reason": "SIGNAL_EXIT"}, ts=30)
chk("EXIT goes flat", m8.pos is None)

m9 = mk({"eod_square": False}, db=os.path.join(tmpdir, "i.db"))
m9.open_position("CE", ts=10, bar_ts=10, condition=1)
chk("positional mode does NOT square off at EOD",
    m9.eod_square_off(ts=900) is None and m9.pos is not None)
chk("but a contract is never held past expiry",
    m9.expiry_exit(ts=901) is not None and m9.pos is None)

m10 = mk(db=os.path.join(tmpdir, "j.db"))
m10.open_position("CE", ts=10, bar_ts=10, condition=1)
k = m10.kill(ts=99)
chk("kill flattens", k is not None and m10.pos is None)
chk("...and freezes against reopening",
    m10.frozen and m10.open_position("CE", ts=100, bar_ts=100,
                                     condition=1) is None)

print("\n── 7. a flip whose re-entry is refused stays FLAT ──")
m11 = mk({"leg_action": "SELL", "hedge_enabled": True,
          "hedge_max_premium": 3.0}, db=os.path.join(tmpdir, "k.db"))
m11.open_position("PE", ts=10, bar_ts=10, condition=1)
m11.cfg["hedge_max_premium"] = 0.01        # wing becomes unobtainable
res = m11.on_decision({"action": FLIP, "side": "CE", "reason": "FLIP",
                       "bar_ts": 20, "condition": -1}, ts=20)
chk("the exit still stands even though the re-entry was refused",
    res and res["closed"] and not res["opened"] and m11.pos is None, res)

print("\n" + ("ALL MANAGER SMOKE CHECKS PASSED" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)
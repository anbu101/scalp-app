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

    # ── VET_LIVE_CONFIRM_20261026 ── LIVE orders are now fill-confirmed;
    # the fake reports every order COMPLETE (avg 0 → the manager keeps its
    # chain/quote prices, so OFF≡ORIGINAL parity comparisons still hold).
    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "avg_price": 0.0, "filled_qty": 0,
                "found": True}

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

print("\n── 8. KILL re-entry block (VET_KILL_BLOCK_20261123 / KILL_PAPER_FLIP_20261123, DEF-097) ──")
import json as _json
from app.engine.vet.vet_live_core import HOLD
kb_path = os.path.join(tmpdir, "state", "vet_kill_block.json")
m12 = mk({"leg_action": "SELL"}, db=os.path.join(tmpdir, "l.db"))
m12.kill_block_path = kb_path
m12.open_position("PE", ts=10, bar_ts=10, condition=1)
m12.kill(ts=50)
chk("TC-VET-0319 a KILL of a PE position blocks PE and saves the block to the state file",
    m12.pos is None and m12.kill_block_side == "PE" and os.path.exists(kb_path)
    and _json.load(open(kb_path))["side"] == "PE", m12.kill_block_side)
chk("TC-VET-0328 the manager's state shows the block side",
    m12.state().get("kill_block_side") == "PE")

# next morning: a new manager (daily re-arm / restart) reads the file
m13 = mk({"leg_action": "SELL"}, db=os.path.join(tmpdir, "l.db"))
m13.kill_block_path = kb_path
chk("TC-VET-0320 a fresh manager (restart / next day) restores the block from the file",
    m13.load_kill_block() == "PE" and m13.kill_block_side == "PE")
enter_pe = {"action": ENTER, "side": "PE", "bar_ts": 100, "condition": 1}
chk("TC-VET-0321 same trend (ENTER PE from flat) is blocked and the block stays",
    m13.kill_gate(enter_pe) is False and m13.kill_block_side == "PE")
chk("TC-VET-0321 ...and the manager itself refuses a PE entry while blocked (not frozen)",
    not m13.frozen and m13.open_position("PE", ts=100, bar_ts=100, condition=1) is None
    and m13.pos is None)
hold_flat = {"action": HOLD, "side": None, "bar_ts": 105, "condition": 0}
chk("TC-VET-0322 a neutral bar is a trend change: gate passes, block cleared, file removed",
    m13.kill_gate(hold_flat) is True and m13.kill_block_side is None
    and not os.path.exists(kb_path))
chk("TC-VET-0323 after the block clears, a PE entry on the next signal opens",
    m13.kill_gate(enter_pe) is True
    and m13.open_position("PE", ts=110, bar_ts=110, condition=1) is not None)

m14 = mk({"leg_action": "SELL"}, db=os.path.join(tmpdir, "m.db"))
m14.kill_block_path = kb_path
m14.open_position("PE", ts=10, bar_ts=10, condition=1)
m14.kill(ts=20)
m15 = mk({"leg_action": "SELL"}, db=os.path.join(tmpdir, "m.db"))
m15.kill_block_path = kb_path
m15.load_kill_block()
enter_ce = {"action": ENTER, "side": "CE", "bar_ts": 200, "condition": -1}
chk("TC-VET-0324 a flip to the other side clears the block and that side opens",
    m15.kill_gate(enter_ce) is True and m15.kill_block_side is None
    and m15.open_position("CE", ts=200, bar_ts=200, condition=-1) is not None)
chk("TC-VET-0324 ...and later bars on the old side are no longer blocked",
    m15.close_position("FLIP", ts=210) is not None
    and m15.kill_gate(enter_pe) is True)

m16 = mk(db=os.path.join(tmpdir, "n.db"))           # harness: no path
m16.open_position("CE", ts=10, bar_ts=10, condition=1)
before = set(os.listdir(tmpdir))
m16.kill(ts=20)
chk("TC-VET-0325 without a state path the block is kept in memory only (no file written)",
    m16.kill_block_side == "CE" and set(os.listdir(tmpdir)) == before)

m17 = mk(db=os.path.join(tmpdir, "o.db"))
m17.kill_block_path = os.path.join(tmpdir, "state", "k17.json")
m17.kill(ts=20)
chk("TC-VET-0326 a KILL while flat sets no block",
    m17.kill_block_side is None and not os.path.exists(m17.kill_block_path))

bad = os.path.join(tmpdir, "state", "bad.json")
with open(bad, "w") as _f:
    _f.write("{not json")
m18 = mk(db=os.path.join(tmpdir, "p.db"))
m18.kill_block_path = bad
chk("TC-VET-0327 an unreadable state file restores no block and does not raise",
    m18.load_kill_block() is None and m18.kill_block_side is None)
chk("TC-VET-0329 with a position open the gate never blocks (exits and flips run)",
    m13.pos is not None and m13.kill_gate(enter_pe) is True)

_loop = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "vet_selection_loop.py"), encoding="utf-8").read()
_i_dec = _loop.find("decision = engine.decide(")
_i_gate = _loop.find("if not manager.kill_gate(decision):")
_i_act = _loop.find("manager.on_decision(decision, ts=ts)")
chk("TC-VET-0330 loop: the KILL gate runs on every decision, after decide() and before any entry",
    0 < _i_dec < _i_gate < _i_act, (_i_dec, _i_gate, _i_act))
_i_path = _loop.find("manager.kill_block_path = _vet_kill_block_path()")
_i_load = _loop.find("manager.load_kill_block()")
chk("TC-VET-0331 loop: the live manager gets the state file and restores the block at arm time",
    0 < _i_path < _i_load < _i_gate and "vet_kill_block.json" in _loop, (_i_path, _i_load))

print("\n── 9. Settings mode followed at each entry (VET_MODE_FOLLOW_20261125, DEF-102) ──")
from app.engine.vet.vet_manager import carried_book_mode
WANT = ["LIVE"]
SELL = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0}

# 25 Nov replay: armed PAPER at 08:30, Settings switched to LIVE at 08:46
ex19 = Exec()
m19 = mk(SELL, db=os.path.join(tmpdir, "q.db"))            # PAPER, no executor
m19.mode_reader, m19.executor_fn = (lambda: WANT[0]), (lambda: ex19)
p19 = m19.open_position("PE", ts=1000, bar_ts=1000, condition=1)
chk("TC-VET-0332 armed PAPER, Settings now LIVE, flat: the next entry is LIVE with real orders",
    p19 is not None and m19.mode == "LIVE" and p19["main"].get("mode") == "LIVE"
    and [c[0] for c in ex19.calls][:2] == ["BUY", "SELL"], (m19.mode, ex19.calls))

ex20 = Exec()
m20 = mk(SELL, db=os.path.join(tmpdir, "r.db"))
m20.open_position("PE", ts=1000, bar_ts=1000, condition=1)  # PAPER position open
m20.mode_reader, m20.executor_fn = (lambda: WANT[0]), (lambda: ex20)
chk("TC-VET-0333 a PAPER position open when Settings go LIVE keeps PAPER and places no order",
    m20.sync_mode() is True and m20.mode == "PAPER" and ex20.calls == []
    and m20.pos["main"].get("mode") == "PAPER")
m20.close_position("SIGNAL_EXIT", ts=1100)
chk("TC-VET-0333 ...its exit is booked PAPER (no broker order)", ex20.calls == [])
p20 = m20.open_position("CE", ts=1200, bar_ts=1200, condition=-1)
chk("TC-VET-0334 after that position closes on its own, the next entry is LIVE",
    p20 is not None and m20.mode == "LIVE" and p20["main"].get("mode") == "LIVE"
    and ("SELL", p20["main"]["tradingsymbol"]) in ex20.calls)

ex21 = Exec()
m21 = mk(SELL, db=os.path.join(tmpdir, "s.db"))
m21.open_position("PE", ts=1000, bar_ts=1000, condition=1)  # PAPER
m21.mode_reader, m21.executor_fn = (lambda: WANT[0]), (lambda: ex21)
r21 = m21.on_decision({"action": FLIP, "side": "CE", "reason": "FLIP",
                       "bar_ts": 1300, "condition": -1}, ts=1300)
chk("TC-VET-0335 a FLIP out of a PAPER position re-enters LIVE (exit PAPER, entry LIVE)",
    r21 and r21["closed"] and r21["opened"] and m21.mode == "LIVE"
    and m21.pos["main"].get("mode") == "LIVE"
    and not any(c[1] for c in ex21.calls if c[0] in ("CLOSE_SHORT", "CLOSE_LONG")), r21)

m22 = mk(SELL, db=os.path.join(tmpdir, "t.db"))
def _no_exec():
    raise RuntimeError("no session")
m22.mode_reader, m22.executor_fn = (lambda: "LIVE"), _no_exec
chk("TC-VET-0336 Settings LIVE but no broker executor: no entry at all (never a LIVE row with paper fills)",
    m22.open_position("PE", ts=1000, bar_ts=1000, condition=1) is None
    and m22.pos is None and m22.mode == "PAPER")

ex23 = Exec()
m23 = mk(SELL, db=os.path.join(tmpdir, "u.db"), execu=ex23)  # LIVE
m23.mode_reader = lambda: "PAPER"
p23 = m23.open_position("PE", ts=1000, bar_ts=1000, condition=1)
chk("TC-VET-0337 armed LIVE, Settings now PAPER, flat: the next entry is PAPER, no order",
    p23 is not None and m23.mode == "PAPER" and ex23.calls == [])

m24 = mk(SELL, db=os.path.join(tmpdir, "v.db"))
chk("TC-VET-0338 a manager with no mode reader (harness) never changes mode",
    m24.sync_mode() is True and m24.mode == "PAPER")

# arm: which book to start in
repo25 = m20.repo                                    # LIVE position open there now
chk("TC-VET-0339 arm with a LIVE position open and Settings PAPER: start LIVE (the live carry is never orphaned)",
    carried_book_mode(repo25, "PAPER") == "LIVE")
m26 = mk(SELL, db=os.path.join(tmpdir, "w.db"))
m26.open_position("PE", ts=1000, bar_ts=1000, condition=1)    # PAPER carry
chk("TC-VET-0340 arm with a PAPER position open and Settings LIVE: start PAPER (the 25 Nov paper trade runs to its own exit)",
    carried_book_mode(m26.repo, "LIVE") == "PAPER")
m27 = mk(SELL, db=os.path.join(tmpdir, "x.db"))
chk("TC-VET-0341 arm with nothing open: start in the Settings book",
    carried_book_mode(m27.repo, "LIVE") == "LIVE" and carried_book_mode(m27.repo, "PAPER") == "PAPER")

_i_book = _loop.find("mode = carried_book_mode(repo, settings_mode)")
_i_mgr = _loop.find("manager = VetManager(")
_i_reader = _loop.find("manager.mode_reader = lambda: _xm.book(_xm.strategy_mode(STRATEGY_ID))")
_i_sync = _loop.find("manager.sync_mode()")
chk("TC-VET-0342 loop: book picked from open positions before the manager is built; Settings read at each decision",
    0 < _i_book < _i_mgr < _i_reader and _i_dec < _i_sync < _i_gate, (_i_book, _i_mgr, _i_reader, _i_sync))
chk("TC-VET-0343 loop: the broker executor is built whatever the arm-time mode",
    'if mode == "LIVE":\n        from app.execution.executor_factory' not in _loop
    and "executor = get_executor_for_strategy(STRATEGY_ID)" in _loop)

print("\n── 10. A PAPER group open beside a LIVE one (VET_ORPHAN_PAPER_20261125, DEF-103) ──")
# 25 Nov replay: PAPER short open since 09:19, the old version opened a LIVE
# short at 10:05 beside it; the next version adopted LIVE and left PAPER open.
db30 = os.path.join(tmpdir, "y.db")
m30 = mk(SELL, db=db30)                                   # PAPER
m30.open_position("PE", ts=1000, bar_ts=1000, condition=1)
ex31 = Exec()
m31 = mk(SELL, db=db30, execu=ex31)                       # LIVE, same DB
m31.open_position("PE", ts=2000, bar_ts=2000, condition=1)
repo30 = m31.repo
chk("TC-VET-0344 both books open: LIVE wins whatever Settings say (real money managed first)",
    carried_book_mode(repo30, "PAPER") == "LIVE" and carried_book_mode(repo30, "LIVE") == "LIVE")
pg = repo30.open_group("PAPER")
lg = repo30.open_group("LIVE")
n_orders = len(ex31.calls)
chk("TC-VET-0346 no price for a leg: nothing is written, the PAPER rows stay OPEN",
    m31.close_orphan_paper(pg, ts=3000, mark_fn=lambda sym: None) is None
    and repo30.open_group("PAPER") is not None)
PX = {pg["main"]["tradingsymbol"]: 200.0, pg["wing"]["tradingsymbol"]: 3.0}
r32 = m31.close_orphan_paper(pg, ts=3000, mark_fn=PX.get)
rows = {r["id"]: r for r in repo30.trades_on(0, 10 ** 10, "PAPER")}
mrow, wrow = rows[pg["main"]["id"]], rows[pg["wing"]["id"]]
q_m, q_w = m31._leg_qty(pg["main"]), m31._leg_qty(pg["wing"])
exp = (float(pg["main"]["entry_price"]) - 200.0) * q_m + (3.0 - float(pg["wing"]["entry_price"])) * q_w
chk("TC-VET-0345 the PAPER group is closed in the paper book: both legs CLOSED, reason MODE_SWITCH, "
    "short P&L = (entry − price) × qty, wing = (price − entry) × qty",
    r32 is not None and mrow["status"] == "CLOSED" and wrow["status"] == "CLOSED"
    and mrow["exit_reason"] == "MODE_SWITCH" and abs(r32["gross"] - round(exp, 2)) < 0.01
    and mrow["net_pnl"] is not None and wrow["net_pnl"] is not None, (r32, exp))
chk("TC-VET-0345 ...with no broker order, and the LIVE position untouched",
    len(ex31.calls) == n_orders and repo30.open_group("LIVE") is not None
    and m31.pos and m31.pos["group_id"] == lg["group_id"])
chk("TC-VET-0347 a LIVE group is never closed this way",
    m31.close_orphan_paper(lg, ts=3100, mark_fn=PX.get) is None and repo30.open_group("LIVE") is not None)

_i_orph = _loop.find("_orphan = repo.open_group(\"PAPER\")")
_i_close = _loop.find("manager.close_orphan_paper(_orphan")
_i_adopt = _loop.find("g = repo.open_group(mode)")
chk("TC-VET-0348 loop: the PAPER group beside a LIVE one is closed at arm, after the manager is built "
    "and before the LIVE position is adopted",
    0 < _i_orph < _i_mgr < _i_close < _i_adopt, (_i_orph, _i_mgr, _i_close, _i_adopt))

print("\n" + ("ALL MANAGER SMOKE CHECKS PASSED" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)
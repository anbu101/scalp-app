# test_fleet_adversarial.py — adversarial fleet tests (written 15 Nov 2026).
#
# Each test states the SAFE behaviour as its expectation. A [MISS] line is a
# reproduced defect (or a design limit), with the symptom printed.
# Built on the existing fleet harness (test_fleet_live.Fleet / FleetKite):
# the four REAL managers, both REAL GTT monitors, the REAL ExecutionRouter.
#
# Run:  PYTHONPATH=backend python3 backend/app/engine/test_fleet_adversarial.py [test_name ...]
# ADV_STAGE=1..4 runs only the tests of the fixes installed so far
#   1 GTT_MONITOR_PROOF   2 GTT_KILL_LIST   3 FLAT_NEEDS_PROOF   4 LEDGER_BOOT
from __future__ import annotations

import os
import sys

from pathlib import Path                                       # noqa: E402

_HERE = Path(__file__).resolve()
ROOT = os.environ.get("SCALP_BACKEND") or (
    str(_HERE.parents[2]) if (_HERE.parents[2] / "app" / "engine").is_dir()
    else "/home/claude/anbu101/scalp-app/backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/app/engine")
os.environ.setdefault("SCALP_TEST_MODE", "1")

import test_fleet_live as F                                    # noqa: E402
from app.execution import broker_truth as BT                    # noqa: E402
from app.engine.ic.ic_live_core import L_OPEN, G_CLOSED         # noqa: E402

if hasattr(BT, "GttKillList"):
    BT.GttKillList.RETRY_GAP_S = 0.0     # the tests drive the retries themselves

S_VT, S_IC_CE, S_IC_PE = F.S_VT, F.S_IC_CE, F.S_IC_PE
RESULTS: list = []          # (id, title, ok, detail)


def expect(tid, title, cond, detail=""):
    RESULTS.append((tid, title, bool(cond), detail))
    print(f"[{'ok  ' if cond else 'MISS'}] {tid} {title}" + (f"\n         -> {detail}" if not cond else ""))


class AdvKite(F.FleetKite):
    """FleetKite plus the faults the fleet suite does not model."""

    def __init__(self, px):
        super().__init__(px)
        self.gtt_api_down = False      # GTT endpoints fail; orders and positions still work
        self.hide_gtt_orders = False   # triggered GTT not yet listing its order (known Kite race)
        self.positions_lie = False     # a SUCCESSFUL positions read that is empty
        self.status_override = {}      # gtt id -> status string still shown in the list

    def _gtt_rows(self):
        rows = super()._gtt_rows()
        for r in rows:
            if str(r["id"]) in self.status_override:
                r["status"] = self.status_override[str(r["id"])]
            if self.hide_gtt_orders:
                r["orders"] = [dict(o, result=None) for o in r["orders"]]
        return rows

    def get_gtts(self):
        return [] if self.gtt_api_down else super().get_gtts()

    def get_gtts_or_none(self):
        return None if self.gtt_api_down else super().get_gtts_or_none()

    def get_gtt_status(self, gid):
        if self.gtt_api_down:
            return None
        if str(gid) in self.status_override:
            return self.status_override[str(gid)]
        return super().get_gtt_status(gid)

    def cancel_gtt(self, gid):
        if self.gtt_api_down:
            raise RuntimeError("GTT API: gateway timeout")
        return super().cancel_gtt(gid)

    def get_open_positions_or_none(self):
        if self.positions_lie:
            return []
        return super().get_open_positions_or_none()

    def get_open_positions(self):
        if self.positions_lie:
            return []
        return super().get_open_positions()


def fleet():
    if hasattr(BT, "reset_app_orders"):
        BT.reset_app_orders()            # a new fake broker reuses order ids
    return F.Fleet(kite=AdvKite(F.prices()))


def pages(f):
    """Every critical page: the managers' own and the monitors' (Telegram sink)."""
    return list(f.crit) + [str(t) for t in f.sent["telegram"] if "notify_critical" in str(t[0])]


def filled(k, s, txn):
    """Quantity actually FILLED on a contract (placed-then-cancelled orders do not count)."""
    return sum(int(o["filled_quantity"]) for o in k.orders
               if o["tradingsymbol"] == s and o["transaction_type"] == txn)


def nz(k):
    return {s: q for s, q in k.net.items() if q}


def gtts_on(k, s):
    return [g for g in k.active_gtts() if g["symbol"] == s]


# ── A1 (S1) Timberwolf: forced exit while the GTT cannot be cancelled ─────────
def a1_timberwolf_forced_exit_gtt_uncancellable():
    f = fleet(); k = f.kite
    f.t2_enter()
    assert f.t2.group is not None and gtts_on(k, S_VT), "setup"
    k.gtt_api_down = True
    f.t2.kill_close(f.ts_of(15, 25))                     # same forced path as the EOD exit
    closed = f.t2.group is None and k.net.get(S_VT, 0) == 0
    k.gtt_api_down = False
    for m in range(1, 6):                                # five minutes of chances to clean up
        f.t2.on_minute(f.ts_of(15, 25) + 60 * m, None, f.chain)
        f.t2_sweep()
    left = gtts_on(k, S_VT)
    expect("A1a", "Timberwolf: after a forced exit with the GTT API down, the SL GTT is cancelled once the API is back",
           closed and not left,
           f"position closed={closed}; GTT still ACTIVE at broker: {[g['id'] for g in left]}; "
           f"app still remembers a GTT id: {bool(f.t2.group)}")
    k.set_px(S_VT, 230.0)                                # next move through the old stop
    expect("A1b", "Timberwolf: the forgotten GTT cannot open a position later",
           k.net.get(S_VT, 0) == 0, f"broker net after price crossed the old stop: {nz(k)}")
    return f


# ── A2 (S2) Icarus: leftover GTT on a strike another strategy still holds ─────
def a2_icarus_leftover_gtt_on_shared_strike(shared=True):
    f = fleet(); k = f.kite
    f.ic_enter()
    if shared:
        f.ts_enter()                                     # Tigris short the same 25300 CE / 24950 PE
    tag = "A2" if shared else "A2u"
    k.gtt_api_down = True
    f.ic.force_square_off_all(reason="KILL")
    g = f.ic.current_group()
    closed = g is not None and g.state == G_CLOSED
    k.gtt_api_down = False
    for _ in range(3):                                   # every periodic hook the engine has
        f.ic.process_due()
        f.ic.morning_square_off()
        f.ic_sweep()
    left = [x for x in k.active_gtts() if x["symbol"] in (S_IC_CE, S_IC_PE) and x["qty"] == 195]
    expect(tag + "a", f"Icarus ({'shared strike' if shared else 'sole holder'}): GTTs of a closed condor are cancelled once the GTT API is back",
           closed and not left,
           f"group closed={closed}; Icarus GTTs still ACTIVE: {[(x['symbol'][-7:], x['qty']) for x in left]}")
    before = k.net.get(S_IC_CE, 0)
    k.set_px(S_IC_CE, 140.0)                             # through Icarus's old stop
    expect(tag + "b", "Icarus: a leftover GTT cannot trade after the condor is closed",
           k.net.get(S_IC_CE, 0) == before,
           f"25300CE net before {before} -> after {k.net.get(S_IC_CE, 0)} "
           f"(Tigris holds {-130 if shared else 0})")
    return f


def a2u_icarus_leftover_gtt_sole_holder():
    return a2_icarus_leftover_gtt_on_shared_strike(shared=False)


# ── A3 (S3) monitors: "triggered/disabled" branch books without an order ──────
def a3_timberwolf_disabled_gtt_and_empty_positions():
    f = fleet(); k = f.kite
    f.t2_enter()
    gid = str(f.t2.group["sell"]["gtt_id"])
    k.status_override[gid] = "disabled"
    k.positions_lie = True                               # successful read, empty answer
    f.t2_sweep(4)
    booked = f.t2.group is None
    expect("A3", "Timberwolf monitor: no exit order means no booking, also when the GTT reads disabled/triggered",
           not booked,
           f"group booked closed={booked} with NO exit order while the broker is still short: {nz(k)}; "
           f"alerts={[c[:60] for c in f.crit][-2:]}")
    return f


def a3i_icarus_disabled_gtt_and_empty_positions():
    f = fleet(); k = f.kite
    f.ic_enter()
    for leg in f.ic_legs().values():
        for gid in f.ic.leg_runtime(leg.leg_id).get("gtt_ids") or []:
            k.status_override[str(gid)] = "disabled"
    k.positions_lie = True
    f.ic_sweep(4)
    closed = [l.leg_id for l in f.ic_legs().values() if l.is_short and l.state != L_OPEN]
    expect("A3i", "Icarus monitor: no exit order means no booking, also when the GTT reads disabled/triggered",
           not closed, f"short legs booked closed with NO exit order: {closed}; broker still holds {nz(k)}")
    return f


# ── A4 (S4) fill misattribution across strategies ─────────────────────────────
def a4_icarus_books_tigris_fill_as_its_own():
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    k.hide_gtt_orders = True                             # GTT shows "triggered" before listing its order
    k.set_px(S_IC_CE, 200.0)                             # gap far past Icarus's stop + buffer: its limit rests
    f.ts.kill_all()                                      # Tigris buys back ITS 130 on the same strike
    f.ic_sweep(3)
    leg = f.ic_legs()["L1"]
    net = k.net.get(S_IC_CE, 0)
    expect("A4", "Icarus monitor: another strategy's buy on the same strike is never booked as Icarus's stop fill",
           not (leg.state != L_OPEN and net < 0),
           f"Icarus L1 booked closed={leg.state != L_OPEN} at {getattr(leg, 'exit_price', None)} "
           f"while the broker is still short {net} on 25300CE; working orders={len(k.open_orders())}")
    return f


# ── A10 Icarus: stop gapped through on a shared strike → nobody acts ──────────
def a10_icarus_gap_on_shared_strike_deadlock():
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    tok = f.ic.leg_runtime("L1").get("token")
    k.set_px(S_IC_CE, 200.0)                             # GTT triggered, its LIMIT rests unfilled (order id visible)
    f.crit.clear(); f.sent["telegram"].clear()
    for _ in range(8):                                   # ~2.5 minutes of monitor sweeps + ticks
        f.ic_sweep()
        f.ic.on_tick(tok, 200.0)
        f.ic.process_due()
    leg = f.ic_legs()["L1"]
    alerted = any("25300CE" in c for c in pages(f))
    expect("A10", "Icarus: a stop that gapped through on a SHARED strike is market-closed (or at least paged)",
           leg.state != L_OPEN or alerted,
           f"after 8 sweeps: Icarus short still open={leg.state == L_OPEN}, broker net {k.net.get(S_IC_CE)}, "
           f"GTT consumed, resting limit orders={len(k.open_orders())}, critical alerts={len(f.crit)}")
    return f


def a10t_timberwolf_gap_on_shared_strike():
    f = fleet(); k = f.kite
    f.vet_enter(); f.t2_enter()
    k.set_px(S_VT, 260.0)                                # far past Timberwolf's stop + buffer
    for m in range(1, 5):
        f.t2_sweep()
        f.t2.on_minute(f.ts_of(13, 0) + 60 * m, None, f.chain)
    expect("A10t", "Timberwolf: a stop that gapped through on a SHARED strike is closed by the app",
           f.t2.group is None and k.net.get(S_VT, 0) == -130 and not k.open_orders(),
           f"group open={f.t2.group is not None}, net {k.net.get(S_VT)}, working={len(k.open_orders())}")
    return f


# ── A6 (S5) "already flat" from one successful-but-empty positions read ───────
def a6_velvet_already_flat_on_empty_read():
    f = fleet(); k = f.kite
    f.vet_enter()
    k.positions_lie = True
    f.vet_exit()
    booked = f.vet.pos is None
    expect("A6v", "Velvet: a single empty positions answer never books an exit that has no order",
           not (booked and k.net.get(S_VT, 0) != 0),
           f"Velvet booked closed={booked} with no order; broker still holds {nz(k)}")
    return f


def a6t_timberwolf_already_flat_on_empty_read():
    f = fleet(); k = f.kite
    f.t2_enter()
    k.positions_lie = True
    f.t2.kill_close(f.ts_of(13, 30))
    booked = f.t2.group is None
    expect("A6t", "Timberwolf: a single empty positions answer never books an exit that has no order",
           not (booked and k.net.get(S_VT, 0) != 0),
           f"Timberwolf booked closed={booked}; broker still holds {nz(k)}; active GTTs={len(k.active_gtts())}")
    return f


def a6i_icarus_morning_squareoff_on_empty_read():
    f = fleet(); k = f.kite
    f.ic_enter()
    k.positions_lie = True
    f.ic.force_square_off_all(reason="KILL")
    g = f.ic.current_group()
    booked = g is not None and g.state == G_CLOSED
    expect("A6i", "Icarus: a single empty positions answer never books a square-off that has no order",
           not (booked and nz(k)),
           f"Icarus booked closed={booked}; broker still holds {nz(k)}; active GTTs={len(k.active_gtts())}")
    return f


# ── A7 (S6) a GTT that is listed but no longer armed ──────────────────────────
def a7_listed_but_dead_gtt(status="cancelled"):
    f = fleet(); k = f.kite
    f.t2_enter()
    gid = str(f.t2.group["sell"]["gtt_id"])
    k.gtts[gid]["status"] = status                       # no longer armed at the broker, still in the list
    f.crit.clear(); f.sent["telegram"].clear()
    f.t2_sweep(6)
    paged = any("GTT" in c for c in pages(f))
    expect(f"A7-{status}", f"Timberwolf monitor: a GTT listed as '{status}' is reported as missing protection",
           paged, f"open short with a dead GTT, 6 sweeps, critical alerts: {len(pages(f))}")
    return f


def a7r(): return a7_listed_but_dead_gtt("rejected")
def a7e(): return a7_listed_but_dead_gtt("expired")


def a7i_icarus_listed_but_dead_gtt():
    f = fleet(); k = f.kite
    f.ic_enter()
    for leg in f.ic_legs().values():
        for gid in f.ic.leg_runtime(leg.leg_id).get("gtt_ids") or []:
            k.gtts[str(gid)]["status"] = "cancelled"
    f.crit.clear(); f.sent["telegram"].clear()
    f.ic_sweep(6)
    expect("A7i", "Icarus monitor: GTTs listed as 'cancelled' are reported as missing protection",
           any("GTT" in c for c in pages(f)), f"two open shorts with dead GTTs, 6 sweeps, critical alerts: {len(pages(f))}")
    return f


# ── A8 (S9) a human closes one strategy's leg on a shared strike ──────────────
def a8_manual_close_on_shared_strike():
    f = fleet(); k = f.kite
    f.vet_enter(); f.t2_enter()
    k.manual(S_VT, "BUY", 130)                           # the human closes "Velvet's" short by hand
    f.t2_sweep(4)
    f.t2.on_minute(f.ts_of(13, 0), None, f.chain)        # recon tick
    paged = [c for c in f.crit if "RECON" in c or S_VT in c]
    f.vet_exit()
    f.t2.kill_close(f.ts_of(15, 20))
    expect("A8", "Manual close on a shared strike: the account still ends flat (or the app refuses to exit blind)",
           k.net.get(S_VT, 0) == 0,
           f"final net on the shared strike: {k.net.get(S_VT, 0)} (an untracked LONG); "
           f"alerts before the exits: {len(paged)}")
    return f


# ── A5 (S7) ledger: a registered manager that has not restored yet ────────────
def a5_registered_but_unrestored_masks_db():
    f = fleet()
    saved = BT._db_holders
    BT._db_holders = lambda: {S_VT: {"TMA_V2": -130}}    # Timberwolf's OPEN LIVE row in the database
    try:
        shared = BT.LEDGER.shared(S_VT, "VET_V1")        # Timberwolf's manager exists, group not adopted yet
    finally:
        BT._db_holders = saved
    expect("A5", "Ledger: during boot, a strategy's open DB rows count until its manager has restored",
           shared, "Timberwolf has an open live row on the strike, but Velvet is told the contract is NOT shared")
    return f


# ── A11 ledger: provider object garbage-collected while holding ───────────────
def a11_dead_provider_masks_db():
    import gc
    L = BT.LiveLedger()

    class M:
        def view(self):
            return {"X": -130}
    m = M()
    L.register("TMA_V2", m.view)
    del m
    gc.collect()
    saved_l, saved_d = BT.LEDGER, BT._db_holders
    BT._db_holders = lambda: {"X": {"TMA_V2": -130}}
    try:
        shared = L.shared("X", "VET_V1")
    finally:
        BT._db_holders = saved_d
    expect("A11", "Ledger: when a manager object is gone, its open DB rows still count",
           shared, "holder with an open DB row is invisible after its manager object was replaced")


# ── A12 reservation: release() drops ALL of a strategy's reservations ─────────
def a12_release_drops_other_reservation():
    L = BT.LiveLedger()
    L.reserve("IC_V2", {"ENTRY_SHORT": -195})            # entry in flight
    L.reserve("IC_V2", {"ADJ_LONG": 1})                  # adjustment placed meanwhile
    L.release("IC_V2", {"ADJ_LONG": 1})                  # adjustment finished (releases only its own)
    expect("A12", "Ledger: finishing one order sequence does not release another one's reservation",
           L.shared("ENTRY_SHORT", "VET_V1"), "the entry's contract is no longer marked as shared")


# ═══ second round: trying to break the FIXES ═════════════════════════════════
def b1_gap_shared_strike_ends_clean():
    """D1 fix: after the escalation only Icarus's quantity was bought, the stale
    GTT limit is cancelled, Tigris is untouched."""
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    b0 = filled(k, S_IC_CE, "BUY")
    k.set_px(S_IC_CE, 200.0)
    f.ic_sweep(4)
    leg = f.ic_legs()["L1"]
    expect("B1", "Gap on a shared strike: Icarus buys back exactly its own 195, nothing left working, Tigris untouched",
           leg.state != L_OPEN and filled(k, S_IC_CE, "BUY") - b0 == 195 and k.net.get(S_IC_CE) == -130
           and not [o for o in k.open_orders() if o["tradingsymbol"] == S_IC_CE],
           f"closed={leg.state != L_OPEN} bought={filled(k, S_IC_CE, 'BUY') - b0} net={k.net.get(S_IC_CE)} "
           f"working={[(o['order_id'], o['quantity']) for o in k.open_orders()]}")
    return f


def b2_gap_shared_strike_cancel_fails():
    """D1 fix: the resting GTT limit cannot be cancelled → NO market-out (it
    would double the buy), one page; once cancellable the leg is closed."""
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    b0 = f.buys(S_IC_CE)
    k.set_px(S_IC_CE, 200.0)
    k.cancel_ok = False
    f.sent["telegram"].clear(); f.crit.clear()
    f.ic_sweep(5)
    held = f.ic_legs()["L1"].state == L_OPEN and f.buys(S_IC_CE) - b0 == 195   # only the GTT's own resting order
    paged = [c for c in pages(f) if "could not be cancelled" in c]
    k.cancel_ok = True
    f.ic_sweep(2)
    expect("B2", "Gap on a shared strike with an uncancellable GTT order: no second buy, one page, closed once cancellable",
           held and len(paged) == 1 and f.ic_legs()["L1"].state != L_OPEN and k.net.get(S_IC_CE) == -130
           and not [o for o in k.open_orders() if o["tradingsymbol"] == S_IC_CE],
           f"held={held} pages={len(paged)} closed={f.ic_legs()['L1'].state != L_OPEN} net={k.net.get(S_IC_CE)}")
    return f


def b3_gap_shared_strike_partial_gtt_fill():
    """D1 fix: the GTT's own order filled 65 of 195 before it was cancelled →
    the escalation buys only the remaining 130."""
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    b0 = f.buys(S_IC_CE)
    k.partial_once[S_IC_CE] = 65
    k.set_px(S_IC_CE, 200.0)                              # GTT order: 65 filled, 130 resting
    f.ic_sweep(4)
    filled = sum(o["filled_quantity"] for o in k.orders
                 if o["tradingsymbol"] == S_IC_CE and o["transaction_type"] == "BUY")
    expect("B3", "Gap on a shared strike after a partial GTT fill: exactly 195 bought in total",
           f.ic_legs()["L1"].state != L_OPEN and filled == 195 and k.net.get(S_IC_CE) == -130,
           f"closed={f.ic_legs()['L1'].state != L_OPEN} total bought={filled} net={k.net.get(S_IC_CE)}")
    return f


def b4_fill_lookup_when_ids_appear_later():
    """D2 fix: nothing is booked from another strategy's order; when the GTT
    lists its own order the leg is booked at ITS fill."""
    f = fleet(); k = f.kite
    f.ic_enter(); f.ts_enter()
    k.hide_gtt_orders = True
    k.set_px(S_IC_CE, 140.0)                              # Icarus's GTT fires AND fills (limit 144.6)
    f.ts.kill_all()                                       # Tigris buys back its 130 at 140 too
    f.ic_sweep(1)
    k.hide_gtt_orders = False
    f.ic_sweep(2)
    leg = f.ic_legs()["L1"]
    expect("B4", "GTT order listed late: Icarus books its own GTT fill and the account matches",
           leg.state != L_OPEN and k.net.get(S_IC_CE, 0) == 0,
           f"closed={leg.state != L_OPEN} net={k.net.get(S_IC_CE, 0)}")
    return f


def b5_kill_list_survives_restart():
    """D3 fix: the id is on disk — a NEW manager (app restart) cancels it."""
    f = fleet(); k = f.kite
    f.t2_enter()
    k.gtt_api_down = True
    f.t2.kill_close(f.ts_of(15, 25))
    f2 = F.Fleet(kite=k, tmp=f.tmp, restore=True)         # the app restarts, GTT API still down
    kept = len(f2.t2._gtt_kill)
    k.gtt_api_down = False
    f2.t2.on_minute(f2.ts_of(15, 31), None, f2.chain)
    expect("B5", "Kill list: a restarted app still knows the leftover GTT and cancels it",
           kept == 1 and not gtts_on(k, S_VT) and len(f2.t2._gtt_kill) == 0,
           f"on list after restart={kept}; still active={len(gtts_on(k, S_VT))}")
    return f2


def b6_kill_list_gtt_fires_first():
    """D3 fix: the leftover GTT fires before it can be cancelled → the user is
    told to check Kite (the app cannot undo a trade)."""
    f = fleet(); k = f.kite
    f.t2_enter()
    k.gtt_api_down = True
    f.t2.kill_close(f.ts_of(15, 25))
    k.set_px(S_VT, 230.0)                                 # it fires while the API is still down
    k.gtt_api_down = False
    f.crit.clear(); f.sent["telegram"].clear()
    f.t2.on_minute(f.ts_of(15, 31), None, f.chain)
    paged = [c for c in pages(f) if "TRIGGERED" in c]
    expect("B6", "Kill list: a leftover GTT that fired is paged as an unintended trade to check",
           len(paged) == 1 and len(f.t2._gtt_kill) == 0, f"pages={len(paged)} list={len(f.t2._gtt_kill)}")
    return f


def b7_kill_list_normal_exits_stay_empty():
    """D3/D4 fix must not page on ordinary days: clean exits and GTT stops
    leave nothing on the list."""
    f = fleet(); k = f.kite
    f.ic_enter(); f.t2_enter()
    k.set_px(S_VT, 226.0); f.t2_sweep(2)                  # Timberwolf's stop fires and fills
    k.set_px(S_IC_CE, 140.0); f.ic_sweep(2)               # Icarus's L1 stop fires and fills
    f.ic.process_due(int(F.time.time()) + 61)
    f.ic.force_square_off_all(reason="EOD")
    f.ic.process_due()
    expect("B7", "Kill list: ordinary stops and exits leave it empty and nothing armed",
           len(f.ic._gtt_kill) == 0 and len(f.t2._gtt_kill) == 0 and not k.active_gtts(),
           f"ic={f.ic._gtt_kill.ids()} t2={f.t2._gtt_kill.ids()} active={len(k.active_gtts())}")
    return f


def b8_flat_proof_prior_day_exit():
    """D5 fix must not strand a leg that really is gone with no order today
    (closed by hand on an earlier day): booked after repeated flat reads."""
    f = fleet(); k = f.kite
    f.vet_enter()
    main, wing = f.vet.pos["main"]["tradingsymbol"], f.vet.pos["wing"]["tradingsymbol"]
    k.net[main] = 0; k.net[wing] = 0                      # gone at the broker, no order in today's book
    n0 = len(k.orders)
    f.vet_exit(ts=2000)
    first = f.vet.pos is not None
    for i in range(1, 6):
        k.clock.t += 61
        f.vet.retry_exit(2000 + 60 * i)
    expect("B8", "Flat with no order today: not booked at once, booked after repeated flat reads, no order sent",
           first and f.vet.pos is None and len(k.orders) == n0,
           f"held at first={first} booked later={f.vet.pos is None} orders sent={len(k.orders) - n0}")
    return f


def b9_flat_proof_real_manual_exit():
    """D5 fix: a real manual exit today leaves an order → booked at once."""
    f = fleet(); k = f.kite
    f.t2_enter()
    k.manual(S_VT, "BUY", 130)
    b0 = f.buys(S_VT)
    f.t2.kill_close(f.ts_of(13, 30))
    expect("B9", "Manual exit today: Timberwolf books at once and does not buy again",
           f.buys(S_VT) == b0 and (f.t2.group is None or f.t2.group["exit_state"]["sell_done"]),
           f"extra bought={f.buys(S_VT) - b0} group={f.t2.group is not None}")
    return f


def b10_flat_lie_then_truth():
    """D5 fix: the empty answer lasts one attempt; the next attempt sees the
    real position and exits properly."""
    f = fleet(); k = f.kite
    f.vet_enter()
    k.positions_lie = True
    f.vet_exit(ts=2000)
    k.positions_lie = False
    f.vet.retry_exit(2060)
    expect("B10", "A one-off empty positions answer: nothing booked, then a normal exit",
           f.vet.pos is None and k.flat(), f"pos={f.vet.pos is not None} net={nz(k)}")
    return f


def b11_dead_gtt_then_stop_still_exits():
    """D6 fix: with its GTT dead the app-side stop still closes the short."""
    f = fleet(); k = f.kite
    f.ic_enter()
    rt = f.ic.leg_runtime("L1")
    for gid in rt.get("gtt_ids") or []:
        k.gtts[str(gid)]["status"] = "rejected"
    f.ic_sweep(3)
    k.px[S_IC_CE] = 140.0                                 # through the stop; no GTT can fire
    f.ic.on_tick(rt.get("token"), 140.0)
    f.ic_sweep(1)
    expect("B11", "Dead GTT: Icarus's own tick stop still closes the short",
           f.ic_legs()["L1"].state != L_OPEN and k.net.get(S_IC_CE, 0) == 0,
           f"closed={f.ic_legs()['L1'].state != L_OPEN} net={k.net.get(S_IC_CE, 0)}")
    return f


def z_soak(seeds=None):
    """Seeded random days WITH faults. Oracle at the end of each day, after the
    faults clear and the app gets its normal hooks: flat, bought == sold on
    every contract, no GTT armed, nothing working."""
    import random
    seeds = int(os.environ.get("ADV_SOAK_SEEDS", seeds or 120))
    bad = []
    for seed in range(seeds):
        rnd = random.Random(1000 + seed)
        f = fleet(); k = f.kite
        tok = lambda: f.ic.leg_runtime("L1").get("token")          # noqa: E731

        lies = [0]

        def lie_blip():
            lies[0] += 1
            if lies[0] > 2:          # three consistent empty answers in a row is the documented limit
                return
            k.positions_lie = True
            try:
                if f.vet.pos is not None:
                    f.vet_exit(ts=3000)
                f.t2.kill_close(f.ts_of(14, 30))
            finally:
                k.positions_lie = False
        acts = {
            "ic_in": f.ic_enter, "ts_in": f.ts_enter, "vet_in": f.vet_enter, "t2_in": f.t2_enter,
            "vet_out": lambda: f.vet.pos is not None and f.vet_exit(ts=2000 + rnd.randint(0, 9) * 60),
            "t2_out": lambda: f.t2.kill_close(f.ts_of(14, rnd.randint(0, 59))),
            "ts_out": lambda: f.ts._core is not None and f.ts.square_off_all("EOD"),
            "ic_out": lambda: f.ic.current_group() is not None and f.ic.force_square_off_all(reason="EOD"),
            # (a stop that fires while the GTT service is unreadable, followed at
            #  once by a forced exit on a shared strike, is a documented limit —
            #  see the report — so prices only move while GTTs can be read)
            "shock_vt": lambda: k.gtt_api_down or k.set_px(S_VT, 226.0),
            "gap_vt": lambda: k.gtt_api_down or k.set_px(S_VT, 260.0),
            "shock_ic": lambda: k.gtt_api_down or k.set_px(S_IC_CE, 140.0),
            "gap_ic": lambda: k.gtt_api_down or k.set_px(S_IC_CE, 200.0),
            "calm": lambda: (k.set_px(S_VT, 200.0), k.set_px(S_IC_CE, 97.0)),
            "sweep": lambda: (f.ic_sweep(3), f.t2_sweep(3)),
            "due": lambda: f.ic.process_due(int(F.time.time()) + 61),
            "tick": lambda: tok() and f.ic.on_tick(tok(), k.px[S_IC_CE]),
            "minute": lambda: f.t2.on_minute(f.ts_of(13, rnd.randint(0, 59)), None, f.chain),
            "gtt_down": lambda: (f.ic_sweep(3), f.t2_sweep(3),
                                 setattr(k, "gtt_api_down", not k.gtt_api_down)),
            "hide": lambda: setattr(k, "hide_gtt_orders", not k.hide_gtt_orders),
            "nocancel": lambda: setattr(k, "cancel_ok", not k.cancel_ok),
            "outage": lambda: (setattr(k, "logged_in", False), f.ic_sweep(4), f.t2_sweep(4),
                               setattr(k, "logged_in", True)),
            "blind": lambda: setattr(k, "positions_readable", not k.positions_readable),
            "lie": lie_blip,
        }
        plan = ["ic_in", "ts_in", "vet_in", "t2_in", "vet_out", "t2_out", "ts_out", "ic_out"] \
            + [rnd.choice(["shock_vt", "gap_vt", "shock_ic", "gap_ic", "calm", "sweep", "sweep", "due",
                           "tick", "minute", "gtt_down", "hide", "nocancel", "outage", "blind", "lie"])
               for _ in range(14)]
        rnd.shuffle(plan)
        done = []
        try:
            for a in plan:
                done.append(a)
                acts[a]()
            # the day ends: faults clear, the app runs its normal hooks
            k.gtt_api_down = k.hide_gtt_orders = k.positions_lie = False
            k.cancel_ok = k.logged_in = k.positions_readable = k.market_open = True
            for i in range(4):
                f.ic_sweep(3); f.t2_sweep(3)
                f.flatten_all()
                if f.vet.pos is not None and f.vet.pos.get("exit_state"):
                    f.vet.retry_exit(9100 + 60 * i)
                f.ic.process_due(int(F.time.time()) + 61)
                f.t2.on_minute(f.ts_of(15, 30 + i), None, f.chain)
                if f.ts._core is not None:
                    f.ts.on_minute(f.ts_of(15, 30 + i)) if False else None
            over = {x[-7:]: (filled(k, x, "BUY"), filled(k, x, "SELL")) for x in k.px
                    if filled(k, x, "BUY") != filled(k, x, "SELL")}
            if not k.flat() or over or k.active_gtts() or k.open_orders():
                bad.append((1000 + seed, done, f"net {nz(k)} bought!=sold {over} "
                                               f"gtts {len(k.active_gtts())} working "
                                               f"{[(o['tradingsymbol'][-7:], o['transaction_type'], o['quantity'], o['src']) for o in k.open_orders()]} "
                                               f"groups ic={getattr(f.ic.current_group(), 'state', None)} t2={f.t2.group is not None} "
                                               f"vet={f.vet.pos is not None}"))
        except Exception as e:
            import traceback
            bad.append((1000 + seed, done[-6:], f"{e!r} {traceback.format_exc()[-300:]}"))
    expect("Z1", f"SOAK: {seeds} random days with faults (GTT API down, late order ids, failed cancels, "
                 f"gaps, outages, empty answers) — every day ends flat, bought == sold, no GTT armed, nothing working",
           not bad, f"{len(bad)} bad day(s); first: {bad[:1]}")


STAGE = {1: ["a3_", "a3i_", "a4_", "a10_", "a10t_", "a7", "b1_", "b2_", "b3_", "b4_", "b11_"],
         2: ["a1_", "a2_", "a2u_", "b5_", "b6_", "b7_"],
         3: ["a6", "a8_", "b8_", "b9_", "b10_", "z_soak"],
         4: ["a5_", "a11_", "a12_"]}


TESTS = [a1_timberwolf_forced_exit_gtt_uncancellable, a2_icarus_leftover_gtt_on_shared_strike,
         a2u_icarus_leftover_gtt_sole_holder, a3_timberwolf_disabled_gtt_and_empty_positions,
         a3i_icarus_disabled_gtt_and_empty_positions, a4_icarus_books_tigris_fill_as_its_own,
         a10_icarus_gap_on_shared_strike_deadlock, a10t_timberwolf_gap_on_shared_strike,
         a6_velvet_already_flat_on_empty_read, a6t_timberwolf_already_flat_on_empty_read,
         a6i_icarus_morning_squareoff_on_empty_read, a7_listed_but_dead_gtt, a7r, a7e,
         a7i_icarus_listed_but_dead_gtt, a8_manual_close_on_shared_strike,
         a5_registered_but_unrestored_masks_db, a11_dead_provider_masks_db,
         a12_release_drops_other_reservation,
         b1_gap_shared_strike_ends_clean, b2_gap_shared_strike_cancel_fails,
         b3_gap_shared_strike_partial_gtt_fill, b4_fill_lookup_when_ids_appear_later,
         b5_kill_list_survives_restart, b6_kill_list_gtt_fires_first,
         b7_kill_list_normal_exits_stay_empty, b8_flat_proof_prior_day_exit,
         b9_flat_proof_real_manual_exit, b10_flat_lie_then_truth,
         b11_dead_gtt_then_stop_still_exits, z_soak]

if __name__ == "__main__":
    import io
    import contextlib
    only = sys.argv[1:]
    stage = int(os.environ.get("ADV_STAGE") or 4)
    allowed = [p for n in range(1, stage + 1) for p in STAGE[n]]
    for t in TESTS:
        if only and t.__name__ not in only:
            continue
        if not only and not any(t.__name__.startswith(p) for p in allowed):
            continue
        print(f"\n── {t.__name__} ──")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):        # hide the base harness's own check lines
                t()
            for line in buf.getvalue().splitlines():
                if line.startswith(("[ok  ] A", "[MISS] A", "[ok  ] B", "[MISS] B", "[ok  ] Z", "[MISS] Z", "         ->")):
                    print(line)
        except AssertionError as e:
            print(f"[SETUP] {t.__name__}: {e}")
            RESULTS.append((t.__name__, "setup failed", None, str(e)))
        except Exception as e:
            import traceback
            print(f"[ERROR] {t.__name__}: {e!r}\n{traceback.format_exc()[-900:]}")
            RESULTS.append((t.__name__, "raised", None, repr(e)))
    ok = sum(1 for r in RESULTS if r[2] is True)
    miss = sum(1 for r in RESULTS if r[2] is False)
    err = sum(1 for r in RESULTS if r[2] is None)
    print(f"\n{ok} held, {miss} MISS (defect or limit reproduced), {err} could not run")
    if miss or err:
        sys.exit(1)
    print("ALL ADVERSARIAL FLEET CHECKS PASSED")

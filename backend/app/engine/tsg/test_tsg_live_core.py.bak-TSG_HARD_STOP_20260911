# backend/app/engine/tsg/test_tsg_live_core.py
#
# TSG_V1 — pure live-core regression tests (no app imports).
# Every rule shared with the backtest core is asserted here in the live
# core's INCREMENTAL form; run alongside the backtest tests before any
# manager/engine edit ships.
#
# Run: python3 backend/app/engine/tsg/test_tsg_live_core.py

import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from tsg_live_core import (  # noqa: E402
    TsgDayCore, TsgLeg, select_strike, resolve_lots, leg_mtm,
    D_OPEN, D_PARTIAL, D_CLOSED, D_SKIPPED, D_ABORTED, L_OPEN, L_DEAD,
)

CFG = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 85},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 85},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 5},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 5},
]
LADDER = {"CE": [("C1", 90.0), ("C2", 78.0), ("C3", 30.0), ("C4", 4.2)],
          "PE": [("P1", 92.0), ("P2", 81.0), ("P3", 28.0), ("P4", 3.9)]}
META = {s: {"strike": 100 + i, "expiry": "2026-08-04"}
        for i, s in enumerate(["C1", "C2", "C3", "C4", "P1", "P2", "P3", "P4"])}


def _entered(**kw):
    c = TsgDayCore(**kw)
    assert c.plan_entry(LADDER, CFG, 65, 10, META) is not None
    for lid, px in (("L1", 78.0), ("L2", 81.0), ("L3", 4.2), ("L4", 3.9)):
        c.leg_filled(lid, px)
    assert c.state == D_OPEN
    c.set_entry_iv("L1", 0.11)
    c.set_entry_iv("L2", 0.12)
    return c


def test_selection_parity():
    assert select_strike(LADDER["CE"], 85) == ("C2", 78.0, False)   # highest <= cap
    assert select_strike(LADDER["CE"], 2) is None
    assert select_strike(LADDER["CE"], 2, fallback_cheapest=True) == ("C4", 4.2, True)


def test_no_short_skips_day():
    c = TsgDayCore()
    out = c.plan_entry({"CE": [("C1", 90.0)], "PE": LADDER["PE"]}, CFG, 65, 10, META)
    assert out is None and c.state == D_SKIPPED and "CE short" in c.skip_reason


def test_wing_absent_allowed():
    lad = {"CE": [("C2", 78.0)], "PE": [("P2", 81.0), ("P4", 3.9)]}
    c = TsgDayCore()
    planned = c.plan_entry(lad, CFG, 65, 10, META)
    assert {l.leg_id for l in planned} == {"L1", "L2", "L4"}   # L3 absent (IV8)


def test_entry_unwind_all_or_nothing():
    c = TsgDayCore()
    c.plan_entry(LADDER, CFG, 65, 10, META)
    c.leg_filled("L3", 4.2); c.leg_filled("L4", 3.9)
    unwind = c.leg_entry_dead("L1")
    assert c.state == D_ABORTED and set(unwind) == {"L3", "L4"}
    assert c.legs["L1"].state == L_DEAD


def test_mtm_sl_all_legs_first_crossing():
    c = _entered(mtm_sl=3000, mtm_target=0)
    assert c.evaluate_minute({"L1": 79, "L2": 82, "L3": 4.2, "L4": 3.9}, {}) is None
    dec = c.evaluate_minute({"L1": 81.5, "L2": 83.0, "L3": 4.2, "L4": 3.9}, {})
    assert dec == ("MTM_SL", ["L1", "L2", "L3", "L4"])   # -(3.5+2)*650 = -3575


def test_target_parity_kept():
    c = _entered(mtm_sl=0, mtm_target=5000)
    dec = c.evaluate_minute({"L1": 72.0, "L2": 78.0, "L3": 4.2, "L4": 3.9}, {})
    assert dec == ("MTM_TARGET", ["L1", "L2", "L3", "L4"])   # (6+3)*650=5850


def test_iv_pair_exit_one_shot_and_iv9():
    c = _entered(mtm_sl=0, iv_sl_delta_pts=8)          # thr L1=.19 L2=.20
    # L1 IV crossed but WINNING (mark < entry) → IV9 blocks
    assert c.evaluate_minute({"L1": 60.0, "L2": 81.0, "L3": 4.2, "L4": 3.9},
                             {"L1": 0.25}) is None
    # L1 losing + crossed → pair exit L1+L3
    dec = c.evaluate_minute({"L1": 86.0, "L2": 81.0, "L3": 4.2, "L4": 3.9},
                            {"L1": 0.25})
    assert dec == ("IV_SL", ["L1", "L3"])
    c.leg_exited("L1", 86.0, "IV_SL"); c.leg_exited("L3", 4.0, "IV_SL_HEDGE")
    assert c.state == D_PARTIAL
    # one-shot: L2 crossing later (losing) must be ignored
    assert c.evaluate_minute({"L2": 95.0, "L4": 3.9}, {"L2": 0.60}) is None


def test_day_mtm_includes_realized_after_partial():
    c = _entered(mtm_sl=6000, iv_sl_delta_pts=8)
    c.evaluate_minute({"L1": 86.0, "L2": 81.0, "L3": 4.2, "L4": 3.9}, {"L1": 0.25})
    c.leg_exited("L1", 86.0, "IV_SL"); c.leg_exited("L3", 4.0, "IV_SL_HEDGE")
    # realized = -(86-78)*650? SELL: (78-86)*650 = -5200; L3 BUY (4.0-4.2)*650=-130
    assert abs(c.realized - (-5330.0)) < 1e-6
    # survivors slip a little → total <= -6000 crosses on REALIZED+unrealized
    dec = c.evaluate_minute({"L2": 82.2, "L4": 3.9}, {})
    assert dec == ("MTM_SL", ["L2", "L4"])              # -5330 + (-780) = -6110


def test_missing_mark_carries_forward():
    c = _entered(mtm_sl=3000)
    c.evaluate_minute({"L1": 79, "L2": 82, "L3": 4.2, "L4": 3.9}, {})
    dec = c.evaluate_minute({"L2": 85.0}, {})
    assert dec == ("MTM_SL", ["L1", "L2", "L3", "L4"])  # -650 + -2600 = -3250


def test_expiry_lots_resolution():
    assert resolve_lots(10, None, True) == 10
    assert resolve_lots(10, 0, True) == 10
    assert resolve_lots(10, 20, True) == 20
    assert resolve_lots(10, 20, False) == 10


def test_state_roundtrip_resume():
    c = _entered(mtm_sl=3000, iv_sl_delta_pts=8)
    c.evaluate_minute({"L1": 80, "L2": 82, "L3": 4.2, "L4": 3.9}, {})
    d = c.to_state()
    import json; json.dumps(d)                          # JSON-safe (LD6)
    r = TsgDayCore.from_state(d)
    assert r.state == D_OPEN and r.legs["L1"].entry_price == 78.0
    assert r.legs["L1"].iv_threshold == c.legs["L1"].iv_threshold
    dec = r.evaluate_minute({"L1": 81.5, "L2": 83.0, "L3": 4.2, "L4": 3.9}, {})
    assert dec == ("MTM_SL", ["L1", "L2", "L3", "L4"])


def test_kill_and_full_close():
    c = _entered(mtm_sl=0)
    assert set(c.kill_exit_ids()) == {"L1", "L2", "L3", "L4"}
    for i in list(c.open_ids()):
        c.leg_exited(i, 50.0, "KILL")
    assert c.state == D_CLOSED and c.open_ids() == []


if __name__ == "__main__":
    ran = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) :
            fn(); ran += 1; print(f"  ok  {name}")
    print(f"{ran} live-core tests passed")
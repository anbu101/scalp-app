# backend/app/backtest/tsg/test_tsg_runner.py
#
# ── TSG_V1 ── regression tests for the PURE decision core (leg_mtm,
# simulate_tsg_day, norm_tsg_leg). No DB, no corpus — the runner plumbing
# is exercised end-to-end by a real run; the invariants that must never
# drift live here. Covers MTM target/SL and the per-leg IV SL (IV1–IV8).
#
# Run: python3 backend/app/backtest/tsg/test_tsg_runner.py

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ic"))
sys.path.insert(0, os.path.dirname(__file__))

from backtest_tsg_runner import (  # noqa: E402
    leg_mtm, simulate_tsg_day, norm_tsg_leg, DEFAULT_TSG_LEGS,
)

HEDGES = {"L1": "L3", "L2": "L4"}


def _legs():
    return [
        {"id": "L1", "action": "SELL", "entry_price": 85.0, "qty": 65},
        {"id": "L2", "action": "SELL", "entry_price": 85.0, "qty": 65},
        {"id": "L3", "action": "BUY", "entry_price": 5.0, "qty": 65},
        {"id": "L4", "action": "BUY", "entry_price": 5.0, "qty": 65},
    ]


def _flat_marks(minutes, px=None):
    px = px or {"L1": 85.0, "L2": 85.0, "L3": 5.0, "L4": 5.0}
    return {m: dict(px) for m in minutes}


def _flat_iv(minutes, iv=None):
    iv = iv or {"L1": 0.12, "L2": 0.12}
    return {m: dict(iv) for m in minutes}


def test_leg_mtm_signs():
    assert leg_mtm("SELL", 85.0, 80.0, 65) == 5.0 * 65
    assert leg_mtm("SELL", 85.0, 90.0, 65) == -5.0 * 65
    assert leg_mtm("BUY", 5.0, 8.0, 65) == 3.0 * 65
    assert leg_mtm("BUY", 5.0, 2.0, 65) == -3.0 * 65


def test_eod_exit_when_nothing_triggers():
    minutes = [100, 160, 220]
    res = simulate_tsg_day(_legs(), minutes, _flat_marks(minutes), 5000.0)
    assert res["day_exit_reason"] == "EOD"
    assert all(e["reason"] == "EOD" and e["ts"] == 220
               for e in res["exits"].values())
    assert res["mtm_final"] == 0.0


def test_mtm_target_exit_first_crossing_minute():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 45.0, "L2": 45.0, "L3": 5.0, "L4": 5.0}  # +5200
    marks[220] = {"L1": 1.0, "L2": 1.0, "L3": 5.0, "L4": 5.0}
    res = simulate_tsg_day(_legs(), minutes, marks, 5000.0)
    assert res["day_exit_reason"] == "MTM_TARGET"
    assert all(e["reason"] == "MTM_TARGET" and e["ts"] == 160
               for e in res["exits"].values())
    assert res["mtm_final"] == 5200.0
    assert res["exits"]["L1"]["price"] == 45.0


def test_mtm_sl_exit_and_precedence_over_iv():
    # candle 160 satisfies BOTH the MTM SL and an IV cross → MTM SL wins (IV5)
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 110.0, "L2": 110.0, "L3": 5.0, "L4": 5.0}  # -3250
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.55, "L2": 0.55}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 3000.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["day_exit_reason"] == "MTM_SL"
    assert all(e["reason"] == "MTM_SL" for e in res["exits"].values())
    assert res["mtm_final"] == -3250.0


def test_iv_sl_closes_pair_only_and_survivors_reach_eod():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 88.0, "L2": 85.0, "L3": 5.0, "L4": 5.0}  # L1 in loss
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.41, "L2": 0.12}          # L1 crosses at 160
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L1"] == {"ts": 160, "reason": "IV_SL", "price": 88.0}
    assert res["exits"]["L3"]["reason"] == "IV_SL_HEDGE"
    assert res["exits"]["L3"]["ts"] == 160
    assert res["exits"]["L2"]["reason"] == "EOD" and res["exits"]["L2"]["ts"] == 220
    assert res["exits"]["L4"]["reason"] == "EOD"
    assert res["day_exit_reason"] == "EOD"      # survivors closed the day


def test_iv_one_shot_disarms_for_other_leg():
    # L1 crosses at 160; L2 crosses LATER at 220 — must be ignored (IV4)
    minutes = [100, 160, 220, 280]
    marks = _flat_marks(minutes)
    for m in (160, 220, 280):
        marks[m] = {"L1": 90.0, "L2": 90.0, "L3": 5.0, "L4": 5.0}  # both in loss
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.45, "L2": 0.12}
    iv[220] = {"L2": 0.60}
    iv[280] = {"L2": 0.60}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L2"]["reason"] == "EOD"
    assert res["exits"]["L2"]["ts"] == 280


def test_iv_both_cross_same_candle_closes_everything():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 90.0, "L2": 90.0, "L3": 5.0, "L4": 5.0}  # both in loss
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.42, "L2": 0.44}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert all(e["ts"] == 160 for e in res["exits"].values())
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L2"]["reason"] == "IV_SL"
    assert res["exits"]["L3"]["reason"] == "IV_SL_HEDGE"
    assert res["day_exit_reason"] == "IV_SL"


def test_day_mtm_includes_realized_after_partial_iv_exit():
    # L1+L3 IV-exit at 160 with L1 mark 95 → realized = -650 - 0 = -650.
    # At 220 the survivor pair gains: L2 45 (+2600), L4 5 (0) → unreal 2600.
    # Day MTM = 1950 < target 2000 → no exit. At 280 L2 40 (+2925):
    # day MTM = 2275 ≥ 2000 → survivors exit MTM_TARGET at 280.
    minutes = [100, 160, 220, 280]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 95.0, "L2": 85.0, "L3": 5.0, "L4": 5.0}
    marks[220] = {"L1": 999.0, "L2": 45.0, "L3": 999.0, "L4": 5.0}
    marks[280] = {"L1": 999.0, "L2": 40.0, "L3": 999.0, "L4": 5.0}
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.50, "L2": 0.12}
    res = simulate_tsg_day(_legs(), minutes, marks, 2000.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L1"]["price"] == 95.0
    assert res["exits"]["L2"]["reason"] == "MTM_TARGET"
    assert res["exits"]["L2"]["ts"] == 280
    assert res["day_exit_reason"] == "MTM_TARGET"
    assert res["mtm_final"] == -650.0 + 2925.0


def test_iv_solver_gap_skips_check_that_minute():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[220] = {"L1": 86.0, "L2": 85.0, "L3": 5.0, "L4": 5.0}  # L1 in loss
    iv = {100: {}, 160: {}, 220: {"L1": 0.45}}   # solves only at 220
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L1"]["ts"] == 220 and res["exits"]["L1"]["reason"] == "IV_SL"


def test_iv_disabled_by_default():
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    iv = _flat_iv(minutes, {"L1": 0.99, "L2": 0.99})
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_by_minute=iv, hedge_map=HEDGES)   # pct absent
    assert all(e["reason"] == "EOD" for e in res["exits"].values())


def test_absent_hedge_short_exits_alone():
    legs = [
        {"id": "L1", "action": "SELL", "entry_price": 85.0, "qty": 65},
        {"id": "L2", "action": "SELL", "entry_price": 85.0, "qty": 65},
        {"id": "L4", "action": "BUY", "entry_price": 5.0, "qty": 65},
    ]
    minutes = [100, 160]
    marks = {m: {"L1": 87.0, "L2": 85.0, "L4": 5.0} for m in minutes}
    iv = {100: {"L1": 0.12}, 160: {"L1": 0.45}}
    res = simulate_tsg_day(legs, minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv,
                           hedge_map={"L2": "L4"})     # L1 has NO hedge
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L2"]["reason"] == "EOD"
    assert res["exits"]["L4"]["reason"] == "EOD"


def test_mtm_sl_still_first_crossing():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 110.0, "L2": 110.0, "L3": 5.0, "L4": 5.0}
    marks[220] = {"L1": 200.0, "L2": 200.0, "L3": 5.0, "L4": 5.0}
    res = simulate_tsg_day(_legs(), minutes, marks, 5000.0, 3000.0)
    assert res["day_exit_reason"] == "MTM_SL"
    assert all(e["ts"] == 160 for e in res["exits"].values())
    assert res["mtm_final"] == -3250.0


def test_target_disabled_means_pure_eod():
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[100] = {"L1": 1.0, "L2": 1.0, "L3": 5.0, "L4": 5.0}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0)
    assert res["day_exit_reason"] == "EOD"


def test_peak_trough_tracked():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[100] = {"L1": 95.0, "L2": 95.0, "L3": 5.0, "L4": 5.0}  # -1300
    marks[160] = {"L1": 70.0, "L2": 70.0, "L3": 5.0, "L4": 5.0}  # +1950
    res = simulate_tsg_day(_legs(), minutes, marks, 999999.0)
    assert res["trough_mtm"] == -1300.0
    assert res["peak_mtm"] == 1950.0


def test_norm_leg_and_defaults():
    legs = [norm_tsg_leg(l) for l in DEFAULT_TSG_LEGS]
    assert [l["id"] for l in legs] == ["L1", "L2", "L3", "L4"]
    assert [l["action"] for l in legs] == ["SELL", "SELL", "BUY", "BUY"]
    assert legs[0]["premium_max"] == 85 and legs[2]["premium_max"] == 5


def test_iv9_winner_is_never_iv_closed():
    # Incident mirror (2026-08-01): crash day, whole strike's vol explodes.
    # L1 deep in PROFIT (mark 5 << entry 85) with iv 0.60 — must NOT close.
    # L2 in LOSS (mark 160) with the same vol — closes with its hedge.
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 5.0, "L2": 160.0, "L3": 1.0, "L4": 30.0}
    marks[220] = {"L1": 1.0, "L2": 200.0, "L3": 1.0, "L4": 40.0}
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.60, "L2": 0.60}
    iv[220] = {"L1": 0.70, "L2": 0.70}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L2"]["reason"] == "IV_SL" and res["exits"]["L2"]["ts"] == 160
    assert res["exits"]["L4"]["reason"] == "IV_SL_HEDGE"
    assert res["exits"]["L1"]["reason"] == "EOD"        # winner protected
    assert res["exits"]["L3"]["reason"] == "EOD"


def test_iv9_at_entry_price_does_not_trigger():
    # mark == entry is not "in loss" — strictly greater required
    minutes = [100, 160]
    marks = _flat_marks(minutes)                         # marks == entries
    iv = _flat_iv(minutes, {"L1": 0.99, "L2": 0.99})
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert all(e["reason"] == "EOD" for e in res["exits"].values())


if __name__ == "__main__":
    g = dict(globals())
    ran = 0
    for name, fn in g.items():
        if name.startswith("test_") and callable(fn):
            fn()
            ran += 1
            print(f"  ok  {name}")
    print(f"{ran} tests passed")

# appended: IV11 relative-threshold tests (run via __main__ walker only if
# defined before it — so invoke directly here for the appended pair)
def test_iv11_per_leg_thresholds():
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 90.0, "L2": 90.0, "L3": 5.0, "L4": 5.0}
    iv = _flat_iv(minutes)
    iv[160] = {"L1": 0.21, "L2": 0.45}
    # L1 anchored low (0.12+0.08=0.20 → 0.21 crosses); L2 anchored high
    # (0.42+0.08=0.50 → 0.45 does NOT cross despite being the bigger vol)
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_by_minute=iv, hedge_map=HEDGES,
                           iv_thresholds={"L1": 0.20, "L2": 0.50})
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L2"]["reason"] == "EOD"


def test_iv11_missing_anchor_means_unmonitored():
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 90.0, "L2": 90.0, "L3": 5.0, "L4": 5.0}
    iv = _flat_iv(minutes, {"L1": 0.99, "L2": 0.99})
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_by_minute=iv, hedge_map=HEDGES,
                           iv_thresholds={"L2": 0.30})   # L1 has no anchor
    assert res["exits"]["L1"]["reason"] == "EOD"          # unmonitored
    assert res["exits"]["L2"]["reason"] == "IV_SL"

test_iv11_per_leg_thresholds(); test_iv11_missing_anchor_means_unmonitored()
print("  ok  test_iv11_per_leg_thresholds\n  ok  test_iv11_missing_anchor_means_unmonitored (appended)")

# appended: TL1-TL6 trailing-lock tests (invoked directly)
def test_trail_arms_and_locks_on_giveback():
    minutes = [100, 160, 220, 280]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 65.0, "L2": 65.0, "L3": 5.0, "L4": 5.0}  # +2600 → arms (2000)
    marks[220] = {"L1": 72.0, "L2": 72.0, "L3": 5.0, "L4": 5.0}  # +1690 → gb 910 > 800
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           mtm_trail_arm=2000.0, mtm_trail_giveback=800.0)
    assert res["day_exit_reason"] == "MTM_TRAIL"
    assert all(e["ts"] == 220 for e in res["exits"].values())
    assert res["mtm_final"] == 1690.0 and res["trail_armed"]


def test_trail_not_armed_never_fires():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[100] = {"L1": 70.0, "L2": 70.0, "L3": 5.0, "L4": 5.0}  # +1950 < arm
    marks[160] = {"L1": 85.0, "L2": 85.0, "L3": 5.0, "L4": 5.0}  # giveback 1950
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           mtm_trail_arm=2000.0, mtm_trail_giveback=800.0)
    assert res["day_exit_reason"] == "EOD" and not res["trail_armed"]


def test_trail_peak_ratchets_after_arming():
    minutes = [100, 160, 220, 280]
    marks = _flat_marks(minutes)
    marks[100] = {"L1": 65.0, "L2": 65.0, "L3": 5.0, "L4": 5.0}  # +2600 arm, peak 2600
    marks[160] = {"L1": 55.0, "L2": 55.0, "L3": 5.0, "L4": 5.0}  # +3900 new peak
    marks[220] = {"L1": 60.0, "L2": 60.0, "L3": 5.0, "L4": 5.0}  # +3250, gb 650 < 800
    marks[280] = {"L1": 62.0, "L2": 62.0, "L3": 5.0, "L4": 5.0}  # +2990, gb 910 ≥ 800
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           mtm_trail_arm=2000.0, mtm_trail_giveback=800.0)
    assert res["day_exit_reason"] == "MTM_TRAIL"
    assert res["exits"]["L1"]["ts"] == 280 and res["mtm_final"] == 2990.0


def test_trail_composes_with_partial_iv_exit():
    # IV cuts L1+L3 at 160 (realized -650); survivors rally the day MTM to
    # +2600 (arms) then give back — trail must fire on TOTAL day MTM.
    minutes = [100, 160, 220, 280]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 95.0, "L2": 88.0, "L3": 5.0, "L4": 5.0}
    marks[220] = {"L1": 999.0, "L2": 35.0, "L3": 999.0, "L4": 5.0}  # -650+3250=2600 arm
    marks[280] = {"L1": 999.0, "L2": 48.0, "L3": 999.0, "L4": 5.0}  # 2600-845 → gb 845
    iv = _flat_iv(minutes); iv[160] = {"L1": 0.50, "L2": 0.12}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES,
                           mtm_trail_arm=2000.0, mtm_trail_giveback=800.0)
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L2"]["reason"] == "MTM_TRAIL"
    assert res["exits"]["L2"]["ts"] == 280
    assert res["day_exit_reason"] == "MTM_TRAIL"


def test_trail_precedence_sl_first():
    # crash from armed peak straight through the SL: SL reason wins
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[100] = {"L1": 60.0, "L2": 60.0, "L3": 5.0, "L4": 5.0}   # +3250 armed
    marks[160] = {"L1": 115.0, "L2": 115.0, "L3": 5.0, "L4": 5.0} # -3900 ≤ -3000
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 3000.0,
                           mtm_trail_arm=2000.0, mtm_trail_giveback=800.0)
    assert res["day_exit_reason"] == "MTM_SL"

test_trail_arms_and_locks_on_giveback(); test_trail_not_armed_never_fires()
test_trail_peak_ratchets_after_arming(); test_trail_composes_with_partial_iv_exit()
test_trail_precedence_sl_first()
print("  ok  5 trailing-lock tests (appended)")

# appended: IV12 keep-hedge tests
def test_iv12_keep_hedge_short_only_exit():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 95.0, "L2": 88.0, "L3": 5.0, "L4": 5.0}
    iv = _flat_iv(minutes); iv[160] = {"L1": 0.50, "L2": 0.12}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES,
                           iv_keep_hedge=True)
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L1"]["ts"] == 160
    assert res["exits"]["L3"]["reason"] == "EOD"          # wing rode to close
    assert not any(e["reason"] == "IV_SL_HEDGE" for e in res["exits"].values())
    assert res["day_exit_reason"] == "EOD"


def test_iv12_kept_hedge_participates_in_mtm_sl():
    minutes = [100, 160, 220]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 95.0, "L2": 88.0, "L3": 5.0, "L4": 5.0}
    # short-only IV exit realizes (85-95)*65 = -650; at 220 L2 slips to
    # 92 → -455 unrealized; total -1105 ≤ -1000 → basket MTM_SL including
    # the KEPT wing (proves it stayed in the day-MTM composition)
    marks[220] = {"L2": 92.0, "L3": 5.0, "L4": 5.0}
    iv = _flat_iv(minutes); iv[160] = {"L1": 0.50, "L2": 0.12}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 1000.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES,
                           iv_keep_hedge=True)
    assert res["exits"]["L1"]["reason"] == "IV_SL"
    assert res["exits"]["L3"]["reason"] == "MTM_SL"       # kept wing in basket
    assert res["exits"]["L2"]["reason"] == "MTM_SL"
    assert res["day_exit_reason"] == "MTM_SL"


def test_iv12_default_false_preserves_pair_exit():
    minutes = [100, 160]
    marks = _flat_marks(minutes)
    marks[160] = {"L1": 95.0, "L2": 88.0, "L3": 5.0, "L4": 5.0}
    iv = _flat_iv(minutes); iv[160] = {"L1": 0.50, "L2": 0.12}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 0.0,
                           iv_sl_pct=40.0, iv_by_minute=iv, hedge_map=HEDGES)
    assert res["exits"]["L3"]["reason"] == "IV_SL_HEDGE"  # unchanged default

test_iv12_keep_hedge_short_only_exit()
test_iv12_kept_hedge_participates_in_mtm_sl()
test_iv12_default_false_preserves_pair_exit()
print("  ok  3 IV12 keep-hedge tests (appended)")

# ── TSG_BANK_20260911 ── profit bank + re-entry ───────────────────────
def _bank_marks(minutes):
    # L1 decays 85→70 by m=160 (+975), then bounces to 80 by 220 (gives back)
    px = {100: 85.0, 130: 80.0, 160: 70.0, 190: 72.0, 220: 80.0, 250: 80.0}
    return {m: {"L1": px[m], "L2": 85.0, "L3": 5.0, "L4": 5.0} for m in minutes}

def test_bank_same_closes_shorts_and_reenters_next_minute():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           bank_mode="SAME")
    assert res["banks"] == 1
    assert res["exits"]["L1"]["reason"] == "MTM_BANK" and res["exits"]["L1"]["ts"] == 160
    assert res["exits"]["L2"]["reason"] == "MTM_BANK"
    assert "L3" not in [k for k, e in res["exits"].items() if e["reason"] == "MTM_BANK"]
    assert set(res["reentries"]) == {"L1#1", "L2#1"}
    assert res["reentries"]["L1#1"]["ts"] == 190 and res["reentries"]["L1#1"]["entry_price"] == 72.0
    # re-entered L1#1 runs to EOD at 80 → −520 ; banked +975 ; total = +455
    assert res["exits"]["L1#1"]["reason"] == "EOD"
    assert abs(res["mtm_final"] - (975.0 - 520.0)) < 1e-6

def test_bank_off_is_byte_identical_to_before():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES))
    assert res["banks"] == 0 and res["reentries"] == {}
    assert all(e["reason"] == "EOD" for e in res["exits"].values())
    assert abs(res["mtm_final"] - 325.0) < 1e-6          # 85→80 on L1 only

def test_bank_position_sl_protects_the_bank():
    minutes = [100, 130, 160, 190, 220, 250]
    marks = _bank_marks(minutes); marks[220]["L1"] = 90.0; marks[250]["L1"] = 90.0
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 1000.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           mtm_sl_basis="POSITION")
    # after re-entry at 72, L1#1 at 90 = −1170 on the OPEN legs → POSITION SL fires
    assert res["day_exit_reason"] == "MTM_SL"
    assert res["exits"]["L1#1"]["reason"] == "MTM_SL"

def test_bank_cutoff_and_eod_minute_block_reentry():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           mtm_bank_target=900.0, bank_reentry_cutoff_ts=150)   # ── TSG_BANK_FIX1_20260911 ──
    assert res["banks"] == 0                              # re-entry would be 190 > 150
    res2 = simulate_tsg_day(_legs(), [100, 160, 250], {100: _bank_marks([100])[100],
                            160: _bank_marks([160])[160], 250: _bank_marks([250])[250]},
                            0.0, mtm_bank_target=900.0)
    assert res2["banks"] == 0                             # next minute IS the EOD minute

def test_bank_max_per_day_and_rebase():
    minutes = list(range(100, 100 + 30 * 8, 30))
    px = [85, 70, 70, 55, 55, 40, 40, 40]                 # two ₹975 steps then a third
    marks = {m: {"L1": float(p), "L2": 85.0, "L3": 5.0, "L4": 5.0} for m, p in zip(minutes, px)}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0,
                           mtm_bank_target=900.0, bank_max_per_day=2)
    assert res["banks"] == 2
    assert "L1#2" in res["reentries"] and "L1#3" not in res["reentries"]
    assert res["exits"]["L1#2"]["reason"] == "EOD"

def test_bank_restrike_uses_callback_and_fails_closed():
    minutes = [100, 130, 160, 190, 220, 250]
    calls = []
    def rs(m, opt_type, cap):
        calls.append((m, opt_type, cap))
        if opt_type == "PE":
            return None                                   # nothing ≤ cap → not re-entered
        return {"symbol": "NEWCE", "entry_price": 110.0,
                "marks": {190: 110.0, 220: 100.0, 250: 95.0}}
    legs = _legs()
    for l in legs:
        l["opt_type"] = "CE" if l["id"] in ("L1", "L3") else "PE"; l["premium_max"] = 120.0
    res = simulate_tsg_day(legs, minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           bank_mode="RESTRIKE", restrike_fn=rs)
    assert [c[1] for c in calls] == ["CE", "PE"] and calls[0][0] == 190
    assert set(res["reentries"]) == {"L1#1"} and res["bank_reentry_fail"] == 1
    assert res["reentries"]["L1#1"]["symbol"] == "NEWCE"
    assert res["exits"]["L1#1"]["price"] == 95.0          # marks injected for the new leg
    assert abs(res["mtm_final"] - (975.0 + 15.0 * 65)) < 1e-6

def test_bank_reentry_minute_reenters_first_then_sl_sees_fresh_runway():
    minutes = [100, 130, 160, 190, 220, 250]
    marks = _bank_marks(minutes)
    marks[190]["L2"] = 140.0                              # L2 gaps on the re-entry minute
    marks[220]["L2"] = 160.0; marks[250]["L2"] = 160.0    # …and keeps going
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 1000.0,
                           mtm_bank_target=900.0, mtm_sl_basis="POSITION")
    assert res["banks"] == 1
    assert res["reentries"]["L2#1"]["entry_price"] == 140.0   # re-entered at the gapped mark
    assert res["exits"]["L2#1"]["reason"] == "MTM_SL" and res["exits"]["L2#1"]["ts"] == 220
    # the banked ₹975 on L1 + the 85→140 move on the ORIGINAL L2 never hit the book:
    # L2 was closed at 85 (MTM_BANK) before the gap.
    assert res["exits"]["L2"]["price"] == 85.0

test_bank_same_closes_shorts_and_reenters_next_minute()
test_bank_off_is_byte_identical_to_before()
test_bank_position_sl_protects_the_bank()
test_bank_cutoff_and_eod_minute_block_reentry()
test_bank_max_per_day_and_rebase()
test_bank_restrike_uses_callback_and_fails_closed()
test_bank_reentry_minute_reenters_first_then_sl_sees_fresh_runway()
print("  ok  7 TSG_BANK_20260911 tests (appended)")


# ── TSG_BANK_FIX1_20260911 ── the runner's real clock: epoch minutes ──
def test_bank_fires_on_epoch_minutes_with_day_clock_cutoff():
    day_start = 1_788_000_000 - (1_788_000_000 % 86400)     # some midnight
    entry_ts = day_start + (9 * 60 + 16) * 60
    eod_ts = day_start + (15 * 60 + 26) * 60
    minutes = list(range(entry_ts + 60, eod_ts + 1, 60))
    marks = {}
    for k, m in enumerate(minutes):
        l1 = 85.0 - min(k, 20) * 1.0                          # decays 85→65 over 20 min (+1300)
        marks[m] = {"L1": l1, "L2": 85.0, "L3": 5.0, "L4": 5.0}
    cutoff_ts = day_start + (14 * 60 + 30) * 60
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, hedge_map=dict(HEDGES),
                           mtm_bank_target=1000.0, bank_reentry_cutoff_ts=cutoff_ts)
    assert res["banks"] == 1, res["banks"]
    assert res["exits"]["L1"]["reason"] == "MTM_BANK"
    assert res["reentries"]["L1#1"]["ts"] == res["exits"]["L1"]["ts"] + 60
    # and a cutoff BEFORE the bank minute blocks it (still on the epoch clock)
    res2 = simulate_tsg_day(_legs(), minutes, marks, 0.0, hedge_map=dict(HEDGES),
                            mtm_bank_target=1000.0, bank_reentry_cutoff_ts=entry_ts + 5 * 60)
    assert res2["banks"] == 0

test_bank_fires_on_epoch_minutes_with_day_clock_cutoff()
print("  ok  1 TSG_BANK_FIX1_20260911 epoch-clock test (appended)")

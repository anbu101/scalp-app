# backend/app/engine/ic_v1/test_ic_live_core.py
#
# Pinned scenarios for the IC_V1 live core. LT-numbers are LIVE tests;
# where a backtest T-scenario has a live analogue it is noted.
import pytest

from ic_live_core import (
    GroupCore, LegCore, StrikePick,
    sl_price, tp_price, select_strike, per_order_cap, slice_qty,
    G_IDLE, G_ENTERING, G_OPEN, G_CLOSING, G_CLOSED, G_ABORTED,
    L_PENDING, L_OPEN, L_CLOSED, L_DEAD,
    MTC_REPIN, MTC_MARKET_OUT,
)


def make_group(*, with_wings=True, qty=1560):
    legs = {
        "L1": LegCore("L1", "SELL", "CE", symbol="N24150CE", qty=qty,
                      entry_price=84.15, sl=119.49, mtc_partner="L2"),
        "L2": LegCore("L2", "SELL", "PE", symbol="N23700PE", qty=qty,
                      entry_price=51.65, sl=73.35, mtc_partner="L1"),
    }
    if with_wings:
        legs["L3"] = LegCore("L3", "BUY", "CE", symbol="N24700CE", qty=qty, entry_price=3.8)
        legs["L4"] = LegCore("L4", "BUY", "PE", symbol="N23200PE", qty=qty, entry_price=3.5)
    return GroupCore(legs=legs)


def open_all(g):
    g.begin_entry()
    for lid in g.legs:
        g.leg_filled(lid)
    assert g.state == G_OPEN


# ── Price math parity (backtest §3 formulas) ────────────────────────────────

def test_price_math_parity():
    assert sl_price("SELL", 84.15, 42, "pct") == pytest.approx(84.15 * 1.42)
    assert tp_price("SELL", 100, 42, "pct") == pytest.approx(58.0)
    assert sl_price("SELL", 100, 10, "pts") == 110
    assert tp_price("SELL", 100, 10, "pts") == 90
    assert sl_price("BUY", 3.0, 99, "pct") == pytest.approx(max(0.05, 3.0 * 0.01))
    assert tp_price("BUY", 3.0, 100, "pct") == pytest.approx(6.0)
    # floors
    assert tp_price("SELL", 1.0, 200, "pts") == 0.05
    # disabled
    assert sl_price("SELL", 100, 0, "pct") is None
    assert tp_price("BUY", 100, None, "pct") is None


# ── Strike selection parity ────────────────────────────────────────────────

CHAIN = [(24100, "A", 92.0), (24150, "B", 84.15), (24200, "C", 71.0),
         (24700, "D", 3.8), (24750, "E", 2.1)]

def test_short_pick_nearest_below_cap():
    p = select_strike(CHAIN, 85, fallback_cheapest=False)
    assert p.symbol == "B" and p.ltp == 84.15 and not p.fallback

def test_short_fail_closed():
    assert select_strike([(24100, "A", 92.0)], 85, fallback_cheapest=False) is None

def test_wing_fallback_cheapest():
    p = select_strike([(24100, "A", 40.0), (24150, "B", 31.0)], 4, fallback_cheapest=True)
    assert p.symbol == "B" and p.fallback

def test_wing_no_candidates():
    assert select_strike([], 4, fallback_cheapest=True) is None

def test_zero_ltp_excluded():
    assert select_strike([(24100, "A", 0.0)], 85, fallback_cheapest=True) is None


# ── D3: freeze slicing (lots & lot_size are runtime values) ────────────────

def test_per_order_cap_current_regime():
    # NIFTY freeze 1800, lot 65 → 1755 (27 lots), per NSE methodology
    assert per_order_cap(1800, 65) == 1755

def test_slice_default_24_lots_single_order():
    assert slice_qty(24 * 65, 1800, 65) == [1560]

def test_slice_30_lots_two_orders():
    # 30 lots = 1950 > 1755 → [1755, 195]
    assert slice_qty(30 * 65, 1800, 65) == [1755, 195]

def test_slice_lot_size_change_survives():
    # lot size revision to 75: cap = 1800//75*75 = 1800
    assert slice_qty(24 * 75, 1800, 75) == [1800]

def test_slice_rejects_non_lot_multiple():
    with pytest.raises(ValueError):
        slice_qty(1000, 1800, 65)

def test_slice_rejects_bad_config():
    with pytest.raises(ValueError):
        slice_qty(65, 0, 65)


# ── LT1: quiet day — EOD closes everything, plain EOD reasons ──────────────

def test_lt1_quiet_day_eod():
    g = make_group(); open_all(g)
    g.state = G_CLOSING
    for lid, px in [("L1", 40.4), ("L2", 22.0), ("L3", 0.6), ("L4", 0.4)]:
        g.close_leg(lid, px, "EOD")
    g.finalize_if_done()
    assert g.state == G_CLOSED
    assert all(l.exit_reason == "EOD" for l in g.legs.values())
    assert g.legs["L1"].pnl() == pytest.approx((84.15 - 40.4) * 1560)


# ── LT2: SL → MTC repin → cost scratch (backtest T2 analogue) ──────────────

def test_lt2_sl_mtc_cost_scratch():
    g = make_group(); open_all(g)
    act = g.on_short_sl_filled("L1", 119.49, partner_ltp=40.0, ts=1000)
    assert act == {"action": MTC_REPIN, "partner": "L2", "cost_stop": 51.65}
    g.confirm_repin("L2")
    assert g.legs["L2"].sl == 51.65 and g.legs["L2"].mtc_repinned
    # later the cost stop fills → MTC_COST, not SL
    g.close_leg("L2", 51.65, "SL")
    assert g.legs["L2"].exit_reason == "MTC_COST"


# ── LT3: MTC survivor rides to EOD → EOD_MTC (verified 01/07 example) ───────

def test_lt3_eod_mtc_tagging():
    g = make_group(); open_all(g)
    act = g.on_short_sl_filled("L1", 119.49, partner_ltp=45.0, ts=1000)
    g.confirm_repin(act["partner"])
    g.close_leg("L2", 40.40, "EOD")
    assert g.legs["L2"].exit_reason == "EOD_MTC"
    assert g.legs["L2"].pnl() == pytest.approx((51.65 - 40.40) * 1560)


# ── LT4: D5 fallback — partner already at/through cost → MARKET_OUT ─────────

def test_lt4_partner_through_cost_market_out():
    g = make_group(); open_all(g)
    act = g.on_short_sl_filled("L1", 119.49, partner_ltp=51.65, ts=1000)
    assert act == {"action": MTC_MARKET_OUT, "partner": "L2"}

def test_lt4b_partner_ltp_unknown_market_out():
    g = make_group(); open_all(g)
    act = g.on_short_sl_filled("L1", 119.49, partner_ltp=None, ts=1000)
    assert act["action"] == MTC_MARKET_OUT

def test_lt4c_repin_rejected_market_out():
    g = make_group(); open_all(g)
    act = g.on_short_sl_filled("L1", 119.49, partner_ltp=30.0, ts=1000)
    assert act["action"] == MTC_REPIN
    fb = g.repin_failed("L2")
    assert fb == {"action": MTC_MARKET_OUT, "partner": "L2"}


# ── LT5: MTC one-shot + double-SL-by-fill-order (backtest T4 analogue) ──────

def test_lt5_double_sl_fill_order():
    g = make_group(); open_all(g)
    a1 = g.on_short_sl_filled("L1", 119.49, partner_ltp=40.0, ts=1000)
    assert a1["action"] == MTC_REPIN
    # partner spikes and blows through its ORIGINAL SL before the repin lands;
    # its SL fill confirms — no second MTC, and double-SL minute flagged
    a2 = g.on_short_sl_filled("L2", 73.35, partner_ltp=None, ts=1030)
    assert a2 is None
    assert g.double_sl_minute is True
    assert g.legs["L2"].exit_reason == "SL"

def test_lt5b_sl_fills_far_apart_not_flagged():
    g = make_group(); open_all(g)
    g.on_short_sl_filled("L1", 119.49, partner_ltp=40.0, ts=1000)
    g.on_short_sl_filled("L2", 73.35, partner_ltp=None, ts=2000)
    assert g.double_sl_minute is False


# ── LT6: wings never participate in MTC ─────────────────────────────────────

def test_lt6_wing_sl_never_triggers_mtc():
    g = make_group(); open_all(g)
    assert g.on_short_sl_filled("L3", 1.0, partner_ltp=40.0, ts=1000) is None
    assert g.legs["L3"].state == L_OPEN   # untouched


# ── LT7: D6 entry-failure unwind, shorts first ──────────────────────────────

def test_lt7_short_entry_dead_unwinds_all_filled():
    g = make_group(); g.begin_entry()
    for lid in ("L3", "L4", "L1"):     # wings + one short filled (D2 order)
        g.leg_filled(lid)
    unwind = g.leg_entry_dead("L2")
    assert g.state == G_ABORTED
    assert unwind == ["L1", "L3", "L4"]    # short risk dies first
    for lid in unwind:
        g.record_unwind(lid, exit_price=1.0)
    assert all(g.legs[l].exit_reason == "UNWIND" for l in unwind)
    assert g.legs["L2"].state == L_DEAD

def test_lt7b_wing_order_failure_also_unwinds():
    g = make_group(); g.begin_entry()
    g.leg_filled("L3")
    unwind = g.leg_entry_dead("L4")
    assert g.state == G_ABORTED and unwind == ["L3"]


# ── LT8: close_leg idempotence + no MTC after group aborted ─────────────────

def test_lt8_double_close_ignored():
    g = make_group(); open_all(g)
    g.close_leg("L1", 100.0, "SL")
    g.close_leg("L1", 90.0, "EOD")
    assert g.legs["L1"].exit_price == 100.0
    assert g.legs["L1"].exit_reason == "SL"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
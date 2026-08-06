"""── IC_IV_SL ── pure-engine semantics check (D2/D4/D5/D6/D8).
Run standalone: python3 test_ic_iv_sl.py
"""
from ic_v1_engine import simulate_session, norm_leg, sl_price

T0 = 1000
def C(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}

def legs(sl_mode="iv", sl_val=30, tp_val=0):
    return [
        norm_leg({"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 1,
                  "premium_max": 85, "sl_val": sl_val, "sl_mode": sl_mode,
                  "tp_val": tp_val, "tp_mode": "pct",
                  "mtc_other_on_sl": True, "mtc_partner": "L2"}),
        norm_leg({"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 1,
                  "premium_max": 85, "sl_val": sl_val, "sl_mode": sl_mode,
                  "tp_val": tp_val, "tp_mode": "pct",
                  "mtc_other_on_sl": True, "mtc_partner": "L1"}),
    ]

SYMS = {"L1": "CE1", "L2": "PE1"}
ok = 0

def check(name, cond):
    global ok
    assert cond, "FAIL: " + name
    ok += 1
    print("  ok  " + name)

# ── D2: an IV mode returns no premium stop, on every builder path ──
check("sl_price('iv') is None", sl_price("SELL", 85, 30, "iv") is None)
check("sl_price('iv_delta') is None", sl_price("SELL", 85, 8, "iv_delta") is None)
check("sl_price('pct') unchanged", abs(sl_price("SELL", 100, 42, "pct") - 142) < 1e-9)

# ── D2: premium runs away 3x and NO SL fires in iv mode ──
cds = {"L1": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 300, 85, 290)],
       "L2": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 85, 80, 82)]}
r = simulate_session(legs(), cds, dict(SYMS), T0+60, T0+180)
l1 = [t for t in r["trades"] if t["leg"] == "L1"][0]
check("iv mode: 3x premium move does NOT fire a price SL",
      l1["exit_reason"] == "EOD" and l1["sl_price"] is None)

# ── D8 + D4: IV_SL fires at the CLOSE on a LOSING short ──
r = simulate_session(legs(), cds, dict(SYMS), T0+60, T0+180,
                     iv_by_minute={T0+60: {"L1": 0.44, "L2": 0.44}},
                     iv_thresholds={"L1": 0.40, "L2": 0.40})
l1 = [t for t in r["trades"] if t["leg"] == "L1"][0]
l2 = [t for t in r["trades"] if t["leg"] == "L2"][0]
check("IV_SL fires on the losing short", l1["exit_reason"] == "IV_SL")
check("IV_SL books AT THE CLOSE (290), not a level", l1["exit_price"] == 290)
check("D4 gate: winning short (82<85) is NOT IV-closed", l2["exit_reason"] == "EOD")
check("engine flag counts the exit", r["flags"]["iv_sl_exits"] == 1)

# ── D6: IV_SL arms MTC on the partner ──
check("IV_SL armed MTC on partner", r["flags"]["mtc_activations"] == 0)  # needs a later candle
cds3 = {"L1": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 300, 85, 290),
               C(T0+120, 290, 290, 290, 290)],
        "L2": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 85, 80, 82),
               C(T0+120, 82, 90, 82, 88)]}
r = simulate_session(legs(), cds3, dict(SYMS), T0+60, T0+240,
                     iv_by_minute={T0+60: {"L1": 0.44}},
                     iv_thresholds={"L1": 0.40, "L2": 0.40})
l2 = [t for t in r["trades"] if t["leg"] == "L2"][0]
check("D6: IV_SL re-pinned partner SL to cost", r["flags"]["mtc_activations"] == 1)
check("D6: partner then exits MTC_COST at 85", l2["exit_reason"] == "MTC_COST"
      and l2["exit_price"] == 85)

# ── D8 ordering: a TP touch outranks an IV_SL on the same candle ──
cds4 = {"L1": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 300, 40, 290)],
        "L2": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 85, 85, 85)]}
r = simulate_session(legs(sl_val=30, tp_val=50), cds4, dict(SYMS),
                     T0+60, T0+180,
                     iv_by_minute={T0+60: {"L1": 0.44}},
                     iv_thresholds={"L1": 0.40})
l1 = [t for t in r["trades"] if t["leg"] == "L1"][0]
check("D8: intrabar TP outranks close-only IV_SL", l1["exit_reason"] == "TP")

# ── D5: NO one-shot latch — both shorts IV_SL on different candles ──
cds5 = {"L1": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 200, 85, 190),
               C(T0+120, 190, 190, 190, 190)],
        "L2": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 90, 85, 88),
               C(T0+120, 88, 150, 88, 140)]}
r = simulate_session(
    [norm_leg({"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 1,
               "premium_max": 85, "sl_val": 30, "sl_mode": "iv"}),
     norm_leg({"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 1,
               "premium_max": 85, "sl_val": 30, "sl_mode": "iv"})],
    cds5, dict(SYMS), T0+60, T0+240,
    iv_by_minute={T0+60: {"L1": 0.44}, T0+120: {"L2": 0.44}},
    iv_thresholds={"L1": 0.40, "L2": 0.40})
reasons = {t["leg"]: t["exit_reason"] for t in r["trades"]}
check("D5: no latch — second short IV_SLs on a later candle",
      reasons["L1"] == "IV_SL" and reasons["L2"] == "IV_SL")

# ── same-candle double IV_SL counts as a double-SL day, MTC never runs ──
cds6 = {"L1": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 200, 85, 190)],
        "L2": [C(T0, 85, 85, 85, 85), C(T0+60, 85, 200, 85, 190)]}
r = simulate_session(legs(), cds6, dict(SYMS), T0+60, T0+180,
                     iv_by_minute={T0+60: {"L1": 0.44, "L2": 0.44}},
                     iv_thresholds={"L1": 0.40, "L2": 0.40})
check("double IV_SL flags double_sl", r["flags"]["double_sl"] is True)
check("double IV_SL never activates MTC", r["flags"]["mtc_activations"] == 0)

# ── fail-open: missing IV at a minute is a skip, never a trigger ──
r = simulate_session(legs(), cds, dict(SYMS), T0+60, T0+180,
                     iv_by_minute={}, iv_thresholds={"L1": 0.40})
l1 = [t for t in r["trades"] if t["leg"] == "L1"][0]
check("unsolved IV is a SKIP, not a trigger", l1["exit_reason"] == "EOD")

# ── no IV inputs at all ⇒ byte-identical legacy behaviour ──
a = simulate_session(legs("pct", 42), cds, dict(SYMS), T0+60, T0+180)
b = simulate_session(legs("pct", 42), cds, dict(SYMS), T0+60, T0+180,
                     iv_by_minute=None, iv_thresholds=None)
check("pct legs unaffected by the new params", a["trades"] == b["trades"])
check("pct SL still fires normally",
      [t for t in a["trades"] if t["leg"] == "L1"][0]["exit_reason"] == "SL")

print("\n%d/%d checks passed" % (ok, ok))
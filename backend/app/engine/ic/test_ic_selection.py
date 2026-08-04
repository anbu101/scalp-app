# backend/app/engine/ic/test_ic_selection.py
#
# Pure-function tests for ic_selection (no kite, no instruments file).
import sys
import types
from datetime import date
import pytest

# Stub the app-package imports so the module loads standalone in CI/dev.
_audit = types.ModuleType("app.event_bus.audit_logger")
_audit.write_audit_log = lambda *a, **k: None
for name, mod in {
    "app": types.ModuleType("app"),
    "app.event_bus": types.ModuleType("app.event_bus"),
    "app.event_bus.audit_logger": _audit,
    "app.engine": types.ModuleType("app.engine"),
    "app.engine.ic": types.ModuleType("app.engine.ic"),
}.items():
    sys.modules.setdefault(name, mod)
import ic_live_core
sys.modules["app.engine.ic.ic_live_core"] = ic_live_core

from ic_selection import build_chain_candidates, select_ic_strikes, ICSelection

EXP = date(2026, 7, 9)

LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 24, "premium_max": 85},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 24, "premium_max": 85},
    {"id": "L3", "action": "BUY",  "opt_type": "CE", "lots": 24, "premium_max": 4},
    {"id": "L4", "action": "BUY",  "opt_type": "PE", "lots": 24, "premium_max": 4},
]

def rows_and_ltps():
    rows, ltps = [], {}
    chain = [
        # strike, CE ltp, PE ltp
        (23200, 950.0, 3.5),
        (23700, 480.0, 51.65),
        (24100, 120.0, 78.0),
        (24150, 84.15, 92.0),
        (24200, 71.0, 130.0),
        (24700, 3.8, 560.0),
        (24750, 2.1, 610.0),
    ]
    tok = 1000
    for strike, ce, pe in chain:
        for t, p in (("CE", ce), ("PE", pe)):
            sym = f"NIFTY26709{strike}{t}"
            rows.append({"tradingsymbol": sym, "instrument_token": tok,
                         "strike": strike, "instrument_type": t})
            ltps[sym] = p
            tok += 1
    return rows, ltps


def test_full_condor_selection():
    rows, ltps = rows_and_ltps()
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    sel = select_ic_strikes(LEGS, ce, pe, tokens, EXP)
    assert sel.ok
    assert sel.picks["L1"].strike == 24150 and sel.picks["L1"].ltp == 84.15
    assert sel.picks["L2"].strike == 24100 and sel.picks["L2"].ltp == 78.0   # highest ≤85
    assert sel.picks["L3"].strike == 24700 and sel.picks["L3"].ltp == 3.8
    assert sel.picks["L4"].strike == 23200 and sel.picks["L4"].ltp == 3.5
    assert not sel.wing_fallback and sel.wing_absent == []
    assert all(sel.tokens[l] > 0 for l in sel.picks)


def test_short_fail_closed_skips_day():
    rows, ltps = rows_and_ltps()
    # every CE > 85 → NO_SHORT_CE
    for k in list(ltps):
        if k.endswith("CE"):
            ltps[k] = max(ltps[k], 90.0)
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    sel = select_ic_strikes(LEGS, ce, pe, tokens, EXP)
    assert not sel.ok and sel.skip_reason == "NO_SHORT_CE"


def test_wing_fallback_cheapest_flagged():
    rows, ltps = rows_and_ltps()
    # cheapest CE now ₹31 (> ₹4 cap) → fallback pick, flagged
    for k in list(ltps):
        if k.endswith("CE") and ltps[k] < 30:
            ltps[k] = 31.0
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    sel = select_ic_strikes(LEGS, ce, pe, tokens, EXP)
    assert sel.ok and sel.wing_fallback
    assert sel.picks["L3"].ltp == 31.0 and sel.picks["L3"].fallback


def test_wing_absent_recorded_not_fatal():
    rows, ltps = rows_and_ltps()
    # zero out all far CEs' LTPs entirely except shorts' zone
    rows2 = [r for r in rows if not (r["instrument_type"] == "CE" and r["strike"] > 24200)]
    ce, pe, tokens = build_chain_candidates(rows2, ltps)
    # remaining CE min ltp is 71 (>4) — still a candidate, so wing falls BACK.
    sel = select_ic_strikes(LEGS, ce, pe, tokens, EXP)
    assert sel.ok and sel.wing_fallback
    # true absence: no CE candidates at all except... construct: drop all CE
    rows3 = [r for r in rows if r["instrument_type"] == "PE"]
    ce3, pe3, tok3 = build_chain_candidates(rows3, ltps)
    sel3 = select_ic_strikes(
        [l for l in LEGS if l["opt_type"] == "PE"] +
        [{"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 24, "premium_max": 4}],
        ce3, pe3, tok3, EXP)
    assert sel3.ok and sel3.wing_absent == ["L3"]


def test_lots_zero_disables_leg_strangle_mode():
    rows, ltps = rows_and_ltps()
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    legs = [dict(l) for l in LEGS]
    legs[2]["lots"] = 0
    legs[3]["lots"] = 0
    sel = select_ic_strikes(legs, ce, pe, tokens, EXP)
    assert sel.ok and set(sel.picks) == {"L1", "L2"}


def test_duplicate_strike_fails_closed():
    rows, ltps = rows_and_ltps()
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    legs = [dict(l) for l in LEGS]
    legs[2]["premium_max"] = 85    # wing cap == short cap → same CE strike
    sel = select_ic_strikes(legs, ce, pe, tokens, EXP)
    assert not sel.ok and sel.skip_reason.startswith("DUPLICATE_STRIKE")


def test_empty_chain_fails_closed():
    sel = select_ic_strikes(LEGS, [], [], {}, EXP)
    assert not sel.ok and sel.skip_reason == "EMPTY_CHAIN"


def test_zero_ltp_symbols_excluded():
    rows, ltps = rows_and_ltps()
    ltps["NIFTY2670924150CE"] = 0.0
    ce, pe, tokens = build_chain_candidates(rows, ltps)
    sel = select_ic_strikes(LEGS, ce, pe, tokens, EXP)
    # 24150CE excluded (ltp=0); next highest CE ≤85 is 24200CE @71
    assert sel.ok and sel.picks["L1"].strike == 24200
    assert sel.picks["L1"].ltp == 71.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
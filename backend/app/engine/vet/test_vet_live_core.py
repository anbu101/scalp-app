# backend/app/engine/vet/test_vet_live_core.py
#
# ── VET_V1 LIVE CORE PARITY TESTS ──
# ============================================================================
# Two things are proven here.
#
# 1. DECISION PARITY. The live core's plan()/replay() is driven over the same
#    condition series as a REFERENCE implementation transcribed directly from
#    backtest_vet_runner's decision block, across randomized series and both
#    leg actions. Identical action/side/reason sequences, or the test fails.
#    This is the guard against the live rule drifting from the backtest rule.
#
# 2. PREFIX STABILITY. The REAL backtest engine (vet_states) is run over
#    growing prefixes of a randomized bar series, exactly as the live signal
#    engine will at each completed 5m bar, and every prefix must restate every
#    earlier bar's condition identically. This is what makes "live signals ==
#    backtest signals" true BY CONSTRUCTION rather than by careful porting.
#    The PrefixGuard is also shown to FREEZE when fed a violation.
#
# Runs standalone:  python3 test_vet_live_core.py
# ============================================================================

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))

try:
    from app.engine.vet.vet_live_core import (
        ENTER, EXIT, FLIP, HOLD, PrefixGuard, R_FLIP, R_SIGNAL,
        plan, replay, want_side_for)
except ImportError:
    from vet_live_core import (                                # type: ignore
        ENTER, EXIT, FLIP, HOLD, PrefixGuard, R_FLIP, R_SIGNAL,
        plan, replay, want_side_for)

F = 0


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


# ── REFERENCE: transcribed from backtest_vet_runner decision block ─────────
def reference_replay(conditions, leg_action="BUY"):
    """Verbatim transcription of the runner's rule. Deliberately written in
    the runner's own shape (not refactored) so a reviewer can diff it against
    the source rather than trust a summary."""
    is_sell = str(leg_action).upper() == "SELL"
    out, pos = [], None
    for i, target in enumerate(conditions):
        target = int(target)
        if target == 1:
            want_side = "PE" if is_sell else "CE"
        elif target == -1:
            want_side = "CE" if is_sell else "PE"
        else:
            want_side = None
        if pos is not None and pos != want_side:
            flip = want_side is not None
            out.append({"idx": i, "action": FLIP if flip else EXIT,
                        "side": want_side, "from": pos,
                        "reason": R_FLIP if flip else R_SIGNAL})
            pos = None
        if pos is None and want_side is not None:
            if out and out[-1]["idx"] == i and out[-1]["action"] == FLIP:
                pass                       # the flip already opened it
            else:
                out.append({"idx": i, "action": ENTER, "side": want_side,
                            "from": None, "reason": None})
            pos = want_side
    return out


print("── 1. decision parity vs the runner's own rule ──")
chk("BUY: +1 wants CE, -1 wants PE, 0 wants nothing",
    (want_side_for(1, "BUY"), want_side_for(-1, "BUY"),
     want_side_for(0, "BUY")) == ("CE", "PE", None))
chk("SELL inverts the contract, not the direction",
    (want_side_for(1, "SELL"), want_side_for(-1, "SELL"),
     want_side_for(0, "SELL")) == ("PE", "CE", None))

random.seed(20260827)
mismatch = None
for trial in range(400):
    n = random.randint(1, 90)
    # bias toward runs of repeated conditions, like a real trend day
    conds, cur = [], 0
    while len(conds) < n:
        cur = random.choice([-1, 0, 1])
        conds += [cur] * random.randint(1, 8)
    conds = conds[:n]
    for leg in ("BUY", "SELL"):
        a = replay(conds, leg)
        b = reference_replay(conds, leg)
        if a != b:
            mismatch = (leg, conds, a, b)
            break
    if mismatch:
        break
chk("400 randomized condition series match the reference exactly, both legs",
    mismatch is None,
    f"first divergence: {mismatch[0]} {mismatch[1]}" if mismatch else "")

print("\n── 2. the documented behaviours people get wrong ──")
# NOTE. RANGE-HOLD is an ENGINE property, not a plan() property, and the
# distinction matters: a bar INSIDE the regime channel does not produce
# target 0 — vet_states CARRIES the previous condition (the Pine
# `else: nz(cond[1])` branch). target 0 is reached only through close_cond
# (trend still intact, EMAs inverted), which IS an exit. So "flat holds"
# and "target 0 exits" are both true and describe different bars. The carry
# itself is asserted against the real engine in section 3.
chk("target 0 with a position exits as SIGNAL_EXIT",
    plan("CE", 0, "BUY") == (EXIT, None, R_SIGNAL), plan("CE", 0, "BUY"))
chk("target 0 with NO position does nothing",
    plan(None, 0, "BUY") == (HOLD, None, None))
chk("same side repeating never re-enters (transition-only)",
    plan("CE", 1, "BUY") == (HOLD, "CE", None))
chk("opposite signal is a FLIP, one bar, exit+entry together",
    plan("CE", -1, "BUY") == (FLIP, "PE", R_FLIP))
r = replay([1, 1, 1, -1, -1, 0, 0, 1], "BUY")
chk("a full day replays to ENTER CE, FLIP PE, EXIT, ENTER CE",
    [x["action"] for x in r] == [ENTER, FLIP, EXIT, ENTER]
    and [x["side"] for x in r] == ["CE", "PE", None, "CE"],
    [(x["action"], x["side"]) for x in r])
rs = replay([1, 1, -1, 0], "SELL")
chk("the same series under SELL takes the mirrored contracts",
    [x["side"] for x in rs] == ["PE", "CE", None], [x["side"] for x in rs])

print("\n── 3. prefix stability of the REAL backtest engine ──")
try:
    try:
        from app.backtest.vet.vet_v1_engine import vet_states
    except ImportError:
        sys.path.insert(0, os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..', 'backtest', 'vet')))
        from vet_v1_engine import vet_states                   # type: ignore

    class Bar:
        __slots__ = ("open", "high", "low", "close")

        def __init__(self, o, h, l, c):
            self.open, self.high, self.low, self.close = o, h, l, c

    random.seed(99)
    px, bars = 24000.0, []
    for _ in range(260):
        drift = random.gauss(0, 18)
        o = px
        c = px + drift
        h = max(o, c) + abs(random.gauss(0, 6))
        lo = min(o, c) - abs(random.gauss(0, 6))
        bars.append(Bar(o, h, lo, c))
        px = c

    # Replay exactly as the live engine will: recompute over the growing
    # prefix at every completed bar and fold into the guard.
    guard = PrefixGuard()
    restated = None
    seen = {}
    for n in range(45, len(bars) + 1):
        states = vet_states(bars[:n], trend_len=40, range_len=0.618)
        conds = [s.condition for s in states]
        for i, c in enumerate(conds):
            if i in seen and seen[i] != c:
                restated = (i, seen[i], c, n)
                break
            seen[i] = c
        if restated:
            break
        guard.check(list(range(n)), conds)
    chk("every growing prefix restates earlier bars identically "
        f"({len(bars) - 44} prefixes)", restated is None,
        f"bar {restated[0]} changed {restated[1]}->{restated[2]} at prefix "
        f"{restated[3]}" if restated else "")
    chk("the guard stayed unfrozen over the whole replay",
        not guard.frozen, guard.reason or "")

    # ── RANGE-HOLD, asserted against the real engine ──
    # Every bar inside the channel must CARRY the previous condition. If this
    # ever became "reset to 0", every chop bar would close the position and
    # the strategy would become a different (much worse) strategy silently.
    states = vet_states(bars, trend_len=40, range_len=0.618)
    inr = [s for s in states if s.valid and s.in_range and s.idx > 0]
    broke = [s for s in inr if s.condition != states[s.idx - 1].condition]
    chk(f"every in-channel bar carries the prior condition "
        f"({len(inr)} flat bars checked)", not broke,
        [(s.idx, states[s.idx - 1].condition, s.condition) for s in broke[:3]])
    # and target 0 is only ever reached via close_cond
    zeros = [s for s in states if s.valid and s.idx > 0
             and s.condition == 0 and states[s.idx - 1].condition != 0]
    bad0 = [s for s in zeros
            if not ((s.dir_trend == 1 and s.ema_f1 < s.ema_f2)
                    or (s.dir_trend == -1 and s.ema_f1 > s.ema_f2))]
    chk(f"condition reaches 0 only on EMA inversion with trend intact "
        f"({len(zeros)} such bars)", not bad0,
        [(s.idx, s.dir_trend) for s in bad0[:3]])
except Exception as exc:                                    # pragma: no cover
    chk("backtest engine importable for the prefix test", False, repr(exc))

print("\n── 4. the guard fails CLOSED when stability is violated ──")
g = PrefixGuard()
chk("clean prefix accepted", g.check([100, 200, 300], [0, 1, 1]))
chk("a restated bar FREEZES the guard",
    g.check([100, 200, 300], [0, -1, 1]) is False and g.frozen)
chk("a frozen guard stays frozen and keeps refusing",
    g.check([100], [0]) is False and g.frozen)
chk("the freeze reason names the offending bar",
    g.reason and "200" in g.reason, g.reason)
g2 = PrefixGuard()
chk("mismatched input lengths also fail closed",
    g2.check([1, 2], [0]) is False and g2.frozen)

print("\n" + ("ALL LIVE-CORE PARITY CHECKS PASSED" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)
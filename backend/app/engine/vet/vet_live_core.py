# backend/app/engine/vet/vet_live_core.py
#
# ── VET_V1 LIVE CORE ── PURE (no app imports, unit-tested)
# ============================================================================
# THE PARITY DESIGN (PST/TMA doctrine): this file holds the DECISION rule
# only — the same rule the backtest runner applies at every completed 5m bar
# — expressed so that live and backtest cannot drift. It contains no
# indicator maths: signals come from the REAL backtest engine
# (app.backtest.vet.vet_v1_engine) re-run over a growing prefix by
# vet_live_signal_engine.py. See that file for the warmup contract.
#
# THE RULE, verbatim from backtest_vet_runner's decision block:
#     target    = condition of the last COMPLETED 5m bar  (−1 | 0 | +1)
#     want_side = BUY : +1→CE, −1→PE      SELL: +1→PE, −1→CE      0→None
#     if pos and pos.side != want_side:
#         exit, reason = FLIP if want_side else SIGNAL_EXIT
#     if not pos and want_side:
#         enter want_side
# Note the two consequences that surprise people reading results:
#   * condition 0 (FLAT / inside the regime channel) is NOT an exit on its
#     own — target 0 only exits a position because want_side becomes None,
#     which is the SIGNAL_EXIT branch. A FLAT bar with no position simply
#     does nothing. This is RANGE-HOLD.
#   * a FLIP is one bar: exit and re-entry share a timestamp.
#
# WHAT THIS FILE DELIBERATELY DOES NOT DO
#   * No SL/TP. All four sealed configs run sl_pct=0 / tp_pct=0; exits are
#     FLIP, SIGNAL_EXIT, EXPIRY_EXIT and (config-dependent) EOD. A live SL
#     would be a parity break, so it is not smuggled in here — if one is
#     ever wanted it is a config decision with a backtest sweep behind it.
#   * No hedge selection. The wing is an execution concern (and in live must
#     be BOUGHT BEFORE the short is sold, or the account is briefly naked and
#     margined as such); it lives in the manager, not the decision core.
# ============================================================================

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

# actions
HOLD = "HOLD"
ENTER = "ENTER"
EXIT = "EXIT"
FLIP = "FLIP"

# exit reasons — identical strings to the backtest so reports/parity diffs
# compare without translation.
R_FLIP = "FLIP"
R_SIGNAL = "SIGNAL_EXIT"
R_EXPIRY = "EXPIRY_EXIT"
R_EOD = "EOD"


def want_side_for(target: int, leg_action: str = "BUY") -> Optional[str]:
    """The option side the signal calls for, or None when flat.

    BUY  expresses an up-trend by holding a CE; SELL expresses the SAME
    up-trend by SHORTING a PE. Same directional exposure, opposite contract
    — the inversion lives here and nowhere else.
    """
    t = int(target or 0)
    sell = str(leg_action or "BUY").upper() == "SELL"
    if t == 1:
        return "PE" if sell else "CE"
    if t == -1:
        return "CE" if sell else "PE"
    return None


def plan(pos_side: Optional[str], target: int,
         leg_action: str = "BUY") -> Tuple[str, Optional[str], Optional[str]]:
    """→ (action, side, reason).

    action is HOLD | ENTER | EXIT | FLIP.
      * FLIP  — close `pos_side` and open `side` on this same bar.
      * EXIT  — close and stand flat (reason SIGNAL_EXIT).
      * ENTER — open `side` from flat.
      * HOLD  — do nothing, including on FLAT bars while holding
                (RANGE-HOLD: chop does not close a position).
    """
    want = want_side_for(target, leg_action)
    if pos_side is None:
        return (ENTER, want, None) if want is not None else (HOLD, None, None)
    if pos_side == want:
        return HOLD, pos_side, None
    if want is None:
        return EXIT, None, R_SIGNAL
    return FLIP, want, R_FLIP


class PrefixGuard:
    """Fail-closed stability check (PST runtime guard).

    A signal emitted for a completed 5m bar must never change or disappear
    as later candles append. The transition rule reads only bars i-1 and i
    of COMPLETED bars, so it is prefix-stable by construction — but the
    property is GUARDED anyway, because a corpus gap, a duplicated tick or a
    warmup that silently shortens would all violate it quietly.

    On violation the guard FREEZES: callers must stop trading, not carry on
    with a signal stream that has already proven itself unreliable.
    """

    def __init__(self) -> None:
        self.seen: Dict[int, int] = {}     # bar_ts -> condition
        self.frozen: bool = False
        self.reason: Optional[str] = None

    def check(self, bars_ts: Sequence[int],
              conditions: Sequence[int]) -> bool:
        """Fold one prefix. Returns False (and freezes) on any restatement
        of a bar that was already reported."""
        if self.frozen:
            return False
        if len(bars_ts) != len(conditions):
            self.frozen = True
            self.reason = "length mismatch between bars and conditions"
            return False
        for ts, cond in zip(bars_ts, conditions):
            prev = self.seen.get(int(ts))
            if prev is not None and prev != int(cond):
                self.frozen = True
                self.reason = (f"bar {ts} restated condition {prev} -> {cond}"
                               " — prefix instability, refusing to trade")
                return False
        for ts, cond in zip(bars_ts, conditions):
            self.seen[int(ts)] = int(cond)
        return True


def replay(conditions: Sequence[int],
           leg_action: str = "BUY") -> List[Dict]:
    """Drive `plan` across a condition series from flat.

    This is the reference the parity test drives BOTH the live core and the
    backtest decision sequence through; it is also handy for reasoning about
    a day after the fact. Returns one record per acting bar.
    """
    out: List[Dict] = []
    pos: Optional[str] = None
    for i, c in enumerate(conditions):
        action, side, reason = plan(pos, int(c), leg_action)
        if action == HOLD:
            continue
        out.append({"idx": i, "action": action, "side": side,
                    "reason": reason, "from": pos})
        pos = None if action == EXIT else side
    return out
#!/usr/bin/env python3
# apply_checklist_maxpanels_scar_20260902.py
#
# ── BRK_V1_LIVE_20260902 · docs ── records the MAX_PANELS silent-drop scar
# in docs/strategy_checklist.md so the next campaign checks numeric caps as
# a class, not just missing references:
#
#   * 3.2 row gains the derived-cap requirement (StrategyHost).
#   * Scar section gains the "numeric caps evade grep sweeps" entry beside
#     its sibling (AppSettingsSection) — same failure family: the gap sweep
#     matches STRINGS, so a NUMBER that disagrees with a list is invisible
#     to it. Bit twice: PST_HEDGE at cap 9, BRK_V1 at cap 14 (2026-09-02).
#
# Single-tree file (docs/ is not bundled). Assert-anchored, idempotent.
#     python3 apply_checklist_maxpanels_scar_20260902.py --check
#     python3 apply_checklist_maxpanels_scar_20260902.py

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FENCE = "BRK_V1_LIVE_20260902"
DOC = Path("docs/strategy_checklist.md")

ROW_OLD = ("| 3.2 | `frontend/src/components/StrategyHost.jsx` | Import, "
           "`ACTIVE_STRATEGY_IDS`, META (name + accent), `renderPanel` "
           "case. |")
ROW_NEW = ("| 3.2 | `frontend/src/components/StrategyHost.jsx` | Import, "
           "`ACTIVE_STRATEGY_IDS`, META (name + accent), `renderPanel` "
           "case. **MAX_PANELS must stay derived** "
           "(`ACTIVE_STRATEGY_IDS.length`) — if anyone has re-hardcoded it, "
           "the slice silently drops the newest strategy (bit PST_HEDGE at "
           "cap 9 and BRK_V1 at cap 14). |")

SCAR_OLD = """- **AppSettingsSection sound matrix** → IC, TMA and TSG all missing from
  the per-strategy sound toggles: the gap sweep only finds files that
  mention a donor strategy, so a list that predates ALL donors evades it.
  When adding a strategy, also grep for a *sibling* id you expect beside
  yours (e.g. `grep -rln SCALP_V5 frontend/src`) and diff the two result
  sets (2026-08-03)."""
SCAR_NEW = SCAR_OLD + """
- **Numeric caps/budgets evade grep sweeps** → StrategyHost's
  `MAX_PANELS = 14` silently `.slice()`-dropped BRK_V1 the moment
  `ACTIVE_STRATEGY_IDS` grew to 15 — a RECURRENCE (PST_HEDGE at cap 9),
  because the first fix bumped the literal to exactly the list size, zero
  headroom. The gap sweep matches STRINGS; a number that disagrees with a
  list is invisible to it. Two rules: (1) whenever you extend a list, read
  the ±5 surrounding lines for caps, budgets, slices, or fixed-size
  assumptions bound to it; (2) where the bound's input IS the list, derive
  it (`LIST.length`) so the class dies instead of the instance
  (2026-09-02)."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not DOC.exists():
        print(f"ABORTED: missing {DOC}", file=sys.stderr)
        return 1
    t = DOC.read_text()
    if "Numeric caps/budgets evade grep sweeps" in t:
        print(f"  already present — skipped   {DOC}")
        return 0
    for old, what in ((ROW_OLD, "3.2 row"), (SCAR_OLD, "scar section")):
        n = t.count(old)
        if n != 1:
            print(f"ABORTED: {what}: anchor x{n}, expected 1 — file drifted",
                  file=sys.stderr)
            return 1
    t = t.replace(ROW_OLD, ROW_NEW).replace(SCAR_OLD, SCAR_NEW)
    if a.check:
        print(f"  would patch (clean)         {DOC}")
    else:
        shutil.copy2(DOC, DOC.with_name(DOC.name + f".bak-{FENCE}-docs"))
        DOC.write_text(t)
        print(f"  patched                     {DOC}")
    print(f"\n{FENCE} checklist update "
          f"{'check complete' if a.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# apply_brk_v1_maxpanels_hotfix_20260902.py
#
# ── BRK_V1_LIVE_20260902 · hotfix ── Dashboard silently hides BRK_V1.
#
# ROOT CAUSE (recurring scar): StrategyHost caps its render list with
# `.slice(0, MAX_PANELS)`. The BRK graft grew ACTIVE_STRATEGY_IDS to 15
# entries while MAX_PANELS stayed 14 — the slice dropped exactly the last
# id, BRK_V1, with no warning. The constant's own comment records the
# IDENTICAL incident (PST_HEDGE was #10 when the cap was 9): the earlier
# fix bumped the number to precisely the then-list-size, zero headroom,
# guaranteeing recurrence on the next strategy.
#
# DURABLE FIX: the sliced array is derived from ACTIVE_STRATEGY_IDS itself,
# so the cap now derives from the list — `ACTIVE_STRATEGY_IDS.length`.
# A number that cannot drift from the list it bounds. The scar class dies.
#
#     python3 apply_brk_v1_maxpanels_hotfix_20260902.py --check
#     python3 apply_brk_v1_maxpanels_hotfix_20260902.py

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FENCE = "BRK_V1_LIVE_20260902"
HOST = Path("frontend/src/components/StrategyHost.jsx")

OLD = ("const MAX_PANELS = 14;   // headroom — was 9 (sized for the "
       "pre-V2-removal list); the slice silently DROPPED strategies beyond "
       "it (PST_HEDGE was #10)")
NEW = ("const MAX_PANELS = ACTIVE_STRATEGY_IDS.length;   // ── BRK_V1 hotfix "
       "2026-09-02 ── DERIVED, never hardcoded again: a literal cap silently "
       "DROPPED the newest strategy twice (PST_HEDGE at cap 9, BRK_V1 at cap "
       "14). The slice's input is built from this same list, so deriving "
       "makes a drop structurally impossible.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not HOST.exists():
        print(f"ABORTED: missing {HOST}", file=sys.stderr)
        return 1
    t = HOST.read_text()
    if "ACTIVE_STRATEGY_IDS.length" in t:
        print(f"  already present — skipped   {HOST}")
        return 0
    n = t.count(OLD)
    if n != 1:
        print(f"ABORTED: anchor x{n}, expected 1 — file drifted",
              file=sys.stderr)
        return 1
    # sanity: the derived constant must be declared AFTER the list it reads
    if t.index("const ACTIVE_STRATEGY_IDS") > t.index(OLD):
        print("ABORTED: declaration order unexpected (TDZ risk) — inspect "
              "manually", file=sys.stderr)
        return 1
    t = t.replace(OLD, NEW)
    if a.check:
        print(f"  would patch (clean)         {HOST}")
    else:
        shutil.copy2(HOST, HOST.with_name(HOST.name + f".bak-{FENCE}-hotfix"))
        HOST.write_text(t)
        print(f"  patched                     {HOST}")
    print(f"\n{FENCE} MAX_PANELS hotfix "
          f"{'check complete' if a.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

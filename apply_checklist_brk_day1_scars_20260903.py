#!/usr/bin/env python3
# apply_checklist_brk_day1_scars_20260903.py
#
# ── BRK_V1_LIVE_20260902 · docs ── encodes every 2026-09-03 first-live-day
# failure into docs/strategy_checklist.md so the NEXT integration cannot
# repeat any of them:
#
#   A. Rows 2.9 / 2.12 CORRECTED — the "Nothing if rows live in
#      paper_trades" verdict was true for PAPER surfaces and FALSE for the
#      four LIVE surfaces; that wrong ruling caused three of the day's
#      leaks. New rule: mode-split, with the generic trade_mode='LIVE'
#      union pattern (now installed at all four sites) named explicitly.
#   B. Part 2b gains three primitive contracts: telegram notify payload
#      keys, the executor's real read primitives, and cancel_gtt_verified's
#      fired-GTT semantics (the 464-sell / naked-short near-miss).
#   C. Part 4 verification gains two mandatory legs: the REAL-IMPORT smoke
#      (unstubbed subprocess import) and the fired-GTT exit legs.
#   D. Scar section gains four entries: nonexistent-import silent fallback,
#      LIVE-surface blindness, sell-into-flat-book, payload-key mismatch.
#
# Single-tree file. Assert-anchored, idempotent.
#     python3 apply_checklist_brk_day1_scars_20260903.py --check
#     python3 apply_checklist_brk_day1_scars_20260903.py

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FENCE = "BRK_V1_LIVE_20260902"
DOC = Path("docs/strategy_checklist.md")

R29_OLD = ("| 2.9 | `backend/app/api/telegram_summary_data.py` | **Nothing** "
           "if rows live in `paper_trades` (generic read). Private table → "
           "dedicated card block. |")
R29_NEW = ("| 2.9 | `backend/app/api/telegram_summary_data.py` | "
           "**Mode-split rule (2026-09-03).** PAPER surfaces read "
           "`paper_trades` generically — nothing needed. But every LIVE "
           "surface (`_live_rows` here, `_query_today_live_summary` + "
           "`_query_open_live_positions` in `telegram_api.py`, and "
           "`_query_trades` in `trade_history_routes.py`) reads `trades` + "
           "private tables and is BLIND to `paper_trades` "
           "`trade_mode='LIVE'` rows. All four now carry a generic fenced "
           "`paper-table LIVE union` — a new paper_trades-LIVE strategy "
           "needs NO edit, but a strategy adding a FIFTH live surface must "
           "copy that union, never a per-strategy merge. Private table → "
           "dedicated card block, both modes. |")

R212_OLD = ("| 2.12 | `backend/app/api/paper_trades_routes.py`, "
            "`trade_history_routes.py`, `db/trades_repo.py` | **Nothing** "
            "for `paper_trades` strategies (verify the generic SELECT has "
            "no whitelist). Private table → isolated union blocks. "
            "Live-history union can be deferred until LIVE is enabled. |")
R212_NEW = ("| 2.12 | `backend/app/api/paper_trades_routes.py`, "
            "`trade_history_routes.py`, `db/trades_repo.py` | **Nothing** "
            "for `paper_trades` strategies on the PAPER side (verify the "
            "generic SELECT has no whitelist). The LIVE side of "
            "`trade_history_routes._query_trades` reads `trades` + private "
            "tables only — `paper_trades` LIVE rows flow through the "
            "generic `paper-table LIVE union` (`_query_brk_live` pattern, "
            "2026-09-03: the day-1 live trade was invisible on Analytics). "
            "Never mark this file no-edit without checking BOTH modes. "
            "Private table → isolated union blocks, and the live union is "
            "NOT deferrable if the strategy ships with LIVE enabled. |")

P2B_ANCHOR = ("| **fetch_warmup_sessions(kite, instruments_df=, days=)** | "
              "`days` = trading SESSIONS (looks back 21 calendar days, "
              "returns the last N). Returns fewer if fewer exist — the "
              "engine must refuse, not degrade. Rows are dicts keyed "
              "`ts/open/high/low/close`, `ts` = bar START. |")
P2B_NEW = P2B_ANCHOR + """
| **telegram notify_* payloads** | The formatters read `strategy_id` — a payload sent with `strategy` renders "Strategy: Unknown" and breaks per-strategy filtering/attribution (BRK, 2026-09-03). Rule: diff your payload dict against a DONOR'S ACTUAL CALL (`grep -A3 notify_trade_entry engine/tsg/tsg_manager.py`), never compose keys from memory. A manager test must assert `strategy_id` on every notify payload. |
| **Executor reads: `get_open_positions_or_none()` / `get_gtt_status(id)` / `get_gtts_or_none()`** | These are the ONLY reconcile primitives — `get_positions()` does not exist (BRK called it for 2h, AttributeError swallowed by a fail-closed except, 2026-09-03). The `_or_none` variants return `None` on a failed read vs a real list on success (2026-07-13 HA scar); `None` must mean "do nothing risky", never "assume flat" and never "assume holding → sell". Preflight the EXIT contract too, and re-verify it on any RESUMED live position. |
| **cancel_gtt_verified(gtt_id)** | "Cancelled ✓" is also what you get for a GTT that already FIRED (a triggered GTT is no longer armed, so the verify passes). A close path that sells on cancel-success sold 464 times into a flat book and was one IP-whitelist away from a naked short (BRK, 2026-09-03). Rule: `get_gtt_status` FIRST — "triggered" → the broker already exited, close the row, place NOTHING; and NEVER `place_market_sell` without a positive `get_open_positions_or_none` read showing the holding exists, regardless of the cancel result. Close failures back off (5s·n, cap 60s), never retry at engine-tick cadence. |"""

P4_OLD = """# 3. Pure-core tests (backtest + live) green."""
P4_NEW = """# 3. Pure-core tests (backtest + live) green.
#
# 3b. REAL-IMPORT smoke (2026-09-03, MANDATORY): in a clean subprocess,
#     `sys.path.insert(0,'backend'); import app.engine.<new>.<manager>` and
#     assert no import fallback engaged (repo fns not None, degraded-flag
#     empty). Test-suite stubs satisfy imports BY NAME — a manager importing
#     the NONEXISTENT app.db.database passed 24 stubbed tests, then traded
#     LIVE with persistence silently disabled (no row, no log, invisible
#     after restart). Rules the incident wrote: import fallbacks are split
#     PER CONCERN and record why they engaged; audit logging never shares a
#     fallback with anything else; a manager whose persistence layer is
#     degraded REFUSES to trade (critical alert), never trades from memory."""
P4_2_OLD = """#    Drive EXIT pricing with an UNALIGNED wall-clock ts (e.g. T+1s) against
#    the REAL ChainStore, not a stub: the stub returned a price, the store
#    returned None (VET, 2026-09-01). Assert the exit price != entry price."""
P4_2_NEW = """#    Drive EXIT pricing with an UNALIGNED wall-clock ts (e.g. T+1s) against
#    the REAL ChainStore, not a stub: the stub returned a price, the store
#    returned None (VET, 2026-09-01). Assert the exit price != entry price.
#    For GTT-protected live strategies the exit legs are mandatory
#    (2026-09-03): (a) gtt_status "triggered" → row closed, NO cancel, NO
#    sell; (b) cancel returns True but broker flat → NO sell; (c) broker
#    state unreadable → no orders, retry with backoff; (d) immediate retry
#    inside the backoff window is a quiet no-op."""

SCAR_ANCHOR = """- **Numeric caps/budgets evade grep sweeps**"""
SCAR_NEW = """- **Nonexistent import + blanket fallback = silent live corruption** → a
  manager imported `app.db.database` (doesn't exist) inside one
  try/except-ImportError with audit logging and the DB layer; the fallback
  set the logger to print() and the repo to None. It placed a real order
  and a real GTT with zero persistence and zero log lines — the position
  vanished from the app on restart and escaped EOD management (BRK,
  2026-09-03). Fix class: split fallbacks per concern + degraded-reason
  flag + persistence-down refuses to trade + real-import smoke (Part 4·3b).
- **LIVE surfaces are a separate leak class from PAPER surfaces** → four
  sites (history route, telegram open-positions, summary card, summary
  text) each read `trades` + private tables and were independently blind
  to `paper_trades` `trade_mode='LIVE'` rows; the summary's PAPER query
  meanwhile counted live rows as paper (TSG live legs mis-sectioned for
  weeks). One wrong "no-edit" sweep verdict propagated to all four. Rule:
  every sweep verdict on a display/notify surface must state WHICH MODE it
  was checked for; the generic `paper-table LIVE union` fence is the fix
  pattern (2026-09-03).
- **Selling on cancel-success** → `cancel_gtt_verified` returns True for a
  GTT that already fired; the close path sold into a flat book every 2s,
  464 attempts, blocked only by the broker's IP whitelist — one config
  away from a naked short call. Reconcile-first + unconditional flat-gate
  + backoff are now Part 2b contract rows (2026-09-03).
- **Payload keys composed from memory** → `strategy` vs `strategy_id` cost
  a morning of "Strategy: Unknown" alerts. Copy a donor's call and assert
  the key in tests (2026-09-03).
- **Manual DB recovery rows must satisfy display filters** → a hand-written
  row without `net_pnl` failed the summary card's `net_pnl IS NOT NULL`
  gate and vanished from the EOD report even after every union fix. Any
  manual INSERT/UPDATE must set `net_pnl`, `pnl_value`, `exit_time`
  (2026-09-03).
- **Numeric caps/budgets evade grep sweeps**"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not DOC.exists():
        print(f"ABORTED: missing {DOC}", file=sys.stderr)
        return 1
    t = DOC.read_text()
    if "paper-table LIVE union" in t:
        print(f"  already present — skipped   {DOC}")
        return 0
    for old, what in ((R29_OLD, "2.9"), (R212_OLD, "2.12"),
                      (P2B_ANCHOR, "2b anchor"), (P4_OLD, "part4 tests"),
                      (P4_2_OLD, "part4 exit legs"),
                      (SCAR_ANCHOR, "scar anchor")):
        n = t.count(old)
        if n != 1:
            print(f"ABORTED: {what}: anchor x{n}, expected 1 — file drifted",
                  file=sys.stderr)
            return 1
    t = (t.replace(R29_OLD, R29_NEW)
          .replace(R212_OLD, R212_NEW)
          .replace(P2B_ANCHOR, P2B_NEW)
          .replace(P4_OLD, P4_NEW)
          .replace(P4_2_OLD, P4_2_NEW)
          .replace(SCAR_ANCHOR, SCAR_NEW))
    if a.check:
        print(f"  would patch (clean)         {DOC}")
    else:
        shutil.copy2(DOC, DOC.with_name(DOC.name + f".bak-{FENCE}-day1docs"))
        DOC.write_text(t)
        print(f"  patched                     {DOC}")
    print(f"\n{FENCE} checklist day-1 scars "
          f"{'check complete' if a.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

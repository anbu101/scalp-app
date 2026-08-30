#!/usr/bin/env python3
# apply_vet_v1_checklist_note_20260829.py — appends the VET_V1 donor entry to
# docs/strategy_checklist.md (idempotent; docs only, single tree).
import os
import sys

P = os.path.join(os.getcwd(), "docs", "strategy_checklist.md")
MARK = "## VET_V1 integration notes (2026-08-29)"
NOTE = f"""

{MARK}

VET_V1 is now the NEWEST DONOR — clone from it, not TSG/TMA_V2, for the next
spot-signal strategy. What it added to the doctrine:

- **Parity by construction, tested by construction**: the live signal engine
  re-runs the backtest's own `resample_spot` + `vet_states` over the growing
  day prefix; the test drives 1m candles one at a time and asserts every
  emitted 5m bar equals the whole-day backtest computation. Copy
  `test_vet_live_signal_engine.py` section 2 verbatim for any new strategy.
- **PrefixGuard**: runtime freeze on any restated bar (fail closed). Lives in
  `vet_live_core.py`, strategy-agnostic — lift it, don't rewrite it.
- **Wing-before-short is a LIVE-ONLY invariant** a backtest cannot see: buy
  the hedge first, sell second; failed short → wing sold back; exits close
  the short first. Asserted by order-recording stub executor in
  `test_vet_manager.py` section 2/3 — copy that pattern for any multi-leg
  entry.
- **Live wings are REAL or the entry is skipped** (`wing_mode
  real_fallback`). If the backtest used synthetic pricing, record the
  divergence in the strategy_loader comment (the "divergence ledger") and
  expect fewer live entries — do not paper over it.
- **Dynamic-mode exemption**: when a lifecycle switch (eod_square) is a USER
  setting, exempt from generic sweeps UNCONDITIONALLY and push the
  mode-awareness into the strategy's own EOD job — an exemption that reads
  config goes stale the day the user flips it mid-week.
- **Widen shared mappers instead of forking them**: `_load_tma_paper` went
  `SELECT *` + direction `in ("SELL","SHORT")` and now serves three tables.
  One mapper, three unions, zero copies.
- **Sweep addition**: `grep VET_V1` is now part of the gap-sweep donor set in
  section 6 (files that name TMA_V2 but not VET_V1 are suspect for the next
  integration, and vice versa).

Apply-script order for reference (all idempotent):
wiring1 (registry/loader/exemption) → engine files + routes + job +
migration → wiring2 (api_server/kill/telegram/license) → display_unions →
settings_panel → dashboard.
"""

t = open(P).read()
if MARK in t:
    print("already applied — nothing to do")
    sys.exit(0)
open(P, "a").write(NOTE)
print("checklist updated: VET_V1 is the newest donor")

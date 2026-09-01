#!/usr/bin/env python3
# apply_checklist_contract_lessons_20260901.py
#
# Records the two VET_V1 first-paper-day bugs (2026-09-01) in
# docs/strategy_checklist.md so the CLASS of failure cannot recur, not just
# the instance. Four edits, each assert-anchored, idempotent, docs only:
#
#   1. Row 2.3 — "verify signatures" becomes "verify CONTRACTS": the
#      signature check passed and the bug shipped anyway.
#   2. New Part 2b — a table of shared primitives with their hidden
#      contracts (canonical DB path, minute-aligned probes, lookback units,
#      no-arg teardown). Grows with every strategy.
#   3. Part 5 — smoke step 4 gains an unaligned-timestamp leg; new step 6:
#      first-paper-day acceptance (rows in the CANONICAL db, no gross-0
#      exits, visible on the PaperTrades page). "It trades" is not "it works".
#   4. Part 7 — two scar-tissue bullets.

import os
import sys

P = os.path.join(os.getcwd(), "docs", "strategy_checklist.md")
FENCE = "Part 2b — Shared primitives"


def die(m):
    print(f"ABORT: {m}\nNothing written.")
    sys.exit(1)


def one(t, a, lbl):
    if t.count(a) != 1:
        die(f"anchor count {t.count(a)} != 1 [{lbl}]")


t = open(P).read()
if FENCE in t:
    print("already applied — nothing to do")
    sys.exit(0)

# ── 1. row 2.3: contract, not arity ────────────────────────────────────────
A1 = ("Verify every imported API against the real source — signatures drift "
      "(`insert_paper_trade` is keyword-only; executor methods are "
      "`place_sell_entry/place_buy/place_buy_exit/place_market_sell/"
      "get_order_fill`). |")
N1 = ("Verify every imported API against the real source — **the contract, "
      "not just the signature**. Arity drifts (`insert_paper_trade` is "
      "keyword-only; executor methods are `place_sell_entry/place_buy/"
      "place_buy_exit/place_market_sell/get_order_fill`) but so do the "
      "*semantics*: read the BODY of any borrowed lookup/store primitive "
      "for its key alignment, units and fallback, and never hardcode a path "
      "or constant a donor already resolves through a helper "
      "(`canonical_db_path()`). See Part 2b. |")
one(t, A1, "row 2.3")
t = t.replace(A1, N1, 1)

# ── 2. Part 2b — shared primitives table ───────────────────────────────────
A2 = "\n---\n\n## Part 3 — Frontend integration points"
N2 = '''
### Part 2b — Shared primitives: contracts, not signatures

Every strategy borrows these. Each has a contract that a signature check
does NOT reveal. Read the body, then copy the donor's *call*, not its name.

| Primitive | The contract that bit us |
|---|---|
| **DB path** — `canonical_db_path()` (`app.engine.pst.pst_common`) | The app's sqlite is wherever this says. A repo with a hardcoded `~/.scalp-app/<x>.db` default writes to a STRAY FILE: the manager trades, the migration creates an empty table in the real DB, every display union finds nothing and skips silently. "No entries" for two days (VET, 2026-09-01). Rule: private repos default to `canonical_db_path()`; the `expanduser` fallback exists for standalone tests only. |
| **ChainStore.last_close_at_or_before(sym, ts, lookback_min)** | Candles are keyed at MINUTE-START epochs; the probe steps in exact 60 s increments *from the ts you pass*. An unaligned wall-clock ts (`int(time.time())` = 12:00:**01**) misses every key → `None` → exit priced at entry ("gross 0" on every trade, VET 2026-09-01). Rule: `ts - ts % 60` before any probe. Third arg is **minutes**, not seconds. |
| **CandleBuilder / on_minute_cb(completed_ts, spot_candle, chain)** | `completed_ts` is the START of the just-completed minute and is already aligned — use it for decision-time lookups. `spot_candle` may be `None` (no spot tick that minute); guard it. |
| **day_cycle.wait_for_teardown()** | Takes NO tag argument (`wait_for_arm_window(tag, last_run_day)` does). A copied call with a tag raises at the first teardown and the loop never re-arms (caught pre-ship, VET). |
| **fetch_warmup_sessions(kite, instruments_df=, days=)** | `days` = trading SESSIONS (looks back 21 calendar days, returns the last N). Returns fewer if fewer exist — the engine must refuse, not degrade. Rows are dicts keyed `ts/open/high/low/close`, `ts` = bar START. |
| **resample_spot(rows, tf, session_start_epoch)** | Buckets from `session_start_epoch + k·tf`. Live MUST pass 09:15 IST of the trading day, or every 5m bar shifts and every signal changes with no error anywhere. |
| **paper_trade_squareoff.OVERNIGHT_EXEMPT_STRATEGIES** | Single source of truth reused by `eod_safety.py`. Exempt UNCONDITIONALLY when a lifecycle switch is a user setting — a config-reading exemption goes stale the day the user flips it. |

When you add a strategy and discover a new one of these, add the row here
before you fix the bug.
''' + A2
one(t, A2, "Part 3 header")
t = t.replace(A2, N2, 1)

# ── 3. Part 5 — smoke leg + first-paper-day acceptance ─────────────────────
A3 = ('''#    The restart leg is mandatory: it caught TSG's unpersisted chain meta
#    (IV checks silently dead after resume).''')
N3 = ('''#    The restart leg is mandatory: it caught TSG's unpersisted chain meta
#    (IV checks silently dead after resume).
#    Drive EXIT pricing with an UNALIGNED wall-clock ts (e.g. T+1s) against
#    the REAL ChainStore, not a stub: the stub returned a price, the store
#    returned None (VET, 2026-09-01). Assert the exit price != entry price.''')
one(t, A3, "smoke step 4")
t = t.replace(A3, N3, 1)

A4 = '''  grep -q "NEW_V1" "$f" || echo "GAP: $f"
done
```
'''
N4 = '''  grep -q "NEW_V1" "$f" || echo "GAP: $f"
done

# 6. FIRST-PAPER-DAY ACCEPTANCE (after the first session, before trusting
#    anything) — "it trades" is not "it works":
DB=$(cd backend && python3 -c "from app.engine.pst.pst_common import canonical_db_path as c; print(c())")
sqlite3 "$DB" "select count(*), sum(exit_price = entry_price) from new_trades"
#    → rows > 0 in the CANONICAL db (a count of 0 with OPEN lines in the log
#      means a stray DB file); exit==entry count must be 0 or explained.
grep -c "no quote\\|gross 0" ~/.scalp-app/logs/$(date +%F).log     # must be 0
#    → then confirm the rows RENDER on the PaperTrades page. Verified-in-log
#      but invisible-in-UI is the checklist's oldest failure shape.
```
'''
one(t, A4, "gap sweep tail")
t = t.replace(A4, N4, 1)

# ── 4. Part 7 scars ────────────────────────────────────────────────────────
A5 = '''  yours (e.g. `grep -rln SCALP_V5 frontend/src`) and diff the two result
  sets (2026-08-03).
'''
N5 = A5 + '''- **Hardcoded DB path** → private repo defaulted to `~/.scalp-app/scalp.db`
  while the app lives at `canonical_db_path()`: two days of paper trades in
  a stray file, every display union silently empty, user reports "no
  entries" (VET, 2026-09-01). The donor already had the helper; the
  signature-level API check never looks at a default argument.
- **Signature verified, contract not** → `last_close_at_or_before` arity was
  checked and correct; its minute-aligned probe was not read. Every exit
  priced at entry, every trade "gross 0" (VET, 2026-09-01). Part 2b exists
  because of this: a borrowed primitive's BODY is part of its API.
'''
one(t, A5, "scar tail")
t = t.replace(A5, N5, 1)

open(P, "w").write(t)
print("checklist updated: row 2.3 hardened, Part 2b added, gauntlet steps 4+6, "
      "two scars recorded")

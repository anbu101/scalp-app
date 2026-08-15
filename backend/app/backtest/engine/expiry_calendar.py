# backend/app/backtest/engine/expiry_calendar.py
#
# Expected weekly-expiry resolution for the BACKTEST, so it picks the SAME
# contract live would have traded on each historical day, and SKIPS days whose
# correct expiry isn't in the corpus (instead of silently using a far-dated
# contract — the bug that produced wrong trades on early days).
#
# ── EXPIRY_ERA (2026-07-16) ────────────────────────────────────────────
# NIFTY weekly expiry weekday CHANGED during the supported corpus window:
#   * THURSDAY era: through 2025-08-28 (the last Thursday expiry)
#   * TUESDAY era:  from 2025-09-02 onward (the first Tuesday expiry)
# The era is decided by the EXPIRY date, not the sim day: on Fri 2025-08-29
# the front contract is already Tue 2025-09-02. Rule: take the next Thursday
# on/after sim_day; if that Thursday still lands inside the Thursday era, use
# it; otherwise the front contract is the next Tuesday on/after sim_day.
#
# The previous version hardcoded Tuesday for the whole window, which (a) made
# pre-Sep-2025 selection wrong and (b) — because dhan_backfill synthesizes
# expiry+tradingsymbol via THIS function at write time — mislabeled every
# pre-Sep-2025 option row in the corpus. Fixing this module makes future
# backfills correct; already-written rows need the one-time relabel tool
# (relabel_expiry_era.py). Until the relabel runs, pre-Sep-2025 days will
# fail CLOSED as days_uncovered (expected Thursday expiry, corpus labeled
# Tuesday) — honest, visible in DIAG, no wrong trades.
#
# Holiday-shifted expiries (Tue/Thu holiday → expiry moves a day earlier) are
# NOT modeled; such weeks fail closed via the corpus-presence gate, same as
# before.
#
# Monthly note: the monthly expiry is the last weekly of the month in both
# eras, so the same "next valid weekday" rule yields it naturally.
#
# This module is PURE (no I/O) and is shared by backtest runners AND the live
# PST selection loop. Live safety: for any current date (Tuesday era) the
# result is identical to the previous hardcoded-Tuesday behavior — only
# historical (< 2025-09) resolution changes.

from __future__ import annotations

from datetime import date, timedelta

# ── EXPIRY_ERA BEGIN ──
LAST_THURSDAY_EXPIRY = date(2025, 8, 28)   # final Thursday-regime expiry
_THU, _TUE = 3, 1                          # date.weekday() codes


def _next_weekday_on_or_after(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7)
# ── EXPIRY_ERA END ──


def expected_expiry_for_day(sim_day: date) -> date:
    """The weekly-expiry contract live would trade on sim_day (era-aware)."""
    thu = _next_weekday_on_or_after(sim_day, _THU)
    if thu <= LAST_THURSDAY_EXPIRY:
        return thu
    return _next_weekday_on_or_after(sim_day, _TUE)


def is_expiry_day(sim_day: date) -> bool:
    return sim_day == expected_expiry_for_day(sim_day)

# ── STOCK_MONTHLY_EXPIRY (2026-08-15, GC-on-stocks) ─────────────────────────
# Stock options (OPTSTK) have MONTHLY expiries only. Same era rule as the
# weekly calendar: last Thursday of the month through the Thursday era, last
# Tuesday after — decided by the EXPIRY date, not the sim day. Holiday-shifted
# expiries are NOT modeled, deliberately: this same function stamps corpus
# rows at backfill time AND will resolve want_expiry in the stock-mode runner,
# so a real-world holiday shift cancels out (self-consistency doctrine — the
# corpus and the selector can never disagree). ADDITIVE: nothing existing
# imports or changes.


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def expected_stock_monthly_expiry_for_day(sim_day: date) -> date:
    """Front MONTHLY stock-option expiry live would trade on sim_day."""
    y, m = sim_day.year, sim_day.month
    for _ in range(2):   # this month, else roll to next
        thu = _last_weekday_of_month(y, m, _THU)
        cand = thu if thu <= LAST_THURSDAY_EXPIRY             else _last_weekday_of_month(y, m, _TUE)
        if cand >= sim_day:
            return cand
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise RuntimeError("unreachable: monthly expiry roll failed")
# ── STOCK_MONTHLY_EXPIRY END ────────────────────────────────────────────────
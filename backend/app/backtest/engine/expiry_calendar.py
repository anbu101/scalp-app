# backend/app/backtest/engine/expiry_calendar.py
#
# Expected weekly-expiry resolution for the BACKTEST, so it picks the SAME
# contract live would have traded on each historical day, and SKIPS days whose
# correct expiry isn't in the corpus (instead of silently using a far-dated
# contract — the bug that produced wrong trades on early days).
#
# NIFTY weekly expiry = TUESDAY for the whole supported backtest period
# (confirmed by the user). The "current week" contract on a given trading day is
# the NEAREST Tuesday ON OR AFTER that day. On expiry day itself (a Tuesday),
# the current contract is THAT day's expiry (it's still trading until close).
#
# Monthly note: the monthly expiry is also a Tuesday (e.g. 2026-06-30), so the
# same "next Tuesday on/after" rule yields it naturally — no special case.
#
# This module is PURE (no I/O). The selector/runner call expected_expiry_for_day
# and then check corpus presence via the CandleSource.

from __future__ import annotations

from datetime import date, timedelta


def expected_expiry_for_day(sim_day: date) -> date:
    """The weekly-expiry contract live would trade on sim_day: the nearest
    Tuesday on or after sim_day. (Tuesday = weekday() 1.)"""
    days_ahead = (1 - sim_day.weekday()) % 7   # 0 if sim_day IS Tuesday
    return sim_day + timedelta(days=days_ahead)


def is_expiry_day(sim_day: date) -> bool:
    return sim_day.weekday() == 1
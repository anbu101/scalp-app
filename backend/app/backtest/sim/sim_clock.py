# backend/app/backtest/sim/sim_clock.py
#
# The simulated clock. The live engine reads real time in two places that the
# backtest must override:
#   * StrategyEngine._is_current_week_expiry() uses date.today()
#   * TradeStateManager / session checks use datetime.now()
#
# The backtest subclass of StrategyEngine consults THIS clock instead of the
# real one, so a run replaying 2025-01-09 evaluates expiry/session as if "now"
# were that candle's timestamp. Pure, thread-local-free, single-run scoped.

from __future__ import annotations

from datetime import date, datetime, timedelta


IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60


class SimClock:
    """Holds the current simulated epoch (IST). Advanced by the runner as it
    steps through candles. Every time-dependent decision in the backtest reads
    from here."""

    def __init__(self, start_epoch: int):
        self._epoch = start_epoch

    # ---- mutation (runner only) ----
    def set_epoch(self, epoch: int) -> None:
        self._epoch = epoch

    def advance_to(self, epoch: int) -> None:
        if epoch < self._epoch:
            # Non-monotonic step would corrupt session/expiry logic. Guard it.
            raise ValueError(f"SimClock cannot move backwards: {epoch} < {self._epoch}")
        self._epoch = epoch

    # ---- reads (engine + selector) ----
    @property
    def epoch(self) -> int:
        return self._epoch

    def now_ist(self) -> datetime:
        """Wall-clock datetime in IST for the current simulated epoch.
        Returned NAIVE (no tzinfo) to match how the live code calls
        datetime.now() — the live session check builds naive datetimes too."""
        # epoch is UTC seconds; add IST offset to get IST wall clock.
        return datetime(1970, 1, 1) + timedelta(seconds=self._epoch + IST_OFFSET_SECONDS)

    def today_ist(self) -> date:
        return self.now_ist().date()
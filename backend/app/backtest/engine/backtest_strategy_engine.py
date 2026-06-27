# backend/app/backtest/engine/backtest_strategy_engine.py
#
# BacktestStrategyEngine — subclass of the LIVE StrategyEngine.
#
# The live file is NEVER edited. We override exactly TWO methods, both of which
# in live reach into runtime that the backtest must supply differently:
#
#   _refresh_in_trade()        live: reads TradeStateManager._REGISTRY + paper DB
#                              bt:   reads the run's VirtualBook
#
#   _is_current_week_expiry()  live: uses date.today()
#                              bt:   uses the SimClock's replayed date
#
# EVERYTHING ELSE — the green-candle gate, EMA conditions, RISK_MIN_SL /
# RISK_MAX_SL / MAX_SL_CAP math, find_previous_red_low usage, signal emission —
# runs as the REAL parent code. If the parent changes, this inherits it. The
# only drift risk is a NEW date.today()/registry read added to the parent; a
# one-line CI grep guard (documented in the runner) catches that.

from __future__ import annotations

from datetime import date, timedelta

from app.engine.strategy_engine import StrategyEngine
from app.backtest.sim.sim_clock import SimClock
from app.backtest.sim.virtual_book import VirtualBook


class BacktestStrategyEngine(StrategyEngine):
    def __init__(
        self,
        *,
        strategy_id: str,
        slot_name: str,
        symbol: str,
        clock: SimClock,
        book: VirtualBook,
    ):
        super().__init__(strategy_id=strategy_id, slot_name=slot_name, symbol=symbol)
        self._clock = clock
        self._book = book

    # ------------------------------------------------------------------
    # OVERRIDE 1 — in-trade truth comes from the VirtualBook, not live state
    # ------------------------------------------------------------------
    def _refresh_in_trade(self):
        """Mirror the parent's contract: set self.in_trade / self.sl / self.tp
        from the recorded trade. Here the 'recorded trade' is the run's
        in-memory VirtualBook position for THIS symbol."""
        pos = self._book.get_open_for_symbol(self.symbol)
        if pos is not None:
            self.in_trade = True
            self.sl = pos.sl
            self.tp = pos.tp
        else:
            if self.in_trade:
                self.in_trade = False
                self.entry_price = None
                self.sl = None
                self.tp = None

    # ------------------------------------------------------------------
    # OVERRIDE 2 — current-week expiry uses the SIM clock, not date.today()
    # ------------------------------------------------------------------
    def _is_current_week_expiry(self) -> bool:
        """Byte-for-byte the parent's logic, but anchored to the simulated date.
        Parent computes the nearest Thursday from 'today' and checks that the
        2-digit year appears in the symbol. We swap today() for the sim date."""
        try:
            today = self._clock.today_ist()
            days_to_thu = (3 - today.weekday()) % 7
            current_expiry = today + timedelta(days=days_to_thu)
            if today.weekday() > 3:
                current_expiry += timedelta(days=7)
            return str(current_expiry.year % 100) in self.symbol
        except Exception:
            return False
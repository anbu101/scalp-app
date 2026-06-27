# backend/app/backtest/sim/hedge_virtual_book.py
#
# Hedge-aware position book for SCALP_V3 / V4 backtests.
#
# ONE LOGICAL TRADE = TWO INSTRUMENTS (mirrors scalp_v3_manager / scalp_v3_repo):
#   signal_*  — tracked, NEVER traded. Drives WHEN to exit (its own SL/TP).
#   hedge_*   — the LONG position actually held. Carries P&L. Protected by an
#               SL-only stop at (hedge_entry - hedge_sl_points).
#
# P&L is LONG on the hedge: (exit - hedge_entry) * qty.
# Charges use direction="LONG" (STT on the exit leg) — opposite of V1's SHORT.
#
# Single open trade at a time (the live DB single-trade gate). One open per book.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class HedgePosition:
    # signal (tracked)
    signal_symbol: str
    signal_token: int
    signal_side: str
    signal_entry_price: float
    signal_sl: float
    signal_tp: float
    signal_candle_ts: int
    # hedge (held LONG)
    hedge_symbol: str
    hedge_token: int
    hedge_side: str
    hedge_entry_ts: int
    hedge_entry_price: float
    hedge_sl: float
    qty: int
    # extremes on the HEDGE (long): adverse = price falling, favorable = rising
    max_adverse: float = 0.0      # worst (hedge_entry - low) seen
    max_favorable: float = 0.0    # best (high - hedge_entry) seen


@dataclass
class HedgeClosedTrade:
    signal_symbol: str
    signal_side: str
    hedge_symbol: str
    hedge_side: str
    strike: float
    expiry: str
    direction: str                # always "LONG" for V3/V4 hedge
    entry_ts: int                 # hedge entry ts (candle close)
    entry_price: float            # hedge entry
    sl: float                     # hedge SL
    signal_sl: float
    signal_tp: float
    exit_ts: int
    exit_price: float
    exit_reason: str              # SIG_SL | SIG_TP | HEDGE_SL | EOD
    pnl: float                    # GROSS LONG: (exit - entry) * qty
    qty: int
    ambiguous_fill: bool
    max_adverse: float
    max_favorable: float
    charges: float = 0.0
    net_pnl: float = 0.0


class HedgeVirtualBook:
    def __init__(self):
        self._open: Optional[HedgePosition] = None
        self.closed: list[HedgeClosedTrade] = []

    def any_open(self) -> bool:
        return self._open is not None

    def open_position(self, pos: HedgePosition) -> None:
        if self._open is not None:
            return  # single-trade gate
        self._open = pos

    def get_open(self) -> Optional[HedgePosition]:
        return self._open

    def update_extremes_signal(self, signal_high: float, signal_low: float) -> None:
        """No P&L impact; extremes are tracked on the hedge. Kept for symmetry."""
        return

    def update_extremes_hedge(self, hedge_high: float, hedge_low: float) -> None:
        p = self._open
        if p is None:
            return
        # LONG hedge: adverse = falling below entry, favorable = rising above.
        adverse = p.hedge_entry_price - hedge_low
        favorable = hedge_high - p.hedge_entry_price
        if adverse > p.max_adverse:
            p.max_adverse = adverse
        if favorable > p.max_favorable:
            p.max_favorable = favorable

    def close_position(self, *, exit_ts: int, exit_price: float,
                       exit_reason: str, ambiguous_fill: bool,
                       strike: float, expiry: str) -> None:
        p = self._open
        if p is None:
            return
        # GROSS LONG P&L
        pnl = (exit_price - p.hedge_entry_price) * p.qty

        # Charges via live calculator, direction="LONG" (STT on EXIT leg).
        # A LONG hedge with a real exit ALWAYS incurs charges; if this ends up
        # 0 it means the charges module failed to import/compute — we LOG it
        # loudly instead of silently zeroing (that silent swallow caused the
        # "CHARGES = ₹0" bug where an older charges_model.py lacked
        # charges_for_long_trade).
        charges = 0.0
        try:
            from app.backtest.charges.charges_model import charges_for_long_trade
            res = charges_for_long_trade(
                entry_price=p.hedge_entry_price, exit_price=exit_price, qty=p.qty)
            charges = float(res.total_charges)
        except Exception as e:
            try:
                from app.event_bus.audit_logger import write_audit_log
                write_audit_log(
                    f"[BACKTEST_HEDGE][CHARGES_FAIL] {p.hedge_symbol} qty={p.qty} "
                    f"entry={p.hedge_entry_price} exit={exit_price} ERR={e!r} "
                    f"— charges set 0 (FIX: ensure charges_model.py has "
                    f"charges_for_long_trade and zerodha_charges import path is correct)"
                )
            except Exception:
                pass
            charges = 0.0
        net_pnl = pnl - charges

        self.closed.append(HedgeClosedTrade(
            signal_symbol=p.signal_symbol, signal_side=p.signal_side,
            hedge_symbol=p.hedge_symbol, hedge_side=p.hedge_side,
            strike=strike, expiry=expiry, direction="LONG",
            entry_ts=p.hedge_entry_ts, entry_price=p.hedge_entry_price,
            sl=p.hedge_sl, signal_sl=p.signal_sl, signal_tp=p.signal_tp,
            exit_ts=exit_ts, exit_price=exit_price, exit_reason=exit_reason,
            pnl=pnl, qty=p.qty, ambiguous_fill=ambiguous_fill,
            max_adverse=p.max_adverse, max_favorable=p.max_favorable,
            charges=charges, net_pnl=net_pnl,
        ))
        self._open = None
# backend/app/backtest/sim/virtual_book.py
#
# The in-memory position book for a backtest run. This is what the backtest
# subclass of StrategyEngine reads in place of TradeStateManager._REGISTRY and
# the paper_trades DB — i.e. it IS the "recorded-trade truth" that
# _refresh_in_trade() reconciles against, but scoped to this single run and
# living entirely in memory.
#
# SCALP_V1 is single-trade-at-a-time per strategy (the live slot gate). We model
# that here: at most one open virtual position per (strategy, symbol). The
# selection layer enforces the 2-CE/2-PE universe; the book enforces "one open
# trade per symbol" and records closed trades for the results writer.
#
# Pure, no DB, no broker. Direction is SHORT for SCALP_V1.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class VirtualPosition:
    symbol: str
    strike: float
    instrument_type: str      # 'CE' | 'PE'
    expiry: str
    direction: str            # 'SHORT'
    entry_ts: int
    entry_price: float
    sl: float                 # ABOVE entry for SHORT
    tp: float                 # BELOW entry for SHORT
    qty: int
    # running extremes for analytics (premium vs entry)
    max_adverse: float = 0.0      # worst (premium rose most) — bad for short
    max_favorable: float = 0.0    # best  (premium fell most) — good for short


@dataclass
class ClosedTrade:
    symbol: str
    strike: float
    instrument_type: str
    expiry: str
    direction: str
    entry_ts: int
    entry_price: float
    sl: float
    tp: float
    qty: int
    exit_ts: int
    exit_price: float
    exit_reason: str          # 'TP' | 'SL' | 'EOD' | 'SESSION_END'
    pnl: float                # GROSS signed P&L: (entry-exit)*qty for SHORT
    ambiguous_fill: bool
    max_adverse: float
    max_favorable: float
    charges: float = 0.0      # round-trip charges (live zerodha_charges)
    net_pnl: float = 0.0      # pnl - charges


class VirtualBook:
    def __init__(self):
        # key: symbol -> open position (one per symbol; SCALP_V1 single-trade)
        self._open: Dict[str, VirtualPosition] = {}
        self._closed: List[ClosedTrade] = []

    # ---- truth queries (read by the engine subclass) ----
    def has_open_for_symbol(self, symbol: str) -> bool:
        return symbol in self._open

    def get_open_for_symbol(self, symbol: str) -> Optional[VirtualPosition]:
        return self._open.get(symbol)

    def any_open(self) -> bool:
        return len(self._open) > 0

    def open_symbols(self) -> List[str]:
        return list(self._open.keys())

    # ---- mutation (runner / fill model) ----
    def open_position(self, pos: VirtualPosition) -> None:
        if pos.symbol in self._open:
            # Should never happen — the slot gate prevents it. Guard loudly.
            raise RuntimeError(f"VirtualBook: double-open for {pos.symbol}")
        self._open[pos.symbol] = pos

    def update_extremes(self, symbol: str, premium: float) -> None:
        pos = self._open.get(symbol)
        if pos is None:
            return
        # For a short, adverse = premium above entry; favorable = below entry.
        adverse = premium - pos.entry_price
        favorable = pos.entry_price - premium
        if adverse > pos.max_adverse:
            pos.max_adverse = adverse
        if favorable > pos.max_favorable:
            pos.max_favorable = favorable

    def close_position(
        self, symbol: str, *, exit_ts: int, exit_price: float,
        exit_reason: str, ambiguous_fill: bool,
    ) -> ClosedTrade:
        pos = self._open.pop(symbol)
        # SHORT GROSS P&L: (entry - exit) * qty
        pnl = (pos.entry_price - exit_price) * pos.qty

        # Charges via the LIVE calculator (direction-aware: SHORT → STT on entry).
        # Local import so the module stays importable in isolated unit tests
        # that don't have the live charges module on the path.
        charges = 0.0
        try:
            from app.backtest.charges.charges_model import charges_for_short_trade
            res = charges_for_short_trade(
                entry_price=pos.entry_price, exit_price=exit_price, qty=pos.qty)
            charges = float(res.total_charges)
        except Exception:
            charges = 0.0   # if charges module unavailable, NET == GROSS (logged upstream)
        net_pnl = pnl - charges

        ct = ClosedTrade(
            symbol=pos.symbol, strike=pos.strike,
            instrument_type=pos.instrument_type, expiry=pos.expiry,
            direction=pos.direction, entry_ts=pos.entry_ts,
            entry_price=pos.entry_price, sl=pos.sl, tp=pos.tp, qty=pos.qty,
            exit_ts=exit_ts, exit_price=exit_price, exit_reason=exit_reason,
            pnl=pnl, ambiguous_fill=ambiguous_fill,
            max_adverse=pos.max_adverse, max_favorable=pos.max_favorable,
            charges=charges, net_pnl=net_pnl,
        )
        self._closed.append(ct)
        return ct

    # ---- results ----
    def closed_trades(self) -> List[ClosedTrade]:
        return list(self._closed)
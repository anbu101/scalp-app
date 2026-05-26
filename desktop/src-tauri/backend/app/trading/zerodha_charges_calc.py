"""
Zerodha Charges Calculator — OPTIONS (NIFTY / BANKNIFTY)

LOCKED v2

Assumptions:
- Flat brokerage model (₹20 per order)
- Options only (NOT futures, NOT equity)
- Intraday or positional (charges identical for options)
- Single completed trade = Entry + Exit

All values in INR.

NOTE: This file is UNCHANGED from the original.
Direction-aware P&L sign is handled by the callers
(paper_trades_repo.close_paper_trade, paper_trades_reconcile).
The charges formula is always computed on turnover regardless
of trade direction.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ZerodhaChargesResult:
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float
    gross_pnl: float
    net_pnl: float


def calculate_option_charges(
    *,
    entry_price: float,
    exit_price: float,
    qty: int,
) -> ZerodhaChargesResult:
    """
    Zerodha-style charges for OPTION trades.

    Args:
        entry_price : Price at which the option was first traded (sold for SHORT)
        exit_price  : Price at which the option was closed (bought back for SHORT)
        qty         : Quantity (lots × lot_size)

    Returns:
        ZerodhaChargesResult with charges computed on turnover.
        gross_pnl = (exit - entry) × qty  — callers flip the sign for SHORT.
    """

    # Turnover
    buy_value  = entry_price * qty
    sell_value = exit_price  * qty
    turnover   = buy_value + sell_value

    # Gross PnL (direction-neutral; callers adjust sign for SHORT)
    pnl_points = exit_price - entry_price
    gross_pnl  = pnl_points * qty

    # Charges (LOCKED v2)
    brokerage        = 40.0                          # ₹20 entry + ₹20 exit
    stt              = 0.0005   * sell_value         # 0.05% on sell premium
    exchange_charges = 0.00053  * turnover           # NSE
    sebi_charges     = 0.000001 * turnover
    stamp_duty       = 0.00003  * buy_value          # buy side only
    gst              = 0.18     * (brokerage + exchange_charges)

    total_charges = (
        brokerage
        + stt
        + exchange_charges
        + sebi_charges
        + stamp_duty
        + gst
    )

    net_pnl = gross_pnl - total_charges

    return ZerodhaChargesResult(
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange_charges=round(exchange_charges, 2),
        sebi_charges=round(sebi_charges, 2),
        stamp_duty=round(stamp_duty, 2),
        gst=round(gst, 2),
        total_charges=round(total_charges, 2),
        gross_pnl=round(gross_pnl, 2),
        net_pnl=round(net_pnl, 2),
    )
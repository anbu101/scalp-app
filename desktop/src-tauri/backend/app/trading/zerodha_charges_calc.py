"""
Zerodha Charges Calculator — OPTIONS (NIFTY / BANKNIFTY)

LOCKED v2

Assumptions:
- Flat brokerage model (₹20 per order)
- Options only (NOT futures, NOT equity)
- Intraday or positional (charges identical for options)
- Single completed trade = Entry + Exit

All values in INR.
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
        entry_price: Buy price (premium)
        exit_price: Sell price (premium)
        qty: Quantity (lots × lot_size)

    Returns:
        ZerodhaChargesResult
    """

    # -----------------------------
    # Turnover
    # -----------------------------
    buy_value = entry_price * qty
    sell_value = exit_price * qty
    turnover = buy_value + sell_value

    # -----------------------------
    # Gross PnL
    # -----------------------------
    pnl_points = exit_price - entry_price
    gross_pnl = pnl_points * qty

    # -----------------------------
    # Charges (LOCKED)
    # -----------------------------
    brokerage = 40.0  # ₹20 entry + ₹20 exit

    # STT: 0.05% on SELL premium only
    stt = 0.0005 * sell_value

    # Exchange transaction charges (NSE)
    exchange_charges = 0.00053 * turnover

    # SEBI charges
    sebi_charges = 0.000001 * turnover

    # Stamp duty (BUY side only)
    stamp_duty = 0.00003 * buy_value

    # GST: 18% on (brokerage + exchange charges)
    gst = 0.18 * (brokerage + exchange_charges)

    # -----------------------------
    # Totals
    # -----------------------------
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

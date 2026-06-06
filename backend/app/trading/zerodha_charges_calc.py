"""
Zerodha Charges Calculator — OPTIONS (NIFTY / BANKNIFTY)

LOCKED v4  —  rates verified against https://zerodha.com/charges/ on 06-Jun-2026
             (F&O – Options, NSE column)

Assumptions:
- Flat brokerage model (₹20 per order, ₹40 round trip)
- Options only (NOT futures, NOT equity)
- Intraday or positional (charges identical for options)
- Single completed trade = Entry + Exit
- NSE (not BSE)

All values in INR.

CHANGES FROM v3:
- STT is now direction-aware. STT is levied on the SELL leg of the round trip.
    * LONG  (option buyer:  BB_V1, BB_V2, HA_V1) — buys at entry, sells at exit.
      Sell leg = exit_price.  (unchanged from v3)
    * SHORT (option seller: SCALP_V1, SCALP_V2) — sells at entry, buys back at exit.
      Sell leg = entry_price.  (NEW — v3 incorrectly used exit_price for shorts)
  Exchange / SEBI / stamp / GST are turnover-based and unaffected by direction.

CHANGES INTRODUCED IN v3 (kept):
- STT sell rate 0.0015 (0.15% of premium, post 01-Apr-2026 Budget 2026-27).
- Exchange (transaction) charge 0.0003553 (NSE options 0.03553% of premium turnover).
- GST base = brokerage + transaction + SEBI.

Direction-aware P&L *sign* is still handled by the callers
(paper_trades_repo.close_paper_trade, paper_trades_reconcile). This module only
uses `direction` to pick the correct STT leg; gross_pnl is returned
direction-neutral as before and callers flip the sign for SHORT.

NOTE ON STT ROUNDING:
Zerodha rounds STT to the nearest rupee per contract note (>= 0.50 up,
< 0.50 down). Per-leg rounding is OFF by default so aggregate estimates stay
smooth; expect sub-rupee differences vs an actual note. Set
ROUND_STT_TO_RUPEE = True to match the note.
"""

from dataclasses import dataclass

# ── Locked rates (NSE options, verified 06-Jun-2026) ──
BROKERAGE_ROUND_TRIP = 40.0       # ₹20 entry + ₹20 exit
STT_SELL_RATE        = 0.0015     # 0.15% of sell-leg premium (post 01-Apr-2026)
EXCHANGE_RATE        = 0.0003553  # NSE options transaction charge, 0.03553% of premium turnover
SEBI_RATE            = 0.000001   # ₹10 / crore
STAMP_RATE           = 0.00003    # 0.003% on buy premium
GST_RATE             = 0.18       # 18% on (brokerage + exchange + SEBI)

ROUND_STT_TO_RUPEE   = False      # set True to match contract-note rounding

# Direction constants — keep in sync with the rest of the codebase.
LONG  = "LONG"    # option buyer  (BB_V1, BB_V2, HA_V1)
SHORT = "SHORT"   # option seller (SCALP_V1, SCALP_V2)


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


def _round_stt(value: float) -> float:
    """Zerodha rounds STT to the nearest rupee (>= 0.50 up, < 0.50 down)."""
    if not ROUND_STT_TO_RUPEE:
        return value
    import math
    frac = value - math.floor(value)
    return float(math.floor(value) + 1) if frac >= 0.5 else float(math.floor(value))


def calculate_option_charges(
    *,
    entry_price: float,
    exit_price: float,
    qty: int,
    direction: str = LONG,
) -> ZerodhaChargesResult:
    """
    Zerodha-style charges for OPTION trades on NSE.

    Args:
        entry_price : Price at which the option was first traded.
                      For SHORT this is the SELL leg; for LONG it is the BUY leg.
        exit_price  : Price at which the option was closed.
                      For SHORT this is the BUY (buy-back) leg; for LONG it is the SELL leg.
        qty         : Quantity (lots × lot_size)
        direction   : "LONG"  -> option buyer  (STT on exit_price)
                      "SHORT" -> option seller (STT on entry_price)

    Returns:
        ZerodhaChargesResult with charges computed on turnover.
        gross_pnl = (exit - entry) × qty  — callers flip the sign for SHORT.
    """

    # Turnover (buy premium + sell premium) — direction-neutral.
    buy_value  = entry_price * qty
    sell_value = exit_price  * qty
    turnover   = buy_value + sell_value

    # Gross PnL (direction-neutral; callers adjust sign for SHORT)
    pnl_points = exit_price - entry_price
    gross_pnl  = pnl_points * qty

    # STT is charged on the SELL leg of the round trip.
    #   SHORT seller's sell leg = entry_price (they sold first)
    #   LONG  buyer's  sell leg = exit_price  (they sell to close)
    stt_leg_value = (entry_price if direction == SHORT else exit_price) * qty

    # Charges (LOCKED v4)
    brokerage        = BROKERAGE_ROUND_TRIP
    stt              = _round_stt(STT_SELL_RATE * stt_leg_value)
    exchange_charges = EXCHANGE_RATE * turnover
    sebi_charges     = SEBI_RATE     * turnover
    stamp_duty       = STAMP_RATE    * buy_value
    gst              = GST_RATE * (brokerage + exchange_charges + sebi_charges)

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


if __name__ == "__main__":
    # LONG (buyer) — STT on exit.
    print("LONG :", calculate_option_charges(
        entry_price=165.25, exit_price=177.50, qty=975, direction=LONG))
    # SHORT (seller) — STT on entry. SCALP V1 winning short row.
    print("SHORT:", calculate_option_charges(
        entry_price=187.05, exit_price=168.85, qty=975, direction=SHORT))
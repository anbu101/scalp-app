# backend/app/backtest/charges/charges_model.py
#
# Backtest charges — SELF-CONTAINED port of the FRONTEND charges calculator.
#
# WHY SELF-CONTAINED (history):
#   Earlier versions tried to import a live Python `zerodha_charges` module and
#   fell back to ₹0 when it wasn't found. But there IS no backend charges
#   module — the charges math has only ever lived in the FRONTEND
#   (PaperTrades.jsx: calcCharges() + ZCHARGES). The backtest import therefore
#   always failed and charges were silently 0. This module fixes that by
#   porting the EXACT frontend rates into Python, so no import can fail.
#
# PARITY CONTRACT — these constants MUST stay identical to PaperTrades.jsx
# ZCHARGES (LOCKED v4, verified against https://zerodha.com/charges/ 06-Jun-2026,
# NSE F&O Options):
#     BROKERAGE  ₹40         (₹20 × 2)
#     STT_SELL   0.0015      (0.15% of SELL-LEG premium, post 01-Apr-2026)
#     EXCHANGE   0.0003553   (NSE options txn, % of premium turnover)
#     SEBI       0.000001    (₹10 / crore)
#     STAMP      0.00003     (0.003% of BUY premium)
#     GST        0.18        (on brokerage + exchange + SEBI)
#
# DIRECTION (v4): STT is on the SELL leg of the round trip.
#   SHORT (SCALP_V1/V2 — seller): sold first  -> STT on ENTRY price.
#   LONG  (SCALP_V3/V4 hedge buy): sells to close -> STT on EXIT price.
# Exchange / SEBI / stamp / GST are turnover-based and direction-neutral.
#
# If a real backend zerodha_charges.py is ever added, this stays valid; it does
# not import or depend on it. Backtest-only; no live file depends on this.

from __future__ import annotations

from dataclasses import dataclass


# ── LOCKED v4 rates — keep identical to frontend ZCHARGES ──
BROKERAGE = 40.0        # ₹20 × 2, round trip
STT_SELL  = 0.0015      # sell-leg premium
EXCHANGE  = 0.0003553   # turnover (buy + sell premium)
SEBI      = 0.000001    # turnover
STAMP     = 0.00003     # buy premium
GST       = 0.18        # on (brokerage + exchange + SEBI)

SHORT = "SHORT"
LONG  = "LONG"


@dataclass(frozen=True)
class ChargesResult:
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float
    gross_pnl: float
    net_pnl: float


def _calc(*, entry_price: float, exit_price: float, qty: int, direction: str) -> ChargesResult:
    """Round-trip option charges, mirroring PaperTrades.jsx calcCharges() to the
    paisa. direction SHORT => STT on entry leg; LONG => STT on exit leg."""
    if not entry_price or not exit_price or not qty:
        return ChargesResult(0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0)

    buy_val   = entry_price * qty
    sell_val  = exit_price  * qty
    turnover  = buy_val + sell_val

    # STT on the sell leg: SHORT sold at entry, LONG sells at exit.
    stt_leg_val = (entry_price if direction == SHORT else exit_price) * qty

    brokerage      = BROKERAGE
    stt            = STT_SELL * stt_leg_val
    exchange_chg   = EXCHANGE * turnover
    sebi           = SEBI     * turnover
    stamp_duty     = STAMP    * buy_val
    gst            = GST * (brokerage + exchange_chg + sebi)

    total = round((brokerage + stt + exchange_chg + sebi + stamp_duty + gst) * 100) / 100

    # Gross P&L: LONG = (exit-entry); SHORT = (entry-exit), both × qty.
    if direction == SHORT:
        gross = (entry_price - exit_price) * qty
    else:
        gross = (exit_price - entry_price) * qty

    return ChargesResult(
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange_charges=round(exchange_chg, 2),
        sebi_charges=round(sebi, 2),
        stamp_duty=round(stamp_duty, 2),
        gst=round(gst, 2),
        total_charges=total,
        gross_pnl=round(gross, 2),
        net_pnl=round(gross - total, 2),
    )


def charges_for_short_trade(*, entry_price: float, exit_price: float, qty: int) -> ChargesResult:
    """SCALP_V1/V2 SHORT option trade (STT on ENTRY leg)."""
    return _calc(entry_price=entry_price, exit_price=exit_price, qty=qty, direction=SHORT)


def charges_for_long_trade(*, entry_price: float, exit_price: float, qty: int) -> ChargesResult:
    """SCALP_V3/V4 LONG hedge option trade (STT on EXIT leg)."""
    return _calc(entry_price=entry_price, exit_price=exit_price, qty=qty, direction=LONG)


# Compatibility alias if any caller used the live-style entry point name.
def calculate_option_charges(*, entry_price: float, exit_price: float, qty: int,
                             direction: str = LONG) -> ChargesResult:
    return _calc(entry_price=entry_price, exit_price=exit_price, qty=qty, direction=direction)
# backend/app/backtest/util/bnf_symbol.py
#
# Build the ZERODHA tradingsymbol for a BANKNIFTY MONTHLY option.
# BANKNIFTY is monthly-only (weeklies discontinued by NSE in late 2024), so the
# format is the Zerodha MONTHLY pattern:
#     BANKNIFTY{YY}{MON3}{strike}{TYPE}   e.g. BANKNIFTY26JUN57400CE
# Mirrors the NIFTY monthly format (NIFTY{YY}{MON3}{strike}{TYPE}) with the
# BANKNIFTY name. (The Dhan SYMBOL_NAME 'BANKNIFTY-Jun2026-57400-CE' is Dhan's
# own; the corpus + live trades key on the Zerodha symbol, so we synthesize it.)

from __future__ import annotations
from datetime import date

_MON3 = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def build_banknifty_symbol(expiry: date, strike, opt_type: str) -> str:
    """e.g. (2026-06-30, 57400, 'CE') -> 'BANKNIFTY26JUN57400CE'."""
    if opt_type not in ("CE", "PE"):
        raise ValueError("opt_type must be CE or PE")
    yy = expiry.year % 100
    mon = _MON3[expiry.month]
    k = int(round(float(strike)))
    return f"BANKNIFTY{yy:02d}{mon}{k}{opt_type}"
# backend/app/backtest/util/nifty_symbol.py
#
# Reproduce Zerodha's NIFTY option tradingsymbol from (expiry, strike, type),
# for synthesizing symbols when importing external historical data (e.g. Dhan
# rolling-options, which is keyed by ATM-relative strike, not by symbol).
#
# RULE (derived from the live instruments dump, verified against all expiries):
#   MONTHLY  = the LAST Tuesday of its month  -> NIFTY{YY}{MON3}{strike}{TYPE}
#                e.g. 2026-06-30 -> NIFTY26JUN24050CE
#   WEEKLY   = any other Tuesday              -> NIFTY{YY}{Mcode}{DD}{strike}{TYPE}
#                e.g. 2026-07-07 -> NIFTY2670724050CE
#   Mcode: '1'..'9' for Jan..Sep, 'O'/'N'/'D' for Oct/Nov/Dec.
#
# The backtest selector matches on expiry+strike+type, so the symbol is cosmetic
# for correctness — but it must look native for the UI/CSV, hence this exact
# reproduction.

from __future__ import annotations
from datetime import date, timedelta

_MON3 = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
         7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
_MCODE = {1:"1",2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",
          10:"O",11:"N",12:"D"}


def is_monthly_expiry(expiry: date) -> bool:
    """True if expiry is the LAST Tuesday of its month (NIFTY monthly contract)."""
    return (expiry + timedelta(days=7)).month != expiry.month


def build_nifty_symbol(expiry: date, strike, opt_type: str) -> str:
    yy = expiry.year % 100
    s = int(round(float(strike)))
    t = opt_type.upper()
    if is_monthly_expiry(expiry):
        return f"NIFTY{yy:02d}{_MON3[expiry.month]}{s}{t}"
    return f"NIFTY{yy:02d}{_MCODE[expiry.month]}{expiry.day:02d}{s}{t}"
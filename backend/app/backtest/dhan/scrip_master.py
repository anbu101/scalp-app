# backend/app/backtest/dhan/scrip_master.py
#
# Parse Dhan's detailed instrument master to resolve BANKNIFTY futures contracts
# (securityId + expiry + symbol) for the backtest backfill. DATA-ONLY helper.
#
# Source CSV (no auth): https://images.dhan.co/api-data/api-scrip-master-detailed.csv
# Relevant columns (header order, verified live):
#   EXCH_ID, SEGMENT, SECURITY_ID, ISIN, INSTRUMENT, UNDERLYING_SECURITY_ID,
#   UNDERLYING_SYMBOL, SYMBOL_NAME, DISPLAY_NAME, INSTRUMENT_TYPE, SERIES,
#   LOT_SIZE, SM_EXPIRY_DATE, STRIKE_PRICE, OPTION_TYPE, ...
#
# BANKNIFTY index-future rows look like:
#   NSE,D,62326,NA,FUTIDX,26009,BANKNIFTY,BANKNIFTY-Jun2026-FUT,BANKNIFTY JUN FUT,
#   FUT,NA,30.0,2026-06-30,-0.01000,XX,...
#
# NOTE: the LIVE master lists ONLY the currently-active monthly futures (NSE keeps
# ~3 active month contracts). Expired months are absent — so this resolves recent
# contracts only. Deep history needs archived masters (a separate problem).

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import requests

_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


@dataclass
class FutContract:
    security_id: str
    symbol: str            # SYMBOL_NAME, e.g. BANKNIFTY-Jun2026-FUT
    underlying: str        # BANKNIFTY
    expiry: date
    lot_size: int

    def __repr__(self):
        return f"FutContract({self.symbol} id={self.security_id} exp={self.expiry})"


def _parse_expiry(s: str) -> Optional[date]:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def download_master_text(timeout: int = 60) -> str:
    """Fetch the detailed scrip master CSV (no auth required)."""
    r = requests.get(_MASTER_URL, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_index_futures(
    master_text: str, underlying: str = "BANKNIFTY"
) -> List[FutContract]:
    """Return all index-future (FUTIDX) contracts for the given underlying,
    sorted by expiry ascending. Robust to column-order changes via DictReader."""
    out: List[FutContract] = []
    reader = csv.DictReader(io.StringIO(master_text))
    for row in reader:
        # INSTRUMENT column holds FUTIDX for index futures.
        instr = (row.get("INSTRUMENT") or "").strip().upper()
        und = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
        if instr != "FUTIDX" or und != underlying.upper():
            continue
        exp = _parse_expiry(row.get("SM_EXPIRY_DATE", ""))
        if not exp:
            continue
        try:
            lot = int(float(row.get("LOT_SIZE") or 0))
        except Exception:
            lot = 0
        out.append(FutContract(
            security_id=str(row.get("SECURITY_ID", "")).strip(),
            symbol=(row.get("SYMBOL_NAME") or "").strip(),
            underlying=underlying.upper(),
            expiry=exp,
            lot_size=lot,
        ))
    out.sort(key=lambda c: c.expiry)
    return out


def front_month_windows(
    contracts: List[FutContract],
) -> List[tuple]:
    """Given expiry-sorted contracts, compute each contract's FRONT-MONTH window:
    (contract, window_start, window_end) where the contract is the current-month
    future from the day AFTER the previous contract's expiry through its OWN
    expiry (inclusive). The earliest contract's window starts at its own expiry
    minus ~1 month (we use a generous lookback; the fetch tolerates empty days).

    This yields a non-overlapping continuous front-month series when stitched.
    """
    from datetime import timedelta
    windows = []
    prev_expiry = None
    for c in contracts:
        if prev_expiry is None:
            # generous start: ~45 days before this expiry (covers the month it
            # was front, before which it was next-month; harmless extra data is
            # de-duped by delete-then-insert on (symbol, ts) anyway, but we keep
            # the continuous symbol so only front-month days matter).
            start = c.expiry - timedelta(days=45)
        else:
            start = prev_expiry + timedelta(days=1)
        windows.append((c, start, c.expiry))
        prev_expiry = c.expiry
    return windows


# ======================================================================
# OPTIONS (OPTIDX) — per-contract resolution for BANKNIFTY options backfill.
# Unlike NIFTY (rolling endpoint), BANKNIFTY options are fetched per-contract by
# securityId via /charts/intraday. SecurityIds are arbitrary, so we MUST look up
# (expiry, strike, type) -> securityId from the master.
# ======================================================================

@dataclass
class OptContract:
    security_id: str
    symbol: str            # Dhan SYMBOL_NAME, e.g. BANKNIFTY-Jun2026-57400-CE
    underlying: str
    strike: float
    opt_type: str          # CE | PE
    expiry: date


def parse_index_options(
    master_text: str, underlying: str = "BANKNIFTY"
) -> List[OptContract]:
    """All OPTIDX option contracts for the given underlying."""
    out: List[OptContract] = []
    reader = csv.DictReader(io.StringIO(master_text))
    for row in reader:
        instr = (row.get("INSTRUMENT") or "").strip().upper()
        und = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
        if instr != "OPTIDX" or und != underlying.upper():
            continue
        otype = (row.get("OPTION_TYPE") or "").strip().upper()
        if otype not in ("CE", "PE"):
            continue
        exp = _parse_expiry(row.get("SM_EXPIRY_DATE", ""))
        if not exp:
            continue
        try:
            strike = float(row.get("STRIKE_PRICE") or 0)
        except Exception:
            continue
        out.append(OptContract(
            security_id=str(row.get("SECURITY_ID", "")).strip(),
            symbol=(row.get("SYMBOL_NAME") or "").strip(),
            underlying=underlying.upper(),
            strike=strike,
            opt_type=otype,
            expiry=exp,
        ))
    return out


def build_option_index(contracts: List[OptContract]) -> dict:
    """Index {(expiry_iso, int(strike), type): security_id} for O(1) lookup.
    Strikes are stored as ints (BANKNIFTY strikes are whole numbers)."""
    idx = {}
    for c in contracts:
        idx[(c.expiry.isoformat(), int(round(c.strike)), c.opt_type)] = c.security_id
    return idx


def monthly_expiries_in_range(
    contracts: List[OptContract], date_from: date, date_to: date
) -> List[date]:
    """Distinct option expiries that fall in/after the range start — the set of
    monthly expiries we backfill. Sorted ascending."""
    exps = sorted({c.expiry for c in contracts
                   if c.expiry >= date_from and c.expiry <= _last_relevant(date_to, contracts)})
    return exps


def _last_relevant(date_to: date, contracts: List[OptContract]) -> date:
    """The latest expiry we'd consider — the smallest expiry >= date_to, so a
    window ending mid-month still includes that month's expiry."""
    later = sorted({c.expiry for c in contracts if c.expiry >= date_to})
    return later[0] if later else max((c.expiry for c in contracts), default=date_to)
# backend/app/backtest/backfill/token_universe.py
#
# Resolves the set of instrument_tokens to backfill for a given window.
#
# HARD LIMITATION (Kite, by design — not a bug):
#   historical_data() needs an instrument_token, and tokens are resolvable ONLY
#   for contracts present in TODAY's instruments dump. Kite's instruments() (and
#   the instruments.csv it writes) lists only CURRENTLY ACTIVE contracts. A
#   weekly that expired inside the 60-day window is very likely ABSENT from the
#   dump, so its token cannot be resolved here and its history is unfetchable
#   via Kite alone. This resolver therefore returns every token it CAN resolve
#   and logs — explicitly, per contract bucket — what it cannot. Coverage is
#   reported, never silently dropped.
#
# WHAT WE PULL ("all contracts, no exception" within Kite's reach):
#   * NIFTY option contracts (CE/PE) present in the dump whose expiry is within
#     [today - lookback_days, today + forward_buffer_days].
#   * BANKNIFTY option contracts (CE/PE), same window.
#   * BANKNIFTY current + next month FUTURES (needed for BB_V1; futures live in
#     the dump for the active + next contract).
#
# This module is PURE: it reads the dump and returns rows. It performs NO
# network calls and writes NO data. The fetcher (kite_backfill.py) consumes
# the list this returns.

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df


# How far back we attempt to backfill, and a small forward buffer so the
# current live weeklies/monthlies are included too.
DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_FORWARD_BUFFER_DAYS = 14


@dataclass
class BackfillToken:
    instrument_token: int
    tradingsymbol: str
    underlying: str          # 'NIFTY' | 'BANKNIFTY'
    instrument_type: str     # 'CE' | 'PE' | 'FUT'
    strike: float            # 0.0 for futures
    expiry: str              # ISO 'YYYY-MM-DD'


def _norm_expiry(val) -> Optional[date]:
    """instruments.csv expiry is already normalized to date by load_instruments_df,
    but be defensive: accept date, pandas Timestamp, or ISO string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, date):
        return val
    try:
        return pd.to_datetime(val, errors="coerce").date()
    except Exception:
        return None


def resolve_backfill_universe(
    *,
    underlyings: List[str],
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forward_buffer_days: int = DEFAULT_FORWARD_BUFFER_DAYS,
    today: Optional[date] = None,
) -> List[BackfillToken]:
    """
    Build the list of tokens to backfill.

    underlyings: subset of {'NIFTY','BANKNIFTY'}.
    Returns BackfillToken rows for every contract resolvable from the dump
    whose expiry falls in [today - lookback_days, today + forward_buffer_days].

    Coverage gaps (expired weeklies no longer in the dump) are LOGGED, not
    raised — the caller proceeds with what is resolvable.
    """
    today = today or date.today()
    win_lo = today - timedelta(days=lookback_days)
    win_hi = today + timedelta(days=forward_buffer_days)

    df = load_instruments_df(api_key, access_token)
    if df.empty:
        write_audit_log("[BACKFILL][UNIVERSE][FATAL] instruments dump empty — nothing resolvable")
        return []

    required = {"instrument_token", "tradingsymbol", "exchange",
                "name", "instrument_type", "strike", "expiry", "segment"}
    missing = required - set(df.columns)
    if missing:
        write_audit_log(f"[BACKFILL][UNIVERSE][FATAL] dump missing columns: {missing}")
        return []

    out: List[BackfillToken] = []

    for under in underlyings:
        under = under.upper()

        # ---- OPTIONS (CE/PE) -------------------------------------------------
        opt = df[
            (df["exchange"] == "NFO")
            & (df["name"] == under)
            & (df["instrument_type"].isin(["CE", "PE"]))
        ].copy()

        opt["_exp"] = opt["expiry"].map(_norm_expiry)
        opt_in_win = opt[
            opt["_exp"].notna()
            & (opt["_exp"] >= win_lo)
            & (opt["_exp"] <= win_hi)
        ]

        # Coverage diagnostics: how many distinct expiries does the window
        # THEORETICALLY contain (weeklies ~ every 7 days) vs how many we resolved?
        resolved_expiries = sorted({e.isoformat() for e in opt_in_win["_exp"] if e})
        write_audit_log(
            f"[BACKFILL][UNIVERSE][{under}] options resolvable: "
            f"{len(opt_in_win)} contracts across {len(resolved_expiries)} expiries "
            f"in window [{win_lo} .. {win_hi}]. "
            f"NOTE: expiries earlier than the dump's earliest active contract "
            f"are NOT resolvable via Kite and are excluded."
        )
        if resolved_expiries:
            write_audit_log(
                f"[BACKFILL][UNIVERSE][{under}] resolved expiries: {resolved_expiries}"
            )

        for _, r in opt_in_win.iterrows():
            exp = r["_exp"]
            out.append(BackfillToken(
                instrument_token=int(r["instrument_token"]),
                tradingsymbol=str(r["tradingsymbol"]),
                underlying=under,
                instrument_type=str(r["instrument_type"]),
                strike=float(r["strike"]) if pd.notna(r["strike"]) else 0.0,
                expiry=exp.isoformat(),
            ))

        # ---- FUTURES (BANKNIFTY only, for BB_V1) -----------------------------
        if under == "BANKNIFTY":
            fut = df[
                (df["exchange"] == "NFO")
                & (df["name"] == under)
                & (df["instrument_type"] == "FUT")
            ].copy()
            fut["_exp"] = fut["expiry"].map(_norm_expiry)
            fut_active = fut[fut["_exp"].notna() & (fut["_exp"] >= today)]
            # current + next month → the two nearest active futures
            fut_active = fut_active.sort_values("_exp").head(2)

            write_audit_log(
                f"[BACKFILL][UNIVERSE][{under}] futures resolvable: "
                f"{len(fut_active)} active contracts "
                f"({[e.isoformat() for e in fut_active['_exp']]})"
            )

            for _, r in fut_active.iterrows():
                out.append(BackfillToken(
                    instrument_token=int(r["instrument_token"]),
                    tradingsymbol=str(r["tradingsymbol"]),
                    underlying=under,
                    instrument_type="FUT",
                    strike=0.0,
                    expiry=r["_exp"].isoformat(),
                ))

    write_audit_log(
        f"[BACKFILL][UNIVERSE] TOTAL resolvable tokens: {len(out)} "
        f"for underlyings={underlyings}"
    )
    return out
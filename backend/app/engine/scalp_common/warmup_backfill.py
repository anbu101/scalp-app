# backend/app/engine/scalp_common/warmup_backfill.py
#
# Near-ATM warmup backfill (SHARED by SCALP V1/V2/V3).
# ============================================================================
# WHY
# ---
# Each machine warms its indicators from its OWN local market_timeline history
# (timeline_repo.fetch_recent_candles_for_warmup). A machine that wasn't running
# yesterday has fewer historical candles, so its EMA seeds DIFFERENTLY and never
# reconverges — two machines then emit the same contract's signal at different
# minutes (observed live: one entered 09:35, the other 09:37, because their
# EMA20 differed by ~5 points on the boundary candle).
#
# This module backfills the MISSING history for the near-ATM band of strikes
# from Zerodha's historical API into market_timeline, so every machine warms
# from the same candles and seeds the same EMA. It writes via the EXISTING
# insert_timeline_row (INSERT OR IGNORE on the unique (symbol,timeframe,ts)
# index), so a machine that already has the candles no-ops, and a machine that
# is short only fills its gaps.
#
# SCOPE (per the product decision)
# --------------------------------
#   - NEAR-ATM BAND ONLY: ±BAND_STRIKES around ATM (the liquid strikes that can
#     actually be selected and signal). Far-OTM strikes are intentionally NOT
#     backfilled — their history is sparse/illiquid and they rarely signal.
#   - LOOKBACK_DAYS calendar days back (enough for EMA20 to converge).
#
# HARD SAFETY CONTRACT (caller relies on this)
# --------------------------------------------
#   run_near_atm_backfill() NEVER raises. Every failure mode — no spot/ATM
#   reference, historical API error, rate-limit, missing token, sparse data,
#   DB error — is caught, logged, and skipped. The function returns a small
#   stats dict for logging, but the caller ignores it and proceeds to the
#   normal live-DB warmup + signal validation UNCHANGED. If this whole module
#   fails, behavior degrades to EXACTLY today's (local-history warmup).
#
#   The caller MUST wrap the call too (belt + suspenders):
#       try:
#           run_near_atm_backfill(...)
#       except Exception:
#           pass
#   ...even though this function already guarantees no-raise.
#
# RATE LIMITING
# -------------
#   Zerodha historical is rate-limited (~3 req/s). We sleep _THROTTLE_S between
#   calls and cap the number of contracts at _MAX_CONTRACTS so a pathological
#   universe can't issue hundreds of calls. On a machine that already has the
#   candles, each contract is detected as "already complete" and SKIPPED with
#   no API call — so a machine that ran yesterday makes ZERO calls.
#
# ISOLATION
# ---------
#   Writes ONLY to market_timeline via insert_timeline_row. Touches no trade
#   state, no GTT, no executor. Used by SCALP V1/V2/V3 warmup; BB/HA unaffected.
# ============================================================================

import time
from datetime import date, timedelta
from typing import List, Optional, Dict

from app.event_bus.audit_logger import write_audit_log

# Tunables (conservative; the caller can override via kwargs).
_BAND_STRIKES   = 10      # ±10 strikes around ATM (~42 contracts incl. CE+PE)
_STRIKE_STEP    = 50      # NIFTY weekly strike step
_LOOKBACK_DAYS  = 3       # calendar days of history to ensure
_THROTTLE_S     = 0.40    # sleep between historical calls (~<3 req/s)
_MAX_CONTRACTS  = 60      # hard cap on calls per run (safety)
_TIMEFRAME      = "1m"    # market_timeline timeframe label SCALP uses
_INTERVAL       = "minute"
_STRATEGY_VER   = "V1.9"  # same tag insert_timeline_row stores for live candles

# A trading day is ~375 one-minute candles (09:15–15:30). We treat a contract
# as "already sufficiently warmed" for a given day if it has at least this many
# rows for that day — avoids re-fetching when local history is already complete.
_PER_DAY_MIN    = 300


def _safe_log(msg: str) -> None:
    try:
        write_audit_log(msg)
    except Exception:
        pass


def _resolve_atm_strike(
    *,
    instruments_df,
    option_tokens: List[int],
    spot_ltp: Optional[float],
) -> Optional[int]:
    """
    Best-effort ATM strike at warmup time, WITHOUT requiring a live spot tick.

    Priority:
      1. If spot_ltp is provided & valid -> round to nearest strike step.
      2. Else -> derive from the option universe itself: the MEDIAN strike of
         the engine's tracked option tokens. The universe is built ±ATM_RANGE
         around spot at selection time, so its median strike is ~ATM. This is
         fully deterministic and needs no network call.

    Returns an int strike, or None if neither path works (caller then skips
    the whole backfill — fail-open).
    """
    # Path 1: explicit spot.
    if spot_ltp and spot_ltp > 0:
        try:
            return int(round(spot_ltp / _STRIKE_STEP) * _STRIKE_STEP)
        except Exception as e:
            _safe_log(f"[WARMUP_BF][ATM_SPOT_ERR] {e} — falling back to universe median")

    # Path 2: median strike of the tracked option universe.
    try:
        strikes = []
        sub = instruments_df[instruments_df["instrument_token"].isin(option_tokens)]
        for _, r in sub.iterrows():
            try:
                strikes.append(int(r["strike"]))
            except Exception:
                continue
        if not strikes:
            return None
        strikes.sort()
        median = strikes[len(strikes) // 2]
        return int(round(median / _STRIKE_STEP) * _STRIKE_STEP)
    except Exception as e:
        _safe_log(f"[WARMUP_BF][ATM_MEDIAN_ERR] {e}")
        return None


def _band_symbols(
    *,
    instruments_df,
    atm_strike: int,
    current_week_expiry,
    band_strikes: int,
) -> List[Dict]:
    """
    The CE+PE contracts within ±band_strikes of ATM for the current weekly
    expiry. Returns [{symbol, token}], best-effort; never raises.
    """
    out: List[Dict] = []
    try:
        lo = atm_strike - band_strikes * _STRIKE_STEP
        hi = atm_strike + band_strikes * _STRIKE_STEP
        sub = instruments_df[
            (instruments_df["segment"] == "NFO-OPT")
            & (instruments_df["name"] == "NIFTY")
            & (instruments_df["strike"] >= lo)
            & (instruments_df["strike"] <= hi)
        ]
        if current_week_expiry is not None:
            sub = sub[sub["expiry"] == current_week_expiry]
        for _, r in sub.iterrows():
            try:
                sym = str(r["tradingsymbol"])
                tok = int(r["instrument_token"])
                if sym.endswith("CE") or sym.endswith("PE"):
                    out.append({"symbol": sym, "token": tok})
            except Exception:
                continue
    except Exception as e:
        _safe_log(f"[WARMUP_BF][BAND_ERR] {e}")
    return out


def _count_existing_per_day(symbol: str, days: List[str]) -> Dict[str, int]:
    """
    How many 1m rows market_timeline already has for `symbol` on each given
    IST day. Used to skip contracts whose history is already complete (so a
    machine that ran yesterday makes no API call). Never raises.
    """
    counts = {d: 0 for d in days}
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        cur = conn.cursor()
        for d in days:
            # IST day bounds -> epoch. d is 'YYYY-MM-DD' (IST). Convert the IST
            # midnight to epoch by subtracting the +5:30 offset.
            try:
                y, m, dd = (int(x) for x in d.split("-"))
                import datetime as _dt
                ist_midnight = _dt.datetime(y, m, dd, 0, 0, 0)
                # epoch of IST midnight = naive-as-IST minus 5h30m
                start = int(ist_midnight.timestamp()) - 0  # see note below
            except Exception:
                continue
            # NOTE: we avoid TZ math fragility by counting via a date string
            # match on the stored ts using SQLite's datetime() in IST, which
            # mirrors how you query the table manually.
            row = cur.execute(
                """
                SELECT COUNT(*) FROM market_timeline
                WHERE symbol = ? AND timeframe = ?
                  AND date(datetime(ts,'unixepoch','+5 hours','+30 minutes')) = ?
                """,
                (symbol, _TIMEFRAME, d),
            ).fetchone()
            counts[d] = int(row[0]) if row else 0
    except Exception as e:
        _safe_log(f"[WARMUP_BF][COUNT_ERR] {symbol} {e}")
    return counts


def _target_days(lookback_days: int) -> List[str]:
    """IST calendar days to ensure (yesterday back lookback_days), excluding
    today (today is partial and fills live). Returns ['YYYY-MM-DD', ...]."""
    out = []
    today = date.today()
    for i in range(1, lookback_days + 1):
        out.append((today - timedelta(days=i)).isoformat())
    return out


def run_near_atm_backfill(
    *,
    kite_data,
    instruments_df,
    option_tokens: List[int],
    current_week_expiry,
    spot_ltp: Optional[float] = None,
    band_strikes: int = _BAND_STRIKES,
    lookback_days: int = _LOOKBACK_DAYS,
) -> Dict:
    """
    Ensure the near-ATM band has LOOKBACK_DAYS of 1m history in market_timeline.

    NEVER RAISES. Returns a stats dict for logging only; the caller ignores it
    and proceeds to normal warmup + signal validation regardless.

      kite_data           : authenticated data KiteConnect (historical_data)
      instruments_df      : the engine's instruments dataframe
      option_tokens       : the engine's full tracked option-token list
      current_week_expiry : the weekly expiry (to scope band symbols)
      spot_ltp            : optional NIFTY spot; if None, ATM is derived from
                            the universe median (no network needed)
    """
    stats = {"attempted": 0, "skipped_complete": 0, "fetched": 0,
             "inserted": 0, "errors": 0}
    try:
        if kite_data is None:
            _safe_log("[WARMUP_BF] no kite_data — skipping backfill (warmup unaffected)")
            return stats

        atm = _resolve_atm_strike(
            instruments_df=instruments_df,
            option_tokens=option_tokens,
            spot_ltp=spot_ltp,
        )
        if atm is None:
            _safe_log("[WARMUP_BF] could not resolve ATM — skipping backfill (warmup unaffected)")
            return stats

        symbols = _band_symbols(
            instruments_df=instruments_df,
            atm_strike=atm,
            current_week_expiry=current_week_expiry,
            band_strikes=band_strikes,
        )
        if not symbols:
            _safe_log(f"[WARMUP_BF] no band symbols around ATM={atm} — skipping")
            return stats

        days = _target_days(lookback_days)
        _safe_log(
            f"[WARMUP_BF] start ATM={atm} band=±{band_strikes} "
            f"contracts={len(symbols)} days={days}"
        )

        try:
            from app.db.timeline_repo import insert_timeline_row
            from app.db.sqlite import get_conn
        except Exception as e:
            _safe_log(f"[WARMUP_BF][IMPORT_ERR] {e} — skipping backfill")
            return stats

        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)

        calls = 0
        for c in symbols:
            if calls >= _MAX_CONTRACTS:
                _safe_log(f"[WARMUP_BF] hit _MAX_CONTRACTS={_MAX_CONTRACTS} — stopping")
                break

            symbol = c["symbol"]
            token  = c["token"]
            stats["attempted"] += 1

            # Skip if this contract already has complete history for all target
            # days (the common case on a machine that ran yesterday → no API call).
            try:
                have = _count_existing_per_day(symbol, days)
                if have and all(have.get(d, 0) >= _PER_DAY_MIN for d in days):
                    stats["skipped_complete"] += 1
                    continue
            except Exception as e:
                # If the count fails, fall through and attempt the fetch — a
                # redundant fetch is harmless (INSERT OR IGNORE dedups).
                _safe_log(f"[WARMUP_BF][COUNT_FALLTHROUGH] {symbol} {e}")

            # Fetch history for this contract (the only network call).
            try:
                candles = kite_data.historical_data(
                    instrument_token=token,
                    from_date=start_date,
                    to_date=end_date,
                    interval=_INTERVAL,
                )
                calls += 1
                stats["fetched"] += 1
            except Exception as e:
                stats["errors"] += 1
                _safe_log(f"[WARMUP_BF][HIST_ERR] {symbol} token={token} {e} — skip")
                time.sleep(_THROTTLE_S)
                continue

            # Insert each candle via the EXISTING idempotent writer.
            if candles:
                conn = None
                try:
                    conn = get_conn()
                except Exception:
                    conn = None
                for k in candles:
                    try:
                        ts = int(k["date"].timestamp())
                        insert_timeline_row({
                            "symbol": symbol,
                            "timeframe": _TIMEFRAME,
                            "ts": ts,
                            "open": k["open"],
                            "high": k["high"],
                            "low": k["low"],
                            "close": k["close"],
                            "strategy_version": _STRATEGY_VER,
                        })
                        stats["inserted"] += 1
                    except Exception:
                        # one bad candle never aborts the contract
                        continue
                # insert_timeline_row uses get_conn() internally and does not
                # commit; commit once per contract.
                try:
                    if conn is not None:
                        conn.commit()
                except Exception as e:
                    _safe_log(f"[WARMUP_BF][COMMIT_ERR] {symbol} {e}")

            time.sleep(_THROTTLE_S)

        _safe_log(
            f"[WARMUP_BF] done attempted={stats['attempted']} "
            f"skipped_complete={stats['skipped_complete']} fetched={stats['fetched']} "
            f"inserted={stats['inserted']} errors={stats['errors']}"
        )
        return stats

    except Exception as e:
        # The whole-function guard. Nothing here can ever propagate.
        _safe_log(f"[WARMUP_BF][FATAL_GUARD] {e!r} — warmup/signal flow UNAFFECTED")
        return stats
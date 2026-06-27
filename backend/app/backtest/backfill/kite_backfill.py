# backend/app/backtest/backfill/kite_backfill.py
#
# Backfills 1-minute OHLC for every resolvable NIFTY/BANKNIFTY option contract
# (+ BANKNIFTY futures) in the lookback window, into backtest_candles_1m.
#
# SESSION
#   Uses the app's existing auth. Prefers the DATA session
#   (access_token_data.json) so heavy historical pulling does not share
#   rate-limit budget with the trade session; falls back to get_kite() (trade).
#   If neither yields a live KiteConnect, the backfill aborts with a clear
#   message — the user must be logged in.
#
# RATE LIMITS (Kite)
#   * historical_data() is limited (~3 req/sec). We sleep THROTTLE_S between
#     calls and back off on 429 / "Too many requests".
#   * One call = one instrument_token for one [from,to] at interval='minute'.
#   * Kite caps minute-data span per request (~60 days). The window here is
#     <= ~74 days (60 lookback + 14 buffer), so we CHUNK into <=60-day spans
#     to stay safely under the cap.
#
# IDEMPOTENT WRITE
#   INSERT ... ON CONFLICT(instrument_token, ts) DO UPDATE — re-running
#   overwrites matching rows with fresh values and adds new ones. NEVER deletes.
#   Safe to re-run after a mid-pull failure; it fills the gaps.
#
# ISOLATION
#   Writes ONLY to backtest_candles_1m. Reads ONLY today's instruments dump
#   (via token_universe). Touches no live table and no live code path.

from __future__ import annotations

import time
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import APP_HOME
from app.config.zerodha_credentials_store import load_credentials
from app.brokers.zerodha_auth import (
    get_kite,
    load_access_token,
)
from kiteconnect import KiteConnect

from app.backtest.backfill.token_universe import (
    resolve_backfill_universe,
    BackfillToken,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_FORWARD_BUFFER_DAYS,
)

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
THROTTLE_S          = 0.40    # base gap between historical calls (~2.5 req/s)
MAX_RETRIES         = 4       # per token, on transient / 429
BACKOFF_BASE_S      = 2.0     # exponential backoff base on 429
CHUNK_DAYS          = 55      # minute-data span per request (< Kite ~60d cap)
INTERVAL            = "minute"

# IST is a FIXED offset in this app (never runtime tz). Kite returns tz-aware
# datetimes in IST; we convert to epoch seconds using the same fixed offset.
IST_OFFSET_SECONDS  = 5 * 3600 + 30 * 60   # +05:30


# --------------------------------------------------------------------------
# DB path + schema bootstrap
# --------------------------------------------------------------------------
def _backtest_db_path() -> Path:
    # Co-locate with app state; separate file keeps the backtest corpus from
    # bloating the live trading DB. (A single shared DB is also fine — change
    # this one function if you prefer the main sqlite file.)
    p = APP_HOME / "backtest" / "backtest.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "repo" / "schema.sql").read_text()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_backtest_db_path()))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(_schema_sql())
    # Self-heal additive schema changes on pre-existing DBs (reuses the repo's
    # column guard so there is one source of truth for expected columns).
    try:
        from app.backtest.repo.backtest_repo import _self_heal_columns
        _self_heal_columns(conn)
    except Exception:
        pass
    return conn


# --------------------------------------------------------------------------
# Session resolution — prefer DATA token, fall back to trade get_kite()
# --------------------------------------------------------------------------
def _resolve_kite() -> Optional[KiteConnect]:
    # Prefer the explicit DATA token if present and valid.
    try:
        from app.brokers.zerodha_manager import load_access_token as _mgr_load
        api_key = (load_credentials() or {}).get("api_key")
        data_tok = _mgr_load("data")
        if api_key and data_tok:
            k = KiteConnect(api_key=api_key)
            k.set_access_token(data_tok)
            # cheap validity probe
            k.profile()
            write_audit_log("[BACKFILL] Using DATA session for historical pulls")
            return k
    except Exception as e:
        write_audit_log(f"[BACKFILL] DATA session unavailable ({e!r}) — trying trade session")

    # Fall back to the validated trade session.
    k = get_kite()
    if k is not None:
        write_audit_log("[BACKFILL] Using TRADE session for historical pulls")
    return k


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def _to_epoch_ist(dt: datetime) -> int:
    """Kite returns tz-aware IST datetimes (e.g. 2025-01-09 09:15:00+05:30).
    Convert to epoch seconds deterministically using the app's FIXED IST
    offset, ignoring the machine's local tz entirely.

    epoch = (wall-clock seconds since 1970, read as IST) - IST_OFFSET
    """
    naive = dt.replace(tzinfo=None)   # drop tz; treat the wall-clock as IST
    seconds_since_epoch_as_if_utc = int((naive - datetime(1970, 1, 1)).total_seconds())
    return seconds_since_epoch_as_if_utc - IST_OFFSET_SECONDS


def _date_chunks(lo: date, hi: date, span_days: int) -> List[Tuple[date, date]]:
    out = []
    cur = lo
    while cur <= hi:
        end = min(cur + timedelta(days=span_days - 1), hi)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# Upsert
# --------------------------------------------------------------------------
_UPSERT = """
INSERT INTO backtest_candles_1m
  (instrument_token, ts, underlying, tradingsymbol, instrument_type,
   strike, expiry, open, high, low, close, volume, oi)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(instrument_token, ts) DO UPDATE SET
  open=excluded.open, high=excluded.high, low=excluded.low,
  close=excluded.close, volume=excluded.volume, oi=excluded.oi
"""


def _write_candles(conn: sqlite3.Connection, tok: BackfillToken, rows: list) -> int:
    payload = []
    for r in rows:
        # kite historical row: dict with date, open, high, low, close, volume, oi
        ts = _to_epoch_ist(r["date"])
        payload.append((
            tok.instrument_token, ts, tok.underlying, tok.tradingsymbol,
            tok.instrument_type, tok.strike, tok.expiry,
            float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
            int(r.get("volume") or 0), int(r.get("oi") or 0),
        ))
    if payload:
        conn.executemany(_UPSERT, payload)
        conn.commit()
    return len(payload)


# --------------------------------------------------------------------------
# Per-token fetch with retry/backoff
# --------------------------------------------------------------------------
def _fetch_token(kite: KiteConnect, tok: BackfillToken,
                 lo: date, hi: date) -> Tuple[list, Optional[str]]:
    """Returns (rows, error). rows is the concatenated historical data across
    chunks; error is a short string if the token ultimately failed."""
    all_rows: list = []
    for (clo, chi) in _date_chunks(lo, hi, CHUNK_DAYS):
        attempt = 0
        while True:
            try:
                rows = kite.historical_data(
                    instrument_token=tok.instrument_token,
                    from_date=clo.strftime("%Y-%m-%d 00:00:00"),
                    to_date=chi.strftime("%Y-%m-%d 23:59:59"),
                    interval=INTERVAL,
                    oi=True,
                )
                all_rows.extend(rows)
                time.sleep(THROTTLE_S)
                break
            except Exception as e:
                msg = str(e)
                is_rate = ("Too many requests" in msg) or ("429" in msg)
                attempt += 1
                if attempt > MAX_RETRIES:
                    return all_rows, f"{type(e).__name__}: {msg[:120]}"
                sleep_s = (BACKOFF_BASE_S ** attempt) if is_rate else THROTTLE_S * 2
                write_audit_log(
                    f"[BACKFILL][RETRY] {tok.tradingsymbol} chunk[{clo}..{chi}] "
                    f"attempt={attempt} rate={is_rate} sleep={sleep_s:.1f}s ERR={msg[:80]}"
                )
                time.sleep(sleep_s)
    return all_rows, None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def run_backfill(
    *,
    underlyings: List[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forward_buffer_days: int = DEFAULT_FORWARD_BUFFER_DAYS,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Orchestrates the full backfill. Blocking; intended to be called from a
    BACKGROUND job (the UI route spawns a thread and reports progress via
    progress_cb). Returns a summary dict.

    progress_cb receives {done, total, ok, failed, current, eta_s} after each
    token so the UI can render a live progress bar.
    """
    started = time.time()
    kite = _resolve_kite()
    if kite is None:
        msg = "No live Kite session (data or trade). Log in to Zerodha, then retry."
        write_audit_log(f"[BACKFILL][ABORT] {msg}")
        return {"status": "error", "error": msg, "ok": 0, "failed": 0, "total": 0}

    today = date.today()
    lo = today - timedelta(days=lookback_days)
    hi = today + timedelta(days=forward_buffer_days)

    universe = resolve_backfill_universe(
        underlyings=underlyings,
        lookback_days=lookback_days,
        forward_buffer_days=forward_buffer_days,
        today=today,
    )
    total = len(universe)
    if total == 0:
        return {"status": "error", "error": "Empty universe — nothing resolvable.",
                "ok": 0, "failed": 0, "total": 0}

    conn = _connect()
    ok = 0
    failed = 0
    failures: List[str] = []
    candles_written = 0

    write_audit_log(
        f"[BACKFILL] START underlyings={underlyings} tokens={total} "
        f"window=[{lo}..{hi}] interval={INTERVAL}"
    )

    for i, tok in enumerate(universe, start=1):
        rows, err = _fetch_token(kite, tok, lo, hi)
        if err is not None:
            failed += 1
            failures.append(f"{tok.tradingsymbol}: {err}")
            write_audit_log(f"[BACKFILL][FAIL] {tok.tradingsymbol} token={tok.instrument_token} {err}")
        else:
            n = _write_candles(conn, tok, rows)
            candles_written += n
            ok += 1

        if progress_cb is not None:
            elapsed = time.time() - started
            per = elapsed / i
            eta = per * (total - i)
            progress_cb({
                "done": i, "total": total, "ok": ok, "failed": failed,
                "current": tok.tradingsymbol, "eta_s": round(eta),
                "candles_written": candles_written,
            })

    conn.close()
    summary = {
        "status": "done",
        "total": total,
        "ok": ok,
        "failed": failed,
        "candles_written": candles_written,
        "elapsed_s": round(time.time() - started),
        "failures": failures[:50],   # cap for payload size; full list in audit log
    }
    write_audit_log(
        f"[BACKFILL] DONE ok={ok} failed={failed} candles={candles_written} "
        f"elapsed={summary['elapsed_s']}s"
    )
    return summary
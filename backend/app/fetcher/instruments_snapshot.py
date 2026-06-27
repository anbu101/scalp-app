# backend/app/fetcher/instruments_snapshot.py
#
# DAILY DATED INSTRUMENT SNAPSHOT  (Fix 1)
#
# WHY: Kite flushes an option contract's instrument_token at expiry, and
# kite.instruments() only ever returns CURRENTLY-ACTIVE contracts. So once a
# weekly expires its token is gone and its historical candles become
# unfetchable. The ONLY way to backfill expired weeklies later is to have
# cached the instrument master WHILE they were live. The live app keeps a single
# OVERWRITING instruments.csv (24h), which loses this history.
#
# This module writes a DATED, never-overwritten snapshot of the NFO instrument
# master once per trading day:
#     ~/.scalp-app/state/instruments_history/NFO_YYYY-MM-DD.csv
# A few hundred KB/day. From the day this runs, every future backtest can
# reconstruct the true per-day weekly-expiry universe and backfill the correct
# contracts — permanently closing the gap that made deep-history backtests
# unfaithful.
#
# SAFETY: pure-additive. Idempotent (skips if today's file exists). Never
# raises into the caller — a snapshot failure logs and returns. Does NOT touch
# the live instruments.csv or any trading path.

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from app.event_bus.audit_logger import write_audit_log

SNAPSHOT_DIR = Path.home() / ".scalp-app" / "state" / "instruments_history"


def _today_path(d: date) -> Path:
    return SNAPSHOT_DIR / f"NFO_{d.isoformat()}.csv"


def snapshot_instruments_for_today(kite, *, today: date | None = None) -> bool:
    """Write today's NFO instrument master to a dated CSV. Returns True if a
    file was written (or already existed), False on failure. Never raises."""
    d = today or date.today()
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _today_path(d)
        if path.exists():
            # Idempotent: already captured today.
            return True
        if kite is None:
            write_audit_log(
                "[INSTR_SNAPSHOT] no kite handle — cannot snapshot instruments today"
            )
            return False

        data = kite.instruments("NFO")   # NFO master only (options + futures)
        if not data:
            write_audit_log("[INSTR_SNAPSHOT] kite.instruments('NFO') returned empty")
            return False

        df = pd.DataFrame(data)
        # Write atomically: temp then replace, so a reader never sees a partial.
        tmp = path.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(path)
        write_audit_log(
            f"[INSTR_SNAPSHOT] wrote {len(df)} NFO instruments → {path.name} "
            f"(dated snapshot for future backtest expiry reconstruction)"
        )
        return True
    except Exception as e:
        write_audit_log(f"[INSTR_SNAPSHOT][ERROR] failed to snapshot instruments: {e!r}")
        return False


def snapshot_job_factory(zerodha_manager):
    """Return a zero-arg job callable for APScheduler that snapshots using the
    data/trade kite handle from the zerodha_manager at call time."""
    def _job():
        try:
            kite = (zerodha_manager.get_data_kite()
                    or zerodha_manager.get_trade_kite())
        except Exception as e:
            write_audit_log(f"[INSTR_SNAPSHOT][ERROR] no kite at job time: {e!r}")
            return
        snapshot_instruments_for_today(kite)
    return _job
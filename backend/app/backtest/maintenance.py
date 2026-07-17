# backend/app/backtest/maintenance.py
#
# ── EXPIRY_ERA_STARTUP ── one-time, self-gating corpus repair.
#
# WHY: dhan_backfill used to synthesize expiry+tradingsymbol with a
# Tuesday-only calendar, mislabeling every pre-Sep-2025 NIFTY option row
# (data correct — Dhan's front weekly is the true contract — labels wrong).
# The era-aware calendar makes the backtest EXPECT Thursday expiries there,
# so an unrepaired corpus fails closed as days_uncovered; and POSITIONAL
# trades would mis-track contract identity across the weekly roll.
#
# DESIGN:
#   * MARKER-GATED: bt_meta['expiry_era_relabeled']='1' → instant no-op on
#     every subsequent boot. First boot on an old corpus pays the one-time
#     cost (minutes on a multi-year corpus; loud progress logs).
#   * RESUMABLE: commits per (day × contract) group. If the app is killed
#     mid-repair, every committed group is already correct; the next boot
#     finds the marker absent and finishes the remainder (idempotent —
#     already-correct groups are skipped by comparison).
#   * FAIL-SAFE: the startup hook wraps this in try/except. A repair failure
#     must never block the trading app; the marker stays unset, the repair
#     retries next boot, and unrepaired old years stay days_uncovered
#     (fail closed) meanwhile.
#   * Fresh/empty corpus (or one with no pre-era option rows): marker is set
#     immediately — all future backfills use the era-aware calendar and are
#     born correct.
#
# Shared by: app startup (api_server.py) and the manual CLI
# (app.backtest.tools.relabel_expiry_era). One implementation, two entries.

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from app.backtest.engine.expiry_calendar import (
    LAST_THURSDAY_EXPIRY, expected_expiry_for_day,
)
from app.backtest.util.nifty_symbol import build_nifty_symbol
from app.utils.app_paths import APP_HOME

IST = timezone(timedelta(hours=5, minutes=30))
MARKER_KEY = "expiry_era_relabeled"


def _default_db_path() -> Path:
    return APP_HOME / "backtest" / "backtest.db"


def _cutoff_ts() -> int:
    d = LAST_THURSDAY_EXPIRY + timedelta(days=5)
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


def _day_bounds_utc(d: date) -> tuple[int, int]:
    start = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return start, start + 86400


def _marker_get(cur) -> bool:
    cur.execute("CREATE TABLE IF NOT EXISTS bt_meta("
                "key TEXT PRIMARY KEY, value TEXT)")
    row = cur.execute("SELECT value FROM bt_meta WHERE key=?",
                      (MARKER_KEY,)).fetchone()
    return bool(row and row[0] == "1")


def _marker_set(conn) -> None:
    conn.execute("INSERT INTO bt_meta(key, value) VALUES (?, '1') "
                 "ON CONFLICT(key) DO UPDATE SET value='1'", (MARKER_KEY,))
    conn.commit()


def ensure_expiry_era_labels(db_path: Optional[str] = None,
                             dry_run: bool = False,
                             log: Callable[[str], None] = print) -> dict:
    """Repair pre-Sep-2025 expiry/tradingsymbol labels once. Returns a
    summary dict {status, groups, rows}. Safe to call on every boot."""
    path = Path(db_path).expanduser() if db_path else _default_db_path()
    if not path.exists():
        return {"status": "no_db", "groups": 0, "rows": 0}

    conn = sqlite3.connect(str(path), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()
    try:
        if not dry_run and _marker_get(cur):
            return {"status": "already_done", "groups": 0, "rows": 0}

        tables = []
        for t in ("backtest_candles_1m", "backtest_candles_1s"):
            try:
                if cur.execute(
                    f"SELECT 1 FROM {t} WHERE instrument_type IN ('CE','PE') "
                    f"LIMIT 1").fetchone():
                    tables.append(t)
            except sqlite3.OperationalError:
                continue
        if not tables:
            if not dry_run:
                _marker_set(conn)
            return {"status": "empty_corpus", "groups": 0, "rows": 0}

        cutoff = _cutoff_ts()
        total_groups = total_rows = 0
        for t in tables:
            groups = cur.execute(f"""
                SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes'),
                       tradingsymbol, strike, instrument_type, expiry
                FROM {t}
                WHERE underlying='NIFTY' AND instrument_type IN ('CE','PE')
                  AND ts < ?
                ORDER BY 1""", (cutoff,)).fetchall()
            wrong = []
            for day_s, sym, strike, side, stored_exp in groups:
                want = expected_expiry_for_day(date.fromisoformat(day_s))
                if stored_exp != want.isoformat():
                    wrong.append((day_s, sym, strike, side, want))
            if not wrong:
                continue
            log(f"[EXPIRY_ERA] {t}: {len(wrong)} mislabeled "
                f"(day × contract) groups to repair"
                f"{' (dry run)' if dry_run else ''}")
            for i, (day_s, sym, strike, side, want) in enumerate(wrong, 1):
                lo, hi = _day_bounds_utc(date.fromisoformat(day_s))
                new_sym = build_nifty_symbol(want, strike, side)
                if dry_run:
                    n = cur.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE tradingsymbol=? "
                        f"AND ts>=? AND ts<?", (sym, lo, hi)).fetchone()[0]
                else:
                    cur.execute(
                        f"UPDATE {t} SET expiry=?, tradingsymbol=? "
                        f"WHERE tradingsymbol=? AND ts>=? AND ts<?",
                        (want.isoformat(), new_sym, sym, lo, hi))
                    n = cur.rowcount
                    conn.commit()          # resumable: per-group durability
                total_rows += n
                total_groups += 1
                if i % 500 == 0 or i == len(wrong):
                    log(f"[EXPIRY_ERA] {t}: {i}/{len(wrong)} groups "
                        f"({total_rows} rows so far)")

        if not dry_run:
            _marker_set(conn)
        status = "repaired" if total_groups else "labels_already_correct"
        log(f"[EXPIRY_ERA] {'DRY RUN — ' if dry_run else ''}{status}: "
            f"{total_groups} groups / {total_rows} rows"
            f"{'' if dry_run else ' — marker set, future boots are no-ops'}")
        return {"status": status, "groups": total_groups, "rows": total_rows}
    finally:
        conn.close()
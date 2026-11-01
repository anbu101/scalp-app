# backend/app/backtest/spot_stamp_repair.py
#
# ── SPOT_STAMP_REPAIR 20260818 ── one-time, self-gating corpus repair.
#
# WHY: dhan_spot_backfill.detect_stamp_offset() inferred 1-minute stamp
# semantics from a 5-MINUTE probe. Dhan's 5m series starts 09:20, so the
# code declared "close-anchored" and subtracted 60s from every 1m spot bar.
# The 1m series was already START-anchored, so every SPOT row landed ONE
# MINUTE EARLY. Verified 2026-08-18 against Kite: corpus ts + 60 == kite ts
# on 370/376 bars, byte-identical OHLC.
#
# IMPACT (why this is not cosmetic):
#   * resample_spot() drops bars before session start, so each day's REAL
#     09:15 candle (stamped 09:14) was discarded from every backtest — C1
#     became the SECOND minute of the session.
#   * Every absolute-time gate (entry cutoff, EOD boundary, time-of-day
#     filters) was evaluated one minute off.
#   Affected runners: GC, IC, PST Sell, PST Hedge, TMA V1, TMA V2.
#   Option-only runners (BB, HA, SCALP*, TSG) read no SPOT rows.
#
# DESIGN (mirrors maintenance.ensure_expiry_era_labels):
#   * MARKER-GATED: bt_meta['spot_stamp_repaired']='1' → instant no-op.
#   * PER-DAY EVIDENCE, NOT BLIND SHIFT: a day is repaired only when its
#     first SPOT bar is 09:14 IST. Days already starting 09:15 are left
#     untouched, so a corrected corpus (or a friend's fresh backfill) is a
#     no-op and re-running is harmless.
#   * PK-SAFE: (instrument_token, ts) is the primary key, so an in-place
#     `ts = ts + 60` can collide mid-statement. Each day is rewritten via a
#     read → delete → insert inside ONE transaction.
#   * RESUMABLE: commits per day. A kill mid-repair leaves committed days
#     correct; the next boot finishes the rest (already-correct days skip).
#   * FAIL-SAFE: the startup hook wraps this in try/except. A repair
#     failure must never block the trading app.

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from app.utils.app_paths import APP_HOME

IST = timezone(timedelta(hours=5, minutes=30))
MARKER_KEY = "spot_stamp_repaired_v2"   # v2: window-based detection
SESSION_OPEN_HM = (9, 15)
SHIFT_S = 60          # corpus is 60s EARLY -> move forward


def _default_db_path() -> Path:
    return APP_HOME / "backtest" / "backtest.db"


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


def ensure_spot_stamps(db_path: Optional[str] = None,
                       dry_run: bool = False,
                       log: Callable[[str], None] = print) -> dict:
    """Shift mis-stamped SPOT rows forward by 60s, one day at a time.
    Returns {status, days_checked, days_repaired, rows_moved}."""
    path = Path(db_path).expanduser() if db_path else _default_db_path()
    if not path.exists():
        return {"status": "no_db", "days_checked": 0,
                "days_repaired": 0, "rows_moved": 0}

    conn = sqlite3.connect(str(path), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()
    days_checked = days_repaired = rows_moved = 0
    try:
        if not dry_run and _marker_get(cur):
            return {"status": "already_done", "days_checked": 0,
                    "days_repaired": 0, "rows_moved": 0}

        has_spot = cur.execute(
            "SELECT 1 FROM backtest_candles_1m "
            "WHERE instrument_type='SPOT' LIMIT 1").fetchone()
        if not has_spot:
            if not dry_run:
                _marker_set(conn)      # nothing to repair; future fills are born correct
            return {"status": "no_spot_rows", "days_checked": 0,
                    "days_repaired": 0, "rows_moved": 0}

        days = [r[0] for r in cur.execute(
            "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d "
            "FROM backtest_candles_1m WHERE instrument_type='SPOT' "
            "ORDER BY d")]
        log(f"[SPOT_STAMP] scanning {len(days)} spot day(s)")

        for d in days:
            days_checked += 1
            ds = int(datetime.fromisoformat(d).replace(tzinfo=IST).timestamp())
            de = ds + 86400
            # ── v2 DETECTION ── key off the first bar INSIDE the opening
            # window (09:00–09:30), not MIN(ts) for the day. The corpus
            # carries stray out-of-session prints (observed 05:38, 07:05,
            # 13:44, 18:14, 00:00); v1 keyed on MIN(ts), so one junk row
            # masked the real session start and left 340 days unrepaired.
            win_lo = ds + (9 * 60) * 60          # 09:00 IST
            win_hi = ds + (9 * 60 + 30) * 60     # 09:30 IST
            row = cur.execute(
                "SELECT MIN(ts) FROM backtest_candles_1m "
                "WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                (win_lo, win_hi)).fetchone()
            if not row or not row[0]:
                log(f"[SPOT_STAMP] {d}: no bars in 09:00-09:30 — SKIPPED "
                    f"(partial/anomalous day, manual review)")
                continue
            first = datetime.fromtimestamp(int(row[0]), IST)
            if (first.hour, first.minute) == SESSION_OPEN_HM:
                continue                      # already correct
            if (first.hour, first.minute) != (9, 14):
                log(f"[SPOT_STAMP] {d}: opening-window first bar "
                    f"{first:%H:%M} — neither 09:14 nor 09:15; SKIPPED "
                    f"(manual review)")
                continue

            # Shift only rows from the shifted session grid: 09:14 through
            # 15:28 inclusive (they become 09:15..15:29). Out-of-session
            # strays are NOT moved — their stamps are a separate defect and
            # guessing at them would corrupt real data.
            sess_lo = ds + (9 * 60 + 14) * 60
            sess_hi = ds + (15 * 60 + 29) * 60      # exclusive
            src = cur.execute(
                "SELECT instrument_token, ts, underlying, tradingsymbol, "
                "instrument_type, strike, expiry, open, high, low, close, "
                "volume, oi FROM backtest_candles_1m "
                "WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                (sess_lo, sess_hi)).fetchall()
            if not src:
                continue
            if dry_run:
                days_repaired += 1
                rows_moved += len(src)
                continue

            shifted = [(r[0], r[1] + SHIFT_S) + tuple(r[2:]) for r in src]
            try:
                conn.execute("BEGIN")
                conn.execute(
                    "DELETE FROM backtest_candles_1m "
                    "WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                    (sess_lo, sess_hi))
                conn.executemany(
                    "INSERT OR REPLACE INTO backtest_candles_1m "
                    "(instrument_token, ts, underlying, tradingsymbol, "
                    " instrument_type, strike, expiry, open, high, low, "
                    " close, volume, oi) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", shifted)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            days_repaired += 1
            rows_moved += len(shifted)
            if days_repaired % 100 == 0:
                log(f"[SPOT_STAMP] repaired {days_repaired} day(s)…")

        if not dry_run:
            _marker_set(conn)
        log(f"[SPOT_STAMP] done: checked={days_checked} "
            f"repaired={days_repaired} rows_moved={rows_moved}"
            + (" (DRY RUN — nothing written)" if dry_run else ""))
        return {"status": "dry_run" if dry_run else "repaired",
                "days_checked": days_checked,
                "days_repaired": days_repaired,
                "rows_moved": rows_moved}
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":          # manual CLI:
    import argparse                 #   python3 -m app.backtest.spot_stamp_repair --dry-run
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(ensure_spot_stamps(a.db, dry_run=a.dry_run))
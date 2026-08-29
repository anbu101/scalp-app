# backend/app/backtest/util/corpus_health.py
#
# ── CORPUS_FRAME_REPAIR_20260828 ──
# Detect and repair PRICE-FRAME SPLITS in a per-stock corpus.
#
# THE BUG CLASS (found on HDFCBANK, 2026-08-28)
#   stock_backfill takes `strike` from Dhan's rolling-ATM response and COMPOSES
#   the tradingsymbol from it (build_stock_symbol). After a split or bonus,
#   Dhan can serve the same (day, expiry) in two frames — as-traded strikes and
#   post-adjustment strikes. Different strike -> different composed symbol ->
#   different surrogate token, so the delete-then-insert on (tradingsymbol, ts)
#   CANNOT collapse them. Both ladders persist, silently.
#
#   Worse, Dhan's SPOT series for the same stock is back-adjusted end to end.
#   So the corpus ends up with adjusted spot sitting against as-traded strikes:
#   an ATM ladder keyed on spot then selects from whichever thin ladder happens
#   to sit near the adjusted price and never raises a thing. A plausible equity
#   curve built on the wrong contracts is the worst failure mode we have.
#
# WHAT REPAIR DOES
#   Keeps the AS-TRADED option frame (it is the dense, real, exchange chain),
#   multiplies pre-ex-date SPOT OHLC by the adjustment factor to put spot back
#   into that same frame, and deletes the duplicate adjusted-frame rows plus
#   any out-of-band junk. Nothing is fabricated: every surviving row is a row
#   Dhan actually returned.
#
# WHAT REPAIR DOES NOT DO
#   It does not make the corpus safe to trade ACROSS the ex-date. In the
#   as-traded frame the underlying genuinely halves overnight — that is what
#   the market did. Any strategy carrying a position through that night books a
#   fake gap. Repair stamps `frame_break_dates` into corpus_meta so a runner
#   guard can fail closed on it; wiring that guard is a separate decision.
#
# IDEMPOTENT — the repair fence lives in corpus_meta. Re-running is refused,
# which matters here because doubling spot twice would quadruple it.
#
# CLI
#   python3 -m app.backtest.util.corpus_health --scan HDFCBANK
#   python3 -m app.backtest.util.corpus_health --repair HDFCBANK \
#           --ex-date 2025-08-26 --factor 2 --dry-run
#   python3 -m app.backtest.util.corpus_health --repair HDFCBANK \
#           --ex-date 2025-08-26 --factor 2

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

FENCE = "CORPUS_FRAME_REPAIR_20260828"

# keep-band, as a multiple of the day's spot IN THE TARGET FRAME.
# The adjusted duplicate sits at ~1/factor (0.5 for a 1:1 bonus), so the lower
# bound only has to clear that; 0.60 is generous to real deep-ITM rows while
# still excluding it. Upper 1.45 excludes the observed junk (ratios 1.6–1.9).
# A rolling ATM+-10 ladder spans roughly +-15% of spot; observed real HDFCBANK
# ladders sat at 0.90..1.13. [0.75, 1.30] is generous to that and — crucially —
# NARROW ENOUGH that the two frames leave a gap between them (see frame_band).
FRAME_LO = 0.75
FRAME_HI = 1.30
MIN_KEEP_SHARE = 0.25       # repair must retain at least this share pre-ex-date
MAX_PRUNE_SHARE = 0.05      # prune above this is a frame split, not junk
KEEP_LO = FRAME_LO          # back-compat aliases
KEEP_HI = FRAME_HI


def frame_band(factor: float) -> tuple:
    """(native_hi, scaled_lo) — the two frames and the GAP between them.

    Returns (FRAME_LO, FRAME_HI) for the frame spot is in, and
    (factor*FRAME_LO, factor*FRAME_HI) for the other one. Everything between
    FRAME_HI and factor*FRAME_LO is JUNK: too far from spot to be a real ATM
    ladder row, too far from factor*spot to belong to the other frame.

    Two bugs lived here, both now fixed and both asserted in tests:
      1. The first cut used [LO,HI] and [factor*LO, factor*HI] with LO=0.60,
         HI=1.45. Those OVERLAP on [1.20, 1.45] for factor 2, so rows were
         counted twice, `junk` went negative and single-frame months were
         flagged DUAL.
      2. Splitting at the geometric divider (sqrt(factor) = 1.414) removed the
         overlap but left no gap, so genuinely bad rows at ratio 1.42-1.44 —
         2738 of them on HDFCBANK, with strike labels that did not match their
         prices — were still filed as `scaled` rather than junk.
    Narrowing to [0.75, 1.30] gives a real gap of [1.30, 1.50] at factor 2.
    """
    return (FRAME_LO, FRAME_HI, factor * FRAME_LO, factor * FRAME_HI)


def bands_disjoint(factor: float) -> bool:
    """The invariant: no ratio can belong to both frames."""
    lo, hi, slo, _shi = frame_band(factor)
    return hi < slo and lo > 0


def corpus_path(underlying: str, base: Optional[Path] = None) -> Path:
    if base is None:
        try:
            from app.utils.app_paths import APP_HOME
            base = Path(APP_HOME) / "backtest"
        except Exception:
            base = Path(os.path.expanduser("~/.scalp-app/backtest"))
    return Path(base) / "corpus" / f"{underlying.upper()}.db"


def _ro_conn(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


_DAY = "date(ts,'unixepoch','+330 minutes')"
_ODAY = "date(o.ts,'unixepoch','+330 minutes')"


# ── scan ───────────────────────────────────────────────────────────────────

def frame_scan(db: str, underlying: str, *, factor: float = 2.0) -> List[Dict]:
    """Per-month census of option rows by frame, relative to that day's spot.

    `native` = strikes near spot (the frame spot is currently in).
    `scaled`  = strikes near spot*factor (the other frame).
    Months with BOTH populated are the corrupt span.
    """
    _lo, _hi, _slo, _shi = frame_band(factor)
    sql = f"""
    WITH s AS (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
               WHERE underlying=? AND instrument_type='SPOT' GROUP BY d)
    SELECT strftime('%Y-%m', o.ts,'unixepoch','+330 minutes') m,
           sum(CASE WHEN o.strike >= s.sp*? AND o.strike <= s.sp*?
                    THEN 1 ELSE 0 END),
           sum(CASE WHEN o.strike >= s.sp*? AND o.strike <= s.sp*?
                    THEN 1 ELSE 0 END),
           count(*)
    FROM backtest_candles_1m o JOIN s ON s.d = {_ODAY}
    WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
    GROUP BY m ORDER BY m"""
    args = (underlying, _lo, _hi, _slo, _shi, underlying)
    orphan_sql = f"""
    SELECT strftime('%Y-%m', ts,'unixepoch','+330 minutes') m, count(*)
    FROM backtest_candles_1m o
    WHERE underlying=? AND instrument_type IN ('CE','PE')
      AND {_DAY} NOT IN (SELECT {_DAY} FROM backtest_candles_1m
                         WHERE underlying=? AND instrument_type='SPOT')
    GROUP BY m"""
    conn = _ro_conn(db)
    try:
        rows = conn.execute(sql, args).fetchall()
        orphans = dict(conn.execute(orphan_sql, (underlying, underlying)).fetchall())
    finally:
        conn.close()
    out = []
    for m, native, scaled, total in rows:
        out.append({"month": m, "native": native or 0, "scaled": scaled or 0,
                    "junk": (total or 0) - (native or 0) - (scaled or 0),
                    "orphan": int(orphans.get(m, 0)), "total": total or 0})
    for m, n in orphans.items():                    # months with ONLY orphans
        if not any(r["month"] == m for r in out):
            out.append({"month": m, "native": 0, "scaled": 0, "junk": 0,
                        "orphan": int(n), "total": int(n)})
    return sorted(out, key=lambda r: r["month"])


def summarize_scan(census: List[Dict]) -> Dict:
    dual = [r["month"] for r in census if r["native"] > 0 and r["scaled"] > 0]
    junk = sum(r["junk"] for r in census)
    orphan = sum(r.get("orphan", 0) for r in census)
    return {"months": len(census), "dual_frame_months": len(dual),
            "dual_span": (dual[0], dual[-1]) if dual else None,
            "junk_rows": junk, "orphan_rows": orphan,
            "clean": not dual and junk == 0 and orphan == 0}


# ── repair ─────────────────────────────────────────────────────────────────

def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    try:
        r = conn.execute("SELECT value FROM corpus_meta WHERE key=?",
                         (key,)).fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def _meta_set(conn: sqlite3.Connection, **kv) -> None:
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS corpus_meta ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);")
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO corpus_meta(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        [(str(k), str(v), now) for k, v in kv.items()])


def boundary_probe(db: str, underlying: str, ex_date: str,
                   n: int = 3) -> List[Dict]:
    """Daily spot closes either side of the ex-date — eyeball the boundary."""
    conn = _ro_conn(db)
    try:
        rows = conn.execute(f"""
            SELECT {_DAY} d, avg(close) FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT'
            GROUP BY d HAVING d >= date(?, '-{n * 2} days')
                          AND d <= date(?, '+{n * 2} days')
            ORDER BY d""", (underlying, ex_date, ex_date)).fetchall()
    finally:
        conn.close()
    return [{"date": d, "spot": round(v, 1),
             "side": "pre" if d < ex_date else "post"} for d, v in rows]


def repair_frame_split(db: str, underlying: str, *, ex_date: str,
                       factor: float = 2.0, dry_run: bool = True,
                       backup: bool = True) -> Dict:
    """Collapse a dual-frame corpus onto the AS-TRADED frame.

    Pre-ex-date: delete option rows outside [spot*factor*KEEP_LO,
    spot*factor*KEEP_HI], then multiply SPOT OHLC by factor.
    Post-ex-date: delete option rows outside [spot*KEEP_LO, spot*KEEP_HI].
    """
    u = underlying.upper()
    _lo, _hi, _slo, _shi = frame_band(factor)
    if not Path(db).exists():
        raise SystemExit(f"ABORT: no corpus at {db}")

    conn = sqlite3.connect(db)
    try:
        applied = _meta_get(conn, FENCE)
        if applied:
            return {"skipped": True, "reason": f"already repaired {applied}"}
    finally:
        conn.close()

    # classify against the CURRENT (pre-repair) spot frame
    conn = _ro_conn(db)
    try:
        pre_del, pre_keep = conn.execute(f"""
            WITH s AS (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT' GROUP BY d)
            SELECT sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 0 ELSE 1 END),
                   sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 1 ELSE 0 END)
            FROM backtest_candles_1m o JOIN s ON s.d = {_ODAY}
            WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
              AND {_ODAY} < ?""",
            (u, _slo, _shi, _slo, _shi, u, ex_date)).fetchone()
        post_del, post_keep = conn.execute(f"""
            WITH s AS (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT' GROUP BY d)
            SELECT sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 0 ELSE 1 END),
                   sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 1 ELSE 0 END)
            FROM backtest_candles_1m o JOIN s ON s.d = {_ODAY}
            WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
              AND {_ODAY} >= ?""",
            (u, _lo, _hi, _lo, _hi, u, ex_date)).fetchone()
        orphan_rows = conn.execute(f"""
            SELECT count(*) FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type IN ('CE','PE')
              AND {_DAY} NOT IN (SELECT {_DAY} FROM backtest_candles_1m
                                 WHERE underlying=? AND instrument_type='SPOT')""",
            (u, u)).fetchone()[0]
        spot_rows = conn.execute(
            f"SELECT count(*) FROM backtest_candles_1m WHERE underlying=? "
            f"AND instrument_type='SPOT' AND {_DAY} < ?",
            (u, ex_date)).fetchone()[0]
    finally:
        conn.close()

    # Sanity guard: a correct factor keeps the DENSE frame, so the survivor
    # share must be large. "keep > 0" is far too weak — stray junk can land
    # inside the band for an absurd factor and satisfy it.
    pre_total = (pre_keep or 0) + (pre_del or 0)
    keep_share = (pre_keep or 0) / pre_total if pre_total else 0.0
    plan_share = round(keep_share, 4)
    if pre_total and keep_share < MIN_KEEP_SHARE:
        raise SystemExit(
            f"ABORT: repair would keep only {plan_share:.1%} of pre-ex-date "
            f"option rows for {u} ({pre_keep or 0}/{pre_total}). A correct "
            f"--factor keeps the dense frame. Check --factor / --ex-date.")

    plan = {"underlying": u, "ex_date": ex_date, "factor": factor,
            "pre_options_delete": pre_del or 0, "pre_options_keep": pre_keep or 0,
            "post_options_delete": post_del or 0,
            "post_options_keep": post_keep or 0,
            "orphan_options_delete": orphan_rows,
            "spot_rows_rescaled": spot_rows,
            "pre_keep_share": plan_share,
            "boundary": boundary_probe(db, u, ex_date)}

    if dry_run:
        plan["dry_run"] = True
        return plan

    if backup:
        bak = f"{db}.bak-{FENCE}"
        if not Path(bak).exists():
            shutil.copy2(db, bak)
        plan["backup"] = bak

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"""
            DELETE FROM backtest_candles_1m WHERE rowid IN (
              SELECT o.rowid FROM backtest_candles_1m o
              JOIN (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                    WHERE underlying=? AND instrument_type='SPOT' GROUP BY d) s
                ON s.d = {_ODAY}
              WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
                AND {_ODAY} < ?
                AND o.strike NOT BETWEEN s.sp*? AND s.sp*?)""",
            (u, u, ex_date, _slo, _shi))
        conn.execute(f"""
            DELETE FROM backtest_candles_1m WHERE rowid IN (
              SELECT o.rowid FROM backtest_candles_1m o
              JOIN (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                    WHERE underlying=? AND instrument_type='SPOT' GROUP BY d) s
                ON s.d = {_ODAY}
              WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
                AND {_ODAY} >= ?
                AND o.strike NOT BETWEEN s.sp*? AND s.sp*?)""",
            (u, u, ex_date, _lo, _hi))
        # orphans: no SPOT row that day, so the JOIN above can never see them.
        # Left in place they survive every classification pass unexamined.
        conn.execute(f"""
            DELETE FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type IN ('CE','PE')
              AND {_DAY} NOT IN (SELECT {_DAY} FROM backtest_candles_1m
                                 WHERE underlying=? AND instrument_type='SPOT')""",
            (u, u))
        # spot LAST — classification above depends on the un-rescaled frame
        conn.execute(f"""
            UPDATE backtest_candles_1m
               SET open=open*?, high=high*?, low=low*?, close=close*?
             WHERE underlying=? AND instrument_type='SPOT' AND {_DAY} < ?""",
            (factor, factor, factor, factor, u, ex_date))
        _meta_set(conn,
                  **{FENCE: datetime.now().isoformat(timespec="seconds"),
                     "frame_break_dates": ex_date,
                     "frame_repair_factor": factor,
                     "frame_repair_kept": "as_traded"})
        plan["sanitizer_reset"] = bool(_reset_sanitizer_meta(conn))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    plan["applied"] = True
    return plan


def _reset_sanitizer_meta(conn: sqlite3.Connection) -> int:
    """Force CORPUS_SANITIZER to rescan after we delete rows. MUST be called
    inside any transaction that removes corpus rows.

    ensure_corpus_sane() fast-paths on `version matches AND rowid_watermark >=
    max(rowid)`. repair/prune delete millions of rows, so max(rowid) drops far
    BELOW the stamped watermark and the sanitizer silently returns "already
    sane" forever after. Worse, SQLite reuses freed rowids, so every future
    backfill lands under the watermark and is never scanned either — which is
    the exact failure the sanitizer's own watermark comment warns about.
    Clearing the stamp costs one full rescan on next CandleSource open.
    Introduced and fixed 2026-08-28.
    """
    try:
        return conn.execute("DELETE FROM corpus_sanitizer_meta").rowcount or 0
    except sqlite3.OperationalError:
        return 0            # sanitizer never ran on this corpus — nothing to do


def prune_out_of_frame(db: str, underlying: str, *, factor: float = 2.0,
                       dry_run: bool = True, backup: bool = False) -> Dict:
    """Delete option rows outside the single sane frame, plus orphans.

    For a corpus that is ALREADY single-frame (post-repair, or never split).
    Unlike repair_frame_split this touches no SPOT row and rescales nothing,
    so it is safe to re-run — there is no doubling to do twice.
    """
    u = underlying.upper()
    lo, hi, _slo, _shi = frame_band(factor)
    if not Path(db).exists():
        raise SystemExit(f"ABORT: no corpus at {db}")

    conn = _ro_conn(db)
    try:
        bad, good = conn.execute(f"""
            WITH s AS (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT' GROUP BY d)
            SELECT sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 0 ELSE 1 END),
                   sum(CASE WHEN o.strike BETWEEN s.sp*? AND s.sp*?
                            THEN 1 ELSE 0 END)
            FROM backtest_candles_1m o JOIN s ON s.d = {_ODAY}
            WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')""",
            (u, lo, hi, lo, hi, u)).fetchone()
        orphans = conn.execute(f"""
            SELECT count(*) FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type IN ('CE','PE')
              AND {_DAY} NOT IN (SELECT {_DAY} FROM backtest_candles_1m
                                 WHERE underlying=? AND instrument_type='SPOT')""",
            (u, u)).fetchone()[0]
        sample = [dict(zip(("symbol", "strike", "spot", "ratio", "rows"), r))
                  for r in conn.execute(f"""
            WITH s AS (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT' GROUP BY d)
            SELECT o.tradingsymbol, o.strike, round(s.sp),
                   round(o.strike/s.sp, 3), count(*)
            FROM backtest_candles_1m o JOIN s ON s.d = {_ODAY}
            WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
              AND o.strike NOT BETWEEN s.sp*? AND s.sp*?
            GROUP BY o.tradingsymbol ORDER BY 5 DESC LIMIT 10""",
            (u, u, lo, hi))]
    finally:
        conn.close()

    bad, good = bad or 0, good or 0
    plan = {"underlying": u, "band": [round(lo, 4), round(hi, 4)],
            "options_delete": bad, "options_keep": good,
            "orphans_delete": orphans,
            "delete_share": round(bad / (bad + good), 5) if (bad + good) else 0.0,
            "sample": sample}

    share = plan["delete_share"]
    if share > MAX_PRUNE_SHARE:
        raise SystemExit(
            f"ABORT: prune would delete {share:.1%} of option rows for {u}. "
            f"That is a frame split, not stray junk — run --scan and use "
            f"--repair instead.")
    if dry_run:
        plan["dry_run"] = True
        return plan

    if backup:
        bak = f"{db}.bak-PRUNE"
        if not Path(bak).exists():
            shutil.copy2(db, bak)
        plan["backup"] = bak

    conn = sqlite3.connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"""
            DELETE FROM backtest_candles_1m WHERE rowid IN (
              SELECT o.rowid FROM backtest_candles_1m o
              JOIN (SELECT {_DAY} d, avg(close) sp FROM backtest_candles_1m
                    WHERE underlying=? AND instrument_type='SPOT' GROUP BY d) s
                ON s.d = {_ODAY}
              WHERE o.underlying=? AND o.instrument_type IN ('CE','PE')
                AND o.strike NOT BETWEEN s.sp*? AND s.sp*?)""",
            (u, u, lo, hi))
        conn.execute(f"""
            DELETE FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type IN ('CE','PE')
              AND {_DAY} NOT IN (SELECT {_DAY} FROM backtest_candles_1m
                                 WHERE underlying=? AND instrument_type='SPOT')""",
            (u, u))
        _meta_set(conn, last_prune=datetime.now().isoformat(timespec="seconds"),
                  last_prune_band=f"{lo:.4f}..{hi:.4f}")
        plan["sanitizer_reset"] = bool(_reset_sanitizer_meta(conn))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    plan["applied"] = True
    return plan


def resync_sanitizer(db: str) -> Dict:
    """Clear a stale sanitizer stamp on a corpus whose rows were already
    deleted by an earlier repair/prune (before the reset above existed)."""
    out = {"db": db, "was_stale": False, "cleared": False}
    if not Path(db).exists():
        raise SystemExit(f"ABORT: no corpus at {db}")
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT rowid_watermark FROM corpus_sanitizer_meta WHERE id=1"
        ).fetchone()
        if not row:
            return out
        max_rid = conn.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM backtest_candles_1m").fetchone()[0]
        out.update(watermark=row[0], max_rowid=max_rid,
                   was_stale=row[0] > max_rid)
        if out["was_stale"]:
            _reset_sanitizer_meta(conn)
            conn.commit()
            out["cleared"] = True
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return out


# ── frame-break guard (read by the runners) ────────────────────────────────

def frame_break_reason(db_path: str, date_from, date_to) -> Optional[str]:
    """Non-None when a run range CROSSES a recorded price-frame break.

    repair_frame_split stamps `frame_break_dates`. On the as-traded side of a
    bonus the underlying genuinely halves overnight — real, but not tradeable:
    any strategy carrying a position through that night books a fake gap that
    will dominate the P&L. Fail closed. To override, clear the key:
        UPDATE corpus_meta SET value='' WHERE key='frame_break_dates';
    """
    try:
        conn = _ro_conn(db_path)
    except Exception:
        return None
    try:
        raw = _meta_get(conn, "frame_break_dates")
    finally:
        conn.close()
    if not raw:
        return None
    lo = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
    hi = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
    hits = [d.strip() for d in raw.split(",") if d.strip() and lo < d.strip() <= hi]
    if not hits:
        return None
    return (f"run range {lo}..{hi} crosses a price-frame break at "
            f"{', '.join(hits)}. The underlying changes scale on that date, so "
            f"a carried position books an artificial gap. Split the run either "
            f"side of it (end {hits[0]} or start after), or clear "
            f"frame_break_dates in corpus_meta to override.")


# ── CLI ────────────────────────────────────────────────────────────────────

def _print_census(census: List[Dict]) -> None:
    print(f"{'month':9} {'native':>9} {'scaled':>9} {'junk':>8} "
          f"{'orphan':>8} {'total':>9}")
    for r in census:
        flag = "  <-- DUAL" if r["native"] and r["scaled"] else (
            "  <-- junk" if r["junk"] or r.get("orphan") else "")
        print(f"{r['month']:9} {r['native']:9d} {r['scaled']:9d} "
              f"{r['junk']:8d} {r.get('orphan', 0):8d} {r['total']:9d}{flag}")


def main() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="corpus frame health")
    ap.add_argument("--scan", metavar="SYMBOL")
    ap.add_argument("--repair", metavar="SYMBOL")
    ap.add_argument("--ex-date", metavar="YYYY-MM-DD")
    ap.add_argument("--factor", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", metavar="SYMBOL",
                    help="delete out-of-frame + orphan option rows")
    ap.add_argument("--resync-sanitizer", metavar="SYMBOL",
                    help="clear a stale sanitizer stamp after repair/prune")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--db", help="explicit corpus path")
    a = ap.parse_args()

    if a.scan:
        db = a.db or str(corpus_path(a.scan))
        census = frame_scan(db, a.scan.upper(), factor=a.factor)
        _print_census(census)
        s = summarize_scan(census)
        print()
        print(json.dumps(s, indent=1))
        if not s["clean"]:
            # Recommending --repair for a junk-only corpus is dangerous: it
            # rescales SPOT. Only a sustained dual span is a real frame split.
            if s["dual_frame_months"] >= 6:
                print(f"\nDUAL FRAME across {s['dual_frame_months']} months "
                      f"{s['dual_span']} — that is a split/bonus. Repair:")
                print(f"  python3 -m app.backtest.util.corpus_health "
                      f"--repair {a.scan.upper()} --ex-date <YYYY-MM-DD> "
                      f"--factor {a.factor:g} --dry-run")
            else:
                if s["dual_frame_months"]:
                    print(f"\n{s['dual_frame_months']} isolated month(s) show a "
                          f"second frame — too few to be a split. Treat as junk.")
                print("\nJunk/orphan rows only — prune (does NOT touch SPOT):")
                print(f"  python3 -m app.backtest.util.corpus_health "
                      f"--prune {a.scan.upper()} --dry-run")
        return 0

    if a.resync_sanitizer:
        db = a.db or str(corpus_path(a.resync_sanitizer))
        print(json.dumps(resync_sanitizer(db), indent=2))
        return 0

    if a.prune:
        db = a.db or str(corpus_path(a.prune))
        r = prune_out_of_frame(db, a.prune.upper(), factor=a.factor,
                               dry_run=a.dry_run, backup=not a.no_backup)
        print(json.dumps(r, indent=1))
        return 0

    if a.repair:
        if not a.ex_date:
            print("ABORT: --repair needs --ex-date", file=sys.stderr)
            return 2
        db = a.db or str(corpus_path(a.repair))
        r = repair_frame_split(db, a.repair.upper(), ex_date=a.ex_date,
                               factor=a.factor, dry_run=a.dry_run,
                               backup=not a.no_backup)
        print(json.dumps(r, indent=1))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

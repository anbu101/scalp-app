#!/usr/bin/env python3
# fix_muhurat_corpus.py — ── CORPUS_MUHURAT_FIX_20261108 ──
#
# One-time, evidence-gated repair of the NIFTY Muhurat-session rows in
# ~/.scalp-app/backtest/backtest.db. Found 2026-11-08 while checking how the
# backtest treats Muhurat days. Data only, no code is touched.
#
#   A. RESTAMP   2022-10-24, 2023-11-12, 2024-11-01 — Muhurat SPOT rows are
#                one minute early (Dhan -60 s defect; spot_stamp_repair only
#                looks at the 09:00–09:30 window, so evening sessions were
#                never repaired). Moved +60 s, only when the day's first
#                evening SPOT bar is exactly one minute before the first
#                evening OPTION bar.
#   B. DELETE    2021-11-04 SPOT rows between 09:00 and 17:00 (43 rows at
#                14:46–15:28). The market was shut until the 18:15 Muhurat
#                session; only done when NO option traded in that window.
#   C. DELETE    2025-10-21 option rows after 14:55 that are zero-volume AND
#                flat (O=H=L=C) — post-close filler (1,428 rows). Only done
#                when NO option row after 14:55 has any volume.
#
# SAFETY
#   * Default is a DRY RUN: prints the plan, writes nothing. --apply writes.
#   * Every row touched is copied first to corpus_fix_backup_20261108
#     (same columns + op), in the SAME transaction as the change.
#   * All-or-nothing: one transaction for A+B+C. Any guard failure aborts
#     before anything is written.
#   * Idempotent: bt_meta['corpus_muhurat_fix_20261108']='1' after apply;
#     a second --apply is a no-op.
#   * --rollback restores every backed-up row exactly and clears the marker.
#   * PK-safe restamp (read -> delete -> insert), like spot_stamp_repair.
#   * Compatible with the boot-time spot_stamp_repair: Muhurat days have no
#     bar in 09:00–09:30, so it keeps skipping them after this fix.
#
# USAGE (quit Scalp Terminal first, or at least don't run a backtest):
#   python3 fix_muhurat_corpus.py              # dry run
#   python3 fix_muhurat_corpus.py --apply
#   python3 fix_muhurat_corpus.py --rollback
#   python3 fix_muhurat_corpus.py --db /path/to/backtest.db --apply

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FENCE = "CORPUS_MUHURAT_FIX_20261108"
MARKER_KEY = "corpus_muhurat_fix_20261108"
BACKUP = "corpus_fix_backup_20261108"
UND = "NIFTY"
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DB = Path.home() / ".scalp-app" / "backtest" / "backtest.db"

RESTAMP_DAYS = ["2022-10-24", "2023-11-12", "2024-11-01"]
SPURIOUS_SPOT_DAY = "2021-11-04"
FILLER_DAY = "2025-10-21"
FILLER_AFTER_HM = (14, 55)          # rows strictly after 14:55 IST


class Abort(Exception):
    pass


# ── helpers ────────────────────────────────────────────────────────────
def day_start(d: str) -> int:
    return int(datetime.fromisoformat(d).replace(tzinfo=IST).timestamp())


def at(d: str, h: int, m: int) -> int:
    return day_start(d) + (h * 60 + m) * 60


def hm(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def columns(cur) -> list:
    return [r[1] for r in cur.execute("PRAGMA table_info(backtest_candles_1m)")]


def option_expiries(cur) -> list:
    """Distinct option expiries (index-assisted; avoids full-table scans in
    the per-day queries below, which then hit (underlying, expiry, ts))."""
    return [r[0] for r in cur.execute(
        "SELECT DISTINCT expiry FROM backtest_candles_1m "
        "WHERE underlying=? AND expiry!=''", (UND,))]


def opt_rows(cur, exps, lo, hi, cols, extra="", params=()):
    out = []
    sel = ",".join(cols)
    for e in exps:
        out += cur.execute(
            f"SELECT {sel} FROM backtest_candles_1m WHERE underlying=? AND expiry=? "
            f"AND instrument_type IN ('CE','PE') AND ts>=? AND ts<? {extra}",
            (UND, e, lo, hi) + tuple(params)).fetchall()
    return out


def spot_rows(cur, lo, hi, cols):
    sel = ",".join(cols)
    return cur.execute(
        f"SELECT {sel} FROM backtest_candles_1m WHERE underlying=? AND expiry='' "
        f"AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts",
        (UND, lo, hi)).fetchall()


def marker_get(cur) -> bool:
    cur.execute("CREATE TABLE IF NOT EXISTS bt_meta(key TEXT PRIMARY KEY, value TEXT)")
    r = cur.execute("SELECT value FROM bt_meta WHERE key=?", (MARKER_KEY,)).fetchone()
    return bool(r and r[0] == "1")


# ── plan (read-only) ───────────────────────────────────────────────────
def build_plan(cur) -> dict:
    cols = columns(cur)
    if not cols:
        raise Abort("backtest_candles_1m not found — wrong DB?")
    i_ts = cols.index("ts")
    exps = option_expiries(cur)
    plan = {"cols": cols, "restamp": [], "spurious": [], "filler": [], "notes": []}

    # A. evening Muhurat restamp
    for d in RESTAMP_DAYS:
        lo, hi = at(d, 17, 0), at(d, 20, 30)
        sp = spot_rows(cur, lo, hi, cols)
        op = opt_rows(cur, exps, lo, hi, ["ts"])
        if not sp or not op:
            plan["notes"].append(f"A {d}: spot={len(sp)} opt={len(op)} rows in evening — skipped")
            continue
        s_first, o_first = sp[0][i_ts], min(r[0] for r in op)
        if s_first == o_first:
            plan["notes"].append(f"A {d}: spot already starts {hm(s_first)} = options — skipped")
            continue
        if s_first != o_first - 60:
            raise Abort(f"A {d}: spot first {hm(s_first)} vs options first {hm(o_first)} "
                        f"— not the -60 s signature, manual review")
        # collision check: target ts must be free or inside the moving set
        moving = {r[i_ts] for r in sp}
        for r in sp:
            t = r[i_ts] + 60
            if t not in moving and cur.execute(
                    "SELECT 1 FROM backtest_candles_1m WHERE instrument_token=? AND ts=?",
                    (r[cols.index("instrument_token")], t)).fetchone():
                raise Abort(f"A {d}: target {hm(t)} already occupied — manual review")
        plan["restamp"].append((d, sp))
        plan["notes"].append(f"A {d}: {len(sp)} spot rows {hm(sp[0][i_ts])}–{hm(sp[-1][i_ts])} "
                             f"→ {hm(sp[0][i_ts] + 60)}–{hm(sp[-1][i_ts] + 60)} "
                             f"(options start {hm(o_first)})")

    # B. spurious afternoon spot on 2021-11-04
    d = SPURIOUS_SPOT_DAY
    lo, hi = at(d, 9, 0), at(d, 17, 0)
    sp = spot_rows(cur, lo, hi, cols)
    if sp:
        n_opt = len(opt_rows(cur, exps, lo, hi, ["ts"]))
        if n_opt:
            raise Abort(f"B {d}: {n_opt} option rows exist 09:00–17:00 — market may have "
                        f"traded; not deleting spot")
        plan["spurious"] = sp
        plan["notes"].append(f"B {d}: delete {len(sp)} spot rows "
                             f"{hm(sp[0][i_ts])}–{hm(sp[-1][i_ts])} (no options traded then)")
    else:
        plan["notes"].append(f"B {d}: no spot rows 09:00–17:00 — already clean")

    # C. post-close filler on 2025-10-21
    d = FILLER_DAY
    lo, hi = at(d, *FILLER_AFTER_HM) + 60, day_start(d) + 86400
    after = opt_rows(cur, exps, lo, hi, cols)
    if after:
        i_v = cols.index("volume")
        i_o, i_h, i_l, i_c = (cols.index(k) for k in ("open", "high", "low", "close"))
        traded = [r for r in after if (r[i_v] or 0) != 0]
        if traded:
            raise Abort(f"C {d}: {len(traded)} option rows after 14:55 have volume — "
                        f"not all filler, manual review")
        flat = [r for r in after if r[i_o] == r[i_h] == r[i_l] == r[i_c]]
        if len(flat) != len(after):
            plan["notes"].append(f"C {d}: {len(after) - len(flat)} non-flat zero-volume rows "
                                 f"after 14:55 kept")
        plan["filler"] = flat
        plan["notes"].append(f"C {d}: delete {len(flat)} zero-volume flat option rows after 14:55")
    else:
        plan["notes"].append(f"C {d}: no option rows after 14:55 — already clean")
    return plan


# ── apply / rollback ───────────────────────────────────────────────────
def apply(conn, plan) -> dict:
    cols = plan["cols"]
    i_ts, i_tok = cols.index("ts"), cols.index("instrument_token")
    collist = ",".join(cols)
    qs = ",".join("?" * len(cols))
    cur = conn.cursor()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} AS "
                    f"SELECT {collist}, '' AS op FROM backtest_candles_1m WHERE 0")
        def backup(rows, op):
            cur.executemany(f"INSERT INTO {BACKUP} ({collist}, op) VALUES ({qs},?)",
                            [tuple(r) + (op,) for r in rows])
        def delete(rows):
            cur.executemany("DELETE FROM backtest_candles_1m WHERE instrument_token=? AND ts=?",
                            [(r[i_tok], r[i_ts]) for r in rows])
        n_moved = 0
        for d, sp in plan["restamp"]:
            backup(sp, f"RESTAMP {d}")
            delete(sp)
            shifted = [tuple(v + 60 if k == i_ts else v for k, v in enumerate(r)) for r in sp]
            cur.executemany(f"INSERT INTO backtest_candles_1m ({collist}) VALUES ({qs})", shifted)
            n_moved += len(sp)
        if plan["spurious"]:
            backup(plan["spurious"], f"DELETE_SPOT {SPURIOUS_SPOT_DAY}")
            delete(plan["spurious"])
        if plan["filler"]:
            backup(plan["filler"], f"DELETE_FILLER {FILLER_DAY}")
            delete(plan["filler"])
        cur.execute("INSERT INTO bt_meta(key, value) VALUES (?, '1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'", (MARKER_KEY,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"restamped": n_moved, "spot_deleted": len(plan["spurious"]),
            "filler_deleted": len(plan["filler"])}


def rollback(conn) -> dict:
    cur = conn.cursor()
    if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (BACKUP,)).fetchone():
        raise Abort(f"no {BACKUP} table — nothing to roll back")
    cols = columns(cur)
    collist = ",".join(cols)
    qs = ",".join("?" * len(cols))
    rows = cur.execute(f"SELECT {collist}, op FROM {BACKUP}").fetchall()
    i_ts, i_tok = cols.index("ts"), cols.index("instrument_token")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in rows:
            op = r[-1]
            if op.startswith("RESTAMP"):
                cur.execute("DELETE FROM backtest_candles_1m WHERE instrument_token=? AND ts=?",
                            (r[i_tok], r[i_ts] + 60))
        cur.executemany(f"INSERT OR REPLACE INTO backtest_candles_1m ({collist}) VALUES ({qs})",
                        [tuple(r[:-1]) for r in rows])
        cur.execute(f"DROP TABLE {BACKUP}")
        cur.execute("DELETE FROM bt_meta WHERE key=?", (MARKER_KEY,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"rows_restored": len(rows)}


# ── main ───────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    a = ap.parse_args(argv)

    path = Path(a.db).expanduser()
    if not path.exists():
        print(f"[{FENCE}] DB not found: {path}")
        return 2
    conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()
    try:
        if a.rollback:
            print(f"[{FENCE}] ROLLBACK {rollback(conn)}")
            return 0
        if marker_get(cur):
            print(f"[{FENCE}] already applied (bt_meta marker set) — nothing to do. "
                  f"Use --rollback to undo.")
            return 0
        plan = build_plan(cur)
        print(f"[{FENCE}] DB: {path}")
        for n in plan["notes"]:
            print("  " + n)
        if not a.apply:
            print(f"[{FENCE}] DRY RUN — nothing written. Re-run with --apply.")
            return 0
        res = apply(conn, plan)
        print(f"[{FENCE}] APPLIED {res}. Backup table: {BACKUP}")
        return 0
    except Abort as e:
        print(f"[{FENCE}] ABORT — nothing written: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

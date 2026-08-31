# backend/app/backtest/repo/backtest_repo.py
#
# Persistence for backtest runs + trades, and CSV export. Writes to the
# separate backtest.db (isolation: never touches the live trading DB).
#
# Tables (created by schema.sql, applied on first connect):
#   backtest_runs    one row per run + frozen config + summary
#   backtest_trades  one row per simulated trade (the CSV source)

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from app.utils.app_paths import APP_HOME
from app.event_bus.audit_logger import write_audit_log


def _db_path() -> Path:
    p = APP_HOME / "backtest" / "backtest.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _schema_sql() -> str:
    """Read schema.sql. In a normal source checkout it sits beside this file.
    In a PyInstaller onedir bundle the .py modules are served from the PYZ
    archive (so __file__ is a virtual path with NO real sibling files), while
    data files collected via collect_data_files land under sys._MEIPASS. Try
    the candidate locations in order and use the first that exists."""
    import sys
    candidates = []
    # 1) beside this module (source checkout, or if datas placed it here)
    candidates.append(Path(__file__).resolve().parent / "schema.sql")
    # 2) PyInstaller bundle root (sys._MEIPASS) → app/backtest/repo/schema.sql
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "app" / "backtest" / "repo" / "schema.sql")
    for p in candidates:
        try:
            if p.is_file():
                return p.read_text()
        except Exception:
            continue
    # Last resort: surface a clear error naming all tried paths.
    tried = " | ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"schema.sql not found. Tried: {tried}")



# ----------------------------------------------------------------------
# SCHEMA SELF-HEAL
# CREATE TABLE IF NOT EXISTS does NOT add columns to a table that already
# exists, so a schema that gains a column would error on an old DB ("table X
# has no column named Y"). This guard reconciles the LIVE table columns against
# the expected set and ALTER-ADDs any that are missing. Idempotent + cheap;
# runs on every connect. Only ADDITIVE changes self-heal (the safe 99% case);
# renames/drops/type-changes still need a real migration.
# ----------------------------------------------------------------------
# Expected columns per table: name -> column DDL fragment used in ALTER ADD.
# Keep in sync with schema.sql. Adding a new column here + in schema.sql is all
# that's needed for old DBs to self-heal on next connect.
_EXPECTED_COLUMNS = {
    "backtest_candles_1m": {
        "instrument_token": "INTEGER", "ts": "INTEGER", "underlying": "TEXT",
        "tradingsymbol": "TEXT", "instrument_type": "TEXT", "strike": "REAL",
        "expiry": "TEXT", "open": "REAL", "high": "REAL", "low": "REAL",
        "close": "REAL", "volume": "INTEGER", "oi": "INTEGER",
    },
    "backtest_candles_1s": {
        "instrument_token": "INTEGER", "ts": "INTEGER", "underlying": "TEXT",
        "tradingsymbol": "TEXT", "instrument_type": "TEXT", "strike": "REAL",
        "expiry": "TEXT", "open": "REAL", "high": "REAL", "low": "REAL",
        "close": "REAL", "volume": "INTEGER", "oi": "INTEGER",
    },
    "backtest_runs": {
        "run_id": "TEXT", "strategy_id": "TEXT", "underlying": "TEXT",
        "date_from": "TEXT", "date_to": "TEXT", "config_json": "TEXT",
        "fill_model": "TEXT", "status": "TEXT", "created_at": "INTEGER",
        "finished_at": "INTEGER", "summary_json": "TEXT", "error_text": "TEXT",
    },
    "backtest_trades": {
        "run_id": "TEXT", "tradingsymbol": "TEXT", "instrument_type": "TEXT",
        "strike": "REAL", "expiry": "TEXT", "direction": "TEXT",
        "entry_ts": "INTEGER", "entry_price": "REAL", "sl": "REAL", "tp": "REAL",
        "exit_ts": "INTEGER", "exit_price": "REAL", "exit_reason": "TEXT",
        "pnl": "REAL", "qty": "INTEGER", "ambiguous_fill": "INTEGER",
        "max_adverse": "REAL", "max_favorable": "REAL",
        "charges": "REAL NOT NULL DEFAULT 0", "net_pnl": "REAL",
        "signal_symbol": "TEXT", "signal_side": "TEXT",
        "signal_sl": "REAL", "signal_tp": "REAL", "hedge_side": "TEXT",
        # ── IC LEG TAGS (2026-07-22) ── L1/L2·MTC/L1·ADJ·SYN etc. + model
        # provenance. NULL for every non-IC strategy (they never set them).
        "condition": "TEXT", "synthetic": "INTEGER NOT NULL DEFAULT 0",
        "synth_kind": "TEXT",
    },
}


def _self_heal_columns(conn: sqlite3.Connection) -> None:
    """ADD any expected-but-missing columns to existing tables. Additive only."""
    for table, cols in _EXPECTED_COLUMNS.items():
        try:
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        except Exception:
            continue  # table doesn't exist yet — CREATE handles it
        if not existing:
            continue
        for name, ddl in cols.items():
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    conn.commit()
                except Exception:
                    # NOT NULL without a default on a populated table can't be
                    # added; that needs a real migration. Don't crash the app.
                    pass




def _heal_tp_not_null(conn: sqlite3.Connection) -> None:
    """Legacy DBs created backtest_trades with `sl REAL NOT NULL` and/or
    `tp REAL NOT NULL`. Rows that lack one of those legs need NULL there:
      * V3/V4 hedge rows have no TP leg (tp=NULL).
      * SCALP_V5 rows can disable SL and/or TP (sl_points/tp_points = 0 → NULL).
    Inserting NULL then fails with 'NOT NULL constraint failed: backtest_trades.sl'
    (or .tp). The column-add self-heal can't change a constraint, so we rebuild
    THIS table when EITHER stale NOT NULL is detected. backtest_trades holds only
    run RESULTS (regenerable by re-running a backtest — unlike the candle corpus),
    so dropping its rows is safe; we preserve existing rows by copying them across.
    """
    try:
        cols = conn.execute("PRAGMA table_info(backtest_trades)").fetchall()
    except Exception:
        return
    if not cols:
        return
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    sl_col = next((c for c in cols if c[1] == "sl"), None)
    tp_col = next((c for c in cols if c[1] == "tp"), None)
    sl_bad = sl_col is not None and int(sl_col[3]) == 1
    tp_bad = tp_col is not None and int(tp_col[3]) == 1
    if not (sl_bad or tp_bad):
        return  # both already nullable (or absent) — nothing to do

    stale = ", ".join([c for c, b in (("sl", sl_bad), ("tp", tp_bad)) if b])
    write_audit_log(
        f"[BACKTEST][SCHEMA_HEAL] backtest_trades.{stale} is legacy NOT NULL — "
        "rebuilding table to make it nullable (V5 SL/TP-disabled and V3/V4 hedge "
        "rows need NULL there)"
    )
    try:
        conn.execute("PRAGMA foreign_keys=OFF;")
        conn.execute("ALTER TABLE backtest_trades RENAME TO backtest_trades_legacy;")
        # Recreate with current schema (sl + tp nullable + hedge columns), via the
        # full schema script (CREATE IF NOT EXISTS makes the fresh table).
        conn.executescript(_schema_sql())
        # Copy the intersection of columns from the legacy table.
        legacy_cols = {c[1] for c in
                       conn.execute("PRAGMA table_info(backtest_trades_legacy)").fetchall()}
        new_cols = {c[1] for c in
                    conn.execute("PRAGMA table_info(backtest_trades)").fetchall()}
        shared = [c for c in new_cols if c in legacy_cols and c != "id"]
        collist = ", ".join(shared)
        conn.execute(
            f"INSERT INTO backtest_trades ({collist}) "
            f"SELECT {collist} FROM backtest_trades_legacy;"
        )
        conn.execute("DROP TABLE backtest_trades_legacy;")
        conn.commit()
        write_audit_log("[BACKTEST][SCHEMA_HEAL] backtest_trades rebuilt OK")
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[BACKTEST][SCHEMA_HEAL][ERROR] sl/tp rebuild failed: {e}")


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.executescript(_schema_sql())   # creates tables if absent (won't add cols)
    _self_heal_columns(c)            # ADD any new columns to pre-existing tables
    _heal_tp_not_null(c)             # rebuild backtest_trades if tp is legacy NOT NULL
    # ── NULL_RUN_PURGE ── remove ghost rows minted by the aborted-run bug
    # (run_id NULL): shown as all-zero runs, undeletable by id from the UI.
    # Idempotent and cheap; runs on every connect like the other self-heals.
    try:
        _cur = c.execute("DELETE FROM backtest_runs WHERE run_id IS NULL")
        if _cur.rowcount:
            c.commit()
            write_audit_log(f"[BACKTEST][SCHEMA_HEAL] purged {_cur.rowcount} ghost run row(s) with NULL run_id")
    except Exception:
        pass
    return c


def _ist_str(epoch: Optional[int]) -> str:
    if not epoch:
        return ""
    from datetime import datetime, timedelta
    return (datetime(1970, 1, 1) + timedelta(seconds=epoch + 5 * 3600 + 30 * 60)
            ).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# WRITE
# ----------------------------------------------------------------------
def persist_run(result: dict) -> str:
    """Persist a completed run (from run_backtest) and its trades. Returns run_id."""
    run_id = result["run_id"]
    # ── NULL_RUN_GUARD ── belt-and-braces behind the routes' aborted-run
    # guard: a falsy run_id must never reach the DB (NULL rows are undeletable
    # ghosts in the UI). Fail loud so the caller's RUN_ERR path reports it.
    if not run_id:
        raise ValueError("persist_run called with empty run_id (aborted run?)")
    s = result["summary"]
    cfg = result.get("config", {})
    trades = result.get("trades", [])

    # date range / underlying are echoed back inside the run audit; pull from cfg
    # and the trades if present. The runner doesn't return date_from/to in
    # summary, so callers pass them via result meta if needed; we store what we have.
    meta = result.get("meta", {})

    with _connect() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO backtest_runs
              (run_id, strategy_id, underlying, date_from, date_to, config_json,
               fill_model, status, created_at, finished_at, summary_json, error_text)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                meta.get("strategy_id", "SCALP_V1"),
                meta.get("underlying", "NIFTY"),
                meta.get("date_from", ""),
                meta.get("date_to", ""),
                json.dumps(cfg),
                meta.get("fill_model", "pessimistic"),
                "done",
                meta.get("created_at", int(time.time())),
                int(time.time()),
                json.dumps(s),
                None,
            ),
        )

        c.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
        for t in trades:
            is_hedge = hasattr(t, "hedge_symbol")  # V3/V4 HedgeClosedTrade
            if is_hedge:
                # Primary row = the HEDGE (the LONG position carrying P&L).
                # tradingsymbol/instrument_type/direction/entry/sl describe the
                # hedge; tp is NULL (hedge has no TP). Signal contract + levels
                # are stored in the hedge-specific columns.
                c.execute(
                    """
                    INSERT INTO backtest_trades
                      (run_id, tradingsymbol, instrument_type, strike, expiry,
                       direction, entry_ts, entry_price, sl, tp, exit_ts, exit_price,
                       exit_reason, pnl, qty, ambiguous_fill, max_adverse, max_favorable,
                       charges, net_pnl, signal_symbol, signal_side, signal_sl,
                       signal_tp, hedge_side, condition)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id, t.hedge_symbol, t.hedge_side, t.strike, t.expiry,
                        t.direction, t.entry_ts, t.entry_price, t.sl, None,
                        t.exit_ts, t.exit_price, t.exit_reason, t.pnl, t.qty,
                        int(t.ambiguous_fill), t.max_adverse, t.max_favorable,
                        getattr(t, "charges", 0.0), getattr(t, "net_pnl", t.pnl),
                        t.signal_symbol, t.signal_side, t.signal_sl, t.signal_tp,
                        t.hedge_side,
                        # ── SCALP_V3_DIAG_20260826 ── getattr-safe: older
                        # pickles / V4 rows without the field -> NULL.
                        getattr(t, "condition", None),
                    ),
                )
            else:
                # V1 SHORT row (unchanged).
                c.execute(
                    """
                    INSERT INTO backtest_trades
                      (run_id, tradingsymbol, instrument_type, strike, expiry,
                       direction, entry_ts, entry_price, sl, tp, exit_ts, exit_price,
                       exit_reason, pnl, qty, ambiguous_fill, max_adverse, max_favorable,
                       charges, net_pnl, condition, synthetic, synth_kind)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id, t.symbol, t.instrument_type, t.strike, t.expiry,
                        t.direction, t.entry_ts, t.entry_price, t.sl, t.tp,
                        t.exit_ts, t.exit_price, t.exit_reason, t.pnl, t.qty,
                        int(t.ambiguous_fill), t.max_adverse, t.max_favorable,
                        getattr(t, "charges", 0.0), getattr(t, "net_pnl", t.pnl),
                        # ── IC LEG TAGS ── getattr-safe: every non-IC trade
                        # object lacks these → NULL/0, zero behaviour change.
                        getattr(t, "condition", None),
                        int(bool(getattr(t, "synthetic", False))),
                        getattr(t, "synth_kind", None),
                    ),
                )
        c.commit()
    return run_id


def mark_run_error(run_id: str, error_text: str, meta: dict) -> None:
    with _connect() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO backtest_runs
              (run_id, strategy_id, underlying, date_from, date_to, config_json,
               fill_model, status, created_at, finished_at, summary_json, error_text)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, meta.get("strategy_id", "SCALP_V1"),
             meta.get("underlying", "NIFTY"), meta.get("date_from", ""),
             meta.get("date_to", ""), json.dumps(meta.get("config", {})),
             "pessimistic", "error", meta.get("created_at", int(time.time())),
             int(time.time()), None, error_text),
        )
        c.commit()


# ----------------------------------------------------------------------
# READ
# ----------------------------------------------------------------------
def list_runs(limit: int = 50) -> List[dict]:
    with _connect() as c:
        rows = c.execute(
            """
            SELECT run_id, strategy_id, underlying, date_from, date_to,
                   fill_model, status, created_at, finished_at,
                   summary_json, config_json, error_text
            FROM backtest_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(r["summary_json"]) if r["summary_json"] else None
        d["config"] = json.loads(r["config_json"]) if r["config_json"] else None
        d.pop("summary_json", None)
        d.pop("config_json", None)
        out.append(d)
    return out


def get_run(run_id: str) -> Optional[dict]:
    with _connect() as c:
        r = c.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["summary"] = json.loads(r["summary_json"]) if r["summary_json"] else None
        d["config"] = json.loads(r["config_json"]) if r["config_json"] else None
        d.pop("summary_json", None)
        d.pop("config_json", None)
        trades = c.execute(
            "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_ts ASC",
            (run_id,),
        ).fetchall()
    d["trades"] = [dict(t) for t in trades]
    return d


def delete_run(run_id: str) -> int:
    """Delete a run and its trades. Returns 1 if a run row was removed, else 0."""
    with _connect() as c:
        c.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
        cur = c.execute("DELETE FROM backtest_runs WHERE run_id = ?", (run_id,))
        return cur.rowcount or 0


def run_trades_csv(run_id: str) -> Optional[str]:
    """Return the run's trades as a CSV string (the download)."""
    with _connect() as c:
        rows = c.execute(
            "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_ts ASC",
            (run_id,),
        ).fetchall()
    if rows is None:
        return None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["symbol", "strike", "type", "expiry", "direction",
                "entry_ist", "entry_price", "sl", "tp",
                "exit_ist", "exit_price", "exit_reason",
                "gross_pnl", "charges", "net_pnl",
                "ambiguous_fill", "max_adverse", "max_favorable", "qty",
                "signal_symbol", "signal_side", "signal_sl", "signal_tp",
                "condition", "synthetic", "synth_kind"])
    for t in rows:
        def _g(k):
            try: return t[k]
            except Exception: return None
        sig_sl = _g("signal_sl"); sig_tp = _g("signal_tp")
        w.writerow([
            t["tradingsymbol"], t["strike"], t["instrument_type"], t["expiry"],
            t["direction"], _ist_str(t["entry_ts"]), f"{t['entry_price']:.2f}",
            f"{t['sl']:.2f}", f"{t['tp']:.2f}" if t["tp"] is not None else "",
            _ist_str(t["exit_ts"]),
            f"{t['exit_price']:.2f}" if t["exit_price"] is not None else "",
            t["exit_reason"] or "", f"{t['pnl']:.2f}",
            f"{t['charges']:.2f}", f"{t['net_pnl']:.2f}",
            int(t["ambiguous_fill"]), f"{t['max_adverse']:.2f}",
            f"{t['max_favorable']:.2f}", t["qty"],
            _g("signal_symbol") or "", _g("signal_side") or "",
            f"{sig_sl:.2f}" if sig_sl is not None else "",
            f"{sig_tp:.2f}" if sig_tp is not None else "",
            _g("condition") or "", int(_g("synthetic") or 0),
            _g("synth_kind") or "",
        ])
    return buf.getvalue()
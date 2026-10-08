# backend/app/backtest/repo/portfolio_repo.py
#
# ── PF_SAVED_PDF_20261008 ── named, saved portfolios for Backtest → Portfolio.
#
# A saved portfolio is a NAME plus the exact run_ids it composes — nothing
# else. The composition math stays where it has always been (client-side,
# composePortfolio in Portfolio.jsx, over the persisted trades), so opening a
# saved portfolio re-composes the same runs and shows the same numbers as the
# day it was saved. Nothing is snapshotted that could drift from the runs.
#
# Storage: table `backtest_portfolios` in backtest.db (the backtest-only DB —
# never the live app.db). Created here with CREATE TABLE IF NOT EXISTS, so
# schema.sql and its PyInstaller data-file path are untouched.
#
# Save-time validation mirrors the page's PF_VALIDATE rules, so a saved
# portfolio is always composable when saved: 2..MAX_LEGS runs, all present and
# status "done", one run per strategy, identical date_from/date_to.
# A run deleted later in Compare Runs is reported per leg (present=False);
# the page refuses to compose a partial portfolio rather than silently
# dropping the leg.

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Dict, List, Optional, Tuple

MAX_LEGS = 5           # same cap as MAX_PF in Portfolio.jsx
MAX_NAME = 80
MAX_NOTE = 500

_DDL = """
CREATE TABLE IF NOT EXISTS backtest_portfolios (
    pf_id         TEXT    PRIMARY KEY,
    name          TEXT    NOT NULL,
    name_key      TEXT    NOT NULL UNIQUE,      -- lower(trim(name)): case-insensitive uniqueness
    run_ids_json  TEXT    NOT NULL,             -- ["run_id", ...] in display order
    legs_json     TEXT    NOT NULL,             -- [{run_id, strategy_id, date_from, date_to}] at save time
    note          TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
"""


class PortfolioError(ValueError):
    """Validation failure; `status` is the HTTP code the route should return."""

    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _db_path():
    from app.backtest.repo.backtest_repo import _db_path as p
    return p()


def _connect(db_path=None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or _db_path()))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.executescript(_DDL)
    return c


def _name_key(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def _clean_name(name) -> str:
    n = " ".join(str(name or "").split())
    if not n:
        raise PortfolioError("Give the portfolio a name.")
    if len(n) > MAX_NAME:
        raise PortfolioError(f"Name is too long (max {MAX_NAME} characters).")
    return n


def _runs_meta(c: sqlite3.Connection, run_ids: List[str]) -> Dict[str, dict]:
    if not run_ids:
        return {}
    has = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_runs'").fetchone()
    if not has:
        return {}
    q = ",".join("?" for _ in run_ids)
    rows = c.execute(
        f"SELECT run_id, strategy_id, date_from, date_to, status FROM backtest_runs WHERE run_id IN ({q})",
        list(run_ids),
    ).fetchall()
    return {r["run_id"]: dict(r) for r in rows}


def validate_legs(run_ids, meta: Dict[str, dict]) -> List[dict]:
    """PF_VALIDATE, server side. Returns the legs in the given order."""
    if not isinstance(run_ids, list) or not all(isinstance(x, str) and x for x in run_ids):
        raise PortfolioError("run_ids must be a list of run ids.")
    ids = list(dict.fromkeys(run_ids))            # de-dupe, keep order
    if len(ids) < 2:
        raise PortfolioError("A portfolio needs at least 2 runs.")
    if len(ids) > MAX_LEGS:
        raise PortfolioError(f"A portfolio holds at most {MAX_LEGS} runs.")
    missing = [i for i in ids if i not in meta]
    if missing:
        raise PortfolioError(f"{len(missing)} run(s) not found: {', '.join(m[:8] for m in missing)}.", 404)
    notdone = [i for i in ids if (meta[i].get("status") or "done") != "done"]
    if notdone:
        raise PortfolioError(f"Only finished runs can be saved — {', '.join(n[:8] for n in notdone)} is not done.")
    seen: Dict[str, str] = {}
    for i in ids:
        sid = meta[i]["strategy_id"]
        if sid in seen:
            raise PortfolioError(f"One run per strategy — {sid} appears twice.")
        seen[sid] = i
    f, t = meta[ids[0]]["date_from"], meta[ids[0]]["date_to"]
    if any(meta[i]["date_from"] != f or meta[i]["date_to"] != t for i in ids):
        raise PortfolioError("Every run in a portfolio must cover the same date range.")
    return [{"run_id": i, "strategy_id": meta[i]["strategy_id"],
             "date_from": meta[i]["date_from"], "date_to": meta[i]["date_to"]} for i in ids]


def _row_out(r: sqlite3.Row, present: Optional[set] = None) -> dict:
    legs = json.loads(r["legs_json"] or "[]")
    if present is not None:
        for leg in legs:
            leg["present"] = leg["run_id"] in present
    return {
        "pf_id": r["pf_id"], "name": r["name"], "note": r["note"],
        "run_ids": json.loads(r["run_ids_json"] or "[]"),
        "legs": legs,
        "missing": sum(1 for leg in legs if leg.get("present") is False),
        "date_from": legs[0]["date_from"] if legs else None,
        "date_to": legs[0]["date_to"] if legs else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_portfolios(db_path=None) -> List[dict]:
    with _connect(db_path) as c:
        rows = c.execute("SELECT * FROM backtest_portfolios ORDER BY name_key ASC").fetchall()
        all_ids = sorted({i for r in rows for i in json.loads(r["run_ids_json"] or "[]")})
        present = set(_runs_meta(c, all_ids).keys())
    return [_row_out(r, present) for r in rows]


def save_portfolio(name, run_ids, note=None, replace: bool = False, db_path=None) -> Tuple[dict, bool]:
    """Create a portfolio, or (replace=True) overwrite the runs/note of the one
    with the same name. Returns (portfolio, replaced). A name clash without
    replace raises PortfolioError(status=409)."""
    nm = _clean_name(name)
    key = _name_key(nm)
    if note is not None:
        note = str(note).strip()[:MAX_NOTE] or None
    now = int(time.time())
    with _connect(db_path) as c:
        legs = validate_legs(run_ids, _runs_meta(c, list(run_ids) if isinstance(run_ids, list) else []))
        ids = [leg["run_id"] for leg in legs]
        existing = c.execute("SELECT * FROM backtest_portfolios WHERE name_key = ?", (key,)).fetchone()
        if existing and not replace:
            raise PortfolioError(f'A portfolio named "{existing["name"]}" already exists.', 409)
        if existing:
            c.execute(
                "UPDATE backtest_portfolios SET name = ?, run_ids_json = ?, legs_json = ?, note = ?, updated_at = ? "
                "WHERE pf_id = ?",
                (nm, json.dumps(ids), json.dumps(legs), note if note is not None else existing["note"],
                 now, existing["pf_id"]),
            )
            pf_id = existing["pf_id"]
        else:
            pf_id = uuid.uuid4().hex[:12]
            c.execute(
                "INSERT INTO backtest_portfolios (pf_id, name, name_key, run_ids_json, legs_json, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pf_id, nm, key, json.dumps(ids), json.dumps(legs), note, now, now),
            )
        c.commit()
        r = c.execute("SELECT * FROM backtest_portfolios WHERE pf_id = ?", (pf_id,)).fetchone()
        present = set(_runs_meta(c, ids).keys())
    return _row_out(r, present), bool(existing)


def delete_portfolio(pf_id: str, db_path=None) -> int:
    """Delete the saved portfolio only — its backtest runs are never touched."""
    with _connect(db_path) as c:
        cur = c.execute("DELETE FROM backtest_portfolios WHERE pf_id = ?", (str(pf_id),))
        c.commit()
        return cur.rowcount or 0

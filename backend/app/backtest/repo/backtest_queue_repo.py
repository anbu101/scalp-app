# backend/app/backtest/repo/backtest_queue_repo.py
#
# Persistent job queue for scheduled backtests. One table: backtest_queue.
# Status lifecycle: pending → running → (done | error | cancelled).
# The worker (queue_worker.py) consumes pending jobs oldest-first, one at a time.

from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

from app.backtest.repo.backtest_repo import _connect  # reuse same DB + heal


_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS backtest_queue (
    job_id       TEXT PRIMARY KEY,
    position     INTEGER,            -- ordering (enqueue sequence)
    strategy_id  TEXT NOT NULL,
    underlying   TEXT NOT NULL,
    date_from    TEXT NOT NULL,
    date_to      TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    label        TEXT,               -- optional human label
    status       TEXT NOT NULL,      -- pending|running|done|error|cancelled
    run_id       TEXT,               -- set when the job produces a run
    error_text   TEXT,
    created_at   INTEGER NOT NULL,
    started_at   INTEGER,
    finished_at  INTEGER
);
"""


def _ensure(conn):
    conn.executescript(_QUEUE_DDL)


def enqueue(*, strategy_id, underlying, date_from, date_to, config, label=None) -> dict:
    job_id = str(uuid.uuid4())
    now = int(time.time())
    with _connect() as c:
        _ensure(c)
        pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM backtest_queue").fetchone()[0]
        c.execute(
            """INSERT INTO backtest_queue
               (job_id, position, strategy_id, underlying, date_from, date_to,
                config_json, label, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, pos, strategy_id, underlying, date_from, date_to,
             json.dumps(config or {}), label, "pending", now),
        )
        c.commit()
    return {"job_id": job_id, "position": pos}


def _row_to_dict(r) -> dict:
    d = dict(r)
    d["config"] = json.loads(d.pop("config_json")) if d.get("config_json") else {}
    return d


def list_jobs() -> List[dict]:
    with _connect() as c:
        _ensure(c)
        rows = c.execute(
            "SELECT * FROM backtest_queue ORDER BY position ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def next_pending() -> Optional[dict]:
    with _connect() as c:
        _ensure(c)
        r = c.execute(
            "SELECT * FROM backtest_queue WHERE status = 'pending' ORDER BY position ASC LIMIT 1"
        ).fetchone()
    return _row_to_dict(r) if r else None


def has_running() -> bool:
    with _connect() as c:
        _ensure(c)
        r = c.execute("SELECT 1 FROM backtest_queue WHERE status = 'running' LIMIT 1").fetchone()
    return r is not None


def mark_running(job_id: str) -> None:
    with _connect() as c:
        _ensure(c)
        c.execute(
            "UPDATE backtest_queue SET status='running', started_at=? WHERE job_id=?",
            (int(time.time()), job_id),
        )
        c.commit()


def mark_done(job_id: str, run_id: str) -> None:
    with _connect() as c:
        _ensure(c)
        c.execute(
            "UPDATE backtest_queue SET status='done', run_id=?, finished_at=? WHERE job_id=?",
            (run_id, int(time.time()), job_id),
        )
        c.commit()


def mark_error(job_id: str, error_text: str) -> None:
    with _connect() as c:
        _ensure(c)
        c.execute(
            "UPDATE backtest_queue SET status='error', error_text=?, finished_at=? WHERE job_id=?",
            (error_text[:2000], int(time.time()), job_id),
        )
        c.commit()


def cancel_job(job_id: str) -> int:
    """Cancel a PENDING job (can't cancel a finished one here; the running job is
    cancelled via the run-cancel flag in the worker). Returns rows affected."""
    with _connect() as c:
        _ensure(c)
        cur = c.execute(
            "UPDATE backtest_queue SET status='cancelled', finished_at=? "
            "WHERE job_id=? AND status='pending'",
            (int(time.time()), job_id),
        )
        c.commit()
        return cur.rowcount or 0


def cancel_all_pending() -> int:
    with _connect() as c:
        _ensure(c)
        cur = c.execute(
            "UPDATE backtest_queue SET status='cancelled', finished_at=? WHERE status='pending'",
            (int(time.time()),),
        )
        c.commit()
        return cur.rowcount or 0


def clear_finished() -> int:
    with _connect() as c:
        _ensure(c)
        cur = c.execute(
            "DELETE FROM backtest_queue WHERE status IN ('done','error','cancelled')"
        )
        c.commit()
        return cur.rowcount or 0


def reset_orphaned_running() -> int:
    """On startup, any job left 'running' (app crashed mid-run) is reset to
    pending so the worker re-runs it."""
    with _connect() as c:
        _ensure(c)
        cur = c.execute(
            "UPDATE backtest_queue SET status='pending', started_at=NULL WHERE status='running'"
        )
        c.commit()
        return cur.rowcount or 0
# backend/app/backtest/repo/backtest_queue_repo.py
#
# Persistent job queue for scheduled backtests. One table: backtest_queue.
# Status lifecycle: pending → running → (done | error | cancelled).
# The worker (queue_worker.py) consumes pending jobs by POSITION ascending,
# one at a time. Positions start as the enqueue sequence but are user-mutable
# via move_job (QUEUE_REORDER) — up / down / top among PENDING jobs only.

from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

from app.backtest.repo.backtest_repo import _connect  # reuse same DB + heal


_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS backtest_queue (
    job_id       TEXT PRIMARY KEY,
    position     INTEGER,            -- ordering (enqueue sequence; user-mutable)
    strategy_id  TEXT NOT NULL,
    underlying   TEXT NOT NULL,
    date_from    TEXT NOT NULL,
    date_to      TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    label        TEXT,               -- optional human label ("PF:<name> · <strat>" for portfolios)
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
        # created_at as the tie-breaker keeps ordering deterministic even if a
        # rare reorder-vs-worker race ever leaves two rows on the same position.
        rows = c.execute(
            "SELECT * FROM backtest_queue ORDER BY position ASC, created_at ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def next_pending() -> Optional[dict]:
    with _connect() as c:
        _ensure(c)
        r = c.execute(
            "SELECT * FROM backtest_queue WHERE status = 'pending' "
            "ORDER BY position ASC, created_at ASC LIMIT 1"
        ).fetchone()
    return _row_to_dict(r) if r else None


# ── QUEUE_REORDER BEGIN ──────────────────────────────────────────────────────
def move_job(job_id: str, direction: str) -> int:
    """Reorder a PENDING job among the pending set: 'up' | 'down' | 'top'.

    Mechanism: read the pending jobs in queue order, permute the JOB ids, then
    write the SAME set of existing position values back onto the permuted
    order. The position-value set is preserved exactly — no renumbering, no
    collisions with finished/running rows, and the full-list display stays
    coherent (finished history keeps its place).

    Only rows still 'pending' are touched (the UPDATE re-checks status), so a
    job the worker grabs mid-call is simply skipped. BEGIN IMMEDIATE takes the
    write lock up front, making the read-permute-write atomic against the
    worker's own writes. Worst-case residual race: the worker had already
    SELECTed its next job before this call — it honors the pre-move order for
    that one job; everything after follows the new order. Benign, and the UI
    disables reordering of the running row anyway.

    Returns rows repositioned (0 = no-op: unknown/non-pending job or already
    at the requested edge). No-op is SUCCESS at the API level (idempotent-
    delete philosophy) — the desired end state already holds.
    """
    if direction not in ("up", "down", "top"):
        return 0
    with _connect() as c:
        _ensure(c)
        try:
            c.execute("BEGIN IMMEDIATE")
        except Exception:
            pass  # a transaction is already open on this connection — fine
        rows = c.execute(
            "SELECT job_id, position FROM backtest_queue WHERE status='pending' "
            "ORDER BY position ASC, created_at ASC"
        ).fetchall()
        ids = [r["job_id"] for r in rows]
        poss = [r["position"] for r in rows]
        if job_id not in ids:
            c.commit()
            return 0
        i = ids.index(job_id)
        if direction == "up":
            if i == 0:
                c.commit()
                return 0
            ids[i - 1], ids[i] = ids[i], ids[i - 1]
        elif direction == "down":
            if i == len(ids) - 1:
                c.commit()
                return 0
            ids[i + 1], ids[i] = ids[i], ids[i + 1]
        else:  # top
            if i == 0:
                c.commit()
                return 0
            ids.insert(0, ids.pop(i))
        moved = 0
        for pos, jid in zip(poss, ids):
            cur = c.execute(
                "UPDATE backtest_queue SET position=? "
                "WHERE job_id=? AND status='pending' AND position != ?",
                (pos, jid, pos),
            )
            moved += cur.rowcount or 0
        c.commit()
        return moved
# ── QUEUE_REORDER END ────────────────────────────────────────────────────────


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


# ── QUEUE_ROW_DELETE BEGIN ──────────────────────────────────────────────────
def job_status(job_id: str) -> Optional[str]:
    with _connect() as c:
        _ensure(c)
        r = c.execute("SELECT status FROM backtest_queue WHERE job_id=?", (job_id,)).fetchone()
    return r["status"] if r else None


def delete_job(job_id: str) -> int:
    """Hard-delete a FINISHED (done/error/cancelled) queue row. Never deletes a
    pending row (cancel it first — the tombstone is deliberate, it keeps 'I
    skipped this' visible) or a running one. Removes only the QUEUE ROW: a
    done job's persisted run in backtest_runs is UNTOUCHED and stays visible
    in Compare Runs / Portfolio (delete runs from Compare Runs if you mean
    that). Returns rows deleted."""
    with _connect() as c:
        _ensure(c)
        cur = c.execute(
            "DELETE FROM backtest_queue "
            "WHERE job_id=? AND status IN ('done','error','cancelled')",
            (job_id,),
        )
        c.commit()
        return cur.rowcount or 0
# ── QUEUE_ROW_DELETE END ─────────────────────────────────────────────────────


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
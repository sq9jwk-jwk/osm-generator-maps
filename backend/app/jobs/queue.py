"""Job queue stored in SQLite. The API enqueues, the worker process claims."""

from __future__ import annotations

import json
from typing import Any

from .. import db


def enqueue(
    kind: str, params: dict[str, Any], map_id: str | None = None, export_id: str | None = None
) -> str:
    job_id = db.new_id()
    t = db.now()
    with db.connect() as c:
        c.execute(
            "INSERT INTO jobs (id, kind, map_id, export_id, params, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, kind, map_id, export_id, json.dumps(params), t, t),
        )
    return job_id


def claim_next() -> dict[str, Any] | None:
    """Atomically take the oldest queued job."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        c.execute(
            "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
            (db.now(), row["id"]),
        )
    job = db.row_to_dict(row)
    assert job is not None
    job["status"] = "running"
    return job


def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def is_cancel_requested(job_id: str) -> bool:
    with db.connect() as c:
        row = c.execute("SELECT cancel FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row and row["cancel"])


def request_cancel(job_id: str) -> bool:
    with db.connect() as c:
        cur = c.execute(
            "UPDATE jobs SET cancel = 1, updated_at = ? WHERE id = ? AND status IN ('queued','running')",
            (db.now(), job_id),
        )
        # A queued job can be cancelled right away.
        c.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? AND status = 'queued'",
            (job_id,),
        )
    return cur.rowcount > 0


def requeue_interrupted() -> int:
    """After a restart, jobs left 'running' are queued again (render resumes)."""
    with db.connect() as c:
        cur = c.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
            (db.now(),),
        )
    return cur.rowcount

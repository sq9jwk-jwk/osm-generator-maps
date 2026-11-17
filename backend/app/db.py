"""SQLite storage (stdlib sqlite3, no ORM, so the package needs no extra deps).

Each call opens its own short-lived connection, which is safe across the API
process and the worker process (WAL mode).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .core.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- uploaded | importing | ready | failed
    bbox        TEXT,                   -- JSON [w,s,e,n]
    message     TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    received    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- import | render
    map_id      TEXT,
    params      TEXT NOT NULL,          -- JSON
    status      TEXT NOT NULL,          -- queued | running | done | failed | cancelled
    progress    REAL NOT NULL DEFAULT 0,
    message     TEXT,
    cancel      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
    id          TEXT PRIMARY KEY,
    map_id      TEXT NOT NULL,
    name        TEXT NOT NULL,
    grid_zoom   INTEGER NOT NULL,
    tiles       TEXT NOT NULL,          -- JSON [[x,y],...]
    created_at  REAL NOT NULL
);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("bbox", "params", "tiles"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d


def now() -> float:
    return time.time()

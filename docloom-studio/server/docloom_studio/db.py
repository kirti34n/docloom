"""SQLite storage: stdlib sqlite3, PRAGMA user_version migrations.

Short synchronous calls run inline with FastAPI's event loop; heavy work
(ingestion, generation) lives in jobs.
# ponytail: sync sqlite3 inline, single local user; aiosqlite only if a
# profiler ever says so.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

from .settings import data_dir

MIGRATIONS = [
    # v1
    """
    CREATE TABLE notebooks (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        created REAL NOT NULL, updated REAL NOT NULL
    );
    CREATE TABLE sources (
        id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id),
        kind TEXT NOT NULL,            -- file | url | text | research
        title TEXT NOT NULL DEFAULT '',
        path TEXT, url TEXT,
        status TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed
        context_mode TEXT NOT NULL DEFAULT 'full', -- full | insights | excluded
        meta_json TEXT NOT NULL DEFAULT '{}',
        created REAL NOT NULL
    );
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id),
        kind TEXT NOT NULL,            -- deck | doc | sheet | diagram | infographic
        title TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        payload_json TEXT NOT NULL,
        created REAL NOT NULL, updated REAL NOT NULL
    );
    CREATE TABLE artifact_versions (
        artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        version INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        created REAL NOT NULL,
        PRIMARY KEY (artifact_id, version)
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',   -- running | done | failed | cancelled
        notebook_id TEXT, artifact_id TEXT,
        events_json TEXT NOT NULL DEFAULT '[]',
        created REAL NOT NULL, updated REAL NOT NULL
    );
    CREATE TABLE assets (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,            -- logo | image | font | palette | icon
        filename TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        slot_hint TEXT,
        brand_kit_id TEXT,
        created REAL NOT NULL
    );
    CREATE TABLE brand_kits (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        theme_json TEXT NOT NULL DEFAULT '{}',
        created REAL NOT NULL
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
    """,
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "studio.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {i}")


def new_id() -> str:
    return secrets.token_urlsafe(9)


def now() -> float:
    return time.time()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(sql, params).fetchone()


def query_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> None:
    with _connect() as conn:
        conn.execute(sql, params)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]

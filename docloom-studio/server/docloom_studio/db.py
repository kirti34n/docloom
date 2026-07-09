"""Storage layer — driver-agnostic over SQLite (default) and Postgres.

By default this is stdlib sqlite3 at data_dir()/studio.db: zero-config, perfect
for local/self-hosted single-node use. Set DOCLOOM_DB_URL to a
postgres://... (or postgresql://...) DSN to run on Postgres instead — needed
for multi-node / horizontally-scaled SaaS where one shared DB backs many app
processes. Postgres support needs psycopg: `pip install docloom-studio[postgres]`.

The same `?`-placeholder queries and the same MIGRATIONS list drive both
backends; a thin translation layer adapts placeholders (`?`→`%s`), DDL types
(REAL→DOUBLE PRECISION), and migration-version bookkeeping per backend. All
callers use execute/query_one/query_all and never see the difference.

Short synchronous calls run inline with FastAPI's event loop; heavy work
(ingestion, generation) lives in jobs.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
from typing import Any

from .settings import data_dir

# postgres://… / postgresql://… → Postgres backend; empty → SQLite (default).
DB_URL = os.environ.get("DOCLOOM_DB_URL", "").strip()
IS_POSTGRES = DB_URL.startswith(("postgres://", "postgresql://"))

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
    # v2 — multi-tenant foundation: users, workspaces, server-side sessions.
    # Notebooks gain a (nullable) workspace_id; app queries enforce scoping.
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE TABLE auth_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created REAL NOT NULL,
        expires REAL NOT NULL
    );
    CREATE INDEX idx_workspaces_user ON workspaces(user_id);
    ALTER TABLE notebooks ADD COLUMN workspace_id TEXT;
    """,
    # v3 — per-user config: settings overrides + owned asset library.
    """
    CREATE TABLE user_settings (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    );
    ALTER TABLE assets ADD COLUMN user_id TEXT;
    """,
    # v4 — persisted chat: conversations survive reload.
    """
    CREATE TABLE chat_messages (
        id TEXT PRIMARY KEY,
        notebook_id TEXT NOT NULL REFERENCES notebooks(id),
        role TEXT NOT NULL,                 -- user | assistant
        text TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        created REAL NOT NULL
    );
    CREATE INDEX idx_chat_notebook ON chat_messages(notebook_id, created);
    """,
    # v5 — health probe: the /api/health check writes+deletes a row here to
    # prove the DB is writable (a read-only or full disk fails the check).
    """
    CREATE TABLE health_probe (id TEXT PRIMARY KEY, t REAL NOT NULL);
    """,
    # v6 — append-only job events: one row per emit() instead of rewriting the
    # whole events_json blob every time (which was O(events^2) per job).
    """
    CREATE TABLE job_events (
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        data_json TEXT,
        t REAL NOT NULL,
        PRIMARY KEY (job_id, seq)
    );
    """,
]


# --------------------------------------------------------- SQL translation

def _to_pg_placeholders(sql: str) -> str:
    """Rewrite `?` bind markers to psycopg's `%s`, skipping any inside single-
    quoted string literals. Our SQL has no `?` in literals, but this keeps the
    rule honest if one is ever added."""
    out, in_str = [], False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def _adapt_ddl_pg(script: str) -> str:
    """Adapt a migration's DDL for Postgres. Column types are otherwise
    standard SQL; only REAL needs widening — SQLite REAL is 8-byte, but PG REAL
    is 4-byte and would mangle epoch timestamps, so map it to DOUBLE PRECISION."""
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", script)


def _split_statements(script: str) -> list[str]:
    """Split a multi-statement DDL script on `;` (our DDL never puts `;` inside
    a literal). psycopg runs one statement per execute()."""
    return [s.strip() for s in script.split(";") if s.strip()]


# ---------------------------------------------------------------- SQLite

def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "studio.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")      # concurrent readers + one writer
    conn.execute("PRAGMA busy_timeout = 10000")    # wait on a locked db instead of erroring
    conn.execute("PRAGMA synchronous = NORMAL")    # safe with WAL, much faster
    return conn


def _init_sqlite() -> None:
    with _connect_sqlite() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {i}")


# --------------------------------------------------------------- Postgres

def _pg_connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:  # pragma: no cover - only when PG selected w/o driver
        raise RuntimeError(
            "DOCLOOM_DB_URL points at Postgres but psycopg isn't installed; "
            "run: pip install docloom-studio[postgres]"
        ) from e
    return psycopg.connect(DB_URL, row_factory=dict_row)


def _init_postgres() -> None:
    with _pg_connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version "
                     "(version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            version = 0
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        else:
            version = row["version"]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            for stmt in _split_statements(_adapt_ddl_pg(script)):
                conn.execute(stmt)
            conn.execute("UPDATE schema_version SET version = %s", (i,))
        conn.commit()


# ------------------------------------------------------------ public API

def init_db() -> None:
    if IS_POSTGRES:
        _init_postgres()
    else:
        _init_sqlite()


def new_id() -> str:
    return secrets.token_urlsafe(9)


def now() -> float:
    return time.time()


def _run(sql: str, params: tuple, fetch: str | None):
    """Execute one statement on the active backend. fetch: 'one' | 'all' | None.
    Rows come back as mappings on both backends (sqlite3.Row / psycopg dict_row),
    so callers use row["col"] uniformly."""
    if IS_POSTGRES:
        with _pg_connect() as conn:  # commits on clean exit, closes after
            cur = conn.execute(_to_pg_placeholders(sql), params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None
    with _connect_sqlite() as conn:
        cur = conn.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None


def query_one(sql: str, params: tuple = ()) -> Any | None:
    return _run(sql, params, "one")


def query_all(sql: str, params: tuple = ()) -> list[Any]:
    return _run(sql, params, "all")


def execute(sql: str, params: tuple = ()) -> None:
    _run(sql, params, None)


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def owner_of_notebook(notebook_id: str) -> str | None:
    """The user_id that owns a notebook (via its workspace), or None."""
    row = query_one(
        "SELECT w.user_id FROM notebooks n JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE n.id = ?", (notebook_id,))
    return row["user_id"] if row else None


def owner_of_source(source_id: str) -> str | None:
    """The user_id that owns a source (via notebook → workspace), or None."""
    row = query_one(
        "SELECT w.user_id FROM sources s JOIN notebooks n ON n.id = s.notebook_id "
        "JOIN workspaces w ON w.id = n.workspace_id WHERE s.id = ?", (source_id,))
    return row["user_id"] if row else None

"""Re-audit guard: _init_postgres must grab a Postgres advisory lock before it
touches the schema, so two app processes booting against one shared Postgres
serialize their migrations instead of colliding on duplicate DDL. A real
two-process race needs a live Postgres (the default backend here is SQLite), so
this checks the migration source directly: the advisory lock has to come first,
before the schema_version table is created."""

import inspect
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-reaudit-db-"))

from docloom_studio import db  # noqa: E402


def test_init_postgres_locks_before_ddl():
    # A real concurrent-boot race isn't unit-testable on SQLite, so assert the
    # guard-rail: the transaction advisory lock precedes the first CREATE TABLE.
    src = inspect.getsource(db._init_postgres)
    assert "pg_advisory_xact_lock" in src, \
        "concurrent PG migrators must serialize on a transaction advisory lock"
    lock_at = src.index("pg_advisory_xact_lock")
    create_at = src.index("CREATE TABLE")
    assert lock_at < create_at, \
        "the advisory lock must be taken before the first CREATE TABLE"

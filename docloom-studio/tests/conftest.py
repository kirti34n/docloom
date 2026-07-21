"""Test-suite isolation, forced before any test module (or docloom_studio.db)
is imported.

pytest imports a directory's conftest.py before any test module collected
from that directory, so the assignments below land before test_auth.py /
test_security.py / etc. run their own module-level
`os.environ.setdefault("DOCLOOM_STUDIO_HOME", ...)` (which then become
harmless no-ops) and before settings.data_dir() or docloom_studio.db (which
freezes DOCLOOM_DB_URL into a module constant at import time) are first
touched.

Without this, a developer who has exported DOCLOOM_STUDIO_HOME or
DOCLOOM_DB_URL to their real data dir / production Postgres DSN (as the
README instructs for normal use) would have this suite's autouse
`DELETE FROM ...` fixtures wipe real data on any test run that happens not
to import test_pipeline.py first.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["DOCLOOM_STUDIO_HOME"] = tempfile.mkdtemp(prefix="ds-tests-")

# DOCLOOM_DB_URL is overloaded: it's both the developer's real/production DSN
# and (per test_db_backend.py) the opt-in flag for the live Postgres
# round-trip. Popping it unconditionally would silently disable that
# round-trip forever, so the opt-in is routed through a dedicated,
# test-only variable instead.
_test_db_url = os.environ.get("DOCLOOM_TEST_DB_URL")
if _test_db_url:
    os.environ["DOCLOOM_DB_URL"] = _test_db_url
else:
    os.environ.pop("DOCLOOM_DB_URL", None)


def pytest_configure(config):
    # Lets pytest collect the regression tests below out of this file too
    # (conftest.py is otherwise loaded as a plugin, never scanned for tests).
    config.addinivalue_line("python_files", "conftest.py")


def _run_test_auth_subprocess(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "test_auth.py", "-q"],
        cwd=Path(__file__).resolve().parent,
        env=env, capture_output=True, text=True, timeout=120,
    )


def test_studio_home_override_survives_preset_real_dir(tmp_path):
    """A DOCLOOM_STUDIO_HOME preset to a real data dir (the documented,
    exported-in-your-shell case) must not be reachable by the suite's
    autouse DELETE FROM fixtures."""
    from docloom_studio import db as _db

    real = tmp_path / "real-home"
    real.mkdir()
    conn = sqlite3.connect(real / "studio.db")
    try:
        conn.execute("BEGIN")
        for script in _db.MIGRATIONS:
            for stmt in _db._split_statements(script):
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {len(_db.MIGRATIONS)}")
        uid, wid = _db.new_id(), _db.new_id()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, "dev@real.example", "x", _db.now()))
        conn.execute(
            "INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "real", _db.now()))
        conn.commit()
    finally:
        conn.close()

    env = dict(os.environ)
    env["DOCLOOM_STUDIO_HOME"] = str(real)
    env.pop("DOCLOOM_DB_URL", None)
    env.pop("DOCLOOM_TEST_DB_URL", None)
    result = _run_test_auth_subprocess(env)
    assert result.returncode == 0, result.stdout + result.stderr

    conn = sqlite3.connect(real / "studio.db")
    try:
        row = conn.execute(
            "SELECT 1 FROM users WHERE email = ?", ("dev@real.example",)).fetchone()
    finally:
        conn.close()
    assert row is not None, (
        "sentinel row was deleted -- DOCLOOM_STUDIO_HOME override was not applied")


def test_ambient_production_db_url_is_neutralized(tmp_path):
    """An ambient DOCLOOM_DB_URL pointing at a real DSN must never be used by
    this suite unless explicitly opted in via DOCLOOM_TEST_DB_URL."""
    env = dict(os.environ)
    env.pop("DOCLOOM_STUDIO_HOME", None)
    env["DOCLOOM_DB_URL"] = "postgresql://app:secret@bogus-prod-host:5432/prod"
    env.pop("DOCLOOM_TEST_DB_URL", None)
    result = _run_test_auth_subprocess(env)
    assert result.returncode == 0, (
        "an ambient production DOCLOOM_DB_URL leaked into the test run instead "
        "of being neutralized:\n" + result.stdout + result.stderr)


def test_dedicated_test_db_url_still_routes_to_postgres(tmp_path):
    """The opt-in Postgres round-trip (test_db_backend.py) must still be
    reachable via DOCLOOM_TEST_DB_URL even though ambient DOCLOOM_DB_URL is
    now neutralized."""
    env = dict(os.environ)
    env.pop("DOCLOOM_STUDIO_HOME", None)
    env.pop("DOCLOOM_DB_URL", None)
    env["DOCLOOM_TEST_DB_URL"] = "postgresql://app:secret@127.0.0.1:1/nonexistent"
    result = _run_test_auth_subprocess(env)
    output = (result.stdout + result.stderr).lower()
    assert result.returncode != 0 and ("postgres" in output or "psycopg" in output), (
        "DOCLOOM_TEST_DB_URL did not route to the Postgres backend:\n" + output)

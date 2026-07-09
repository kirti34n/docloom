"""Driver-agnostic DB layer: the SQL-translation helpers are pure and always
tested; a full round-trip against Postgres runs only when DOCLOOM_DB_URL points
at one (CI/local can opt in, the default sqlite run skips it)."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-dbbk-"))

import pytest  # noqa: E402

from docloom_studio import db  # noqa: E402


# ---- pure translation helpers (backend-independent) ----------------------

def test_placeholder_translation():
    assert db._to_pg_placeholders("SELECT * FROM t WHERE a = ? AND b = ?") == \
        "SELECT * FROM t WHERE a = %s AND b = %s"


def test_placeholder_skips_string_literals():
    # a literal ? inside quotes must survive untranslated
    sql = "SELECT * FROM t WHERE note = 'huh?' AND id = ?"
    assert db._to_pg_placeholders(sql) == \
        "SELECT * FROM t WHERE note = 'huh?' AND id = %s"


def test_ddl_widens_real_for_pg():
    out = db._adapt_ddl_pg("CREATE TABLE x (id TEXT, created REAL, n INTEGER)")
    assert "DOUBLE PRECISION" in out
    assert "REAL" not in out
    # doesn't clobber words merely containing 'real'
    assert db._adapt_ddl_pg("CREATE TABLE realm (id TEXT)") == \
        "CREATE TABLE realm (id TEXT)"


def test_split_statements():
    stmts = db._split_statements("CREATE TABLE a (x TEXT);\nCREATE INDEX i ON a(x);\n")
    assert stmts == ["CREATE TABLE a (x TEXT)", "CREATE INDEX i ON a(x)"]


def test_every_migration_adapts_cleanly():
    # each migration must split into runnable statements after PG adaptation
    for script in db.MIGRATIONS:
        stmts = db._split_statements(db._adapt_ddl_pg(script))
        assert stmts and all(s for s in stmts)


# ---- live Postgres round-trip (opt-in) -----------------------------------

_PG = os.environ.get("DOCLOOM_DB_URL", "").startswith(("postgres://", "postgresql://"))


@pytest.mark.skipif(not _PG, reason="set DOCLOOM_DB_URL to a Postgres DSN to run")
def test_postgres_roundtrip():
    assert db.IS_POSTGRES
    db.init_db()
    uid = db.new_id()
    db.execute("INSERT INTO users (id, email, password_hash, created) "
               "VALUES (?, ?, ?, ?)", (uid, f"{uid}@t.local", "x", db.now()))
    row = db.query_one("SELECT email, created FROM users WHERE id = ?", (uid,))
    assert row["email"] == f"{uid}@t.local"
    # REAL widened to DOUBLE PRECISION → epoch float keeps full precision
    assert abs(row["created"] - db.now()) < 5
    rows = db.query_all("SELECT id FROM users WHERE id = ?", (uid,))
    assert db.rows_to_dicts(rows)[0]["id"] == uid

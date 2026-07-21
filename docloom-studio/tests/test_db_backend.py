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


def test_pg_translation_of_atomic_save_sql():
    # the exact UPDATE...RETURNING save_artifact runs inside transaction():
    # every bind `?` becomes `%s`, and the 'ready' string literal (which
    # contains no `?`) survives untouched.
    sql = (
        "UPDATE artifacts SET title = ?, version = version + 1, "
        "payload_json = ?, updated = ?, status = 'ready' "
        "WHERE id = ? RETURNING version")
    out = db._to_pg_placeholders(sql)
    assert out.count("%s") == 4
    assert "?" not in out
    assert "'ready'" in out


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


@pytest.mark.skipif(not _PG, reason="set DOCLOOM_DB_URL to a Postgres DSN to run")
def test_postgres_concurrent_saves():
    """Same lost-update race as test_artifact_save_race.py's sqlite test, run
    against live Postgres: two savers at the same head version must not both
    compute the same next version. Smaller than the sqlite version (4 threads
    x 2 rounds) since this only runs opt-in against a real server."""
    import threading

    from docloom_studio.generate import save_artifact

    db.init_db()
    uid, wid, nid, aid = db.new_id(), db.new_id(), db.new_id(), db.new_id()
    db.execute("INSERT INTO users (id, email, password_hash, created) "
               "VALUES (?, ?, ?, ?)", (uid, f"{uid}@t.local", "x", db.now()))
    db.execute("INSERT INTO workspaces (id, user_id, name, created) "
               "VALUES (?, ?, ?, ?)", (wid, uid, "w", db.now()))
    db.execute("INSERT INTO notebooks (id, name, created, updated, workspace_id) "
               "VALUES (?, ?, ?, ?, ?)", (nid, "n", db.now(), db.now(), wid))
    db.execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
               "payload_json, created, updated) VALUES (?, ?, 'deck', 'T', 0, "
               "'{}', ?, ?)", (aid, nid, db.now(), db.now()))

    n_threads, rounds = 4, 2
    results: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    for r in range(rounds):
        barrier = threading.Barrier(n_threads)

        def worker(i: int, r: int = r) -> None:
            barrier.wait()
            try:
                v = save_artifact(aid, f"t-{r}-{i}", {"round": r, "i": i})
                with lock:
                    results.append(v)
            except BaseException as e:  # noqa: BLE001 - collecting for assert
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    total = n_threads * rounds
    assert errors == []
    assert sorted(results) == list(range(1, total + 1))

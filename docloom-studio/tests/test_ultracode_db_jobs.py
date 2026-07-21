"""Regression tests for the ultracode studio-db-jobs fixes:

1. get_render / get_audio path-traversal (Windows backslash escape of the
   artifact directory, reaching studio.db).
2. reconcile_jobs lease mode: a multi-node reconcile must distinguish a
   sibling node's live job from a crashed node's zombie by heartbeat, not
   status alone.
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.jobs import reconcile_jobs  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import data_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users", "jobs", "job_events"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> TestClient:
    c = TestClient(app)
    c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c


def _notebook(client: TestClient) -> str:
    wid = client.get("/api/workspaces").json()[0]["id"]
    return client.post(
        "/api/notebooks", json={"name": "n", "workspace_id": wid}).json()["id"]


def _artifact_row(notebook_id: str, kind: str = "diagram") -> str:
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, ?, 'T', 1, '{}', ?, ?)",
            (aid, notebook_id, kind, now(), now()))
    return aid


# ---- get_render / get_audio: cannot escape the artifact directory ---------

def test_get_render_serves_legit_file_but_blocks_traversal_to_studio_db():
    assert (data_dir() / "studio.db").is_file()  # otherwise this test proves nothing
    a = _register("render-escape-a@ex.com")
    aid = _artifact_row(_notebook(a))
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.svg").write_text("<svg>ok</svg>", encoding="utf-8")

    r = a.get(f"/api/artifacts/{aid}/render.svg")
    assert r.status_code == 200
    assert r.content == b"<svg>ok</svg>"

    for payload in ("%5C..%5C..%5C..%5Cstudio.db", "\\..\\..\\..\\studio.db"):
        r = a.get(f"/api/artifacts/{aid}/render.{payload}")
        assert r.status_code == 404, payload
        assert b"SQLite format 3" not in r.content


def test_get_audio_serves_legit_file_but_blocks_traversal_to_studio_db():
    assert (data_dir() / "studio.db").is_file()
    a = _register("audio-escape-a@ex.com")
    aid = _artifact_row(_notebook(a), kind="podcast")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "audio.wav").write_bytes(b"RIFF....WAVEfake")

    r = a.get(f"/api/artifacts/{aid}/audio.wav")
    assert r.status_code == 200
    assert r.content == b"RIFF....WAVEfake"

    for payload in ("%5C..%5C..%5C..%5Cstudio.db", "\\..\\..\\..\\studio.db"):
        r = a.get(f"/api/artifacts/{aid}/audio.{payload}")
        assert r.status_code == 404, payload
        assert b"SQLite format 3" not in r.content


def test_get_audio_ext_allowlist_blocks_content_type_reflection():
    """A crafted ext that is a direct child of the artifact dir (so it passes
    plain containment) must still be rejected by the alnum allowlist, since
    get_audio otherwise reflects ext unsanitized into the response's
    audio/{ext} Content-Type header."""
    a = _register("audio-reflect-a@ex.com")
    aid = _artifact_row(_notebook(a), kind="podcast")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "audio.wav;evil").write_bytes(b"RIFF....WAVEfake")

    r = a.get(f"/api/artifacts/{aid}/audio.wav;evil")
    assert r.status_code == 404


# ---- reconcile_jobs lease mode: sibling-node liveness discriminator -------

def _setup_owner() -> tuple[str, str]:
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) "
            "VALUES (?, ?, 'x', ?)", (uid, f"{uid}@test.local", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) "
            "VALUES (?, ?, 'ws', ?)", (wid, uid, now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, 'nb', ?, ?, ?)", (nb, wid, now(), now()))
    return uid, nb


def _artifact(nb: str, status: str) -> str:
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, status, created, updated) "
            "VALUES (?, ?, 'podcast', 'T', 1, '{}', ?, ?, ?)",
            (aid, nb, status, now(), now()))
    return aid


def _job(job_id: str, status: str, heartbeat: float, artifact_id: str | None) -> None:
    execute("INSERT INTO jobs (id, kind, status, notebook_id, artifact_id, "
            "events_json, created, updated, heartbeat) "
            "VALUES (?, 'deck', ?, NULL, ?, '[]', ?, ?, ?)",
            (job_id, status, artifact_id, now(), now(), heartbeat))


LEASE_SECONDS = 120.0


def test_reconcile_lease_mode_spares_a_siblings_live_job_but_reaps_a_zombie():
    _, nb = _setup_owner()
    live_art = _artifact(nb, "building")
    orphan_art = _artifact(nb, "building")

    _job("live", "running", now(), live_art)                          # sibling, fresh
    _job("zombie", "running", now() - (LEASE_SECONDS + 60), None)     # crashed node

    reconcile_jobs(lease_seconds=LEASE_SECONDS)

    assert query_one("SELECT status FROM jobs WHERE id = 'live'")["status"] == "running"
    assert query_one("SELECT status FROM jobs WHERE id = 'zombie'")["status"] == "failed"
    assert query_one(
        "SELECT status FROM artifacts WHERE id = ?", (live_art,))["status"] == "building"
    assert query_one(
        "SELECT status FROM artifacts WHERE id = ?", (orphan_art,))["status"] == "failed"


def test_reconcile_blanket_mode_unchanged_when_lease_seconds_omitted():
    """The default (no lease_seconds) call must still be the old blanket
    behavior: everything 'running' fails, regardless of heartbeat freshness."""
    _, nb = _setup_owner()
    art = _artifact(nb, "building")
    _job("fresh", "running", now(), art)  # would be spared under a lease

    reconcile_jobs()

    assert query_one("SELECT status FROM jobs WHERE id = 'fresh'")["status"] == "failed"
    assert query_one(
        "SELECT status FROM artifacts WHERE id = ?", (art,))["status"] == "failed"


def test_start_job_stamps_a_fresh_heartbeat_so_it_is_not_immediately_leasable():
    import asyncio

    from docloom_studio.jobs import JOBS, start_job

    async def work(ctx):
        await asyncio.sleep(0)

    async def run():
        jid = start_job("test", work)
        await JOBS[jid].task
        return jid

    jid = asyncio.run(run())
    row = query_one("SELECT heartbeat FROM jobs WHERE id = ?", (jid,))
    assert row["heartbeat"] > 0
    assert row["heartbeat"] >= now() - 5


def test_heartbeat_advances_while_job_is_running(monkeypatch):
    """The INSERT in start_job stamps a heartbeat once; a job's body can then
    run (or sit queued behind _MAX_CONCURRENT) for a long time with nothing
    else touching that column unless something refreshes it periodically.
    Assert the column actually moves forward mid-job, not just that it was
    non-zero at creation -- a hand-written fixture value could satisfy that
    weaker check even with no updater at all."""
    import asyncio

    from docloom_studio import jobs as J

    monkeypatch.setattr(J, "_HEARTBEAT_INTERVAL", 0.05)

    async def work(ctx):
        await asyncio.sleep(0.3)

    async def run():
        jid = J.start_job("test", work)
        initial = query_one(
            "SELECT heartbeat FROM jobs WHERE id = ?", (jid,))["heartbeat"]
        await asyncio.sleep(0.2)  # several beats at the patched 0.05s interval
        mid = query_one(
            "SELECT heartbeat FROM jobs WHERE id = ?", (jid,))["heartbeat"]
        await J.JOBS[jid].task
        return initial, mid

    initial, mid = asyncio.run(run())
    assert mid > initial


def test_heartbeat_failure_never_wedges_a_successful_job(monkeypatch):
    """A transient DB error out of the heartbeat UPDATE (locked SQLite file,
    closed connection at shutdown, ...) must not be able to prevent a job
    that otherwise completes successfully from reaching status='done', nor
    from pushing the SSE sentinel. Before the fix, _beat only caught
    asyncio.CancelledError, so this exception was stored on beat_task and
    re-raised by `await beat_task` in runner()'s finally -- escaping before
    the terminal status UPDATE and the sentinel push, wedging a successful
    job at status='running' forever and hanging the SSE generator."""
    import asyncio

    from docloom_studio import jobs as J

    monkeypatch.setattr(J, "_HEARTBEAT_INTERVAL", 0.02)

    real_execute = J.execute

    def flaky_execute(sql, params=()):
        if "SET heartbeat" in sql:
            raise sqlite3.OperationalError("database is locked")
        return real_execute(sql, params)

    monkeypatch.setattr(J, "execute", flaky_execute)

    async def work(ctx):
        # Several heartbeat ticks (at the patched interval) fire, and fail,
        # while the job body is still running.
        await asyncio.sleep(0.15)

    async def run():
        jid = J.start_job("test", work)
        frames: list[str] = []

        async def consume():
            async for frame in J.sse_events(jid):
                frames.append(frame)

        # If the bug reappears, sse_events() never returns and this hangs;
        # the timeout turns that into a clear test failure instead of a
        # stuck test run.
        await asyncio.wait_for(consume(), timeout=5)
        return jid, frames

    jid, frames = asyncio.run(run())

    # asyncio.wait_for above already proves sse_events() returned instead of
    # hanging (the sentinel, which itself carries no frame -- it's a
    # return-signal, not a payload -- got pushed and consumed); this checks
    # the terminal "job done" event actually reached the stream too.
    assert any('"stage": "job"' in f and '"status": "done"' in f for f in frames)
    row = query_one("SELECT status FROM jobs WHERE id = ?", (jid,))
    assert row["status"] == "done"


# ---- migration v3: pre-scoping assets get adopted, not orphaned -----------

def test_migration_v3_backfills_orphan_assets_to_oldest_user():
    """An asset created before v3 added assets.user_id has a NULL owner; the new
    user-scoped asset queries + irx export resolver would make it invisible and
    un-exportable. Migration v3's backfill must adopt it into the oldest user."""
    import sqlite3

    from docloom_studio import db as _db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # v1 (pre-owner assets table) + v2 (users/workspaces)
    for script in _db.MIGRATIONS[:2]:
        for stmt in _db._split_statements(script):
            conn.execute(stmt)
    uid = _db.new_id()
    conn.execute("INSERT INTO users (id, email, password_hash, created) "
                 "VALUES (?, ?, 'x', ?)", (uid, "owner@ex.com", _db.now()))
    aid = _db.new_id()
    conn.execute("INSERT INTO assets (id, type, filename, created) "
                 "VALUES (?, 'logo', 'logo.png', ?)", (aid, _db.now()))
    # v3 adds assets.user_id (NULL on the existing row), then the backfill runs
    for stmt in _db._split_statements(_db.MIGRATIONS[2]):
        conn.execute(stmt)
    assert conn.execute(
        "SELECT user_id FROM assets WHERE id = ?", (aid,)).fetchone()["user_id"] is None
    _db._backfill_orphan_assets(conn, translate=False)

    assert conn.execute(
        "SELECT user_id FROM assets WHERE id = ?", (aid,)).fetchone()["user_id"] == uid
    conn.close()

"""Phase-3 durability: jobs orphaned by a restart don't hang the UI forever,
and /api/health proves the DB is writable."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-jobs-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.jobs import reconcile_jobs  # noqa: E402
from docloom_studio.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


def test_reconcile_fails_orphaned_running_jobs():
    jid = new_id()
    execute("INSERT INTO jobs (id, kind, status, events_json, created, updated) "
            "VALUES (?, 'deck', 'running', '[]', ?, ?)", (jid, now(), now()))

    n = reconcile_jobs()
    assert n >= 1

    row = query_one("SELECT status FROM jobs WHERE id = ?", (jid,))
    assert row["status"] == "failed"
    # events are now append-only in job_events; read them back via job_state
    from docloom_studio.jobs import job_state
    events = job_state(jid)["events"]
    assert events[-1]["stage"] == "job" and events[-1]["status"] == "failed"
    assert "restart" in events[-1]["detail"]


def test_reconcile_leaves_terminal_jobs_untouched():
    done = new_id()
    execute("INSERT INTO jobs (id, kind, status, events_json, created, updated) "
            "VALUES (?, 'doc', 'done', '[]', ?, ?)", (done, now(), now()))
    reconcile_jobs()
    assert query_one("SELECT status FROM jobs WHERE id = ?", (done,))["status"] == "done"


def test_reconcile_is_idempotent():
    jid = new_id()
    execute("INSERT INTO jobs (id, kind, status, events_json, created, updated) "
            "VALUES (?, 'deck', 'running', '[]', ?, ?)", (jid, now(), now()))
    assert reconcile_jobs() >= 1
    assert reconcile_jobs() == 0  # nothing left running


def test_health_reports_db_writable():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["db"] is True
        assert "version" in body


def test_events_are_append_only_one_row_each():
    import asyncio

    from docloom_studio.jobs import job_state, start_job

    async def work(ctx):
        ctx.emit("a", "done")
        ctx.emit("b", "running", detail="x")
        ctx.emit("c", "done", data={"n": 1})

    async def run():
        jid = start_job("test", work)
        # let the runner finish
        from docloom_studio.jobs import JOBS
        await JOBS[jid].task
        return jid

    jid = asyncio.run(run())
    # one row per emit + the terminal "job/done" event
    rows = query_one("SELECT COUNT(*) AS n FROM job_events WHERE job_id = ?", (jid,))
    assert rows["n"] == 4
    events = job_state(jid)["events"]
    assert [e["stage"] for e in events] == ["a", "b", "c", "job"]
    assert events[-1]["status"] == "done" and events[2]["data"] == {"n": 1}

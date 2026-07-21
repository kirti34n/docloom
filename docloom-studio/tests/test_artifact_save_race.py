"""Regression tests for the save_artifact lost-update race.

save_artifact used to SELECT version -> UPDATE artifacts -> INSERT
artifact_versions as three separate auto-committing statements (db.py's old
_run opened a new connection per call). Two concurrent saves at the same
head version both computed the same next version: the second UPDATE silently
clobbered the first payload, and the two INSERTs either collided on the
(artifact_id, version) primary key or -- when the interleaving happened to
avoid that -- left the head row and its own version snapshot permanently
diverged with no error at all.

The fix (db.py's transaction() + generate.py's rewritten save_artifact) makes
the whole read-modify-write one BEGIN IMMEDIATE transaction and lets the
UPDATE itself allocate the version via `version = version + 1 ... RETURNING`,
so SQLite's write-lock serializes concurrent savers into distinct versions
instead of both computing the same one.
"""

from __future__ import annotations

import os
import tempfile
import threading

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-saverace-"))

import pytest  # noqa: E402

from docloom_studio.db import (  # noqa: E402
    execute, init_db, new_id, now, query_one, transaction,
)
from docloom_studio.generate import save_artifact  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users", "jobs", "job_events"):
        execute(f"DELETE FROM {t}")


def _seed_artifact() -> str:
    """A real user -> workspace -> notebook -> artifact chain (artifacts.id
    and notebooks.id are both FKs enforced with PRAGMA foreign_keys=ON), with
    the artifact seeded at version 0 and an empty payload -- exactly what
    create_artifact() leaves behind before the first save."""
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) "
            "VALUES (?, ?, ?, ?)", (uid, f"race-{uid}@ex.com", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) "
            "VALUES (?, ?, ?, ?)", (wid, uid, "w", now()))
    nid = new_id()
    execute("INSERT INTO notebooks (id, name, created, updated, workspace_id) "
            "VALUES (?, ?, ?, ?, ?)", (nid, "n", now(), now(), wid))
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, 'deck', 'T', 0, "
            "'{}', ?, ?)", (aid, nid, now(), now()))
    return aid


def test_concurrent_saves_allocate_distinct_versions_and_keep_head_consistent():
    aid = _seed_artifact()
    n_threads, rounds = 8, 3
    results: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    for r in range(rounds):
        barrier = threading.Barrier(n_threads)

        def worker(i: int, r: int = r) -> None:
            barrier.wait()  # maximize overlap of every thread's read-modify-write
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
    assert errors == []                                    # old code: PK IntegrityError
    assert sorted(results) == list(range(1, total + 1))    # old code: duplicate versions
    head = query_one("SELECT version, payload_json FROM artifacts WHERE id = ?", (aid,))
    snap = query_one(
        "SELECT payload_json FROM artifact_versions WHERE artifact_id = ? AND version = ?",
        (aid, head["version"]))
    assert head["version"] == total
    assert snap is not None and snap["payload_json"] == head["payload_json"]  # old: divergence
    count = query_one(
        "SELECT COUNT(*) AS c FROM artifact_versions WHERE artifact_id = ?", (aid,))
    assert count["c"] == total


def test_save_artifact_missing_id_raises_and_writes_nothing():
    with pytest.raises(LookupError):
        save_artifact("nope-does-not-exist", "T", {"a": 1})
    count = query_one(
        "SELECT COUNT(*) AS c FROM artifact_versions WHERE artifact_id = ?",
        ("nope-does-not-exist",))
    assert count["c"] == 0


def test_transaction_rolls_back_on_error():
    nid = new_id()
    with pytest.raises(RuntimeError):
        with transaction() as tx:
            tx.execute(
                "INSERT INTO notebooks (id, name, created, updated) "
                "VALUES (?, ?, ?, ?)", (nid, "rollback-me", now(), now()))
            raise RuntimeError("boom")
    assert query_one("SELECT id FROM notebooks WHERE id = ?", (nid,)) is None
    # the sqlite branch must release its write lock in `finally`, not hold it
    execute("INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
            (new_id(), "after-rollback", now(), now()))


def test_stale_snapshot_row_is_healed_not_kept():
    aid = _seed_artifact()
    execute("INSERT INTO artifact_versions (artifact_id, version, payload_json, created) "
            "VALUES (?, 1, '{\"stale\": true}', ?)", (aid, now()))
    version = save_artifact(aid, "T", {"fresh": True})
    assert version == 1
    head = query_one("SELECT payload_json FROM artifacts WHERE id = ?", (aid,))
    snap = query_one(
        "SELECT payload_json FROM artifact_versions WHERE artifact_id = ? AND version = 1",
        (aid,))
    assert snap["payload_json"] == head["payload_json"]

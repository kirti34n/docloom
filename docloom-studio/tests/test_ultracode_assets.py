"""Regression test for the ultracode audit, AREA: studio-assets.

Only the fix touching a file this area owns (embeddings.py) is covered here.
The other two findings researched for this area required editing db.py and
irx.py/artifacts.py, which are outside this area's file ownership, so they
were skipped rather than implemented (see the IMPLEMENT step's report)."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-assets-"))

import asyncio  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from docloom_studio import embeddings as E  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.ingest import _source_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _notebook() -> str:
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


def _source(nb: str, title: str, chunks: list[str], vecs: np.ndarray) -> str:
    sid = new_id()
    execute("INSERT INTO sources (id, notebook_id, kind, title, status, created) "
            "VALUES (?, ?, 'text', ?, 'ready', ?)", (sid, nb, title, now()))
    d = _source_dir(sid)
    (d / "chunks.jsonl").write_text(
        "\n".join(json.dumps({"text": t, "chunk_ix": i, "section": ""})
                  for i, t in enumerate(chunks)), encoding="utf-8")
    np.save(d / "embeddings.npy", vecs.astype(np.float32))
    return sid


def _stub_embed(monkeypatch, qvec: np.ndarray):
    async def fake_embed(cfg, texts):
        return np.array([qvec] * len(texts), dtype=np.float32)
    monkeypatch.setattr(E, "embed", fake_embed)


def test_model_switch_marks_outdated_source_not_fresh_one(monkeypatch):
    """After an embedding-model switch, retrieve() must flag the source still
    at the OLD dimension as stale and keep the freshly re-embedded source that
    already matches the query's (current-model) width -- not the reverse.
    Source A (old model, loaded first) must lose to source B (new model,
    loaded second), because the query's own embedding is the authority for
    which dimension is 'current', not whichever source happened to load first.
    """
    nb = _notebook()
    old_v = np.array([[1.0, 0.0]], dtype=np.float32)
    a = _source(nb, "Old", ["stale content from before the switch"], old_v)
    new_v = np.array([[0.0, 0.0, 1.0]], dtype=np.float32)
    b = _source(nb, "Fresh", ["fresh content after the switch"], new_v)
    _stub_embed(monkeypatch, np.array([0.0, 0.0, 1.0], dtype=np.float32))

    out = asyncio.run(E.retrieve(nb, "fresh content", k=5))

    assert {r.source_id for r in out} == {b}
    assert out[0].score > 0  # dense cosine is live: 3-dim corpus matches 3-dim query
    assert query_one("SELECT status FROM sources WHERE id = ?", (a,))["status"] == "stale"
    assert query_one("SELECT status FROM sources WHERE id = ?", (b,))["status"] == "ready"

"""Hybrid retrieval: BM25 lexical + cosine dense fused with RRF, near-duplicate
dedup, per-source coverage floor, and stale-source health marking."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-retr-"))

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


# ---- pure BM25 unit ------------------------------------------------------

def test_bm25_ranks_exact_term():
    corpus = [E._tokens("the quarterly revenue was strong"),
              E._tokens("employees enjoy remote work flexibility")]
    scores = E._bm25_scores(corpus, "revenue")
    assert scores[0] > 0 and scores[1] == 0


def test_bm25_zero_when_no_overlap():
    corpus = [E._tokens("alpha beta gamma")]
    assert E._bm25_scores(corpus, "delta epsilon").sum() == 0


# ---- fused retrieval -----------------------------------------------------

def test_lexical_signal_surfaces_exact_id(monkeypatch):
    """An exact token (an order id) the dense vectors miss still gets retrieved
    because BM25 catches it and RRF fuses it in."""
    nb = _notebook()
    # dense vectors all point the same way → cosine is uninformative here
    v = np.array([[1.0, 0.0]], dtype=np.float32)
    _source(nb, "Tickets", ["order ORD-99423 was refunded",
                            "general chit chat about weather"], np.vstack([v, v]))
    _stub_embed(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    out = asyncio.run(E.retrieve(nb, "ORD-99423", k=2))
    assert out and out[0].text.startswith("order ORD-99423")


def test_coverage_floor_represents_each_source(monkeypatch):
    nb = _notebook()
    v = np.array([[1.0, 0.0]], dtype=np.float32)
    a = _source(nb, "A", ["async standups reduce interruptions"], v)
    b = _source(nb, "B", ["remote teams value flexibility"], v)
    _stub_embed(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    out = asyncio.run(E.retrieve(nb, "remote async work", k=2))
    assert {r.source_id for r in out} == {a, b}


def test_dedupes_near_duplicate_chunks(monkeypatch):
    nb = _notebook()
    v = np.array([[1.0, 0.0]], dtype=np.float32)
    dup = "the mission is to make documents accurate and editable"
    _source(nb, "Doc", [dup, dup], np.vstack([v, v]))
    _stub_embed(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    out = asyncio.run(E.retrieve(nb, "documents accurate editable", k=5))
    assert len(out) == 1  # the duplicate chunk collapses


def test_stale_source_is_flagged(monkeypatch):
    nb = _notebook()
    # 2 chunks but only 1 vector → mismatch → marked stale, skipped
    v = np.array([[1.0, 0.0]], dtype=np.float32)
    sid = _source(nb, "Mismatch", ["chunk one", "chunk two"], v)
    _stub_embed(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    out = asyncio.run(E.retrieve(nb, "anything", k=5))
    assert out == []
    assert query_one("SELECT status FROM sources WHERE id = ?", (sid,))["status"] == "stale"

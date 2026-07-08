"""Embeddings + brute-force cosine retrieval.

Embeddings come from the configured provider (Ollama nomic-embed-text by
default). Vectors live as one .npy per source; retrieval concatenates the
enabled sources and does an exact cosine top-k.
# ponytail: brute-force cosine, instant to ~100k chunks; add hnswlib past that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .db import query_all
from .ingest import _source_dir, load_chunks
from .providers import ProviderConfig, embed
from .settings import get_setting


def _embed_cfg() -> ProviderConfig:
    return ProviderConfig(**get_setting("provider.embeddings"))


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


async def embed_source(source_id: str, texts: list[str]) -> None:
    if not texts:
        return
    vectors = await embed(_embed_cfg(), texts)
    np.save(_source_dir(source_id) / "embeddings.npy", vectors.astype(np.float32))


@dataclass
class Retrieved:
    source_id: str
    source_title: str
    chunk_ix: int
    page: int | None
    section: str
    text: str
    score: float


def _enabled_sources(notebook_id: str) -> list[tuple[str, str]]:
    rows = query_all(
        "SELECT id, title FROM sources WHERE notebook_id = ? AND status = 'ready' "
        "AND context_mode != 'excluded'",
        (notebook_id,),
    )
    return [(r["id"], r["title"]) for r in rows]


async def retrieve(notebook_id: str, query: str, k: int = 12) -> list[Retrieved]:
    sources = _enabled_sources(notebook_id)
    if not sources:
        return []

    mats: list[np.ndarray] = []
    index: list[tuple[str, str, dict]] = []  # (source_id, title, chunk)
    for source_id, title in sources:
        npy = _source_dir(source_id) / "embeddings.npy"
        if not npy.is_file():
            continue
        vecs = np.load(npy)
        chunks = load_chunks(source_id)
        if len(chunks) != len(vecs):
            continue  # stale; re-ingest needed
        mats.append(vecs)
        for c in chunks:
            index.append((source_id, title, c))
    if not mats:
        return []

    corpus = _normalize(np.vstack(mats).astype(np.float32))
    q = await embed(_embed_cfg(), [query])
    qn = _normalize(q.astype(np.float32))[0]
    scores = corpus @ qn
    top = np.argsort(-scores)[:k]

    out: list[Retrieved] = []
    for i in top:
        source_id, title, c = index[int(i)]
        out.append(Retrieved(
            source_id=source_id, source_title=title,
            chunk_ix=int(c.get("chunk_ix", 0)), page=c.get("page"),
            section=c.get("section", ""), text=c["text"], score=float(scores[int(i)]),
        ))
    return out

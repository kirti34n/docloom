"""Embeddings + hybrid retrieval.

Embeddings come from the configured provider (Ollama nomic-embed-text by
default). Vectors live as one .npy per source. Retrieval fuses two signals so
generated documents stay accurate:

  * dense cosine over the embeddings — semantic match, paraphrase-tolerant
  * a lexical BM25 over the same chunks — exact terms, IDs, numbers, code

The two rankings are combined with Reciprocal Rank Fusion (backend-agnostic,
so this works identically on SQLite and Postgres — no FTS5/tsvector split).
Near-duplicate chunks are dropped and a per-source coverage floor guarantees a
multi-source notebook ("research all") can't collapse onto one verbose source.
# ponytail: brute-force cosine+bm25, instant to ~100k chunks; add hnswlib past that.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .db import execute, owner_of_notebook, owner_of_source, query_all
from .ingest import _source_dir, load_chunks
from .providers import ProviderConfig, embed
from .settings import get_setting

_WORD = re.compile(r"[a-z0-9]+")
_RRF_K = 60  # standard reciprocal-rank-fusion damping constant


def _embed_cfg(user_id: str | None) -> ProviderConfig:
    return ProviderConfig(**get_setting("provider.embeddings", user_id))


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _bm25_scores(corpus_tokens: list[list[str]], query: str) -> np.ndarray:
    """BM25 relevance of each chunk to the query (0 when no term overlaps)."""
    q_terms = set(_tokens(query))
    n = len(corpus_tokens)
    scores = np.zeros(n, dtype=np.float32)
    if not q_terms or n == 0:
        return scores
    lengths = np.array([len(t) for t in corpus_tokens], dtype=np.float32)
    avg_len = float(lengths.mean()) or 1.0
    df = Counter()
    for toks in corpus_tokens:
        for term in set(toks) & q_terms:
            df[term] += 1
    k1, b = 1.5, 0.75
    for term in q_terms:
        n_q = df.get(term, 0)
        if n_q == 0:
            continue
        idf = math.log(1 + (n - n_q + 0.5) / (n_q + 0.5))
        for i, toks in enumerate(corpus_tokens):
            tf = toks.count(term)
            if tf == 0:
                continue
            denom = tf + k1 * (1 - b + b * lengths[i] / avg_len)
            scores[i] += idf * (tf * (k1 + 1)) / denom
    return scores


def _rrf_ranks(scores: np.ndarray) -> dict[int, int]:
    """Map row index -> 1-based rank by descending score (only positive scores
    rank; zeros contribute nothing to fusion)."""
    order = np.argsort(-scores)
    ranks: dict[int, int] = {}
    rank = 0
    for i in order:
        if scores[int(i)] <= 0:
            break
        rank += 1
        ranks[int(i)] = rank
    return ranks


async def embed_source(source_id: str, texts: list[str]) -> None:
    if not texts:
        return
    vectors = await embed(_embed_cfg(owner_of_source(source_id)), texts)
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


def _mark_stale(source_id: str) -> None:
    """Flag a source whose vectors no longer match its chunks so the UI can
    prompt a re-ingest, instead of it silently showing 'ready' but unretrievable."""
    execute("UPDATE sources SET status = 'stale' WHERE id = ? AND status = 'ready'",
            (source_id,))


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
            _mark_stale(source_id)  # surface it rather than skip silently
            continue
        mats.append(vecs)
        for c in chunks:
            index.append((source_id, title, c))
    if not mats:
        return []

    # dense cosine
    corpus = _normalize(np.vstack(mats).astype(np.float32))
    q = await embed(_embed_cfg(owner_of_notebook(notebook_id)), [query])
    qn = _normalize(q.astype(np.float32))[0]
    cosine = corpus @ qn

    # lexical BM25 over the same chunks
    corpus_tokens = [_tokens(c["text"]) for _, _, c in index]
    bm25 = _bm25_scores(corpus_tokens, query)

    # reciprocal-rank fusion of the two signals
    dense_ranks = _rrf_ranks(cosine)
    lex_ranks = _rrf_ranks(bm25)
    fused = np.zeros(len(index), dtype=np.float32)
    for i in range(len(index)):
        s = 0.0
        if i in dense_ranks:
            s += 1.0 / (_RRF_K + dense_ranks[i])
        if i in lex_ranks:
            s += 1.0 / (_RRF_K + lex_ranks[i])
        fused[i] = s

    order = [int(i) for i in np.argsort(-fused) if fused[int(i)] > 0]

    # drop near-duplicate chunk text (normalized), keep first (highest fused)
    seen_text: set[str] = set()
    deduped: list[int] = []
    for i in order:
        key = " ".join(corpus_tokens[i][:40])
        if key in seen_text:
            continue
        seen_text.add(key)
        deduped.append(i)

    # per-source coverage floor: guarantee each source with a hit contributes at
    # least one chunk before filling the rest by fused score
    picked: list[int] = []
    per_source_seen: set[str] = set()
    for i in deduped:
        sid = index[i][0]
        if sid not in per_source_seen:
            per_source_seen.add(sid)
            picked.append(i)
    for i in deduped:
        if len(picked) >= k:
            break
        if i not in picked:
            picked.append(i)
    picked = picked[:k]

    out: list[Retrieved] = []
    for i in picked:
        source_id, title, c = index[i]
        out.append(Retrieved(
            source_id=source_id, source_title=title,
            chunk_ix=int(c.get("chunk_ix", 0)), page=c.get("page"),
            section=c.get("section", ""), text=c["text"], score=float(fused[i]),
        ))
    return out

"""M3: ingestion + retrieval (real Ollama embeddings) and grounded generation
with the citation gate (stubbed generation provider)."""

import asyncio
import json
import os
import tempfile

import httpx
import pytest

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="dstudio-nb-"))

from docloom import Slide  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now  # noqa: E402
from docloom_studio.embeddings import retrieve  # noqa: E402
from docloom_studio.generate import Outline, OutlineItem  # noqa: E402
from docloom_studio.ingest import ingest_source  # noqa: E402


def _ollama_up() -> bool:
    try:
        httpx.get("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="Ollama not running")


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _notebook() -> str:
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
            (nb, "nb", now(), now()))
    return nb


def _text_source(nb: str, title: str, text: str) -> str:
    sid = new_id()
    execute("INSERT INTO sources (id, notebook_id, kind, title, status, "
            "context_mode, meta_json, created) VALUES (?, ?, 'text', ?, 'pending', "
            "'full', ?, ?)", (sid, nb, title, json.dumps({"text": text}), now()))
    return sid


@needs_ollama
def test_ingest_and_retrieve_ranks_relevant_chunk():
    nb = _notebook()
    _text_source(nb, "Whales", "Blue whales are the largest animals ever known "
                 "to have lived, reaching 30 metres in length.")
    sid2 = _text_source(nb, "Volcanoes", "Basaltic lava flows are runny and "
                        "travel far; rhyolitic magma is viscous and explosive.")
    asyncio.run(ingest_source(list_first_source(nb)))
    asyncio.run(ingest_source(sid2))

    hits = asyncio.run(retrieve(nb, "how big is a blue whale", k=3))
    assert hits, "no retrieval hits"
    assert hits[0].source_title == "Whales", hits[0].source_title


def list_first_source(nb: str) -> str:
    from docloom_studio.db import query_one

    return query_one("SELECT id FROM sources WHERE notebook_id = ? ORDER BY created",
                     (nb,))["id"]


@needs_ollama
def test_excluded_source_drops_from_retrieval():
    nb = _notebook()
    sid = _text_source(nb, "Secret", "The password is hunter2 and nothing else.")
    asyncio.run(ingest_source(sid))
    assert asyncio.run(retrieve(nb, "password", k=3))  # visible while full
    execute("UPDATE sources SET context_mode = 'excluded' WHERE id = ?", (sid,))
    assert asyncio.run(retrieve(nb, "password", k=3)) == []


def test_grounded_generation_injects_sources_and_gates_cites(monkeypatch):
    """Stubbed generation: the model cites a real source id and a hallucinated
    one; the deck must carry the real source and drop the fake cite."""
    outline = Outline(deck_title="Whales",
                      slides=[OutlineItem(title="Size", layout="content",
                                          intent="how big")])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3,
                      on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            return outline
        return Slide(layout="content", title="Size", blocks=[
            {"type": "paragraph", "text": [
                {"text": "Blue whales reach 30m", "cite": "src-real"},
                {"text": " and can fly", "cite": "src-fake"},
            ]},
        ])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")

    class Ctx:
        def emit(self, *a, **k):
            pass

    sources = [{"id": "src-real", "title": "Marine Biology"}]
    asyncio.run(gen.run_deck_pipeline(Ctx(), nb, aid, "whales", ["evidence"], sources))

    from docloom_studio.db import query_one

    payload = json.loads(query_one("SELECT payload_json FROM artifacts WHERE id = ?",
                                   (aid,))["payload_json"])
    doc = payload["ir"]
    assert any(s["id"] == "src-real" for s in doc["sources"])
    # the fake cite was stripped; the real one survives
    spans = doc["slides"][1]["blocks"][0]["text"]
    cites = [sp.get("cite") for sp in spans]
    assert "src-real" in cites and "src-fake" not in cites


@needs_ollama
def test_research_adds_cited_web_sources(monkeypatch):
    """Real ddgs search + page fetch + embed; only the plan LLM is stubbed."""
    from docloom_studio import research as rsm
    from docloom_studio.research import ResearchPlan, run_research
    from docloom_studio.db import query_all

    async def fake_plan(cfg, messages, schema, parse, lint_fn=None, max_rounds=3,
                        on_round=None):
        return ResearchPlan(queries=["async standups remote engineering teams"])

    monkeypatch.setattr(rsm, "generate_validated", fake_plan)

    class Ctx:
        def emit(self, *a, **k):
            pass

    nb = _notebook()
    try:
        asyncio.run(run_research(Ctx(), nb, "async standups"))
    except Exception as e:
        pytest.skip(f"network/search unavailable: {e}")

    rows = query_all("SELECT id, kind, status FROM sources WHERE notebook_id = ?", (nb,))
    ready = [r for r in rows if r["kind"] == "research" and r["status"] == "ready"]
    if not ready:
        pytest.skip("no readable pages fetched (network)")
    assert len(ready) >= 1
    # the fetched pages are retrievable + grounded
    hits = asyncio.run(retrieve(nb, "how do async standups work", k=3))
    assert hits

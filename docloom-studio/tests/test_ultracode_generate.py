"""Regression tests for the _section_block cite-set widening fix.

_section_block runs a fresh, per-section/per-slide retrieval over ALL
enabled sources (not just the broad top-16 `sources` set), and injects
`[cite id: "X"]` for each chunk it surfaces, explicitly inviting the model
to cite ids outside the broad set. Before this fix, the per-unit lint,
_citation_gate, and the final Document.sources all validated cites against
ONLY the broad set, so a compliant cite to a section-only source was
flagged cite/unknown-source, the unit was retried to exhaustion, and the
fully-generated slide/section was discarded as a blank skeleton.

_section_block now also returns the distinct sources it surfaced in that
call; run_deck_pipeline/run_doc_pipeline merge those into a per-unit id-set
(broad ∪ this unit's section ids) used for that unit's lint, and into the
document-level union used by _citation_gate and Document.sources.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-"))

import pytest  # noqa: E402

from docloom import Document, Paragraph, Slide, Span, lint  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.embeddings import Retrieved  # noqa: E402
from docloom_studio.providers import GenerationFailed  # noqa: E402


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


class FakeCtx:
    def __init__(self):
        self.events = []

    def emit(self, stage, status="running", detail="", data=None):
        self.events.append((stage, status, detail, data))


async def _fake_retrieve(nb, query, k=12):
    return [Retrieved(source_id="src_z", source_title="Niche Memo", chunk_ix=0,
                      page=3, section="", text="Niche fact only in src_z.", score=0.9)]


# ================================================== 1. _section_block itself


def test_section_block_returns_distinct_sources_it_injected(monkeypatch):
    monkeypatch.setattr("docloom_studio.embeddings.retrieve", _fake_retrieve)
    block, srcs = asyncio.run(gen._section_block("nb", "q", "base", True))
    assert 'cite id: "src_z"' in block
    assert srcs == [{"id": "src_z", "title": "Niche Memo"}]


def test_section_block_returns_empty_sources_when_no_sources_enabled():
    block, srcs = asyncio.run(gen._section_block("nb", "q", "base", False))
    assert block == "base"
    assert srcs == []


def test_section_block_returns_empty_sources_when_retrieval_finds_nothing(monkeypatch):
    async def empty_retrieve(nb, query, k=12):
        return []
    monkeypatch.setattr("docloom_studio.embeddings.retrieve", empty_retrieve)
    block, srcs = asyncio.run(gen._section_block("nb", "q", "base", True))
    assert block == "base"
    assert srcs == []


# ============================================ 2. run_deck_pipeline end-to-end


def test_deck_pipeline_keeps_slide_citing_section_only_source(monkeypatch):
    """A slide that cites a source surfaced only by this slide's own section
    retrieval (not in the broad top-16 `sources` set) must survive: the
    per-slide lint must accept it, _citation_gate must not strip the cite,
    and the saved Document.sources/export-time lint must not flag it."""
    monkeypatch.setattr("docloom_studio.embeddings.retrieve", _fake_retrieve)

    outline = gen.Outline(deck_title="Deck", slides=[
        gen.OutlineItem(title="S1", layout="content", intent="cover the niche fact"),
        gen.OutlineItem(title="S2", layout="content", intent="more"),
        gen.OutlineItem(title="S3", layout="content", intent="more"),
    ])

    def make_slide(title):
        return Slide(layout="content", title=title,
                    blocks=[Paragraph(text=[
                        Span(text="Niche fact only in src_z.", cite="src_z")])])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        user = messages[-1]["content"]
        if "Draft slide" not in user:
            return outline
        result = make_slide("Grounded slide")
        if lint_fn is not None:
            errs = lint_fn(result)
            if errs:
                raise GenerationFailed(
                    [{"round": r, "lint_errors": errs} for r in (1, 2, 3)])
        return result

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")
    ctx = FakeCtx()
    asyncio.run(gen.run_deck_pipeline(
        ctx, nb, aid, "topic",
        context_lines=['[cite id: "src_a"] (Broad) broad fact'],
        sources=[{"id": "src_a", "title": "Broad"}]))

    ir = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])["ir"]

    content_slides = [s for s in ir["slides"] if s["layout"] != "title"]
    assert content_slides
    for s in content_slides:
        assert s["blocks"], "content slide was discarded as a blank skeleton"
        assert "(generation failed)" not in (s.get("notes") or "")

    cites = {
        sp.get("cite")
        for s in content_slides
        for b in s["blocks"]
        for sp in b.get("text", [])
        if isinstance(sp, dict)
    }
    assert "src_z" in cites  # the section-only cite survived _citation_gate

    source_ids = {s["id"] for s in ir["sources"]}
    assert "src_z" in source_ids  # widened into Document.sources
    assert "src_a" in source_ids  # broad set still present

    doc = Document.model_validate(ir)
    findings = lint(doc)
    assert [f for f in findings if f.rule == "cite/unknown-source"] == []


def test_deck_pipeline_rejects_a_cite_to_a_source_a_different_slide_saw(monkeypatch):
    """Guard against over-widening: the per-slide lint must stay scoped to
    THIS slide's own evidence (broad ∪ this slide's own section retrieval),
    never the cumulative running union of every slide processed so far.
    Slide 1's retrieval surfaces src_only_1 (legitimately cited by slide 1,
    and correctly merged into Document.sources). Slide 2's own retrieval
    surfaces a DIFFERENT source, src_only_2 -- but slide 2 fabricates a cite
    to src_only_1, a source only ever shown to slide 1. Even though
    src_only_1 is already sitting in the pipeline's cumulative known-sources
    accumulator by the time slide 2 runs, slide 2's own lint must still
    reject that cite (GenerationFailed -> blank skeleton for slide 2 only),
    exactly as it would have before src_only_1 existed at all. A wrong fix
    that lints each slide against the cumulative running union instead of
    that slide's own shown evidence would let this cite through."""
    async def routed_retrieve(nb, query, k=12):
        if "S1" in query:
            return [Retrieved(source_id="src_only_1", source_title="Only1",
                              chunk_ix=0, page=None, section="",
                              text="fact only slide 1 saw", score=0.9)]
        if "S2" in query:
            return [Retrieved(source_id="src_only_2", source_title="Only2",
                              chunk_ix=0, page=None, section="",
                              text="fact only slide 2 saw", score=0.9)]
        return []

    monkeypatch.setattr("docloom_studio.embeddings.retrieve", routed_retrieve)

    outline = gen.Outline(deck_title="Deck", slides=[
        gen.OutlineItem(title="S1", layout="content", intent="x"),
        gen.OutlineItem(title="S2", layout="content", intent="x"),
        gen.OutlineItem(title="S3", layout="content", intent="x"),
    ])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        user = messages[-1]["content"]
        if "Draft slide" not in user:
            return outline
        if '"S1"' in user:
            result = Slide(layout="content", title="S1",
                          blocks=[Paragraph(text=[
                              Span(text="fact only slide 1 saw", cite="src_only_1")])])
        elif '"S2"' in user:
            # fabricated: cites the source only slide 1 was ever shown
            result = Slide(layout="content", title="S2",
                          blocks=[Paragraph(text=[
                              Span(text="fact only slide 1 saw", cite="src_only_1")])])
        else:
            result = Slide(layout="content", title="S3",
                          blocks=[Paragraph(text=[Span(text="an uncited fact")])])
        if lint_fn is not None:
            errs = lint_fn(result)
            if errs:
                raise GenerationFailed(
                    [{"round": r, "lint_errors": errs} for r in (1, 2, 3)])
        return result

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")
    ctx = FakeCtx()
    asyncio.run(gen.run_deck_pipeline(
        ctx, nb, aid, "topic",
        context_lines=['[cite id: "src_a"] (Broad) broad fact'],
        sources=[{"id": "src_a", "title": "Broad"}]))

    ir = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])["ir"]
    by_title = {s["title"]: s for s in ir["slides"] if s["layout"] != "title"}

    # slide 1's own, legitimately-shown cite survives
    assert by_title["S1"]["blocks"]
    assert "(generation failed)" not in (by_title["S1"].get("notes") or "")

    # slide 2's fabricated cite to a source it never saw must still be
    # rejected, discarding slide 2 to its blank-skeleton fallback
    assert "(generation failed)" in (by_title["S2"].get("notes") or "")


# ============================================= 3. run_doc_pipeline end-to-end


def test_doc_pipeline_keeps_section_citing_section_only_source(monkeypatch):
    """Mirror of the deck test for run_doc_pipeline / _section_errors /
    DocSection paragraphs."""
    monkeypatch.setattr("docloom_studio.embeddings.retrieve", _fake_retrieve)

    outline = gen.DocOutline(doc_title="Report", sections=[
        gen.DocOutlineItem(heading="Executive summary", intent="cover the niche fact"),
        gen.DocOutlineItem(heading="Detail", intent="more"),
    ])

    def make_section():
        return gen.DocSection(blocks=[Paragraph(text=[
            Span(text="Niche fact only in src_z.", cite="src_z")])])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        sys = messages[0]["content"]
        if "You plan written reports" in sys:
            return outline
        result = make_section()
        if lint_fn is not None:
            errs = lint_fn(result)
            if errs:
                raise GenerationFailed(
                    [{"round": r, "lint_errors": errs} for r in (1, 2, 3)])
        return result

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    nb = _notebook()
    aid = gen.create_artifact(nb, "doc")
    ctx = FakeCtx()
    asyncio.run(gen.run_doc_pipeline(
        ctx, nb, aid, "topic",
        context_lines=['[cite id: "src_a"] (Broad) broad fact'],
        sources=[{"id": "src_a", "title": "Broad"}]))

    ir = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])["ir"]

    paragraphs = [b for b in ir["blocks"] if b.get("type") == "paragraph"]
    assert paragraphs, "section was discarded as a blank skeleton"
    cites = {sp.get("cite") for b in paragraphs for sp in b.get("text", [])
             if isinstance(sp, dict)}
    assert "src_z" in cites

    source_ids = {s["id"] for s in ir["sources"]}
    assert "src_z" in source_ids
    assert "src_a" in source_ids

    doc = Document.model_validate(ir)
    findings = lint(doc)
    assert [f for f in findings if f.rule == "cite/unknown-source"] == []

"""Artifact generation pipelines. Deck (M1): context → outline → one LLM
call per slide (tiny schema = reliable on local models, independent retries,
slide_ready events) → lint + fix → save."""

from __future__ import annotations

import json
import re
from typing import Literal

import httpx
from docloom import (
    AUTHORING_GUIDE, Column, Document, Sheet, Slide, Source, Span, ensure_ids,
    lint, llm_schema,
)
from docloom.ir import Block, Heading, Image, Table
from docloom.llm import parse_llm_output
from pydantic import BaseModel, Field

from .db import execute, new_id, now, owner_of_notebook, query_one
from .jobs import JobCtx
from .providers import (
    GenerationFailed, ProviderConfig, ProviderError, TruncatedOutput,
    generate_validated,
)
from .settings import data_dir, get_setting

# One flaky HTTP call (a timeout, a 5xx, a truncated response) must not sink
# a whole deck/doc/sheet job and discard every unit already generated.
_UNIT_FAILURES = (GenerationFailed, ProviderError, httpx.HTTPError)

OutlineLayout = Literal["section", "content", "two_column", "quote",
                        "hero", "image_left", "image_right"]

IMAGE_LAYOUT_HINT = """
You may also use "hero" (full-bleed image + title), "image_left", or
"image_right" (image beside content) — use them where a picture helps.
"""
NO_IMAGE_HINT = '\nUse only section, content, two_column, and quote.\n'
IMAGE_SLIDE_HINT = ('\nFor hero/image_left/image_right layouts, set `image.query` '
                    'to 2-4 words naming the ideal picture (e.g. "remote team call").')


class OutlineItem(BaseModel):
    title: str = Field(description="slide title, at most 60 chars")
    layout: OutlineLayout = "content"
    intent: str = Field(description="one sentence: what this slide must convey")


class Outline(BaseModel):
    deck_title: str
    slides: list[OutlineItem]


OUTLINE_SYSTEM = """\
You plan slide decks. Given a request, return an outline as JSON:
deck_title plus 4-12 slides, each {title, layout, intent}.
Layouts: "section" for chapter breaks, "content" for points/evidence,
"two_column" for comparisons, "quote" for one big statement.
Do NOT include the opening title slide - it is added automatically.
Slide titles: specific and under 60 characters. Intents: one sentence.
"""

SLIDE_SYSTEM = AUTHORING_GUIDE + """
You are drafting ONE slide of a deck as a single JSON object matching the
provided schema (a docloom Slide). Follow the requested layout and intent.
Keep it tight: bullets under 130 chars, at most 6 bullets, tables at most
4x4. two_column slides put contrasting material in `blocks` (left) and
`right`. quote slides carry exactly one quote block. Set speaker `notes`
with anything that does not fit on the slide.
"""


def _citation_gate(doc: Document, known_ids: set[str]) -> None:
    """Drop any span cite the model invented that isn't a real source id.
    docloom's cite/unknown-source lint would otherwise flag the deck as broken;
    this is the deterministic grounding gate — no hallucinated references ship."""

    def clean(text) -> None:
        if isinstance(text, list):
            for sp in text:
                if isinstance(sp, Span) and sp.cite and sp.cite not in known_ids:
                    sp.cite = None

    def walk_blocks(blocks) -> None:
        for b in blocks:
            for attr in ("text",):
                if hasattr(b, attr):
                    clean(getattr(b, attr))
            if hasattr(b, "items"):
                for it in b.items:
                    if hasattr(it, "text"):
                        clean(it.text)
            if isinstance(b, Table):  # cites can hide in table cells too
                for cell in b.header:
                    clean(cell)
                for row in b.rows:
                    for cell in row:
                        clean(cell)

    for s in doc.slides:
        walk_blocks(s.blocks)
        walk_blocks(s.right)
    walk_blocks(doc.blocks)


# Layout-only findings are advisory during generation: discarding an
# otherwise-correct, fully parsed slide/section over a soft overflow would
# trade real content for a blank skeleton. They still surface in the editor
# (the unfiltered lint(doc) call below emits every finding) and still block
# export (export_artifact lints again, unfiltered) -- only the "should
# generate_validated retry-or-discard this unit" decision ignores them.
_ADVISORY_RULES = {"deck/overflow"}


def _lint_errors(source_ids: set[str], **doc_kwargs) -> list[str]:
    """Error-severity lint findings for one generated unit (a slide or a doc
    section), linted against the notebook's real source ids so a cite the
    model was asked to emit isn't flagged as cite/unknown-source."""
    sources = [Source(id=i, title=i) for i in sorted(source_ids)]
    findings = lint(Document(sources=sources, **doc_kwargs))
    return [f"{f.severity} [{f.rule}] {f.message}" for f in findings
            if f.severity == "error" and f.rule not in _ADVISORY_RULES]


def _slide_errors(deck_title: str, slide: Slide, source_ids: set[str]) -> list[str]:
    return _lint_errors(source_ids, title=deck_title, slides=[slide])


def _resolve_deck_images(doc: Document, user_id: str | None) -> None:
    """Fill image-layout slots from the user's tagged assets, and put the brand
    logo on the title slide. Slots that resolve to nothing render empty."""
    from .assets import active_brand, resolve_image

    logo = active_brand(user_id).get("logo_asset_id")
    for s in doc.slides:
        if s.layout in ("hero", "image_left", "image_right"):
            q = (s.image.query if s.image and s.image.query else s.title) or ""
            aid = resolve_image(q, user_id)
            if aid:
                s.image = Image(asset_id=aid, path=f"asset://{aid}", alt=q or None)
        elif s.layout == "title" and logo:
            s.image = Image(asset_id=logo, path=f"asset://{logo}", alt="logo")


async def run_deck_pipeline(
    ctx: JobCtx,
    notebook_id: str,
    artifact_id: str,
    prompt: str,
    context_lines: list[str] | None = None,
    sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    sources = sources or []

    ctx.emit("context", "done",
             detail=f"{len(context_lines or [])} evidence chunks, {len(sources)} sources")
    context_block = ""
    if context_lines:
        context_block = (
            "\n\nGround every factual claim in this evidence and set the span's "
            '`cite` to the given source id. Do not state facts the evidence '
            "does not support.\nEvidence:\n" + "\n".join(context_lines)
        )

    has_images = query_one(
        "SELECT 1 FROM assets WHERE type IN ('image','logo') AND user_id = ? LIMIT 1",
        (owner,)) is not None
    outline_sys = OUTLINE_SYSTEM + (IMAGE_LAYOUT_HINT if has_images else NO_IMAGE_HINT)
    slide_sys = SLIDE_SYSTEM + (IMAGE_SLIDE_HINT if has_images else "")

    ctx.emit("outline", "running")
    outline: Outline = await generate_validated(
        cfg,
        [{"role": "system", "content": outline_sys},
         {"role": "user", "content": prompt + context_block}],
        schema=llm_schema(Outline),
        parse=lambda t: parse_llm_output(t, Outline),
        lint_fn=lambda o: (["outline needs between 3 and 14 slides"]
                           if not 3 <= len(o.slides) <= 14 else []),
    )
    ctx.emit("outline", "done", data={
        "deck_title": outline.deck_title,
        "slides": [{"title": s.title, "layout": s.layout} for s in outline.slides],
    })

    slides: list[Slide] = [
        Slide(layout="title", title=outline.deck_title)
    ]
    plan_lines = "\n".join(
        f"{i + 1}. [{s.layout}] {s.title}" for i, s in enumerate(outline.slides)
    )
    for index, item in enumerate(outline.slides):
        ctx.emit("slide", "running", detail=item.title,
                 data={"index": index + 1, "total": len(outline.slides)})
        sec_block = await _section_block(
            notebook_id, f"{item.title} — {item.intent}", context_block, bool(sources))
        user = (
            f'Deck: "{outline.deck_title}"\nFull outline:\n{plan_lines}\n\n'
            f'Draft slide {index + 1}: "{item.title}" (layout: {item.layout}).\n'
            f"Intent: {item.intent}{sec_block}"
        )
        try:
            slide: Slide = await generate_validated(
                cfg,
                [{"role": "system", "content": slide_sys},
                 {"role": "user", "content": user}],
                schema=llm_schema(Slide),
                parse=lambda t: parse_llm_output(t, Slide),
                lint_fn=lambda s: _slide_errors(
                    outline.deck_title, s, {so["id"] for so in sources}),
            )
        except _UNIT_FAILURES as e:
            # one flaky call (a timeout, a 5xx, an exhausted retry budget)
            # must not sink the whole deck: keep a skeleton slide the user
            # can fill in and move on to the next one
            ctx.emit("slide", "skipped", detail=item.title, data={
                "index": index + 1, "total": len(outline.slides),
                "error": str(e)[:200]})
            slide = Slide(layout=item.layout, title=item.title,
                          notes=f"(generation failed) intent: {item.intent}")
        if slide.layout == "title":
            slide.layout = item.layout  # only the opener is a title slide
        if not slide.title:
            slide.title = item.title
        slides.append(slide)
        ctx.emit("slide", "done", detail=item.title, data={
            "index": index + 1, "total": len(outline.slides),
            "slide": slide.model_dump(exclude_none=True),
        })

    doc = Document(
        title=outline.deck_title, slides=slides,
        sources=[Source(**s) for s in sources],
    )
    _citation_gate(doc, {s["id"] for s in sources})
    doc = ensure_ids(doc)
    # lint the model's content before image slots are filled with asset:// refs
    # (those are baked to real files at export, so linting them here is spurious)
    findings = lint(doc)
    ctx.emit("lint", "done", data={
        "findings": [f.model_dump() for f in findings],
    })
    _resolve_deck_images(doc, owner)

    theme_name = get_setting("deck.theme", owner)
    payload = {"ir": doc.model_dump(exclude_none=True),
               "theme_name": theme_name, "brand_kit_id": None}
    save_artifact(artifact_id, title=doc.title, payload=payload)
    ctx.emit("save", "done", data={"artifact_id": artifact_id,
                                   "title": doc.title})


# ------------------------------------------------------------------ document


class DocOutlineItem(BaseModel):
    heading: str = Field(description="section heading")
    intent: str = Field(description="one sentence: what this section covers")


class DocOutline(BaseModel):
    doc_title: str
    sections: list[DocOutlineItem]


class DocSection(BaseModel):
    blocks: list[Block] = Field(description="the section body (no heading block)")


DOC_OUTLINE_SYSTEM = """\
You plan written reports. Return JSON: doc_title plus 3-8 sections, each
{heading, intent}. Headings are specific noun phrases. Intents are one
sentence. Do not include an introduction heading unless it adds real content."""

DOC_SECTION_SYSTEM = AUTHORING_GUIDE + """
You are drafting ONE section of a report as a JSON object with a `blocks`
array (docloom blocks: paragraph, bullets, numbered, quote, callout, table,
stats, chart). Do NOT include a heading block — the heading is added for you.
Keep paragraphs tight; prefer bullets and a small table or chart where it
helps. Ground every claim in the provided evidence and cite it."""


def _section_errors(doc_title: str, section: DocSection, source_ids: set[str]) -> list[str]:
    return _lint_errors(source_ids, title=doc_title, blocks=section.blocks)


async def run_doc_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    sources = sources or []
    ctx.emit("context", "done", detail=f"{len(sources)} sources")
    context_block = _context_block(context_lines)

    ctx.emit("outline", "running")
    outline: DocOutline = await generate_validated(
        cfg,
        [{"role": "system", "content": DOC_OUTLINE_SYSTEM},
         {"role": "user", "content": prompt + context_block}],
        schema=llm_schema(DocOutline),
        parse=lambda t: parse_llm_output(t, DocOutline),
        lint_fn=lambda o: ([] if 2 <= len(o.sections) <= 10
                           else ["need 2-10 sections"]),
    )
    ctx.emit("outline", "done", data={"doc_title": outline.doc_title,
             "sections": [s.heading for s in outline.sections]})

    blocks: list[Block] = []
    for i, item in enumerate(outline.sections):
        ctx.emit("section", "running", detail=item.heading,
                 data={"index": i + 1, "total": len(outline.sections)})
        blocks.append(Heading(level=2, text=item.heading))
        sec_block = await _section_block(
            notebook_id, f"{item.heading} — {item.intent}", context_block, bool(sources))
        try:
            section: DocSection = await generate_validated(
                cfg,
                [{"role": "system", "content": DOC_SECTION_SYSTEM},
                 {"role": "user", "content":
                    f'Report: "{outline.doc_title}"\nSection: "{item.heading}"\n'
                    f"Intent: {item.intent}{sec_block}"}],
                schema=llm_schema(DocSection),
                parse=lambda t: parse_llm_output(t, DocSection),
                lint_fn=lambda s: _section_errors(
                    outline.doc_title, s, {so["id"] for so in sources}),
            )
            blocks.extend(section.blocks)
        except _UNIT_FAILURES as e:
            # one flaky call must not sink the whole report: keep a plain
            # paragraph placeholder for this section and move on
            ctx.emit("section", "skipped", detail=item.heading, data={
                "index": i + 1, "total": len(outline.sections), "error": str(e)[:200]})
            blocks.append({"type": "paragraph", "text": item.intent})
        ctx.emit("section", "done", detail=item.heading,
                 data={"index": i + 1, "total": len(outline.sections)})

    doc = Document(title=outline.doc_title, blocks=blocks,
                   sources=[Source(**s) for s in sources])
    _citation_gate(doc, {s["id"] for s in sources})
    doc = ensure_ids(doc)
    findings = lint(doc)
    ctx.emit("lint", "done", data={"findings": [f.model_dump() for f in findings]})
    save_artifact(artifact_id, doc.title,
                  {"ir": doc.model_dump(exclude_none=True),
                   "theme_name": get_setting("deck.theme", owner), "brand_kit_id": None})
    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": doc.title})


# ------------------------------------------------------------------- sheet


class SheetDoc(BaseModel):
    title: str
    sheets: list[Sheet]


SHEET_SYSTEM = AUTHORING_GUIDE + """
You produce spreadsheets. Return JSON with a `title` and a `sheets` array.
Each sheet has a name, columns (header + optional Excel number format like
"$#,##0" or "0.0%"), and rows of typed cells (numbers as numbers, not
strings). Use a {"formula": "=SUM(B2:B10)"} cell for totals. Ground figures
in the evidence when provided."""


class SheetOutlineItem(BaseModel):
    name: str = Field(description="sheet tab name")
    intent: str = Field(description="one sentence: what data this sheet holds")


class SheetOutline(BaseModel):
    title: str
    sheets: list[SheetOutlineItem]


SHEET_OUTLINE_SYSTEM = """\
You plan spreadsheet workbooks. Return JSON: a workbook `title` plus 1-6
sheets, each {name, intent}. One sheet per distinct table; do not split one
table across sheets."""

SHEET_SECTION_SYSTEM = AUTHORING_GUIDE + """
You produce ONE sheet of a workbook as a JSON object matching the provided
schema (a docloom Sheet): a name, columns (header + optional Excel number
format like "$#,##0" or "0.0%"), and rows of typed cells (numbers as numbers,
not strings). Use a {"formula": "=SUM(B2:B10)"} cell for totals. Ground
figures in the evidence when provided."""


async def run_sheet_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    ctx.emit("context", "done")
    context_block = _context_block(context_lines, cite=False)  # SheetDoc has no Span type

    ctx.emit("sheet", "running")
    try:
        result: SheetDoc = await generate_validated(
            cfg,
            [{"role": "system", "content": SHEET_SYSTEM},
             {"role": "user", "content": prompt + context_block}],
            schema=llm_schema(SheetDoc),
            parse=lambda t: parse_llm_output(t, SheetDoc),
            lint_fn=lambda d: ([] if d.sheets else ["produce at least one sheet"]),
        )
        title, sheets = result.title, result.sheets
        ctx.emit("sheet", "done", detail=title)
    except TruncatedOutput:
        # the whole workbook doesn't fit one call (a big multi-sheet request
        # easily exceeds the token cap in one shot): plan sheet names first,
        # then fill each sheet with its own bounded call, same shape as the
        # deck/doc pipelines, so a big workbook still produces something
        # instead of failing 3 retries against the same cap and shipping
        # nothing
        ctx.emit("outline", "running",
                 detail="workbook too large for one call; splitting by sheet")
        outline: SheetOutline = await generate_validated(
            cfg,
            [{"role": "system", "content": SHEET_OUTLINE_SYSTEM},
             {"role": "user", "content": prompt + context_block}],
            schema=llm_schema(SheetOutline),
            parse=lambda t: parse_llm_output(t, SheetOutline),
            lint_fn=lambda o: ([] if 1 <= len(o.sheets) <= 8 else ["need 1-8 sheets"]),
        )
        ctx.emit("outline", "done", data={"title": outline.title,
                 "sheets": [s.name for s in outline.sheets]})
        title, sheets = outline.title, []
        for i, item in enumerate(outline.sheets):
            ctx.emit("sheet", "running", detail=item.name,
                     data={"index": i + 1, "total": len(outline.sheets)})
            try:
                sheet: Sheet = await generate_validated(
                    cfg,
                    [{"role": "system", "content": SHEET_SECTION_SYSTEM},
                     {"role": "user", "content":
                        f'Workbook: "{outline.title}"\nSheet: "{item.name}"\n'
                        f"Intent: {item.intent}{context_block}"}],
                    schema=llm_schema(Sheet),
                    parse=lambda t: parse_llm_output(t, Sheet),
                    lint_fn=lambda s: [] if s.columns else ["a sheet needs at least one column"],
                )
            except _UNIT_FAILURES as e:
                ctx.emit("sheet", "skipped", detail=item.name, data={
                    "index": i + 1, "total": len(outline.sheets), "error": str(e)[:200]})
                sheet = Sheet(name=item.name, columns=[Column(header=item.intent)], rows=[])
            sheets.append(sheet)
            ctx.emit("sheet", "done", detail=item.name,
                     data={"index": i + 1, "total": len(outline.sheets)})

    doc = ensure_ids(Document(title=title, sheets=sheets))
    findings = lint(doc)
    ctx.emit("lint", "done", data={"findings": [f.model_dump() for f in findings]})
    save_artifact(artifact_id, doc.title,
                  {"ir": doc.model_dump(exclude_none=True),
                   "theme_name": get_setting("deck.theme", owner), "brand_kit_id": None})
    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": doc.title})


# ------------------------------------------------------------------ diagram


class DiagramGen(BaseModel):
    title: str
    d2: str = Field(description="complete D2 (d2lang) diagram source, code only")


DIAGRAM_SYSTEM = """\
You produce architecture and process diagrams as D2 (d2lang) source only.
D2 is NOT Mermaid: never write `flowchart`, `graph`, `[...]` node brackets, or
`-->`. Use D2 syntax exactly like the example.

Rules:
- First line: `direction: right` (or `down`).
- A node is `id: Label`. A connection is `a -> b`. Chains `a -> b -> c` are fine.
- Special shapes: `{ shape: cylinder }` for databases/stores, `{ shape: person }`
  for users/actors, `{ shape: document }` for files/outputs.
- Keep to at most 20 nodes with short labels.

Example of the exact format:
direction: right
user: User { shape: person }
api: API service
db: Store { shape: cylinder }
user -> api
api -> db

Output ONLY valid D2 source in the `d2` field, nothing else."""


_MERMAID_PREFIX = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram)\b",
    re.IGNORECASE,
)
_MERMAID_NODE_BRACKETS = re.compile(r"[A-Za-z0-9_]\s*\[[^\[\]]+\]")


def _looks_like_d2(src: str) -> list[str]:
    s = src.strip()
    if not s:
        return ["empty diagram"]
    # Mermaid is the exact failure the prompt warns against: "A --> B" passes
    # a bare "'->' in s" check because "-->" contains "->" as a substring, and
    # Mermaid's own bracket node syntax ("a[Label]") isn't D2 either.
    if _MERMAID_PREFIX.search(s):
        return ["this is Mermaid syntax (flowchart/graph/sequenceDiagram/...), "
                "not D2 -- start with `direction: right` and use `a -> b`"]
    if "-->" in s:
        return ["this is a Mermaid arrow (-->); a D2 connection uses a single `->`"]
    if _MERMAID_NODE_BRACKETS.search(s):
        return ["this is Mermaid node bracket syntax (e.g. `a[Label]`); "
                "a D2 node is `id: Label`"]
    if "->" not in s and "--" not in s:
        return ["a D2 diagram needs at least one connection, e.g. `a -> b`"]
    if s.count("{") != s.count("}"):
        return ["unbalanced braces"]
    return []


async def run_diagram_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    ctx.emit("context", "done")
    ctx.emit("body", "running", detail="drawing the diagram")
    result: DiagramGen = await generate_validated(
        cfg,
        [{"role": "system", "content": DIAGRAM_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines, cite=False)}],
        schema=llm_schema(DiagramGen),
        parse=lambda t: parse_llm_output(t, DiagramGen),
        lint_fn=lambda d: _looks_like_d2(d.d2),
    )
    ctx.emit("body", "done", detail=result.title)
    save_artifact(artifact_id, result.title, {
        "source": result.d2, "render": None,
    })
    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": result.title})


# ---------------------------------------------------------------- infographic

# a small curated set of AntV templates that share the {title, lists:[...]}
# data shape (verified via SSR render); the editor lets the user switch.
IG_TEMPLATES = {
    "list": "list-column-vertical-icon-arrow",
    "steps": "sequence-steps-badge-card",
    "pyramid": "list-pyramid-badge-card",
    "grid": "list-grid-badge-card",
}


class InfographicItem(BaseModel):
    label: str = Field(description="short item title (<= 5 words)")
    desc: str = Field("", description="one short supporting phrase")


class InfographicSpec(BaseModel):
    style: Literal["list", "steps", "pyramid", "grid"] = "list"
    title: str
    items: list[InfographicItem] = Field(description="3-6 items")


IG_SYSTEM = """\
You design infographics. Return JSON: a `style` (list, steps, pyramid, or
grid), a short `title`, and 3-6 `items` each with a punchy `label` and a
one-line `desc`. Keep every label under 5 words. Ground in evidence when
provided."""


async def run_infographic_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    ctx.emit("context", "done")
    ctx.emit("body", "running", detail="composing the infographic")
    spec: InfographicSpec = await generate_validated(
        cfg,
        [{"role": "system", "content": IG_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines, cite=False)}],
        schema=llm_schema(InfographicSpec),
        parse=lambda t: parse_llm_output(t, InfographicSpec),
        lint_fn=lambda s: ([] if 2 <= len(s.items) <= 8 else ["need 2-8 items"]),
    )
    ctx.emit("body", "done", detail=spec.title)
    payload = {
        "style": spec.style,
        "antv": {
            "template": IG_TEMPLATES[spec.style],
            "data": {"title": spec.title,
                     "lists": [{"label": i.label, "desc": i.desc} for i in spec.items]},
        },
        "render": None,
    }
    save_artifact(artifact_id, spec.title, payload)
    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": spec.title})


# ------------------------------------------------------------------ podcast


class PodcastTurn(BaseModel):
    speaker: Literal["A", "B"] = Field(description="A = host, B = expert guest")
    text: str = Field(description="one conversational turn, 1-4 sentences")


class PodcastScript(BaseModel):
    title: str
    turns: list[PodcastTurn]


PODCAST_SYSTEM = """\
You write short two-host audio overviews (like a podcast). Two speakers:
A is a warm, curious host; B is a knowledgeable guest. Return JSON: a `title`
and a `turns` array of {speaker, text}. Alternate A/B, open with A introducing
the topic, and close with A wrapping up. 8-24 turns, each 1-4 spoken sentences,
natural and conversational (contractions, brief reactions). Ground every claim
in the provided evidence; do not invent facts. No stage directions or markdown
— just what each person says."""


async def run_podcast_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    ctx.emit("context", "done")
    ctx.emit("script", "running")
    script: PodcastScript = await generate_validated(
        cfg,
        [{"role": "system", "content": PODCAST_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines, cite=False)}],
        schema=llm_schema(PodcastScript),
        parse=lambda t: parse_llm_output(t, PodcastScript),
        lint_fn=lambda s: ([] if 6 <= len(s.turns) <= 40
                           else ["need between 6 and 40 turns"]),
    )
    ctx.emit("script", "done",
             data={"title": script.title, "turns": len(script.turns)})

    payload = {"script": script.model_dump(), "audio_path": None,
               "duration_s": None}
    save_artifact(artifact_id, script.title, payload)

    # Synthesize audio if a TTS backend is available; the transcript ships
    # either way (audio is a best-effort enrichment).
    try:
        from .tts import synthesize_podcast

        ctx.emit("audio", "running", detail="synthesizing voices")
        out = data_dir() / "artifacts" / artifact_id / "audio.wav"
        duration = await synthesize_podcast(
            script.model_dump(), out, get_setting("provider.tts", owner))
        payload["audio_path"] = f"artifacts/{artifact_id}/audio.wav"
        payload["duration_s"] = round(duration, 1)
        save_artifact(artifact_id, script.title, payload)
        ctx.emit("audio", "done", detail=f"{payload['duration_s']}s")
    except Exception as e:
        ctx.emit("audio", "skipped", detail=str(e)[:200])

    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": script.title})


async def repair_diagram(src: str, error: str, user_id: str | None) -> str:
    cfg = ProviderConfig(**get_setting("provider.generation", user_id))
    out: DiagramGen = await generate_validated(
        cfg,
        [{"role": "system", "content": DIAGRAM_SYSTEM},
         {"role": "user", "content":
            f"This D2 diagram failed to parse with error:\n{error}\n\n"
            f"Code:\n{src}\n\nReturn ONLY the corrected D2 source."}],
        schema=llm_schema(DiagramGen),
        parse=lambda t: parse_llm_output(t, DiagramGen),
        lint_fn=lambda d: _looks_like_d2(d.d2),
    )
    return out.d2


def _context_block(context_lines: list[str] | None, cite: bool = True) -> str:
    """`cite=False` for schemas with no Span type to set a `cite` on (SheetDoc
    cells, DiagramGen.d2, InfographicSpec labels, PodcastScript) -- appending
    the citation instruction there just invites cite-shaped garbage in a D2
    source string or a spreadsheet cell."""
    if not context_lines:
        return ""
    instruction = "Ground every factual claim in this evidence" + (
        " and set the span's `cite` to the given source id." if cite else ".")
    return f"\n\n{instruction}\nEvidence:\n" + "\n".join(context_lines)


async def _section_block(
    notebook_id: str, query: str, base_block: str, have_sources: bool, k: int = 6
) -> str:
    """Retrieve evidence targeted at ONE section/slide and append it to the
    broad context block. Falls back to just `base_block` when the notebook has
    no sources or retrieval finds nothing (keeps stubbed tests deterministic)."""
    if not have_sources:
        return base_block
    try:
        from .embeddings import retrieve

        chunks = await retrieve(notebook_id, query, k=k)
    except Exception:
        chunks = []
    if not chunks:
        return base_block
    lines = [
        f'[cite id: "{c.source_id}"] '
        f'({c.source_title}{f", p.{c.page}" if c.page else ""}) {c.text}'
        for c in chunks
    ]
    return base_block + "\n\nMost relevant to THIS section:\n" + "\n".join(lines)


def _set_artifact_status(artifact_id: str, status: str) -> None:
    # local import: artifacts.py imports create_artifact/save_artifact/the
    # pipelines from this module at its top level, so importing artifacts.py
    # back at OUR top level would be a circular import
    from .artifacts import set_artifact_status

    set_artifact_status(artifact_id, status)


def create_artifact(notebook_id: str, kind: str, title: str = "") -> str:
    artifact_id = new_id()
    t = now()
    execute(
        "INSERT INTO artifacts (id, notebook_id, kind, title, version, "
        "payload_json, created, updated) VALUES (?, ?, ?, ?, 0, '{}', ?, ?)",
        (artifact_id, notebook_id, kind, title, t, t),
    )
    # the row exists but generation hasn't produced anything yet: 'building'
    # (not the column's 'ready' default) so the artifacts list and the
    # editor can tell a fresh stub apart from a finished artifact
    _set_artifact_status(artifact_id, "building")
    return artifact_id


def save_artifact(artifact_id: str, title: str, payload: dict) -> int:
    row = query_one("SELECT version FROM artifacts WHERE id = ?", (artifact_id,))
    version = (row["version"] if row else 0) + 1
    text = json.dumps(payload)
    execute("UPDATE artifacts SET title = ?, version = ?, payload_json = ?, "
            "updated = ? WHERE id = ?",
            (title, version, text, now(), artifact_id))
    execute("INSERT INTO artifact_versions (artifact_id, version, payload_json, "
            "created) VALUES (?, ?, ?, ?)",
            (artifact_id, version, text, now()))
    # a payload just landed (first generation, a manual edit, or a revert):
    # the artifact is viewable/exportable, so it's 'ready' regardless of
    # which caller reached this point
    _set_artifact_status(artifact_id, "ready")
    return version


SUGGESTED_QUESTIONS_SYSTEM = """\
Given evidence from a notebook's sources, suggest short, specific questions a
reader could ask that these sources can actually answer. Return JSON: a
`questions` array of exactly 3 short questions (one sentence each, no
preamble, no numbering)."""


class SuggestedQuestions(BaseModel):
    questions: list[str]


async def suggest_questions(notebook_id: str, user_id: str) -> list[str]:
    """3 short grounded questions the notebook's enabled sources can answer,
    for an empty-state "ask something" prompt. Best-effort: any failure
    (no sources yet, provider down, ...) returns an empty list rather than
    surfacing an error for what is a minor UI nicety."""
    from .embeddings import retrieve

    try:
        chunks = await retrieve(notebook_id, "key facts and topics", k=8)
        if not chunks:
            return []
        evidence = "\n\n".join(f"({c.source_title}) {c.text[:400]}" for c in chunks)
        cfg = ProviderConfig(**get_setting("provider.generation", user_id))
        result: SuggestedQuestions = await generate_validated(
            cfg,
            [{"role": "system", "content": SUGGESTED_QUESTIONS_SYSTEM},
             {"role": "user", "content": f"Evidence:\n{evidence}"}],
            schema=llm_schema(SuggestedQuestions),
            parse=lambda t: parse_llm_output(t, SuggestedQuestions),
            lint_fn=lambda r: [] if r.questions else ["produce at least one question"],
        )
    except Exception:
        return []
    return [q.strip() for q in result.questions if q.strip()][:3]

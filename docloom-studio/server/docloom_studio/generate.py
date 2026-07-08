"""Artifact generation pipelines. Deck (M1): context → outline → one LLM
call per slide (tiny schema = reliable on local models, independent retries,
slide_ready events) → lint + fix → save."""

from __future__ import annotations

import json
from typing import Literal

from docloom import (
    AUTHORING_GUIDE, Document, Sheet, Slide, Source, Span, ensure_ids, lint,
    llm_schema,
)
from docloom.ir import Block, Heading, Image
from docloom.llm import parse_llm_output
from pydantic import BaseModel, Field

from .db import execute, new_id, now, query_one
from .jobs import JobCtx
from .providers import GenerationFailed, ProviderConfig, generate_validated
from .settings import get_setting

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

    for s in doc.slides:
        walk_blocks(s.blocks)
        walk_blocks(s.right)
    walk_blocks(doc.blocks)


def _slide_errors(deck_title: str, slide: Slide) -> list[str]:
    findings = lint(Document(title=deck_title, slides=[slide]))
    return [f"{f.severity} [{f.rule}] {f.message}"
            for f in findings if f.severity == "error"]


def _resolve_deck_images(doc: Document) -> None:
    """Fill image-layout slots from the user's tagged assets, and put the brand
    logo on the title slide. Slots that resolve to nothing render empty."""
    from .assets import active_brand, resolve_image

    logo = active_brand().get("logo_asset_id")
    for s in doc.slides:
        if s.layout in ("hero", "image_left", "image_right"):
            q = (s.image.query if s.image and s.image.query else s.title) or ""
            aid = resolve_image(q)
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
    cfg = ProviderConfig(**get_setting("provider.generation"))
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
        "SELECT 1 FROM assets WHERE type IN ('image','logo') LIMIT 1") is not None
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
        user = (
            f'Deck: "{outline.deck_title}"\nFull outline:\n{plan_lines}\n\n'
            f'Draft slide {index + 1}: "{item.title}" (layout: {item.layout}).\n'
            f"Intent: {item.intent}{context_block}"
        )
        try:
            slide: Slide = await generate_validated(
                cfg,
                [{"role": "system", "content": slide_sys},
                 {"role": "user", "content": user}],
                schema=llm_schema(Slide),
                parse=lambda t: parse_llm_output(t, Slide),
                lint_fn=lambda s: _slide_errors(outline.deck_title, s),
            )
        except GenerationFailed:
            # keep the deck moving: a skeleton slide the user can fill in
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
    _resolve_deck_images(doc)
    doc = ensure_ids(doc)
    findings = lint(doc)
    ctx.emit("lint", "done", data={
        "findings": [f.model_dump() for f in findings],
    })

    theme_name = get_setting("deck.theme")
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


async def run_doc_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    cfg = ProviderConfig(**get_setting("provider.generation"))
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
        try:
            section: DocSection = await generate_validated(
                cfg,
                [{"role": "system", "content": DOC_SECTION_SYSTEM},
                 {"role": "user", "content":
                    f'Report: "{outline.doc_title}"\nSection: "{item.heading}"\n'
                    f"Intent: {item.intent}{context_block}"}],
                schema=llm_schema(DocSection),
                parse=lambda t: parse_llm_output(t, DocSection),
            )
            blocks.extend(section.blocks)
        except GenerationFailed:
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
                   "theme_name": get_setting("deck.theme"), "brand_kit_id": None})
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


async def run_sheet_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    cfg = ProviderConfig(**get_setting("provider.generation"))
    ctx.emit("context", "done")
    ctx.emit("sheet", "running")
    result: SheetDoc = await generate_validated(
        cfg,
        [{"role": "system", "content": SHEET_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines)}],
        schema=llm_schema(SheetDoc),
        parse=lambda t: parse_llm_output(t, SheetDoc),
        lint_fn=lambda d: ([] if d.sheets else ["produce at least one sheet"]),
    )
    doc = ensure_ids(Document(title=result.title, sheets=result.sheets))
    findings = lint(doc)
    ctx.emit("lint", "done", data={"findings": [f.model_dump() for f in findings]})
    save_artifact(artifact_id, doc.title,
                  {"ir": doc.model_dump(exclude_none=True),
                   "theme_name": get_setting("deck.theme"), "brand_kit_id": None})
    ctx.emit("save", "done", data={"artifact_id": artifact_id, "title": doc.title})


# ------------------------------------------------------------------ diagram


class DiagramGen(BaseModel):
    title: str
    mermaid: str = Field(description="a complete Mermaid flowchart, code only")


DIAGRAM_SYSTEM = """\
You produce architecture and process diagrams as Mermaid FLOWCHARTS only.
Rules:
- Start with `flowchart TD` or `flowchart LR`.
- Use at most 20 nodes; short labels.
- Only flowchart syntax (nodes, edges, subgraphs) — no sequence/class/other
  diagram types, no styling/classDef.
- Output ONLY the mermaid code, nothing else."""


def _looks_like_flowchart(src: str) -> list[str]:
    s = src.strip()
    first = s.splitlines()[0].strip().lower() if s else ""
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return ['must start with "flowchart TD" or "flowchart LR"']
    if s.count("[") != s.count("]") or s.count("(") != s.count(")"):
        return ["unbalanced brackets/parentheses"]
    return []


async def run_diagram_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    cfg = ProviderConfig(**get_setting("provider.generation"))
    ctx.emit("context", "done")
    ctx.emit("body", "running", detail="drawing the diagram")
    result: DiagramGen = await generate_validated(
        cfg,
        [{"role": "system", "content": DIAGRAM_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines)}],
        schema=llm_schema(DiagramGen),
        parse=lambda t: parse_llm_output(t, DiagramGen),
        lint_fn=lambda d: _looks_like_flowchart(d.mermaid),
    )
    ctx.emit("body", "done", detail=result.title)
    save_artifact(artifact_id, result.title, {
        "mermaid_src": result.mermaid, "excalidraw_scene": None,
        "canvas_dirty": False, "render": None,
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
    cfg = ProviderConfig(**get_setting("provider.generation"))
    ctx.emit("context", "done")
    ctx.emit("body", "running", detail="composing the infographic")
    spec: InfographicSpec = await generate_validated(
        cfg,
        [{"role": "system", "content": IG_SYSTEM},
         {"role": "user", "content": prompt + _context_block(context_lines)}],
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


async def repair_mermaid(src: str, error: str) -> str:
    cfg = ProviderConfig(**get_setting("provider.generation"))
    out: DiagramGen = await generate_validated(
        cfg,
        [{"role": "system", "content": DIAGRAM_SYSTEM},
         {"role": "user", "content":
            f"This Mermaid failed to parse with error:\n{error}\n\n"
            f"Code:\n{src}\n\nReturn ONLY the corrected Mermaid flowchart."}],
        schema=llm_schema(DiagramGen),
        parse=lambda t: parse_llm_output(t, DiagramGen),
        lint_fn=lambda d: _looks_like_flowchart(d.mermaid),
    )
    return out.mermaid


def _context_block(context_lines: list[str] | None) -> str:
    if not context_lines:
        return ""
    return ("\n\nGround every factual claim in this evidence and set the span's "
            '`cite` to the given source id.\nEvidence:\n' + "\n".join(context_lines))


def create_artifact(notebook_id: str, kind: str, title: str = "") -> str:
    artifact_id = new_id()
    t = now()
    execute(
        "INSERT INTO artifacts (id, notebook_id, kind, title, version, "
        "payload_json, created, updated) VALUES (?, ?, ?, ?, 0, '{}', ?, ?)",
        (artifact_id, notebook_id, kind, title, t, t),
    )
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
    return version

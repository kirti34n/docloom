"""Artifact generation pipelines. Deck (M1): context → outline → one LLM
call per slide (tiny schema = reliable on local models, independent retries,
slide_ready events) → lint + fix → save."""

from __future__ import annotations

import json
import re
from typing import Literal

import httpx
from docloom import (
    AUTHORING_GUIDE, Column, Diagram, Document, Sheet, Slide, Source, Span,
    ensure_ids, lint, llm_schema, render_diagram,
)
from docloom.ir import (
    Artifact, Block, BulletList, Chart, Code, Diagram, Heading, Image,
    NumberedList, StatRow, Table, plain,
)
from docloom.render.diagram_svg import solve
# Fit-by-budget constants (see _budget_errors below) are docloom's own, not a
# second, drifting copy: docloom/src/docloom/lint.py already defines and
# tunes MAX_BULLETS_PER_SLIDE / MAX_BULLET_CHARS / MAX_TITLE_CHARS for the
# exact same rule (deck/too-many-bullets, deck/title-too-long), and docloom
# is a real installed dependency here, so import instead of redeclaring.
from docloom.lint import MAX_BULLET_CHARS, MAX_BULLETS_PER_SLIDE, MAX_TITLE_CHARS
from docloom.llm import parse_llm_output
from pydantic import BaseModel, Field

from .db import execute, new_id, now, owner_of_notebook, query_one, transaction
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
"image_right" (image beside content), use them where a picture helps.
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
You plan slide decks. Given a request and the evidence provided, return an
outline as JSON: deck_title plus 3-14 slides, each {title, layout, intent}.
Do NOT include the opening title slide, it is added automatically.

STRUCTURE
- One idea per slide. Read top to bottom, the slide titles alone must tell
  the whole story (problem, evidence, implication), so a reader who only
  skims the titles still gets the point.
- Base the structure on what the evidence actually contains, not a generic
  template; let the sources decide how many slides you need and what each
  one covers.

LAYOUTS
- "section" for chapter breaks, "content" for a single point plus its
  evidence, "two_column" for a direct comparison, "quote" for one big
  statement that deserves its own slide.
- Vary layouts across the deck. Do not make every slide "content".

TITLES
- Every slide title is a complete declarative sentence stating the
  takeaway, 5-15 words, with a real verb. Never a topic label ("Overview",
  "Introduction", "Results", "Q3 Metrics", "Background", "Agenda").
  Bad: "Revenue". Good: "Revenue grew 14 percent on APAC expansion".

INTENT
- One sentence naming the single idea AND the evidence that proves it (a
  number, a comparison, a quote, a trend). If the point rests on one or
  two numbers, a trend, or a ranking, say so, that slide should lead with
  a stats or chart block, not another bullet list.
"""

SLIDE_SYSTEM = AUTHORING_GUIDE + f"""
You are drafting ONE slide of a deck as a single JSON object matching the
provided schema (a docloom Slide). Follow the requested layout and intent,
but you own the content: pick the block type the evidence calls for.

ONE IDEA
- This slide makes exactly one assertion. If you find yourself wanting to
  say two things, drop the weaker one or move it to speaker notes.
- Title = the takeaway, not a label: a complete sentence with a verb,
  5-15 words and at most {MAX_TITLE_CHARS} characters (e.g. "Support tickets
  dropped 30 percent after the rollout", never "Support Tickets" or "Results").

BLOCK SELECTION (do not default to bullets; pick what the evidence needs)
- The point is one or two numbers: a "stats" block (a big, bold value plus
  label), not a number buried in a sentence.
- The point is a trend, comparison, ranking, or share of a whole: a
  "chart" block (bar, column, line, or area). NEVER a "pie" chart.
- The reader needs to look up precise or mixed-unit values: a "table".
- The point is a sequence of steps: a "numbered" list.
- Items are genuinely parallel, discrete, and few: up to 5 short "bullets"
  (each under 12 words). Anything else reads better as a short paragraph.
- two_column slides put contrasting material in `blocks` (left) and
  `right`. quote slides carry exactly one quote block, the single
  strongest line.

NEVER EMPTY
- Every block MUST carry real content. A "bullets" or "numbered" block has
  3-5 real items -- NEVER an empty items list. A "stats" block has 2-4 stats,
  a "chart" has labelled series, a "table" has rows, a "quote" has its text.
  A content slide that comes back with an empty block, or with only a title
  and no filled body, is a FAILED slide. Always fill the slide with concrete,
  grounded content drawn from the evidence.

LIMITS
- At most 6 blocks/elements and about 25 words of on-slide body text.
  Bullets and numbered items: under {MAX_BULLET_CHARS} chars each, at most
  {MAX_BULLETS_PER_SLIDE} per SLIDE in total (not per list -- on a two_column
  slide that means `blocks` and `right` combined), fewer is better. These are
  hard budgets, not suggestions -- a slide that needs shrink-to-fit to read is
  already a bad slide, so cut content instead of writing long lines.
  Tables: at most 4x4 on a slide. Put everything else in speaker `notes`.
- Every chart, table, or image carries its own short title and a one-line
  takeaway caption (what the reader should conclude from it), not just
  raw data.

GROUNDING
- Ground every number, date, name, and claim in the evidence provided and
  set the span's `cite` to the matching source id. If the evidence does
  not support a claim, cut the claim, do not invent one.
- Never write placeholder, lorem-ipsum, "TBD", or "insert X here" text,
  and never ship a title with nothing under it: if the evidence is thin,
  write the strongest true sentence it supports instead of leaving the
  slide empty.

Set speaker `notes` with anything that does not fit on the slide (detail,
sourcing, transitions).
"""


def _citation_gate(doc: Document, known_ids: set[str]) -> None:
    """Drop any span cite the model invented that isn't a real source id.
    docloom's cite/unknown-source lint would otherwise flag the deck as broken;
    this is the deterministic grounding gate, no hallucinated references ship."""

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


def _blocks_text(blocks) -> list[str]:
    """Flatten the literal text carried by a list of blocks, for the cheap
    content checks below (empty-body / anti-placeholder). Not a citation
    walk, see _citation_gate for that."""
    out: list[str] = []
    for b in blocks:
        if hasattr(b, "text"):
            out.append(plain(b.text))
        if hasattr(b, "items"):
            # BulletList/NumberedList items have `.text`; StatRow also has
            # `.items` but its Stat items do not, guard per-item like
            # _citation_gate does instead of assuming the shape
            for it in b.items:
                if hasattr(it, "text"):
                    out.append(plain(it.text))
        if isinstance(b, Table):
            out.extend(plain(c) for c in b.header)
            out.extend(plain(c) for row in b.rows for c in row)
        # Code source is deliberately NOT collected: its only consumer is the
        # placeholder scan, and empty literals (results = []) or a "# TODO"
        # comment are legitimate code, not filler to reject.
        if isinstance(b, Chart) and b.title:
            out.append(b.title)
        if isinstance(b, StatRow):
            for s in b.items:
                out.append(s.label)
                out.append(s.value)
    return out


_PLACEHOLDER_RE = re.compile(
    r"lorem ipsum|\btodo\b|\btbd\b|\bxxx\b|\[\s*\]|insert\s+\w[\w \-]*\s+here",
    re.IGNORECASE,
)


def _placeholder_errors(texts: list[str]) -> list[str]:
    """Flag stock filler text a model sometimes ships instead of real
    content (a lorem-ipsum block, a literal TODO/TBD, an empty bracket). Code
    source is excluded upstream in _blocks_text, so `x = []` never trips this.
    Cheap and deterministic; feeds straight into generate_validated's retry
    loop, the same way a lint finding does."""
    for t in texts:
        if t and _PLACEHOLDER_RE.search(t):
            snippet = t.strip()[:80]
            return [f'placeholder text found ("{snippet}"); replace it with '
                    "real, grounded content"]
    return []


def _fallback_topic(prompt: str) -> str:
    """A short, single-line topic carved out of the raw prompt, for the
    deterministic default outlines below. No LLM call, so this must not
    depend on generation succeeding."""
    topic = " ".join(prompt.split())[:48].strip()
    return topic or "the requested topic"


_VISUAL_BLOCKS = (Table, Chart, StatRow, Image, Diagram, Artifact, Code)


def _slide_content_errors(slide: Slide) -> list[str]:
    all_blocks = slide.blocks + slide.right
    needs_content = slide.layout in ("content", "two_column", "quote")
    if needs_content and not all_blocks:
        return ["this slide has no content blocks; add real, grounded "
                "content, not an empty placeholder"]
    # A block can be structurally present but carry NO content -- most often a
    # bullets/numbered list a provider's structured output returned with an
    # empty items[] (observed frequently from Gemini). Those pass the check
    # above (a block exists) yet render as a title over blank space. Treat a
    # content-layout slide with no real body text AND no standalone visual
    # (table/chart/stats/diagram/image/code) as empty: a hard error, so
    # generate_validated retries it with "fill this slide" feedback instead of
    # silently shipping a blank slide.
    body = "".join(_blocks_text(all_blocks)).strip()
    if needs_content and not body and not any(isinstance(b, _VISUAL_BLOCKS) for b in all_blocks):
        return ["this slide's content blocks are empty (e.g. a bullet list with "
                "no items); fill them with real, grounded content"]
    texts = _blocks_text(all_blocks)
    if slide.title:
        texts.append(slide.title)
    return _placeholder_errors(texts)


# Fit-by-budget: a slide whose body text had to be autofit-shrunk to fit its
# box is already a bad slide -- prevention beats autofit. docloom's
# llm_schema() strips minLength/maxLength/pattern before the schema reaches
# the model (see llm.py), so a Field(description=...) budget is advisory
# prose the model may ignore; these caps are enforced here, deterministically,
# against the PARSED slide, and fed back through the same generate_validated
# retry loop as every other lint finding (see providers.py). Because slides
# are generated one at a time (the per-slide generate_validated call in
# run_deck_pipeline's loop above), a budget violation re-asks for THIS slide
# only -- the rest of the deck is untouched.
#
# MAX_BULLETS_PER_SLIDE / MAX_BULLET_CHARS / MAX_TITLE_CHARS are imported from
# docloom.lint above, not redeclared: docloom's deck/too-many-bullets and
# deck/title-too-long rules already enforce this exact budget at
# severity="warning" (never "error" -- see the standing rule at
# docloom/src/docloom/lint.py:349-365 that quality/density rules must stay
# warnings, because hard-blocking them destroys valid documents). A second,
# hardcoded copy here would silently drift from docloom's tuned values, which
# is exactly what happened before (6 here vs. 7 there).
def _budget_errors(slide: Slide) -> list[str]:
    """Deterministic per-slot capacity check: title length, total bullet/
    numbered item count, and per-item length. Item count is summed across
    the WHOLE slide (blocks + right), matching docloom's own
    deck/too-many-bullets rule (lint.py:616-624) exactly: a two_column slide
    with 5 items on the left and 5 on the right is 10 bullets on one slide
    and must be caught, even though neither list alone is over budget."""
    errors: list[str] = []
    if slide.title and len(slide.title) > MAX_TITLE_CHARS:
        errors.append(
            f"title is {len(slide.title)} chars, over the {MAX_TITLE_CHARS}-char "
            "budget; shorten it to one punchy sentence")
    all_blocks = slide.blocks + slide.right
    n_items = sum(
        len(b.items) for b in all_blocks if isinstance(b, (BulletList, NumberedList))
    )
    if n_items > MAX_BULLETS_PER_SLIDE:
        errors.append(
            f"{n_items} bullet/numbered items on this slide (counted across "
            f"all lists on the slide, not per list), over the "
            f"{MAX_BULLETS_PER_SLIDE}-item budget; cut the weakest ones or "
            "move them to speaker notes")
    for b in all_blocks:
        if not isinstance(b, (BulletList, NumberedList)):
            continue
        for it in b.items:
            text = plain(it.text)
            if len(text) > MAX_BULLET_CHARS:
                errors.append(
                    f'{b.type} item is {len(text)} chars, over the '
                    f'{MAX_BULLET_CHARS}-char budget ("{text[:60]}..."); '
                    "shorten it instead of relying on autofit to shrink it")
    return errors


def _slide_hard_errors(deck_title: str, slide: Slide, source_ids: set[str]) -> list[str]:
    """Errors that mean a slide is genuinely unusable: broken/hallucinated
    citations, missing content blocks, placeholder text. Unlike a fit-by-
    budget overflow (see _budget_errors), there is no authored content worth
    keeping when one of these fires, so run_deck_pipeline's retry-exhaustion
    fallback treats this set -- and only this set -- as grounds to discard
    the slide for an empty skeleton."""
    return (_lint_errors(source_ids, title=deck_title, slides=[slide])
            + _slide_content_errors(slide))


def _slide_errors(deck_title: str, slide: Slide, source_ids: set[str]) -> list[str]:
    """Full lint_fn for the per-slide generate_validated call: hard errors
    plus the fit-by-budget checks. Budget violations still shape retries (the
    model is asked to trim, exactly like any other finding) but must never by
    themselves be the reason a slide's content gets discarded -- see
    run_deck_pipeline, which tracks the last hard-error-free parse separately
    so retry exhaustion can fall back to real content instead of a blank
    skeleton (HIGH-1)."""
    return _slide_hard_errors(deck_title, slide, source_ids) + _budget_errors(slide)


def _default_outline(prompt: str) -> Outline:
    """A minimal generic outline used only when the outline call itself
    fails after every retry (bad JSON, an exhausted lint loop, a dead
    provider): keeps the job moving into the per-slide loop, each of which
    still gets real grounded content, instead of failing before a single
    slide is even attempted."""
    topic = _fallback_topic(prompt)
    return Outline(deck_title=topic, slides=[
        OutlineItem(title=f"An overview of {topic}", layout="content",
                    intent=f"introduce {topic} and why it matters"),
        OutlineItem(title=f"The key points behind {topic}", layout="content",
                    intent=f"the main points to know about {topic}"),
        OutlineItem(title="What the evidence shows", layout="content",
                    intent=f"supporting detail and evidence about {topic}"),
        OutlineItem(title="What this means going forward", layout="content",
                    intent=f"the practical takeaway about {topic}"),
    ])


# Nano Banana (Gemini image generation) is for illustrative slide imagery
# only -- diagrams stay on the deterministic D2 path (see run_diagram_pipeline
# below). The model only emits a bare 2-4 word `image.query`; enriching it
# with a fixed style suffix and an aspect matching the layout before calling
# the provider is the single biggest lever on output quality (see
# understand-image-diagram-gen.md 3.4).
_IMAGE_ASPECT_BY_LAYOUT = {"hero": "16:9", "image_left": "4:3", "image_right": "4:3"}
_IMAGE_STYLE_SUFFIX = "clean editorial illustration, flat vector, soft palette, no text"


def _enrich_image_prompt(subject: str, layout: str) -> tuple[str, str]:
    """(prompt, aspect_ratio) for one generated slide image. Pure and
    deterministic, so it is cheap to unit test without touching the network."""
    aspect = _IMAGE_ASPECT_BY_LAYOUT.get(layout, "16:9")
    return f"{subject}, {_IMAGE_STYLE_SUFFIX}, {aspect}", aspect


async def _generate_slide_image(
    s: Slide, query: str, user_id: str | None, image_settings: dict,
    ctx: JobCtx | None,
) -> None:
    """Best-effort illustrative image for ONE unmatched hero/image_left/
    image_right slot. A timeout, a refusal, or any other provider-shaped
    failure (_UNIT_FAILURES -- the same tuple every per-slide text call above
    swallows) leaves the slot exactly as it was, query-only with no path, so
    it simply renders empty instead of sinking the whole deck. ValueError is
    also swallowed here: save_generated_image raises it for an
    over-size generated image, a content-level rejection in the same spirit
    as a provider error, not an infrastructure fault."""
    subject = query or s.title or "an abstract illustration"
    if ctx is not None:
        ctx.emit("image", "running", detail=subject)
    try:
        from .assets import save_generated_image
        from .providers import ImageProviderConfig, generate_image

        prompt, aspect = _enrich_image_prompt(subject, s.layout)
        data = await generate_image(
            ImageProviderConfig(**image_settings), prompt, aspect_ratio=aspect)
        aid = save_generated_image(user_id, data, prompt=prompt)
        s.image = Image(asset_id=aid, path=f"asset://{aid}", alt=query or "")
    except (*_UNIT_FAILURES, ValueError) as e:
        if ctx is not None:
            ctx.emit("image", "skipped", detail=subject, data={"error": str(e)[:200]})
        return
    if ctx is not None:
        ctx.emit("image", "done", detail=subject)


async def _resolve_deck_images(
    doc: Document, user_id: str | None, ctx: JobCtx | None = None,
) -> None:
    """Fill image-layout slots from the user's tagged assets first, and put
    the brand logo on the title slide. When nothing matches AND AI image
    generation (Nano Banana) is enabled for this owner, generate an
    illustrative image instead of leaving the slot empty. A slot only stays
    empty when nothing matches and generation is off, disabled, or fails."""
    from .assets import active_brand, resolve_image

    image_settings = get_setting("provider.image", user_id) or {}
    gen_enabled = bool(image_settings.get("enabled"))

    logo = active_brand(user_id).get("logo_asset_id")
    for s in doc.slides:
        if s.layout in ("hero", "image_left", "image_right"):
            q = (s.image.query if s.image and s.image.query else s.title) or ""
            aid = resolve_image(q, user_id)
            if aid:
                s.image = Image(asset_id=aid, path=f"asset://{aid}", alt=q or None)
            elif gen_enabled:
                await _generate_slide_image(s, q, user_id, image_settings, ctx)
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

    # Image layouts are offered whenever a slot can actually be filled:
    # either the user has tagged assets to match against, or AI image
    # generation is enabled and will fill an unmatched slot itself. Relaxing
    # this for the enabled case is what lets a user with an empty asset
    # library still get hero/image slides (_resolve_deck_images below does
    # the actual generation).
    image_gen_enabled = bool((get_setting("provider.image", owner) or {}).get("enabled"))
    has_images = image_gen_enabled or query_one(
        "SELECT 1 FROM assets WHERE type IN ('image','logo') AND user_id = ? LIMIT 1",
        (owner,)) is not None
    outline_sys = OUTLINE_SYSTEM + (IMAGE_LAYOUT_HINT if has_images else NO_IMAGE_HINT)
    slide_sys = SLIDE_SYSTEM + (IMAGE_SLIDE_HINT if has_images else "")

    ctx.emit("outline", "running")
    # a touch of targeted retrieval for the outline call too, not just the
    # per-slide calls below: the same evidence resurfaced right before the
    # generation point instead of only buried in the broad context block
    outline_evidence, _ = await _section_block(notebook_id, prompt, context_block, bool(sources))
    try:
        outline: Outline = await generate_validated(
            cfg,
            [{"role": "system", "content": outline_sys},
             {"role": "user", "content": prompt + outline_evidence}],
            schema=llm_schema(Outline),
            parse=lambda t: parse_llm_output(t, Outline),
            lint_fn=lambda o: (["outline needs between 3 and 14 slides"]
                               if not 3 <= len(o.slides) <= 14 else []),
        )
    except _UNIT_FAILURES as e:
        # the outline is the least critical, most disposable stage: a
        # generic default still lets every per-slide call below produce
        # real, grounded content instead of failing the whole job before a
        # single slide has even been attempted
        ctx.emit("outline", "skipped",
                 detail="using a default outline after a generation failure",
                 data={"error": str(e)[:200]})
        outline = _default_outline(prompt)
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
    known_sources: dict[str, dict] = {s["id"]: s for s in sources}
    broad_ids = set(known_sources)
    for index, item in enumerate(outline.slides):
        ctx.emit("slide", "running", detail=item.title,
                 data={"index": index + 1, "total": len(outline.slides)})
        sec_block, sec_sources = await _section_block(
            notebook_id, f"{item.title} - {item.intent}", context_block, bool(sources))
        for s in sec_sources:
            known_sources.setdefault(s["id"], s)
        slide_ids = broad_ids | {s["id"] for s in sec_sources}
        user = (
            f'Deck: "{outline.deck_title}"\nFull outline:\n{plan_lines}\n\n'
            f'Draft slide {index + 1}: "{item.title}" (layout: {item.layout}).\n'
            f"Intent: {item.intent}{sec_block}"
        )
        # last_hard_ok captures the most recent round's parsed slide that had
        # NO hard errors (see _slide_hard_errors) even if it still tripped a
        # fit-by-budget check -- generate_validated itself only ever returns
        # the object on a fully clean round or raises GenerationFailed with
        # round diagnostics (no object) on exhaustion, so this closure is the
        # only way to keep hold of real, usable content across the retry loop
        # (HIGH-1: a budget-only failure must never discard authored content).
        last_hard_ok: dict[str, Slide] = {}

        def _lint_fn(s: Slide, ids=slide_ids) -> list[str]:
            hard = _slide_hard_errors(outline.deck_title, s, ids)
            if not hard:
                last_hard_ok["slide"] = s
            return hard + _budget_errors(s)

        degraded = False
        try:
            slide: Slide = await generate_validated(
                cfg,
                [{"role": "system", "content": slide_sys},
                 {"role": "user", "content": user}],
                schema=llm_schema(Slide),
                parse=lambda t: parse_llm_output(t, Slide),
                lint_fn=_lint_fn,
            )
        except _UNIT_FAILURES as e:
            if "slide" in last_hard_ok:
                # every round kept tripping the fit-by-budget check (e.g. the
                # model would not shorten a list under repeated feedback), but
                # at least one round produced real, grounded, non-empty
                # content with no hard errors -- keep that content instead of
                # discarding it for a blank skeleton. docloom's own lint(doc)
                # call below still surfaces the overflow as its native
                # warning-severity finding (deck/too-many-bullets or
                # deck/title-too-long), it just does not block the deck.
                slide = last_hard_ok["slide"]
                degraded = True
            else:
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
            **({"budget_degraded": True} if degraded else {}),
        })

    doc = Document(
        title=outline.deck_title, slides=slides,
        sources=[Source(**s) for s in known_sources.values()],
    )
    _citation_gate(doc, set(known_sources))
    doc = ensure_ids(doc)
    # lint the model's content before image slots are filled with asset:// refs
    # (those are baked to real files at export, so linting them here is spurious)
    findings = lint(doc)
    ctx.emit("lint", "done", data={
        "findings": [f.model_dump() for f in findings],
    })
    await _resolve_deck_images(doc, owner, ctx)

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
You plan written reports. Given a request and the evidence provided, return
JSON: doc_title plus 2-10 sections, each {heading, intent}.

STRUCTURE (answer-first)
- Section 1 is always an executive summary: state the single main
  conclusion the evidence supports, then name the (roughly 3) supporting
  points the rest of the report backs up. Head it "Executive summary".
- Order the remaining sections most-important-first, then supporting
  detail, then implications. Base them on what the evidence actually
  covers, not a generic template.
- Do not add a section that only restates another section's content.

HEADINGS
- A heading states a claim or topic in a specific noun phrase (e.g. "APAC
  expansion drove the Q3 revenue increase"), never a generic label
  ("Introduction", "Background", "Discussion", "Conclusion", "Section 2").
- Intents are one sentence: the point this section proves and the evidence
  it will lean on (a stat, a comparison, a quote).
"""

DOC_SECTION_SYSTEM = AUTHORING_GUIDE + """
You are drafting ONE section of a report as a JSON object with a `blocks`
array (docloom blocks: paragraph, bullets, numbered, quote, callout, table,
stats, chart, heading). Do NOT repeat this section's own title as a heading
block (level 1 or 2); that title is added for you. You MAY use level-3
sub-headings inside the section to break it into chunks (see below).
If this section is the executive summary, open with the single main
conclusion in the first sentence, then the supporting points.

PARAGRAPHS
- Every paragraph opens with a topic sentence stating its claim, then
  supports it. At most 4 sentences per paragraph; split up longer ones.
- If the section runs long, add a `heading` block (level 3) roughly every
  150-250 words to break it into scannable chunks.

BLOCK SELECTION (pick the block the evidence calls for)
- One or two key numbers: a "stats" block, not a number buried in a
  sentence.
- A trend, comparison, ranking, or share of a whole: a "chart" block (bar,
  column, line, or area). NEVER a "pie" chart.
- Precise or mixed-unit values a reader would look up: a "table".
- A genuinely parallel, discrete, short list (at most 5-6 items):
  "bullets". Otherwise write it as prose, walls of bullets are not
  analysis.
- Every chart, table, or image gets a short title and a one-line takeaway
  caption stating what it shows.

GROUNDING
- Ground every claim, number, date, and name in the provided evidence and
  cite it. If the evidence does not support a claim, cut it, never invent
  one.
- Never ship placeholder, lorem-ipsum, "TBD", or "insert X here" text, and
  never return an empty or title-only section: if the evidence is thin,
  write the strongest true paragraph it supports."""


def _section_content_errors(section: DocSection) -> list[str]:
    if not section.blocks:
        return ["this section has no content blocks; add real, grounded "
                "content, not an empty placeholder"]
    return _placeholder_errors(_blocks_text(section.blocks))


def _section_errors(doc_title: str, section: DocSection, source_ids: set[str]) -> list[str]:
    return (_lint_errors(source_ids, title=doc_title, blocks=section.blocks)
            + _section_content_errors(section))


def _default_doc_outline(prompt: str) -> DocOutline:
    """Same purpose as _default_outline (deck), for the report pipeline."""
    topic = _fallback_topic(prompt)
    return DocOutline(doc_title=topic, sections=[
        DocOutlineItem(heading=f"Overview of {topic}",
                       intent=f"introduce {topic} and why it matters"),
        DocOutlineItem(heading=f"What the evidence shows about {topic}",
                       intent=f"the main findings about {topic}"),
        DocOutlineItem(heading="What this means going forward",
                       intent=f"the practical implications of {topic}"),
    ])


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
    outline_evidence, _ = await _section_block(notebook_id, prompt, context_block, bool(sources))
    try:
        outline: DocOutline = await generate_validated(
            cfg,
            [{"role": "system", "content": DOC_OUTLINE_SYSTEM},
             {"role": "user", "content": prompt + outline_evidence}],
            schema=llm_schema(DocOutline),
            parse=lambda t: parse_llm_output(t, DocOutline),
            lint_fn=lambda o: ([] if 2 <= len(o.sections) <= 10
                               else ["need 2-10 sections"]),
        )
    except _UNIT_FAILURES as e:
        ctx.emit("outline", "skipped",
                 detail="using a default outline after a generation failure",
                 data={"error": str(e)[:200]})
        outline = _default_doc_outline(prompt)
    ctx.emit("outline", "done", data={"doc_title": outline.doc_title,
             "sections": [s.heading for s in outline.sections]})

    blocks: list[Block] = []
    known_sources: dict[str, dict] = {s["id"]: s for s in sources}
    broad_ids = set(known_sources)
    for i, item in enumerate(outline.sections):
        ctx.emit("section", "running", detail=item.heading,
                 data={"index": i + 1, "total": len(outline.sections)})
        blocks.append(Heading(level=2, text=item.heading))
        sec_block, sec_sources = await _section_block(
            notebook_id, f"{item.heading} - {item.intent}", context_block, bool(sources))
        for s in sec_sources:
            known_sources.setdefault(s["id"], s)
        section_ids = broad_ids | {s["id"] for s in sec_sources}
        try:
            section: DocSection = await generate_validated(
                cfg,
                [{"role": "system", "content": DOC_SECTION_SYSTEM},
                 {"role": "user", "content":
                    f'Report: "{outline.doc_title}"\nSection: "{item.heading}"\n'
                    f"Intent: {item.intent}{sec_block}"}],
                schema=llm_schema(DocSection),
                parse=lambda t: parse_llm_output(t, DocSection),
                lint_fn=lambda s, ids=section_ids: _section_errors(
                    outline.doc_title, s, ids),
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
                   sources=[Source(**s) for s in known_sources.values()])
    _citation_gate(doc, set(known_sources))
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
One sheet per distinct table; do not split one table across sheets, and do
not merge unrelated tables into one.

Each sheet has a name, columns (header + optional Excel number format like
"$#,##0" or "0.0%"), and rows of typed cells (numbers as numbers, not
strings). Use a {"formula": "=SUM(B2:B10)"} cell for totals and other
derived values (subtotals, averages, percent-of-total) instead of
precomputing them yourself. Every sheet needs at least one column and at
least one real data row; a header row with no data is not a spreadsheet.

Ground every figure in the evidence provided; never invent numbers, and
never fill a cell with placeholder text ("TBD", "example", "N/A" as a
stand-in for a real value)."""


class SheetOutlineItem(BaseModel):
    name: str = Field(description="sheet tab name")
    intent: str = Field(description="one sentence: what data this sheet holds")


class SheetOutline(BaseModel):
    title: str
    sheets: list[SheetOutlineItem]


SHEET_OUTLINE_SYSTEM = """\
You plan spreadsheet workbooks. Given a request and the evidence provided,
return JSON: a workbook `title` plus 1-8 sheets, each {name, intent}. One
sheet per distinct table; do not split one table across sheets or merge
unrelated tables into one. Base the sheets on what the evidence actually
contains. Intents are one sentence naming the data this sheet holds and
where it comes from."""

SHEET_SECTION_SYSTEM = AUTHORING_GUIDE + """
You produce ONE sheet of a workbook as a JSON object matching the provided
schema (a docloom Sheet): a name, columns (header + optional Excel number
format like "$#,##0" or "0.0%"), and rows of typed cells (numbers as numbers,
not strings). Use a {"formula": "=SUM(B2:B10)"} cell for totals and other
derived values instead of precomputing them yourself.

This sheet must carry real data: at least one column and at least one data
row. Ground every figure in the evidence provided; never invent numbers,
and never use placeholder text ("TBD", "example", "N/A" as a stand-in for a
real value) in a cell."""


def _sheet_text(sheet: Sheet) -> list[str]:
    texts = [sheet.name] + [c.header for c in sheet.columns]
    for row in sheet.rows:
        texts.extend(cell for cell in row if isinstance(cell, str))
    return texts


def _sheet_content_errors(sheet: Sheet) -> list[str]:
    if not (sheet.columns and sheet.rows):
        return ["a sheet needs at least one column and at least one data row"]
    return _placeholder_errors(_sheet_text(sheet))


def _default_sheet_outline(prompt: str) -> SheetOutline:
    """Same purpose as _default_outline (deck), for the sheet split-fallback
    path; kept to a single generic sheet rather than 3-5 items since a bare
    workbook name is a much weaker signal than a deck/report topic."""
    topic = _fallback_topic(prompt)
    return SheetOutline(title=topic, sheets=[
        SheetOutlineItem(name="Data", intent=f"the data behind {topic}")])


async def run_sheet_pipeline(
    ctx: JobCtx, notebook_id: str, artifact_id: str, prompt: str,
    context_lines: list[str] | None = None, sources: list[dict] | None = None,
) -> None:
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))
    ctx.emit("context", "done")
    context_block = _context_block(context_lines, cite=False)  # SheetDoc has no Span type
    have_sources = bool(sources)
    # a touch of targeted retrieval for the whole-workbook request, the same
    # mechanism the deck/doc per-unit calls use, instead of leaving the
    # sheet pipeline on the broad context block alone
    outline_evidence, _ = await _section_block(notebook_id, prompt, context_block, have_sources)

    ctx.emit("sheet", "running")
    try:
        result: SheetDoc = await generate_validated(
            cfg,
            [{"role": "system", "content": SHEET_SYSTEM},
             {"role": "user", "content": prompt + outline_evidence}],
            schema=llm_schema(SheetDoc),
            parse=lambda t: parse_llm_output(t, SheetDoc),
            lint_fn=lambda d: (["produce at least one sheet"] if not d.sheets
                               else [msg for s in d.sheets
                                     for msg in _sheet_content_errors(s)]),
        )
        title, sheets = result.title, result.sheets
        ctx.emit("sheet", "done", detail=title)
    except (TruncatedOutput, GenerationFailed) as e:
        # the whole workbook doesn't fit one call (a big multi-sheet request
        # easily exceeds the token cap in one shot), or it came back empty
        # after every retry (GenerationFailed): plan sheet names first, then
        # fill each sheet with its own bounded call, same shape as the
        # deck/doc pipelines, so a big workbook still produces something
        # instead of failing 3 retries against the same cap and shipping
        # nothing
        detail = ("workbook too large for one call; splitting by sheet"
                  if isinstance(e, TruncatedOutput)
                  else "one-shot workbook failed validation; splitting by sheet")
        ctx.emit("outline", "running", detail=detail)
        try:
            outline: SheetOutline = await generate_validated(
                cfg,
                [{"role": "system", "content": SHEET_OUTLINE_SYSTEM},
                 {"role": "user", "content": prompt + outline_evidence}],
                schema=llm_schema(SheetOutline),
                parse=lambda t: parse_llm_output(t, SheetOutline),
                lint_fn=lambda o: ([] if 1 <= len(o.sheets) <= 8 else ["need 1-8 sheets"]),
            )
        except _UNIT_FAILURES as e:
            ctx.emit("outline", "skipped",
                     detail="using a default outline after a generation failure",
                     data={"error": str(e)[:200]})
            outline = _default_sheet_outline(prompt)
        ctx.emit("outline", "done", data={"title": outline.title,
                 "sheets": [s.name for s in outline.sheets]})
        title, sheets = outline.title, []
        for i, item in enumerate(outline.sheets):
            ctx.emit("sheet", "running", detail=item.name,
                     data={"index": i + 1, "total": len(outline.sheets)})
            sec_block, _ = await _section_block(
                notebook_id, f"{item.name} - {item.intent}", context_block, have_sources)
            try:
                sheet: Sheet = await generate_validated(
                    cfg,
                    [{"role": "system", "content": SHEET_SECTION_SYSTEM},
                     {"role": "user", "content":
                        f'Workbook: "{outline.title}"\nSheet: "{item.name}"\n'
                        f"Intent: {item.intent}{sec_block}"}],
                    schema=llm_schema(Sheet),
                    parse=lambda t: parse_llm_output(t, Sheet),
                    lint_fn=_sheet_content_errors,
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


class DiagramGen(Diagram):
    """LLM-facing diagram schema: docloom's own coordinate-free Diagram IR
    (nodes/edges/groups/direction -- see docloom.ir.Diagram) with `title`
    promoted from optional to required, so every generated diagram gets a
    real name without a second, drifting copy of the node/edge/group
    fields. `id`/`caption`/`alt` are inherited but harmless: `id` is editor
    bookkeeping llm_schema() already strips from the emitted schema, and
    `caption`/`alt` stay optional."""

    title: str = Field(description="short diagram title, e.g. 'Order Processing Flow'")


DIAGRAM_SYSTEM = """\
You design architecture and process diagrams as a structured, coordinate-free
graph: nodes, edges, and groups only. Never invent coordinates, sizes, or
routing -- the renderer lays everything out.

- `direction`: "LR" (left-to-right, most flows) or "TB" (top-to-bottom).
- Each node has a short `id` (referenced by edges/groups, never shown) and a
  `label`. Pick the closest `type`: `client` (user/browser/app), `service`
  (API/business logic), `store` (database), `queue` (message bus/queue),
  `security` (auth/firewall/gateway), `cloud` (external cloud platform), or
  `external` (third-party system). Set `sublabel` for a tech detail (e.g.
  "PostgreSQL 16") when it helps.
- Each edge has a `source` and `target` that MUST be ids of nodes you
  defined, plus an optional `label`. `style` is "solid" (default), "dashed"
  (async/optional), "emphasis" (the critical path), or "secure" (TLS/
  encrypted).
- A `group` is a labeled boundary box: give it an `id` and `label`, then set
  every member node's `group` to that id (e.g. everything inside "VPC").
- Keep to at most 20 nodes with short labels.

Example (as JSON, matching the schema):
{"title": "Checkout Flow", "direction": "LR",
 "nodes": [{"id": "user", "label": "Shopper", "type": "client"},
           {"id": "api", "label": "Checkout API", "type": "service", "group": "vpc"},
           {"id": "db", "label": "Orders", "type": "store", "group": "vpc"}],
 "edges": [{"source": "user", "target": "api", "label": "HTTPS"},
           {"source": "api", "target": "db"}],
 "groups": [{"id": "vpc", "label": "VPC"}]}

Give the diagram a short, descriptive `title`."""


def _diagram_ir_errors(d: Diagram) -> list[str]:
    """Lint gate for generated/repaired diagrams: replaces the old D2-syntax
    sniffing (_looks_like_d2) now that generation emits the coordinate-free
    Diagram IR directly. A diagram is only as good as its ability to lay
    out, so the check IS running solve() once -- the exact validation
    /diagram/layout and /diagram/render perform on save (artifacts.py) --
    not a syntax check. Dangling edges are checked explicitly first for a
    clear, actionable retry message; solve() itself would otherwise raise a
    bare KeyError for the same problem (and ValueError for duplicate node/
    group ids), so it's still wrapped below as a catch-all."""
    if not d.nodes:
        return ["diagram has no nodes"]
    node_ids = {n.id for n in d.nodes}
    dangling = sorted(
        f"{e.source!r}->{e.target!r}" for e in d.edges
        if e.source not in node_ids or e.target not in node_ids
    )
    if dangling:
        return ["edge(s) reference a node id that doesn't exist (every edge "
                "source/target must be one of the node ids you defined): "
                + ", ".join(dangling)]
    try:
        solve(d)
    except Exception as e:
        return [f"diagram failed to lay out: {e}"]
    return []


def _write_diagram_renders(artifact_id: str, svg: str | None, png: bytes | None) -> None:
    """Write render.svg/render.png under the artifact's data dir -- the same
    fixed-name file plumbing artifacts.py's _write_renders/save_renders and
    irx.py's _resolve_artifact_render read (editor-design.md section 2), so
    the editor preview and the export bake are the same bytes by
    construction. Duplicated here (not imported from artifacts.py) to avoid
    a generate.py <-> artifacts.py import cycle: artifacts.py already
    imports pipelines from this module at module load time."""
    adir = data_dir() / "artifacts" / artifact_id
    adir.mkdir(parents=True, exist_ok=True)
    if svg:
        (adir / "render.svg").write_text(svg, encoding="utf-8")
    if png is not None:
        (adir / "render.png").write_bytes(png)


def _diagram_theme(theme_name: str, owner: str | None):
    """Same 6-key-overlay theme resolution as artifacts.py's _diagram_theme
    (studio theme -> brand overrides -> docloom Theme), so a diagram primed
    at generation time renders identically to one solved/rendered later
    through /diagram/layout|render."""
    from .assets import apply_brand
    from .irx import studio_theme, to_docloom_theme

    return to_docloom_theme(apply_brand(studio_theme(theme_name), owner))


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
        lint_fn=_diagram_ir_errors,
    )
    ctx.emit("body", "done", detail=result.title)
    d = Diagram(**result.model_dump(exclude_none=True))
    theme_name = get_setting("deck.theme", owner) or "paper"
    save_artifact(artifact_id, result.title, {
        "type": "diagram_ir",
        "diagram_ir": d.model_dump(exclude_none=True),
        "theme_name": theme_name,
        "layout": "native",
        "overlay": None,
        "render": "svg",
    })
    # prime render.svg/render.png (best-effort: a raster backend can be
    # missing; _resolve_artifact_render server-rasterizes at export anyway)
    # so the editor's first paint and a same-second export both find a
    # ready render instead of racing the canvas's own /diagram/render call.
    try:
        theme = _diagram_theme(theme_name, owner)
        svg = render_diagram(d, theme, "svg")
        png = render_diagram(d, theme, "png")
        _write_diagram_renders(artifact_id, svg, png)
    except Exception as e:
        ctx.emit("render", "skipped", data={"error": str(e)[:200]})
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


# Hard caps matching what the fixed-size AntV cards actually fit (see
# understand-infographic.md). The library never wraps/ellipsizes past its
# pre-baked line budget -- it OVERLAPS the neighboring card instead. A
# pydantic Field(max_length=...) alone would not help: docloom.llm.llm_schema
# strips maxLength/minLength before the schema ever reaches the model, so the
# model is never told about the limit; and if we instead relied on
# Field(max_length=...) validation, an over-cap value would be a hard parse
# failure (risking GenerationFailed after a few rounds) rather than a soft,
# actionable retry. So the caps are enforced only in _infographic_errors
# below (the lint_fn generate_validated's retry loop actually re-prompts on),
# plus a deterministic clamp as a backstop once every retry round is spent.
IG_ITEMS_MIN, IG_ITEMS_MAX = 3, 6
IG_TITLE_MAX = 40
IG_LABEL_MAX = 24
IG_DESC_MAX = 90


class InfographicItem(BaseModel):
    label: str = Field(description=f"short item title, at most {IG_LABEL_MAX} characters")
    desc: str = Field(
        "", description=f"one short supporting phrase, at most {IG_DESC_MAX} characters")


class InfographicSpec(BaseModel):
    style: Literal["list", "steps", "pyramid", "grid"] = "list"
    title: str = Field(description=f"short title, at most {IG_TITLE_MAX} characters")
    items: list[InfographicItem] = Field(
        description=f"{IG_ITEMS_MIN}-{IG_ITEMS_MAX} items")


IG_SYSTEM = f"""\
You design infographics. Return JSON: a `style` (list, steps, pyramid, or
grid), a short `title`, and {IG_ITEMS_MIN}-{IG_ITEMS_MAX} `items` each with a
punchy `label` and a one-line `desc`.

Cards are a FIXED size: text past the limit does not wrap onto a new line,
it overlaps the neighboring card. These are hard limits, not suggestions:
- `title`: at most {IG_TITLE_MAX} characters (fits one line).
- each `label`: at most {IG_LABEL_MAX} characters (2-4 words).
- each `desc`: at most {IG_DESC_MAX} characters (one short phrase), or leave
  it empty rather than run long.
- exactly {IG_ITEMS_MIN}-{IG_ITEMS_MAX} `items`.

Ground in evidence when provided."""


def _clamp_text(text: str, limit: int) -> str:
    """Deterministic backstop: if the model still ships an over-length value
    after every lint retry (see _infographic_errors), cut it at the last word
    boundary within the cap rather than shipping text guaranteed to overlap a
    fixed-size card. Falls back to a hard cut only when there is no space to
    break on (one very long token)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:")
    return cut or text[:limit]


def _infographic_errors(spec: InfographicSpec) -> list[str]:
    """The lint_fn generate_validated retries on. It enforces ONLY the item
    count, which the deterministic _clamp_text backstop below cannot fix.
    Over-length title/label/desc are deliberately NOT failed here: doing so
    could exhaust every retry and hard-fail the whole generation on a model
    that keeps overshooting, whereas _clamp_text (applied right after) trims
    them deterministically so a long value never overlaps a card and never
    sinks the artifact. IG_SYSTEM still asks the model to keep them short."""
    if not IG_ITEMS_MIN <= len(spec.items) <= IG_ITEMS_MAX:
        return [f"need {IG_ITEMS_MIN}-{IG_ITEMS_MAX} items, got {len(spec.items)}"]
    return []


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
        lint_fn=_infographic_errors,
    )
    # deterministic backstop: never ship a value the fixed-size cards are
    # guaranteed to overlap, even if the model exhausted every lint retry
    spec.title = _clamp_text(spec.title, IG_TITLE_MAX)
    for item in spec.items:
        item.label = _clamp_text(item.label, IG_LABEL_MAX)
        item.desc = _clamp_text(item.desc, IG_DESC_MAX)
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
in the provided evidence; do not invent facts. No stage directions or markdown,
just what each person says."""


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


async def repair_diagram(diagram_ir_json: str, error: str, user_id: str | None) -> dict:
    """Repair a Diagram IR that failed to lay out (see _diagram_ir_errors):
    given the solve() error and the offending diagram, ask the LLM for a
    corrected docloom Diagram and return {"diagram_ir": ...} for the
    artifact payload's `diagram_ir` key (editor-design.md section 3d).

    `diagram_ir_json` is the failing diagram serialized as JSON text (the
    caller's own working IR, JSON-encoded) rather than a dict, so this stays
    callable the same way the old D2 `src` string was.

    NOTE for whoever owns artifacts.py: the current /repair route still does
    `fixed = await repair_diagram(body.src, body.error, user["id"]); return
    {"source": fixed}` -- both `body.src`'s "a D2 string" framing and the
    `{"source": ...}` response shape predate this IR switch and need a
    matching update (request should carry a serialized `diagram_ir`;
    response should forward this function's {"diagram_ir": ...} return
    value directly) before the editor's repair flow works end to end. Not
    changed here: out of this module's ownership."""
    cfg = ProviderConfig(**get_setting("provider.generation", user_id))
    out: DiagramGen = await generate_validated(
        cfg,
        [{"role": "system", "content": DIAGRAM_SYSTEM},
         {"role": "user", "content":
            f"This diagram failed to lay out with error:\n{error}\n\n"
            f"Diagram JSON:\n{diagram_ir_json}\n\n"
            "Return the complete corrected diagram JSON."}],
        schema=llm_schema(DiagramGen),
        parse=lambda t: parse_llm_output(t, DiagramGen),
        lint_fn=_diagram_ir_errors,
    )
    d = Diagram(**out.model_dump(exclude_none=True))
    return {"diagram_ir": d.model_dump(exclude_none=True)}


def _context_block(context_lines: list[str] | None, cite: bool = True) -> str:
    """`cite=False` for schemas with no Span type to set a `cite` on (SheetDoc
    cells, DiagramGen node/edge labels, InfographicSpec labels, PodcastScript)
    -- appending the citation instruction there just invites cite-shaped
    garbage in a diagram label or a spreadsheet cell."""
    if not context_lines:
        return ""
    instruction = "Ground every factual claim in this evidence" + (
        " and set the span's `cite` to the given source id." if cite else ".")
    return f"\n\n{instruction}\nEvidence:\n" + "\n".join(context_lines)


async def _section_block(
    notebook_id: str, query: str, base_block: str, have_sources: bool, k: int = 6
) -> tuple[str, list[dict]]:
    """Retrieve evidence targeted at ONE section/slide and append it to the
    broad context block. Falls back to just `base_block` when the notebook has
    no sources or retrieval finds nothing (keeps stubbed tests deterministic).
    Also returns the distinct sources surfaced in THIS block (id/title), so
    callers can widen their citation-validation id-set to match what the
    model was actually shown -- otherwise a compliant cite to a section-only
    source is flagged cite/unknown-source and the unit is discarded."""
    if not have_sources:
        return base_block, []
    try:
        from .embeddings import retrieve

        chunks = await retrieve(notebook_id, query, k=k)
    except Exception:
        chunks = []
    if not chunks:
        return base_block, []
    lines = [
        f'[cite id: "{c.source_id}"] '
        f'({c.source_title}{f", p.{c.page}" if c.page else ""}) {c.text}'
        for c in chunks
    ]
    seen: dict[str, dict] = {}
    for c in chunks:
        seen.setdefault(c.source_id, {"id": c.source_id, "title": c.source_title})
    return (base_block + "\n\nMost relevant to THIS section:\n" + "\n".join(lines),
            list(seen.values()))


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
    """Persist payload as the artifact's next version. The head bump, the
    version snapshot, and the status flip land in ONE transaction, and the
    version is allocated by the UPDATE itself (version = version + 1 ...
    RETURNING), so two concurrent saves get distinct consecutive versions
    instead of both computing the same one and silently clobbering each
    other's payload. Raises LookupError if the artifact does not exist
    (previously this silently allocated version 1 for a nonexistent id)."""
    text = json.dumps(payload)
    t = now()
    with transaction() as tx:
        # a payload just landed (first generation, a manual edit, or a
        # revert): the artifact is viewable/exportable, so it's 'ready'
        # regardless of which caller reached this point
        rows = tx.execute(
            "UPDATE artifacts SET title = ?, version = version + 1, "
            "payload_json = ?, updated = ?, status = 'ready' "
            "WHERE id = ? RETURNING version",
            (title, text, t, artifact_id)).fetchall()
        # fetchall (not fetchone): sqlite3 must step the RETURNING statement
        # to completion before commit, or the commit sees it still pending
        if not rows:
            raise LookupError(f"artifact {artifact_id} not found")
        version = rows[0]["version"]
        # belt for a legacy DB the OLD racy code corrupted (a snapshot row at
        # this version already present): overwrite it so head and snapshot
        # agree — DO NOTHING would freeze exactly the divergence being fixed,
        # and no clause at all would make this artifact permanently unsavable
        tx.execute(
            "INSERT INTO artifact_versions (artifact_id, version, "
            "payload_json, created) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (artifact_id, version) DO UPDATE SET "
            "payload_json = excluded.payload_json, created = excluded.created",
            (artifact_id, version, text, t))
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

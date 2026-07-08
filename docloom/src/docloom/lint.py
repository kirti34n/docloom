"""Deterministic document linter.

Catches the failures LLM-generated documents actually ship with — overflowing
slides, walls of text, dangling citations, unreadable theme contrast — and
returns machine-readable findings an LLM can self-correct against:

    findings = lint(doc)
    if findings: retry_with(json.dumps([f.model_dump() for f in findings]))
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .ir import (
    Artifact, Block, BulletList, Callout, Chart, Code, Document, Formula,
    Heading, Image, NumberedList, Paragraph, Quote, Slide, StatRow, Table,
    cited_ids, plain, spans,
)
from .theme import Theme, contrast_ratio

Severity = Literal["error", "warning", "info"]


class Finding(BaseModel):
    rule: str
    severity: Severity
    where: str  # human/machine-readable location, e.g. "slides[3]"
    message: str


# Heuristic budgets for a 16:9 slide. Deliberately simple character-count
# estimates — they catch the 95% case (walls of text) without a layout engine.
MAX_BULLETS_PER_SLIDE = 7
MAX_BULLET_CHARS = 130
MAX_TITLE_CHARS = 60
MAX_SLIDE_CHARS = 800
MAX_TABLE_ROWS_ON_SLIDE = 8
MAX_TABLE_COLS_ON_SLIDE = 6
MIN_CONTRAST_BODY = 4.5  # WCAG AA


def _block_chars(block: Block) -> int:
    if isinstance(block, (Heading, Paragraph, Quote, Callout)):
        return len(plain(block.text))
    if isinstance(block, (BulletList, NumberedList)):
        return sum(len(plain(it.text)) for it in block.items)
    if isinstance(block, Code):
        return len(block.code)
    if isinstance(block, (Image, Artifact)):
        return len(block.caption or "")
    if isinstance(block, Chart):
        return len(block.title or "") + sum(len(x) for x in block.labels)
    if isinstance(block, StatRow):
        return sum(len(s.label) + len(s.value) for s in block.items)
    if isinstance(block, Table):
        return sum(len(plain(c)) for c in block.header) + sum(
            len(plain(c)) for row in block.rows for c in row
        )
    return 0


def _lint_slide(slide: Slide, where: str, out: list[Finding]) -> None:
    all_blocks = slide.blocks + slide.right

    if slide.title and len(slide.title) > MAX_TITLE_CHARS:
        out.append(Finding(
            rule="deck/title-too-long", severity="warning", where=where,
            message=f"slide title is {len(slide.title)} chars "
                    f"(max {MAX_TITLE_CHARS}); it will wrap or shrink",
        ))

    if slide.layout in ("content", "two_column") and not all_blocks:
        out.append(Finding(
            rule="deck/empty-slide", severity="warning", where=where,
            message="content slide has no blocks",
        ))

    if slide.layout in ("title", "section") and all_blocks:
        out.append(Finding(
            rule="deck/ignored-blocks", severity="warning", where=where,
            message=f'"{slide.layout}" slides render only title/subtitle; '
                    "these blocks will not appear — use a content slide",
        ))

    if slide.layout in ("hero", "image_left", "image_right") and slide.image is None:
        out.append(Finding(
            rule="deck/missing-slot-image", severity="warning", where=where,
            message=f'"{slide.layout}" layout has no image slot filled; '
                    "set slide.image (a path, asset, or query) or switch layout",
        ))

    n_bullets = sum(
        len(b.items) for b in all_blocks if isinstance(b, (BulletList, NumberedList))
    )
    if n_bullets > MAX_BULLETS_PER_SLIDE:
        out.append(Finding(
            rule="deck/too-many-bullets", severity="warning", where=where,
            message=f"{n_bullets} bullets on one slide (max {MAX_BULLETS_PER_SLIDE}); "
                    "split the slide",
        ))

    for b in all_blocks:
        if isinstance(b, (BulletList, NumberedList)):
            for j, it in enumerate(b.items):
                n = len(plain(it.text))
                if n > MAX_BULLET_CHARS:
                    out.append(Finding(
                        rule="deck/bullet-too-long", severity="warning",
                        where=f"{where}.items[{j}]",
                        message=f"bullet is {n} chars (max {MAX_BULLET_CHARS}); "
                                "tighten it or move detail to speaker notes",
                    ))
        elif isinstance(b, Table):
            cols = max(len(b.header), max((len(r) for r in b.rows), default=0))
            if len(b.rows) > MAX_TABLE_ROWS_ON_SLIDE or cols > MAX_TABLE_COLS_ON_SLIDE:
                out.append(Finding(
                    rule="deck/table-too-big", severity="warning", where=where,
                    message=f"table is {len(b.rows)}x{cols} "
                            f"(max {MAX_TABLE_ROWS_ON_SLIDE}x{MAX_TABLE_COLS_ON_SLIDE} "
                            "on a slide); move it to the report or a sheet",
                ))

    # two_column slides get half the width per column, so each column gets
    # half the character budget; other layouts get the full-width budget
    if slide.layout in ("hero", "image_left", "image_right"):
        total = sum(_block_chars(b) for b in slide.blocks)
        if total > MAX_SLIDE_CHARS // 2:
            out.append(Finding(
                rule="deck/overflow", severity="error", where=where,
                message=f"~{total} chars beside the image "
                        f"(budget {MAX_SLIDE_CHARS // 2}); split the slide",
            ))
    elif slide.layout == "two_column":
        for name, blocks in (("blocks", slide.blocks), ("right", slide.right)):
            total = sum(_block_chars(b) for b in blocks)
            if total > MAX_SLIDE_CHARS // 2:
                out.append(Finding(
                    rule="deck/overflow", severity="error", where=f"{where}.{name}",
                    message=f"~{total} chars in one column "
                            f"(budget {MAX_SLIDE_CHARS // 2} at half width); "
                            "this will overflow the slide — split it",
                ))
    else:
        total = sum(_block_chars(b) for b in all_blocks)
        if total > MAX_SLIDE_CHARS:
            out.append(Finding(
                rule="deck/overflow", severity="error", where=where,
                message=f"~{total} chars of content (budget {MAX_SLIDE_CHARS}); "
                        "this will overflow the slide — split it",
            ))


def _walk_images(blocks: list[Block]):
    for b in blocks:
        if isinstance(b, Image):
            yield b


def lint(doc: Document, theme: Theme | None = None) -> list[Finding]:
    out: list[Finding] = []

    # citations: unique ids, every cite resolves, every source is used
    seen_ids: set[str] = set()
    for s in doc.sources:
        if s.id in seen_ids:
            out.append(Finding(
                rule="cite/duplicate-source", severity="error", where="sources",
                message=f'two sources share the id "{s.id}"; ids must be unique',
            ))
        seen_ids.add(s.id)
    known = seen_ids
    used = cited_ids(doc)
    for cid in sorted(used - known):
        out.append(Finding(
            rule="cite/unknown-source", severity="error", where="sources",
            message=f'span cites "{cid}" but no source with that id exists',
        ))
    for cid in sorted(known - used):
        out.append(Finding(
            rule="cite/unused-source", severity="info", where="sources",
            message=f'source "{cid}" is never cited',
        ))

    # slides
    for i, slide in enumerate(doc.slides):
        _lint_slide(slide, f"slides[{i}]", out)

    # heading structure in report blocks
    prev_level = 0
    for i, b in enumerate(doc.blocks):
        if isinstance(b, Heading):
            if prev_level and b.level > prev_level + 1:
                out.append(Finding(
                    rule="doc/heading-skip", severity="info", where=f"blocks[{i}]",
                    message=f"heading level jumps from {prev_level} to {b.level}",
                ))
            prev_level = b.level

    # block-level rules across report blocks, slide blocks, and image slots
    slide_blocks = [b for s in doc.slides for b in s.blocks + s.right]
    slot_images = [s.image for s in doc.slides if s.image is not None]
    every_block = doc.blocks + slide_blocks + slot_images
    for i, b in enumerate(every_block):
        if isinstance(b, Table):
            widths = {len(b.header), *(len(r) for r in b.rows)}
            if len(widths) > 1:
                out.append(Finding(
                    rule="table/ragged", severity="warning", where=f"tables[{i}]",
                    message=f"header and rows have differing widths {sorted(widths)}; "
                            "short rows are padded with blank cells",
                ))
        elif isinstance(b, Chart):
            for s_ix, series in enumerate(b.series):
                if len(series.values) != len(b.labels):
                    out.append(Finding(
                        rule="chart/ragged-series", severity="error",
                        where=f"blocks[{i}].series[{s_ix}]",
                        message=f'series "{series.name}" has {len(series.values)} '
                                f"values for {len(b.labels)} labels",
                    ))
        elif isinstance(b, Artifact):
            if not b.artifact_id:
                out.append(Finding(
                    rule="artifact/unbound", severity="warning", where=f"blocks[{i}]",
                    message="artifact block has no artifact_id; nothing to render",
                ))
            elif not b.path:
                out.append(Finding(
                    rule="artifact/unrendered", severity="warning",
                    where=f"blocks[{i}]",
                    message="artifact has no rendered file yet; export will "
                            "skip it until a render is baked",
                ))

    # image slots resolve to something; files that are named must exist
    for img in _walk_images(every_block):
        if img.path:
            if not Path(img.path).is_file():
                out.append(Finding(
                    rule="image/missing", severity="error", where=img.path,
                    message=f"image file not found: {img.path}",
                ))
        elif not (img.asset_id or img.query):
            out.append(Finding(
                rule="image/unresolved", severity="warning", where="images",
                message="image has no path, asset, or query; the slot will "
                        "render empty",
            ))

    # sheets: empty formulas are silently skipped by xlsxwriter
    for si, sheet in enumerate(doc.sheets):
        for ri, row in enumerate(sheet.rows):
            for cell in row:
                if isinstance(cell, Formula) and not cell.formula.strip():
                    out.append(Finding(
                        rule="sheet/empty-formula", severity="error",
                        where=f"sheets[{si}].rows[{ri}]",
                        message="formula cell is empty; give it a formula "
                                "or use null for a blank cell",
                    ))

    # theme contrast
    if theme is not None:
        for name, fg, bg in (
            ("text on background", theme.text, theme.background),
            ("text on surface", theme.text, theme.surface),
        ):
            ratio = contrast_ratio(fg, bg)
            if ratio < MIN_CONTRAST_BODY:
                out.append(Finding(
                    rule="theme/low-contrast", severity="error", where="theme",
                    message=f"{name} contrast is {ratio:.1f}:1 "
                            f"(WCAG AA needs {MIN_CONTRAST_BODY}:1)",
                ))

    return out


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)

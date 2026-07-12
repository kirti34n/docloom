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

# Physical-height budgets (inches), mirroring the PPTX renderer's fixed-size
# blocks (render/pptx.py LAYOUT + _natural_h) so overflow is caught even when
# a block's character count is tiny: a chart or resolved image is a near-
# fixed ~4.5-4.6in tall no matter how short its title/caption is, which the
# char budget above cannot see. Keep these numbers in sync with render/pptx.py.
SLIDE_BODY_H_IN = 5.48   # slide height 7.5 - margin 0.6 - title band 1.42
FULL_BODY_W_IN = 12.13   # slide width 13.333 - 2 x margin 0.6
NARROW_BODY_W_IN = FULL_BODY_W_IN / 2  # two_column column / image-slot pane
CHART_H_IN = 4.5         # render/pptx.py LAYOUT["chart_max_h_in"]
IMAGE_H_IN = 4.6         # render/pptx.py _natural_h's resolved image/artifact estimate
STATS_H_IN = 1.4         # render/pptx.py LAYOUT["stat_card_h_in"]
STATS_MAX_CARDS = 5      # render/pptx.py LAYOUT["stat_max_cards"]
TABLE_ROW_H_IN = 0.36    # render/pptx.py _table_block's row height cap
BLOCK_GAP_IN = 0.14      # render/pptx.py LAYOUT["gap_in"], summed between blocks
LINE_H_IN = 0.26         # ~ render/pptx.py _line_h(14pt), body text line height


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


def _block_height(block: Block, width_in: float) -> float:
    """Rough physical height `block` occupies on a rendered slide (inches),
    mirroring render/pptx.py's fixed-size blocks: a chart, resolved image,
    stats row, or table takes a near-constant amount of vertical space no
    matter how few characters it carries, which _block_chars cannot see."""
    if isinstance(block, Chart):
        return CHART_H_IN
    if isinstance(block, (Image, Artifact)):
        return IMAGE_H_IN if block.path and Path(block.path).is_file() else 0.0
    if isinstance(block, StatRow):
        return STATS_H_IN if block.items else 0.0
    if isinstance(block, Table):
        return (len(block.rows) + 1) * TABLE_ROW_H_IN
    per_line = max(8, int(width_in * 144 / 14))

    def lines(text: str) -> int:
        if not text:
            return 0
        return sum(max(1, -(-len(ln) // per_line)) for ln in text.split("\n"))

    if isinstance(block, (BulletList, NumberedList)):
        return sum(max(1, lines(plain(it.text))) * LINE_H_IN for it in block.items)
    if isinstance(block, (Heading, Paragraph, Quote, Callout)):
        return lines(plain(block.text)) * LINE_H_IN
    if isinstance(block, Code):
        return lines(block.code) * LINE_H_IN
    return 0.0  # Divider and anything else: negligible


def _lint_slide(slide: Slide, where: str, out: list[Finding]) -> None:
    all_blocks = slide.blocks + slide.right

    if slide.title and len(slide.title) > MAX_TITLE_CHARS:
        out.append(Finding(
            rule="deck/title-too-long", severity="warning", where=where,
            message=f"slide title is {len(slide.title)} chars "
                    f"(max {MAX_TITLE_CHARS}); it will wrap or shrink",
        ))

    if slide.layout in ("content", "two_column", "quote") and not all_blocks:
        out.append(Finding(
            rule="deck/empty-slide", severity="warning", where=where,
            message=f'"{slide.layout}" slide has no blocks',
        ))

    if (slide.layout == "quote" and all_blocks
            and not any(isinstance(b, Quote) for b in all_blocks)):
        out.append(Finding(
            rule="deck/missing-quote-block", severity="warning", where=where,
            message='"quote" layout has content but no Quote block; the '
                    "large pull-quote will be blank. Add a quote block or "
                    "switch layout",
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
    if slide.layout in ("image_left", "image_right"):
        total = sum(_block_chars(b) for b in all_blocks)
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
    elif slide.layout == "hero":
        # a hero body renders in a short bottom caption band (~1.5in), not a
        # full slide body, so it holds far less: use the half budget. Warn
        # rather than error because the renderer auto-shrinks a lone block and
        # only drops trailing ones, so this degrades instead of crashing and
        # must not hard-block export.
        total = sum(_block_chars(b) for b in all_blocks)
        if total > MAX_SLIDE_CHARS // 2:
            out.append(Finding(
                rule="deck/overflow", severity="warning", where=where,
                message=f"~{total} chars in the hero caption band "
                        f"(budget {MAX_SLIDE_CHARS // 2}); trim it or move the "
                        "body to a content slide",
            ))
    elif slide.layout in ("content", "quote"):
        total = sum(_block_chars(b) for b in all_blocks)
        if total > MAX_SLIDE_CHARS:
            out.append(Finding(
                rule="deck/overflow", severity="error", where=where,
                message=f"~{total} chars of content (budget {MAX_SLIDE_CHARS}); "
                        "this will overflow the slide — split it",
            ))

    # height budget: fixed-size blocks (chart/image/table/stats) are blind to
    # the char budget above but not to physical space. Estimate inches and
    # compare to the slide body's usable height. two_column/image-slot
    # layouts get a narrower text column (the same "half" approximation the
    # char budget above makes), but the *vertical* budget does not shrink for
    # them: only the image or the other column takes width, not height.
    narrow = slide.layout in ("image_left", "image_right", "two_column")
    w_in = NARROW_BODY_W_IN if narrow else FULL_BODY_W_IN
    if slide.layout == "two_column":
        height_groups = [
            (f"{where}.blocks", slide.blocks), (f"{where}.right", slide.right),
        ]
    else:
        height_groups = [(where, all_blocks)]
    for group_where, blocks in height_groups:
        total_h = sum(_block_height(b, w_in) for b in blocks)
        if len(blocks) > 1:
            total_h += BLOCK_GAP_IN * (len(blocks) - 1)
        if total_h > SLIDE_BODY_H_IN:
            # advisory, not blocking: the PPTX renderer shrinks fixed-size
            # blocks (charts especially) toward the available space, so an
            # over-budget estimate usually still renders. A warning surfaces
            # the crowding without hard-failing export on a chart + a few
            # bullets (the char-budget rule above stays an error: text cannot
            # shrink indefinitely).
            out.append(Finding(
                rule="deck/overflow", severity="warning", where=group_where,
                message=f"~{total_h:.1f}in of estimated content height "
                        f"(budget {SLIDE_BODY_H_IN}in); a chart, image, "
                        "table, or stats row takes a near-fixed amount of "
                        "space regardless of caption length. Consider "
                        "splitting the slide or dropping a block",
            ))


def _walk_images(blocks: list[Block]):
    for b in blocks:
        if isinstance(b, Image):
            yield b


def _is_numeric(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def _lint_block_refs(b: Block, where: str, out: list[Finding]) -> None:
    if isinstance(b, Table):
        widths = {len(b.header), *(len(r) for r in b.rows)}
        if len(widths) > 1:
            out.append(Finding(
                rule="table/ragged", severity="warning", where=where,
                message=f"header and rows have differing widths {sorted(widths)}; "
                        "short rows are padded with blank cells",
            ))
    elif isinstance(b, Chart):
        widest = max([len(b.labels)] + [len(s.values) for s in b.series])
        if not b.series or widest == 0:
            out.append(Finding(
                rule="chart/empty", severity="error", where=where,
                message="chart has no data; fill labels and series",
            ))
        for s_ix, series in enumerate(b.series):
            if len(series.values) != len(b.labels):
                out.append(Finding(
                    rule="chart/ragged-series", severity="error",
                    where=f"{where}.series[{s_ix}]",
                    message=f'series "{series.name}" has {len(series.values)} '
                            f"values for {len(b.labels)} labels",
                ))
        if b.chart == "pie" and len(b.series) > 1:
            out.append(Finding(
                rule="chart/pie-multi-series", severity="warning", where=where,
                message="pie chart has multiple series; PPTX can only render "
                        "a native pie chart with one series and will fall "
                        "back to a plain data table, losing the editable "
                        "chart. Use one series per pie chart",
            ))
        if b.chart == "scatter":
            bad = [lb for lb in b.labels if not _is_numeric(lb)]
            if bad:
                out.append(Finding(
                    rule="chart/scatter-non-numeric", severity="warning", where=where,
                    message="scatter chart labels are plotted as numeric "
                            f"x-values; non-numeric label(s) {bad[:3]} make "
                            "the PPTX renderer fall back to a data table",
                ))
    elif isinstance(b, Artifact):
        if not b.artifact_id:
            out.append(Finding(
                rule="artifact/unbound", severity="warning", where=where,
                message="artifact block has no artifact_id; nothing to render",
            ))
        elif not b.path:
            out.append(Finding(
                rule="artifact/unrendered", severity="warning",
                where=where,
                message="artifact has no rendered file yet; export will "
                        "skip it until a render is baked",
            ))
    elif isinstance(b, StatRow):
        if len(b.items) > STATS_MAX_CARDS:
            out.append(Finding(
                rule="stats/too-many", severity="warning", where=where,
                message=f"{len(b.items)} stat cards (PPTX fits at most "
                        f"{STATS_MAX_CARDS} per row); the rest are dropped",
            ))


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

    # block-level rules across report blocks, slide blocks, and image slots.
    # Each source is walked with its own index so `where` points at a real
    # location instead of an offset into a combined list.
    for i, b in enumerate(doc.blocks):
        _lint_block_refs(b, f"blocks[{i}]", out)
    slide_blocks: list[Block] = []
    for si, s in enumerate(doc.slides):
        slide_blocks.extend(s.blocks)
        slide_blocks.extend(s.right)
        # walk each column with its own index so `where` points at the real
        # location (a right-column block is at .right[i], not .blocks[i])
        for bi, b in enumerate(s.blocks):
            _lint_block_refs(b, f"slides[{si}].blocks[{bi}]", out)
        for ri, b in enumerate(s.right):
            _lint_block_refs(b, f"slides[{si}].right[{ri}]", out)
    slot_images = [s.image for s in doc.slides if s.image is not None]
    logo_images = [doc.logo] if doc.logo is not None else []
    every_block = doc.blocks + slide_blocks + slot_images + logo_images

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

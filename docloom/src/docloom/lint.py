"""Deterministic document linter.

Catches the failures LLM-generated documents actually ship with — overflowing
slides, walls of text, dangling citations, unreadable theme contrast — and
returns machine-readable findings an LLM can self-correct against:

    findings = lint(doc)
    if findings: retry_with(json.dumps([f.model_dump() for f in findings]))
"""

from __future__ import annotations

import re
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

# Authoring-quality thresholds (research-notebooklm-quality.md section 6).
# These rules are advisory only: severity="warning" so they never hard-block
# export the way deck/overflow and chart/empty (severity="error") can.

# title.is_takeaway: an exact topic label, or a short phrase with no
# recognizable finite verb, reads as a label rather than a takeaway sentence.
_BANNED_TITLE_LABELS = frozenset({
    "overview", "introduction", "background", "results", "agenda", "conclusion",
})
MAX_VERBLESS_WORDS = 6  # longer titles are more likely a real sentence using a
                         # verb outside the curated list below; only flag short ones
_WORD_RE = re.compile(r"[A-Za-z']+")
_VERB_CUES = frozenset({
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "do", "does", "did",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    "grow", "grows", "grew", "rise", "rises", "rose", "risen",
    "fall", "falls", "fell", "fallen", "drop", "drops", "dropped",
    "increase", "increases", "increased", "decrease", "decreases", "decreased",
    "improve", "improves", "improved", "exceed", "exceeds", "exceeded",
    "miss", "misses", "missed", "drive", "drives", "drove", "driven",
    "deliver", "delivers", "delivered", "win", "wins", "won",
    "lead", "leads", "led", "beat", "beats", "gain", "gains", "gained",
    "lose", "loses", "lost", "remain", "remains", "remained",
    "continue", "continues", "continued", "show", "shows", "showed",
    "reveal", "reveals", "revealed", "confirm", "confirms", "confirmed",
    "require", "requires", "required", "need", "needs", "needed",
    "enable", "enables", "enabled", "support", "supports", "supported",
    "launch", "launches", "launched", "expand", "expands", "expanded",
    "target", "targets", "targeted", "plan", "plans", "planned",
    "boost", "boosts", "boosted", "cut", "cuts", "add", "adds", "added",
    "double", "doubles", "doubled", "outperform", "outperforms", "outperformed",
    "signal", "signals", "signaled", "highlight", "highlights", "highlighted",
    "reflect", "reflects", "reflected", "suggest", "suggests", "suggested",
    "set", "sets", "hit", "hits", "top", "tops", "topped",
    "surge", "surges", "surged", "jump", "jumps", "jumped",
    "climb", "climbs", "climbed", "decline", "declines", "declined",
    "slow", "slows", "slowed", "accelerate", "accelerates", "accelerated",
    "stabilize", "stabilizes", "stabilized", "become", "becomes", "became",
    "make", "makes", "made", "help", "helps", "helped",
    "cause", "causes", "caused", "contribute", "contributes", "contributed",
    "indicate", "indicates", "indicated", "mean", "means", "meant",
    "provide", "provides", "provided", "give", "gives", "gave",
    "cost", "costs", "save", "saves", "saved", "allow", "allows", "allowed",
    "offer", "offers", "offered", "outpace", "outpaces", "outpaced",
    # ordinary general-English verbs (a takeaway title is a normal sentence,
    # not always a financial one; without these, common constructions like
    # "what worked" read as verb-less even though "worked" is a real verb)
    "work", "works", "worked", "working", "use", "uses", "used",
    "build", "builds", "built", "create", "creates", "created",
    "review", "reviews", "reviewed", "watch", "watches", "watched",
    "see", "sees", "saw", "seen", "know", "knows", "knew", "known",
    "think", "thinks", "thought", "want", "wants", "wanted",
    "try", "tries", "tried", "keep", "keeps", "kept",
    "find", "finds", "found", "tell", "tells", "told",
    "ask", "asks", "asked", "look", "looks", "looked",
    "call", "calls", "called", "feel", "feels", "felt",
    "leave", "leaves", "left", "run", "runs", "ran",
    "meet", "meets", "met", "hold", "holds", "held",
    "bring", "brings", "brought", "happen", "happens", "happened",
    "write", "writes", "wrote", "written", "open", "opens", "opened",
    "close", "closes", "closed", "buy", "buys", "bought",
    "send", "sends", "sent", "move", "moves", "moved",
    "start", "starts", "started", "stop", "stops", "stopped",
    "turn", "turns", "turned", "spend", "spends", "spent",
})

# report.has_exec_summary_first: names that read as "this is the executive
# summary", the one topic label that is *supposed* to open a report (BLUF).
_SUMMARY_HEADING_RE = re.compile(
    r"^(executive\s+summary|exec\.?\s+summary|summary|key\s+takeaways?|"
    r"tl;?dr|highlights?|at\s+a\s+glance|bottom\s+line)$",
    re.IGNORECASE,
)
MIN_HEADINGS_FOR_REPORT = 2     # need >=2 sections before "no exec summary" applies
MAX_SECTION_HEADING_LEVEL = 2   # heading levels treated as "section titles"

# anti-placeholder: filler/unfinished-draft text that should never ship.
_PLACEHOLDER_RE = re.compile(
    r"\blorem\b|\bipsum\b|\btodo\b|\btbd\b|\bxxx\b|\[\s*\]|\binsert\b.*\bhere\b",
    re.IGNORECASE,
)

# deck.block_variety: monotone-deck thresholds.
MIN_SLIDES_FOR_VARIETY_CHECK = 6
MIN_DISTINCT_BLOCK_TYPES = 3
MAX_CONSECUTIVE_BULLET_SLIDES = 2


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


def _banned_label(text: str) -> str | None:
    """A title that is EXACTLY a generic topic label (Overview, Results, ...).
    Used for report section headings, where a topic heading is conventional and
    only the truly generic labels are worth flagging."""
    normalized = text.strip().strip(" :.-").lower()
    if normalized in _BANNED_TITLE_LABELS:
        return f'title "{text.strip()}" is a generic topic label'
    return None


def _is_weak_title(title: str) -> str | None:
    """Return a short reason if a SLIDE `title` reads as a topic label instead
    of a complete takeaway sentence (McKinsey-style action title), else None.
    The verb-less heuristic applies to slide titles only, not report headings
    (which conventionally use topic phrases); see _banned_label for those."""
    text = title.strip()
    if not text:
        return None
    banned = _banned_label(text)
    if banned:
        return banned
    normalized = text.strip(" :.-").lower()
    if _SUMMARY_HEADING_RE.match(normalized):
        return None  # a named executive-summary heading is allowed as-is
    words = _WORD_RE.findall(text)
    # an -ed word is almost always a finite/past verb ("Revenue tripled"), so
    # only flag a short phrase with neither a known verb cue nor an -ed word
    if (words and len(words) <= MAX_VERBLESS_WORDS
            and not any(w.lower() in _VERB_CUES for w in words)
            and not any(w.lower().endswith("ed") for w in words)):
        return (f'title "{text}" has no verb; it reads as a topic label, '
                "not a complete takeaway sentence")
    return None


def _check_placeholder(text: str | None, where: str, out: list[Finding]) -> bool:
    """Flag filler/unfinished-draft text. Returns True on a hit so a caller
    scanning several strings from one block can stop at the first one."""
    if text and _PLACEHOLDER_RE.search(text):
        out.append(Finding(
            rule="content/placeholder", severity="warning", where=where,
            message=f'placeholder or filler text: "{text.strip()[:60]}"; '
                    "replace with real content",
        ))
        return True
    return False


def _block_texts(block: Block) -> list[str]:
    """All literal text strings inside `block`, for placeholder scanning."""
    if isinstance(block, (Heading, Paragraph, Quote, Callout)):
        return [plain(block.text)]
    if isinstance(block, (BulletList, NumberedList)):
        return [plain(it.text) for it in block.items]
    if isinstance(block, Code):
        return []  # code source is not placeholder-scanned: `x = []` and a
        #            `# TODO` comment are legitimate code, not filler
    if isinstance(block, (Image, Artifact)):
        return [block.caption or "", block.alt or ""]
    if isinstance(block, Chart):
        return [block.title or "", block.caption or "", *block.labels]
    if isinstance(block, StatRow):
        texts: list[str] = []
        for s in block.items:
            texts.extend([s.label, s.value, s.delta or ""])
        return texts
    if isinstance(block, Table):
        texts = [plain(c) for c in block.header]
        texts.extend(plain(c) for row in block.rows for c in row)
        if block.caption:
            texts.append(block.caption)
        return texts
    return []


def _is_bullet_only(slide: Slide) -> bool:
    blocks = slide.blocks + slide.right
    return bool(blocks) and all(isinstance(b, (BulletList, NumberedList)) for b in blocks)


def _lint_slide(slide: Slide, where: str, out: list[Finding]) -> None:
    all_blocks = slide.blocks + slide.right

    if slide.title and len(slide.title) > MAX_TITLE_CHARS:
        out.append(Finding(
            rule="deck/title-too-long", severity="warning", where=where,
            message=f"slide title is {len(slide.title)} chars "
                    f"(max {MAX_TITLE_CHARS}); it will wrap or shrink",
        ))

    # title.is_takeaway: cover/divider slides (title, section) legitimately
    # carry a short topic label (the deck name, "Part 2: Financials"), so
    # only content-bearing layouts are held to the action-title standard.
    if slide.title and slide.layout not in ("title", "section"):
        reason = _is_weak_title(slide.title)
        if reason:
            out.append(Finding(
                rule="deck/weak-title", severity="warning", where=where,
                message=reason,
            ))

    for field in ("title", "subtitle", "notes"):
        _check_placeholder(getattr(slide, field), f"{where}.{field}", out)

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
    # anti-placeholder: scan every literal string this block carries; one
    # finding per block is plenty, so stop at the first hit.
    for text in _block_texts(b):
        if _check_placeholder(text, where, out):
            break

    if isinstance(b, Table):
        widths = {len(b.header), *(len(r) for r in b.rows)}
        if len(widths) > 1:
            out.append(Finding(
                rule="table/ragged", severity="warning", where=where,
                message=f"header and rows have differing widths {sorted(widths)}; "
                        "short rows are padded with blank cells",
            ))
        if not (b.caption and b.caption.strip()):
            out.append(Finding(
                rule="visual/unlabeled", severity="warning", where=where,
                message="table has no caption; add a one-line takeaway caption",
            ))
    elif isinstance(b, Image):
        if not (b.caption and b.caption.strip()):
            out.append(Finding(
                rule="visual/unlabeled", severity="warning", where=where,
                message="image has no caption; add a one-line takeaway caption",
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
        missing = [name for name, val in (("title", b.title), ("caption", b.caption))
                   if not (val and val.strip())]
        if missing:
            out.append(Finding(
                rule="chart/unlabeled", severity="warning", where=where,
                message=f"chart is missing a {' and '.join(missing)}; give "
                        "every chart a title and a one-line takeaway caption",
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
        if not (b.caption and b.caption.strip()):
            out.append(Finding(
                rule="visual/unlabeled", severity="warning", where=where,
                message=f"{b.kind} has no caption; add a one-line takeaway caption",
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

    _check_placeholder(doc.title, "title", out)
    _check_placeholder(doc.subtitle, "subtitle", out)

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

    # deck.block_variety: a big deck that is nothing but bullets has no
    # visual thinking; flag both a monotone block palette and long runs of
    # bullet-only slides in a row.
    if len(doc.slides) >= MIN_SLIDES_FOR_VARIETY_CHECK:
        block_types = {blk.type for s in doc.slides for blk in (s.blocks + s.right)}
        # a filled image slot (hero/image_left/image_right) is genuine visual
        # variety even though it is not a body "block"; an image-driven deck is
        # not the wall-of-bullets failure this rule targets, so count image
        # slots and skip the finding when several slides carry one
        image_slides = sum(1 for s in doc.slides if s.image is not None)
        effective_types = len(block_types) + (1 if image_slides else 0)
        if effective_types < MIN_DISTINCT_BLOCK_TYPES and image_slides < 2:
            out.append(Finding(
                rule="deck/monotone", severity="warning", where="slides",
                message=f"deck uses only {len(block_types)} distinct block "
                        f"type(s) across {len(doc.slides)} slides (want at "
                        f"least {MIN_DISTINCT_BLOCK_TYPES}); vary charts, "
                        "tables, stats, and images instead of only bullets",
            ))

    run_start, run_len = 0, 0
    for i, s in enumerate(doc.slides):
        if _is_bullet_only(s):
            if run_len == 0:
                run_start = i
            run_len += 1
            if run_len == MAX_CONSECUTIVE_BULLET_SLIDES + 1:
                out.append(Finding(
                    rule="deck/monotone", severity="warning",
                    where=f"slides[{run_start}:{i + 1}]",
                    message=f"{run_len} consecutive bullet-only slides (max "
                            f"{MAX_CONSECUTIVE_BULLET_SLIDES} in a row); vary "
                            "the block type",
                ))
        else:
            run_len = 0

    # heading structure in report blocks, plus title.is_takeaway for section
    # headings and report.has_exec_summary_first for the document as a whole.
    prev_level = 0
    section_headings: list[tuple[int, Heading]] = []
    for i, b in enumerate(doc.blocks):
        if isinstance(b, Heading):
            if prev_level and b.level > prev_level + 1:
                out.append(Finding(
                    rule="doc/heading-skip", severity="info", where=f"blocks[{i}]",
                    message=f"heading level jumps from {prev_level} to {b.level}",
                ))
            prev_level = b.level
            section_headings.append((i, b))
            if b.level <= MAX_SECTION_HEADING_LEVEL:
                htext = plain(b.text)
                # report headings only flag EXACTLY-generic labels; a topic
                # heading ("Risks", "Methodology") is conventional in a report,
                # unlike a slide title which is held to the action-title standard
                if not _SUMMARY_HEADING_RE.match(htext.strip().strip(" :.-").lower()) \
                        and _banned_label(htext):
                    out.append(Finding(
                        rule="doc/weak-heading", severity="warning",
                        where=f"blocks[{i}]",
                        message=f'heading "{htext.strip()}" is a generic label; '
                                "use a specific, descriptive heading",
                    ))

    if len(section_headings) >= MIN_HEADINGS_FOR_REPORT:
        first_i, first_h = section_headings[0]
        first_text = plain(first_h.text).strip()
        if not _SUMMARY_HEADING_RE.match(first_text.strip(" :.-")):
            out.append(Finding(
                rule="doc/no-summary", severity="warning", where=f"blocks[{first_i}]",
                message=f'report opens with "{first_text}", not an executive '
                        "summary; lead with the thesis and its supporting "
                        "points first (BLUF), then detail",
            ))

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

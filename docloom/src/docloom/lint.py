"""Deterministic document linter.

Catches the failures LLM-generated documents actually ship with (overflowing
slides, walls of text, dangling citations, unreadable theme contrast) and
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
    Artifact, Block, BulletList, Callout, Chart, Code, Diagram, Document,
    Formula, Heading, Image, NumberedList, Paragraph, Quote, Slide, StatRow,
    Table, cited_ids, plain, spans,
)
from .theme import Theme, contrast_ratio

# render/diagram_svg.py (the painter) owns estimate_depth; rank_nodes there
# computes the identical layering internally for layout, so this is the one
# real implementation, not a lint-local copy. Contract this lint call site
# relies on: estimate_depth(node_ids: list[str],
# edges: list[tuple[str, str]]) -> int -- longest path on the acyclic
# projection (back edges from a DFS cycle-break dropped), returned as a
# layer COUNT (1 for a single node).
#
# A lint-local reimplementation of this algorithm used to live here (finding
# 16, docs/diagram-status.md): it was dead weight once diagram_svg.py landed
# for real, and a second copy of a layering algorithm is exactly the kind of
# thing that silently diverges from the original over time. The try/except
# stays only as an import guard -- diagram_svg.py is pure stdlib with no
# optional dependency that could plausibly fail to import, so hitting this
# branch means something is actually broken and should fail loud, not fall
# back to a shadow copy of the algorithm that could quietly disagree with it.
try:
    from .render.diagram_svg import estimate_depth  # type: ignore[import-not-found]
except ImportError as _diagram_svg_import_error:  # pragma: no cover - should not happen
    raise ImportError(
        "docloom.lint requires docloom.render.diagram_svg.estimate_depth "
        "(the diagram painter's layering algorithm) to import; a lint-local "
        "duplicate used to mask this, but it silently diverged from the "
        "real algorithm and was removed (docs/diagram-status.md finding 16)"
    ) from _diagram_svg_import_error

Severity = Literal["error", "warning", "info"]


class Finding(BaseModel):
    rule: str
    severity: Severity
    where: str  # human/machine-readable location, e.g. "slides[3]"
    message: str


# Heuristic budgets for a 16:9 slide. Deliberately simple character-count
# estimates: they catch the 95% case (walls of text) without a layout engine.
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
#
# These are plain duplicated literals, not an import, because render/pptx.py
# is the actual layout engine (fonts, python-pptx, LibreOffice quirks) and
# lint.py must stay import-light and layout-agnostic (deck.block_variety,
# citations, etc. run with no renderer available at all). That duplication is
# exactly how finding 13 (docs/diagram-status.md) happened: CHART_H_IN and
# the unresolved-Artifact height silently drifted from pptx.py's real values.
# Rather than trust a comment to catch the next drift, test_reaudit_lint.py
# imports render/pptx.py's real constants and asserts equality with the
# mirrors below, so a future edit to one side without the other fails CI
# instead of silently drifting again.
SLIDE_BODY_H_IN = 5.48   # slide height 7.5 - margin 0.6 - title band 1.42
FULL_BODY_W_IN = 12.13   # slide width 13.333 - 2 x margin 0.6
NARROW_BODY_W_IN = FULL_BODY_W_IN / 2  # two_column column / image-slot pane
CHART_H_IN = 4.8         # render/pptx.py LAYOUT["chart_max_h_in"]
IMAGE_H_IN = 4.6         # render/pptx.py _natural_h's resolved image/artifact estimate
DIAGRAM_H_IN = 4.6       # render/pptx.py DIAGRAM_H_IN / _natural_h's diagram estimate
# render/pptx.py _natural_h returns this for an UNRESOLVED Artifact (no
# path/artifact_id yet): it now draws a real placeholder box (P5 audit
# defect 1), not nothing, so it reserves real layout room and this rule must
# score it accordingly -- see _block_height below. An unresolved Image slot
# is unaffected (stays the deliberate 0.0 no-op): only Artifact renders a
# placeholder.
ARTIFACT_PLACEHOLDER_H_IN = 1.6  # render/pptx.py _natural_h's unresolved-Artifact estimate
# The compact-card floor; a stats row that is its slide's dominant block
# (PINNED CONTRACT item 4: 1 stat -> a big number, 2-4 -> an upgraded card
# row) renders TALLER than this when it has the room. That is a deliberate
# one-directional gap in this estimate, not a drift to fix: it only makes
# deck/overflow UNDER-estimate a dominant stats row's real footprint, never
# over-estimate it, so it can never cause this lint to wrongly reject a
# deck using the new treatment (item 9) -- the opposite direction would.
STATS_H_IN = 1.4         # render/pptx.py LAYOUT["stat_card_h_in"]
STATS_MAX_CARDS = 5      # render/pptx.py LAYOUT["stat_max_cards"]
TABLE_ROW_H_IN = 0.36    # render/pptx.py _table_block's row height cap
BLOCK_GAP_IN = 0.14      # render/pptx.py LAYOUT["gap_in"], summed between blocks
LINE_H_IN = 0.26         # ~ render/pptx.py _line_h(14pt), body text line height
# Caption/attribution/subtitle reserves: mirror render/pptx.py's own named
# constants of the same name (CAPTION_H_IN, IMAGE_CAPTION_H_IN,
# QUOTE_ATTR_H_IN, SUBTITLE_PAD_IN), pinned by test_reaudit_lint.py. Before
# these existed, a subtitle-bearing slide's real available body height and a
# captioned chart's real footprint were both invisible to this rule -- the
# exact repro that let a trailing block get silently dropped by the renderer
# while lint scored the slide as safe (docs/... silent-content-loss class).
CAPTION_H_IN = 0.26       # table/chart/diagram/placeholder caption strip
IMAGE_CAPTION_H_IN = 0.3  # image caption strip (slightly taller)
QUOTE_ATTR_H_IN = 0.28    # quote attribution line
SUBTITLE_PAD_IN = 0.12    # fixed pad below a subtitle's estimated lines

# hero/section/quote/title draw their body blocks into a much smaller
# SECONDARY zone than SLIDE_BODY_H_IN -- a bottom caption band, a divider
# band below the section title, the leftover below a display pull-quote, or
# the cover leftover below title/subtitle/byline -- not the full content-slide
# body. SLIDE_BODY_H_IN alone used to stand in for all of them, so a slide
# whose renderer squeezed an authored visual block into that small secondary
# zone (and dropped it below MIN_VISUAL_BLOCK_H_IN) emitted zero deck/overflow
# findings. These mirror render/pptx.py's own per-layout geometry
# (_hero_slide, _section_slide, _quote_slide, _title_slide), same
# import-light reasoning as the block above.
SLIDE_H_IN = 7.5     # render/pptx.py SLIDE_H (LAYOUT["slide_h_in"])
SLIDE_W_IN = 13.333  # render/pptx.py SLIDE_W (LAYOUT["slide_w_in"])
MARGIN_IN = 0.6      # render/pptx.py MARGIN (LAYOUT["margin_in"])
# _section_slide now vertically centers its title group instead of pinning
# it at a fixed y (item 3); SECTION_GROUP_PAD_IN mirrors the rule-gap +
# title/subtitle geometry used to compute that group's height, and
# SECTION_BODY_GAP_IN the fixed gap _section_slide adds before its body.
SECTION_TITLE_PT = 36           # render/pptx.py _section_slide's title font size
SECTION_SUB_PT = 18             # render/pptx.py _section_slide's subtitle font size
SECTION_RULE_GAP_IN = 0.05 + 0.28  # _section_slide's accent-rule-to-title gap
SECTION_BODY_GAP_IN = 0.3       # _section_slide's fixed gap before its body
HERO_PAD_IN = 0.3      # _hero_slide's pad inside the bottom band
HERO_TITLE_PT = 36     # render/pptx.py LAYOUT["hero_title_pt"]
HERO_SUB_PT = 20       # _hero_slide's subtitle font size
HERO_CAP_PT = 11       # _hero_slide's image-caption font size
HERO_BLOCK_BAND_IN = 1.7      # _hero_slide's flat guess for a photo-backed band's blocks
HERO_BAND_MAX_H_IN = SLIDE_H_IN * 0.6  # _hero_slide's band_h cap
# _title_slide now renders title/subtitle/byline as ONE vertically-centered
# group at a display-scale 54-64pt title (item 3), not a fixed y=2.5/40pt
# title with the byline floor-pinned to y>=6.3; these mirror that group's
# own geometry instead.
TITLE_TEXT_W_IN = SLIDE_W_IN - 1.1 - MARGIN_IN  # _title_slide's tx=1.1
TITLE_TITLE_MIN_H_IN = 1.4  # _title_slide's title_h floor
TITLE_TITLE_MAX_PT = 64     # _title_slide's title_pt ceiling
TITLE_TITLE_MIN_PT = 54     # _title_slide's title_pt floor
TITLE_SUB_PT = 20           # _title_slide's subtitle font size
TITLE_BYLINE_H_IN = 0.85    # _title_slide's byline block (0.5in gap + 0.35in line)
QUOTE_COL_W_IN = SLIDE_W_IN - 4.6      # _quote_slide's qx/qw
QUOTE_TOP_RESERVE_IN = 1.4             # _quote_slide's avail/y floor
QUOTE_ATTR_RESERVE_IN = 0.6            # _quote_slide's attr_h


def _est_lines(text: str, size: float, width_in: float) -> int:
    """Mirrors render/pptx.py's _est_lines at an arbitrary font size: the
    display-scale hero/section/quote/title bands size text far from body's
    fixed 14pt, which _block_height's own hardcoded-14pt estimate cannot
    model."""
    per = max(8, int(width_in * 144 / size))
    return sum(max(1, (len(ln) + per - 1) // per) for ln in text.split("\n"))


def _line_h(size: float) -> float:
    """Mirrors render/pptx.py's _line_h."""
    return size * 1.3 / 72


def _usable_image(img: Image | None) -> bool:
    """Mirrors render/pptx.py's _usable_image: whether an image slot resolves
    to a real file the renderer will actually draw (a bare query/asset_id,
    not yet resolved to a path, is not)."""
    return img is not None and bool(img.path) and Path(img.path).is_file()


def _hero_body_budget(slide: Slide) -> float:
    """Mirrors render/pptx.py's _hero_slide: the body blocks draw into a
    bottom band whose height depends on whether the hero has a usable
    background photo."""
    tw = FULL_BODY_W_IN
    title_h = (
        _est_lines(slide.title, HERO_TITLE_PT, tw) * _line_h(HERO_TITLE_PT)
        if slide.title else 0.0
    )
    sub_h = (
        _est_lines(slide.subtitle, HERO_SUB_PT, tw) * _line_h(HERO_SUB_PT) + 0.08
        if slide.subtitle else 0.0
    )
    if _usable_image(slide.image):
        cap_h = (
            _est_lines(slide.image.caption, HERO_CAP_PT, tw) * _line_h(HERO_CAP_PT) + 0.08
            if slide.image.caption else 0.0
        )
        band_h = min(
            HERO_BAND_MAX_H_IN,
            2 * HERO_PAD_IN + title_h + sub_h + cap_h + HERO_BLOCK_BAND_IN,
        )
        band_y = SLIDE_H_IN - band_h
    else:
        # An imageless hero grows its band to fit its blocks' real height
        # instead (never dropping them), so an exact mirror of that growth
        # would never fire here either. Model the band at its floor
        # (MARGIN_IN, i.e. fully grown) instead: this only trips when the
        # grown band genuinely cannot fit the content -- real DROP territory
        # -- not the ordinary shrink-not-drop case.
        cap_h = 0.0
        band_y = MARGIN_IN
    y_start = band_y + HERO_PAD_IN + (title_h + 0.08 if slide.title else 0.0) + sub_h + cap_h
    return (SLIDE_H_IN - MARGIN_IN) - y_start


def _section_body_budget(slide: Slide) -> float:
    """Mirrors render/pptx.py's _section_slide: the title group is now
    vertically centered (item 3) instead of pinned at a fixed y, so the
    body's start depends on the group's own height (rule gap + title +
    optional subtitle)."""
    tw = FULL_BODY_W_IN
    title_h = max(
        0.9, _est_lines(slide.title or "", SECTION_TITLE_PT, tw) * _line_h(SECTION_TITLE_PT)
    )
    sub_h = (
        _est_lines(slide.subtitle, SECTION_SUB_PT, tw) * _line_h(SECTION_SUB_PT) + 0.15
        if slide.subtitle else 0.0
    )
    group_h = SECTION_RULE_GAP_IN + title_h + sub_h
    y = max(0.6, (SLIDE_H_IN - group_h) / 2) + group_h + SECTION_BODY_GAP_IN
    return (SLIDE_H_IN - MARGIN_IN) - y


def _title_body_budget(slide: Slide, doc: Document) -> float:
    """Mirrors render/pptx.py's _title_slide: title/subtitle/byline now
    render as ONE vertically-centered group at a 54-64pt display-scale
    title (item 3), not a fixed y=2.5/40pt title with the byline
    floor-pinned to y>=6.3."""
    title = slide.title or doc.title or ""
    tw = TITLE_TEXT_W_IN
    title_pt = TITLE_TITLE_MAX_PT
    while title_pt > TITLE_TITLE_MIN_PT and _est_lines(title, title_pt, tw) > 2:
        title_pt -= 2
    title_h = max(TITLE_TITLE_MIN_H_IN, _est_lines(title, title_pt, tw) * _line_h(title_pt))
    subtitle = slide.subtitle or doc.subtitle
    sub_h = (
        _est_lines(subtitle, TITLE_SUB_PT, tw) * _line_h(TITLE_SUB_PT) + 0.18
        if subtitle else 0.0
    )
    by_h = TITLE_BYLINE_H_IN if (doc.authors or doc.date) else 0.0
    group_h = title_h + sub_h + by_h
    ty = max(0.7, (SLIDE_H_IN - group_h) / 2 - 0.3)
    body_y = ty + group_h + 0.25
    return max(0.0, (SLIDE_H_IN - MARGIN_IN) - body_y)


def _quote_rest_budget(quote_text: str, attribution: str | None) -> float:
    """Mirrors render/pptx.py's _quote_slide: the pull-quote self-sizes at
    display scale and never overflows (it is excluded from the summed height
    entirely -- see _lint_slide), so this is the leftover height available to
    any OTHER blocks sharing the slide."""
    if not quote_text:
        return SLIDE_BODY_H_IN
    qw = QUOTE_COL_W_IN
    attr_h = QUOTE_ATTR_RESERVE_IN if attribution else 0.0
    avail_q = SLIDE_H_IN - MARGIN_IN - QUOTE_TOP_RESERVE_IN - attr_h
    size = next(
        (pt for pt in (30, 24, 20, 16)
         if _est_lines(quote_text, pt, qw) * _line_h(pt) <= avail_q),
        14,
    )
    qh = min(avail_q, _est_lines(quote_text, size, qw) * _line_h(size))
    y = max(QUOTE_TOP_RESERVE_IN, (SLIDE_H_IN - qh - 0.3 - attr_h) / 2)
    y += qh + 0.25 + (0.55 if attribution else 0.0)
    return max(0.0, (SLIDE_H_IN - MARGIN_IN) - y)


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

# diagram/* rules (docs/diagram-plan.md section 6). llm_schema() strips
# minLength/maxLength/pattern (see llm.py), so every one of these length
# limits is enforceable only here, never as a Pydantic field constraint.
DIAGRAM_NODE_LABEL_MAX = 40
DIAGRAM_SUBLABEL_MAX = 40
DIAGRAM_TAG_MAX = 12
DIAGRAM_EDGE_LABEL_MAX = 30
DIAGRAM_GROUP_LABEL_MAX = 40
# density budget: the rule that actually fixes "will not be legible on a
# 16:9 slide" for diagrams. depth is the longest path on the acyclic
# projection (estimate_depth).
#
# Both tiers below are severity="warning", never "error" (finding 6,
# docs/diagram-status.md). A dense diagram is an aesthetic/legibility
# problem, not a correctness defect: every render path (native PPTX shapes,
# the raster fallback, SVG/DOCX/HTML/typst/markdown) still produces a valid
# file past every threshold here, it just gets visually tight -- that is a
# genuinely different failure class from diagram/dangling-edge or
# diagram/duplicate-id, which really would leave broken output, and only
# those keep severity="error". A crowded-but-otherwise-fine diagram must
# never carry error severity, because has_errors() drives `docloom render`
# refusing the ENTIRE deck (exit 2, no output, not even --diagram-sources
# sidecars) over one diagram, which is not a proportionate response to
# "this one block looks tight." The DIAGRAM_MAX_*_DENSE tier exists only to
# escalate the message's wording for genuinely extreme cases, not to escalate
# severity -- there is no line at which lint should be allowed to block a
# whole deck's render for this rule, because any such line eventually sits
# inside the range real diagrams occupy: that is exactly what happened to
# the old depth>7 error threshold below.
#
# Evidence for the DEPTH numbers: 5 independent, ordinary 13-14 node
# reference-architecture bake-off specs (scratchpad/bakeoff/specs/*.json,
# each a plausible AWS-style system diagram, not an adversarial stress case)
# measured through this exact estimate_depth() give depths 5, 6, 8, 8, 10.
# The old error threshold of >7 already sat inside that range (3 of 5 specs
# exceeded it) and would have hard-blocked those decks; DIAGRAM_MAX_DEPTH_DENSE
# below is set with headroom above the deepest of those five (10) so no
# ordinary reference architecture reaches even the stronger-worded tier.
DIAGRAM_MAX_NODES_WARN = 8
DIAGRAM_MAX_DEPTH_WARN = 5
DIAGRAM_MAX_NODES_DENSE = 14  # unaffected by finding 6: node count was never
                              # the reported trigger, only depth was
DIAGRAM_MAX_DEPTH_DENSE = 12  # raised from the old error threshold of 7
# crowded-slide: a diagram sharing a slide with more than this many other
# non-Heading blocks gets squeezed regardless of its own node count.
DIAGRAM_MAX_OTHER_BLOCKS = 1


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
    if isinstance(block, Diagram):
        # title + caption only, mirroring Image/Artifact's caption-only
        # accounting: node/edge content is governed by diagram/too-dense
        # instead, so counting every label here would double-regulate it
        # against the same MAX_SLIDE_CHARS budget.
        return len(block.title or "") + len(block.caption or "")
    return 0


def _block_height(block: Block, width_in: float) -> float:
    """Rough physical height `block` occupies on a rendered slide (inches),
    mirroring render/pptx.py's fixed-size blocks: a chart, resolved image,
    stats row, or table takes a near-constant amount of vertical space no
    matter how few characters it carries, which _block_chars cannot see."""
    if isinstance(block, Chart):
        return CHART_H_IN + (CAPTION_H_IN if block.caption else 0.0)
    if isinstance(block, (Image, Artifact)):
        cap = IMAGE_CAPTION_H_IN if block.caption else 0.0
        if block.path and Path(block.path).is_file():
            return IMAGE_H_IN + cap
        # An unresolved Artifact still draws a real placeholder box in PPTX
        # (P5 audit defect 1), so it reserves real layout room; an
        # unresolved Image slot stays the deliberate 0.0 no-op (finding 13,
        # docs/diagram-status.md -- this used to score every unresolved
        # Artifact at 0.0, letting deck/overflow pass slides that now
        # overflow once pptx.py started drawing the placeholder).
        return (ARTIFACT_PLACEHOLDER_H_IN + cap) if isinstance(block, Artifact) else 0.0
    if isinstance(block, Diagram):
        return (DIAGRAM_H_IN + (CAPTION_H_IN if block.caption else 0.0)) if block.nodes else 0.0
    if isinstance(block, StatRow):
        return STATS_H_IN if block.items else 0.0
    if isinstance(block, Table):
        return (len(block.rows) + 1) * TABLE_ROW_H_IN + (CAPTION_H_IN if block.caption else 0.0)
    per_line = max(8, int(width_in * 144 / 14))

    def lines(text: str) -> int:
        if not text:
            return 0
        return sum(max(1, -(-len(ln) // per_line)) for ln in text.split("\n"))

    if isinstance(block, (BulletList, NumberedList)):
        return sum(max(1, lines(plain(it.text))) * LINE_H_IN for it in block.items)
    if isinstance(block, Quote):
        return lines(plain(block.text)) * LINE_H_IN + (
            QUOTE_ATTR_H_IN if block.attribution else 0.0
        )
    if isinstance(block, (Heading, Paragraph, Callout)):
        return lines(plain(block.text)) * LINE_H_IN
    if isinstance(block, Code):
        return lines(block.code) * LINE_H_IN
    return 0.0  # Divider and anything else: negligible


def _subtitle_reserve_h(subtitle: str | None, width_in: float) -> float:
    """Height render/pptx.py's _subtitle_line reserves before the body
    starts, mirroring its own formula (estimated lines at 15pt, plus its
    fixed SUBTITLE_PAD_IN pad) so a subtitle-bearing slide's real available
    body height is modeled instead of silently assumed away. Only the
    layouts that actually call _subtitle_line before their body (content,
    image_left/right, two_column -- see the call site below) are affected;
    hero/quote/title/section handle a subtitle differently."""
    if not subtitle:
        return 0.0
    per_line = max(8, int(width_in * 144 / 15))
    n_lines = sum(max(1, -(-len(ln) // per_line)) for ln in subtitle.split("\n"))
    return n_lines * LINE_H_IN + SUBTITLE_PAD_IN


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
    if isinstance(block, Diagram):
        texts = [block.title or "", block.caption or "", block.alt or ""]
        for n in block.nodes:
            texts.extend([n.label, n.sublabel or "", n.tag or ""])
        for e in block.edges:
            texts.append(e.label or "")
        for g in block.groups:
            texts.append(g.label)
        return texts
    return []


def _is_bullet_only(slide: Slide) -> bool:
    blocks = slide.blocks + slide.right
    return bool(blocks) and all(isinstance(b, (BulletList, NumberedList)) for b in blocks)


def _lint_slide(slide: Slide, where: str, out: list[Finding], doc: Document) -> None:
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

    # title/section slides deliberately NOT flagged: render/pptx.py's
    # _title_slide/_section_slide draw their blocks (P5 audit), so warning that
    # "these blocks will not appear" is false and pushes the LLM to delete
    # content that renders fine. Overflow on those layouts is caught generically.

    # hero deliberately excluded: an imageless hero is a first-class,
    # supported configuration (render/pptx.py's _hero_slide falls back to a
    # solid theme.primary full-bleed fill, not a degraded layout), so
    # flagging it here contradicted the product -- only image_left/
    # image_right genuinely degrade (falling through to a plain content
    # layout) when their image slot is empty.
    if slide.layout in ("image_left", "image_right") and slide.image is None:
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

    # diagram/crowded-slide: a diagram squeezed beside several other blocks
    # loses the room it needs to stay legible, independent of its own node
    # count (which diagram/too-dense already governs).
    for bi, b in enumerate(all_blocks):
        if isinstance(b, Diagram):
            others = sum(
                1 for oi, ob in enumerate(all_blocks)
                if oi != bi and not isinstance(ob, Heading)
            )
            if others > DIAGRAM_MAX_OTHER_BLOCKS:
                out.append(Finding(
                    rule="diagram/crowded-slide", severity="warning", where=where,
                    message=f"diagram shares this slide with {others} other "
                            "block(s); it will be squeezed. Give it its own "
                            "slide or drop a neighboring block",
                ))

    # two_column slides get half the width per column, so each column gets
    # half the character budget; other layouts get the full-width budget
    if slide.layout in ("image_left", "image_right"):
        total = sum(_block_chars(b) for b in all_blocks)
        if total > MAX_SLIDE_CHARS // 2:
            out.append(Finding(
                rule="deck/overflow", severity="warning", where=where,
                message=f"~{total} chars beside the image (soft budget "
                        f"{MAX_SLIDE_CHARS // 2} for a half-width column); "
                        "dense -- tighten it or move detail to speaker notes",
            ))
    elif slide.layout == "two_column":
        for name, blocks in (("blocks", slide.blocks), ("right", slide.right)):
            total = sum(_block_chars(b) for b in blocks)
            if total > MAX_SLIDE_CHARS // 2:
                out.append(Finding(
                    rule="deck/overflow", severity="warning", where=f"{where}.{name}",
                    message=f"~{total} chars in one column (soft budget "
                            f"{MAX_SLIDE_CHARS // 2} at half width); dense -- "
                            "tighten it or move detail to speaker notes",
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
                        "this will overflow the slide; split it",
            ))

    # height budget: fixed-size blocks (chart/image/table/stats) are blind to
    # the char budget above but not to physical space. Estimate inches and
    # compare to the slide body's usable height. two_column/image-slot
    # layouts get a narrower text column (the same "half" approximation the
    # char budget above makes), but the *vertical* budget does not shrink for
    # them: only the image or the other column takes width, not height.
    narrow = slide.layout in ("image_left", "image_right", "two_column")
    w_in = NARROW_BODY_W_IN if narrow else FULL_BODY_W_IN
    # content/image_left/image_right/two_column all draw a subtitle band
    # (render/pptx.py's _subtitle_line) right after the title band, before
    # the body starts -- eating into the same fixed SLIDE_BODY_H_IN budget
    # that otherwise assumes just the title band alone. This is the exact
    # gap that let a subtitle-bearing chart-plus-trailing-block slide pass
    # deck/overflow silently while the renderer dropped the trailing block.
    # two_column's subtitle spans the FULL width (drawn before the two
    # columns split), unlike its body blocks, which each get the narrow
    # per-column width. hero/quote/title/section handle a subtitle as part
    # of their own layout-specific budget below instead.
    if slide.layout == "section":
        body_budget = _section_body_budget(slide)
        height_groups = [(where, all_blocks)]
    elif slide.layout == "hero":
        body_budget = _hero_body_budget(slide)
        height_groups = [(where, all_blocks)]
    elif slide.layout == "title":
        body_budget = _title_body_budget(slide, doc)
        height_groups = [(where, all_blocks)]
    elif slide.layout == "quote":
        # the pull-quote itself self-sizes at display scale and never
        # overflows (see _quote_rest_budget), so it is excluded from the
        # summed height below; only the OTHER blocks sharing the slide are
        # checked against its leftover room. Identify it exactly as
        # render/pptx.py's _quote_slide does.
        q = next((b for b in all_blocks if isinstance(b, Quote)), None)
        if q is not None:
            quote_text = plain(q.text)
            attribution = q.attribution or slide.subtitle
            rest = [b for b in all_blocks if b is not q]
        else:
            quote_text = slide.title or ""
            attribution = slide.subtitle
            rest = all_blocks
        body_budget = _quote_rest_budget(quote_text, attribution)
        height_groups = [(where, rest)]
    elif slide.layout == "two_column":
        body_budget = SLIDE_BODY_H_IN - _subtitle_reserve_h(slide.subtitle, FULL_BODY_W_IN)
        height_groups = [
            (f"{where}.blocks", slide.blocks), (f"{where}.right", slide.right),
        ]
    else:
        sub_w = NARROW_BODY_W_IN if slide.layout in ("image_left", "image_right") else FULL_BODY_W_IN
        body_budget = SLIDE_BODY_H_IN - _subtitle_reserve_h(slide.subtitle, sub_w)
        height_groups = [(where, all_blocks)]
    for group_where, blocks in height_groups:
        total_h = sum(_block_height(b, w_in) for b in blocks)
        if len(blocks) > 1:
            total_h += BLOCK_GAP_IN * (len(blocks) - 1)
        if total_h > body_budget:
            # advisory, not blocking: the PPTX renderer shrinks/reserves
            # room for fixed-size blocks (charts especially) and their
            # captions instead of dropping them, so an over-budget estimate
            # usually still renders, just tightly. A warning surfaces the
            # crowding without hard-failing export on a chart + a few
            # bullets (the char-budget rule above stays an error: text
            # cannot shrink indefinitely).
            out.append(Finding(
                rule="deck/overflow", severity="warning", where=group_where,
                message=f"~{total_h:.1f}in of estimated content height "
                        f"(budget {body_budget:.2f}in); a chart, image, "
                        "table, diagram, or stats row takes a near-fixed "
                        "amount of space, and captions/attributions/a "
                        "subtitle add to it. Consider splitting the slide "
                        "or dropping a block",
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


def _lint_diagram(d: Diagram, where: str, out: list[Finding]) -> None:
    """diagram/* rules (docs/diagram-plan.md section 6). Geometric checks
    (overlap, routing, label placement) are NOT here: lint runs on the
    coordinate-free IR before any layout exists; those live in the painter's
    check()/layout_report() (P0, exercised in its own tests)."""
    if not d.nodes:
        out.append(Finding(
            rule="diagram/empty", severity="error", where=where,
            message="diagram has no nodes",
        ))

    node_ids: set[str] = set()
    for i, n in enumerate(d.nodes):
        if n.id in node_ids:
            out.append(Finding(
                rule="diagram/duplicate-id", severity="error",
                where=f"{where}.nodes[{i}]",
                message=f'duplicate node id "{n.id}"; node ids must be unique',
            ))
        node_ids.add(n.id)
        if len(n.label) > DIAGRAM_NODE_LABEL_MAX:
            out.append(Finding(
                rule="diagram/label-too-long", severity="warning",
                where=f"{where}.nodes[{i}]",
                message=f'node label "{n.label}" is {len(n.label)} chars '
                        f"(max {DIAGRAM_NODE_LABEL_MAX})",
            ))
        if n.sublabel and len(n.sublabel) > DIAGRAM_SUBLABEL_MAX:
            out.append(Finding(
                rule="diagram/label-too-long", severity="warning",
                where=f"{where}.nodes[{i}]",
                message=f'node sublabel "{n.sublabel}" is {len(n.sublabel)} '
                        f"chars (max {DIAGRAM_SUBLABEL_MAX})",
            ))
        if n.tag and len(n.tag) > DIAGRAM_TAG_MAX:
            out.append(Finding(
                rule="diagram/label-too-long", severity="warning",
                where=f"{where}.nodes[{i}]",
                message=f'node tag "{n.tag}" is {len(n.tag)} chars '
                        f"(max {DIAGRAM_TAG_MAX})",
            ))

    group_ids: set[str] = set()
    for i, g in enumerate(d.groups):
        if g.id in group_ids:
            out.append(Finding(
                rule="diagram/duplicate-id", severity="error",
                where=f"{where}.groups[{i}]",
                message=f'duplicate group id "{g.id}"; group ids must be unique',
            ))
        group_ids.add(g.id)
        if len(g.label) > DIAGRAM_GROUP_LABEL_MAX:
            out.append(Finding(
                rule="diagram/label-too-long", severity="warning",
                where=f"{where}.groups[{i}]",
                message=f'group label "{g.label}" is {len(g.label)} chars '
                        f"(max {DIAGRAM_GROUP_LABEL_MAX})",
            ))

    for i, n in enumerate(d.nodes):
        if n.group is not None and n.group not in group_ids:
            out.append(Finding(
                rule="diagram/unknown-group", severity="error",
                where=f"{where}.nodes[{i}]",
                message=f'node "{n.id}" references group "{n.group}", which '
                        "does not exist",
            ))

    members = {n.group for n in d.nodes if n.group is not None}
    for i, g in enumerate(d.groups):
        if g.id not in members:
            out.append(Finding(
                rule="diagram/empty-group", severity="warning",
                where=f"{where}.groups[{i}]",
                message=f'group "{g.id}" has no member nodes',
            ))

    connected: set[str] = set()
    for i, e in enumerate(d.edges):
        if e.source not in node_ids:
            out.append(Finding(
                rule="diagram/dangling-edge", severity="error",
                where=f"{where}.edges[{i}]",
                message=f'edge source "{e.source}" is not a node id',
            ))
        if e.target not in node_ids:
            out.append(Finding(
                rule="diagram/dangling-edge", severity="error",
                where=f"{where}.edges[{i}]",
                message=f'edge target "{e.target}" is not a node id',
            ))
        if e.source == e.target:
            out.append(Finding(
                rule="diagram/self-loop", severity="warning",
                where=f"{where}.edges[{i}]",
                message=f'edge from "{e.source}" to itself; the painter does '
                        "not draw self-loops well",
            ))
        if e.label and len(e.label) > DIAGRAM_EDGE_LABEL_MAX:
            out.append(Finding(
                rule="diagram/label-too-long", severity="warning",
                where=f"{where}.edges[{i}]",
                message=f'edge label "{e.label}" is {len(e.label)} chars '
                        f"(max {DIAGRAM_EDGE_LABEL_MAX})",
            ))
        connected.add(e.source)
        connected.add(e.target)

    for i, n in enumerate(d.nodes):
        if n.id not in connected:
            out.append(Finding(
                rule="diagram/disconnected-node", severity="info",
                where=f"{where}.nodes[{i}]",
                message=f'node "{n.id}" has no edges',
            ))

    n_nodes = len(d.nodes)
    depth = estimate_depth(
        [n.id for n in d.nodes], [(e.source, e.target) for e in d.edges]
    )
    # Both tiers are severity="warning" -- see the DIAGRAM_MAX_*_DENSE
    # comment above for why this rule never blocks a render (finding 6).
    if n_nodes > DIAGRAM_MAX_NODES_DENSE or depth > DIAGRAM_MAX_DEPTH_DENSE:
        density_msg: str | None = (
            f"{n_nodes} nodes, depth {depth}; very dense for a 16:9 slide "
            "and unlikely to stay legible; split it into multiple diagrams "
            "or move detail to sublabels"
        )
    elif n_nodes > DIAGRAM_MAX_NODES_WARN or depth > DIAGRAM_MAX_DEPTH_WARN:
        density_msg = (
            f"{n_nodes} nodes, depth {depth}; getting dense for a 16:9 "
            "slide; consider splitting it or moving detail to sublabels"
        )
    else:
        density_msg = None
    if density_msg:
        out.append(Finding(
            rule="diagram/too-dense", severity="warning", where=where,
            message=density_msg,
        ))

    if not (d.caption and d.caption.strip()):
        out.append(Finding(
            rule="visual/unlabeled", severity="warning", where=where,
            message="diagram has no caption; add a one-line takeaway caption",
        ))


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
    elif isinstance(b, Diagram):
        _lint_diagram(b, where, out)


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
        _lint_slide(slide, f"slides[{i}]", out, doc)

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

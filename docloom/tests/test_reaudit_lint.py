"""Re-audit regression tests for the lint severity fixes: layouts that
render fine must not emit a blocking severity="error" finding that would
hard-block the CLI render and the studio export (HTTP 422).

  - hero renders its body in a short bottom caption band, so it keeps the
    half budget, but as a non-blocking warning (not the error that wrongly
    hard-blocked export of an otherwise-fine hero deck).
  - title/section slides render only their title/subtitle, so their
    (ignored) blocks must not fire a deck/overflow error, only the
    existing deck/ignored-blocks warning.

Also covers docs/diagram-status.md finding 13: lint.py mirrors several
render/pptx.py layout constants as plain duplicated literals (lint.py must
stay import-light and layout-agnostic; see the comment above SLIDE_BODY_H_IN
in lint.py for why this is a literal, not an import). CHART_H_IN and the
unresolved-Artifact height silently drifted from pptx.py's real values once;
the tests below import pptx.py's real constants and assert equality, so the
next drift fails CI instead of silently shipping a lint rule that scores the
wrong slide as safe.
"""

from docloom import Artifact, Chart, Document, Image, Paragraph, Series, Slide, has_errors, lint
# docloom/__init__.py does `from .lint import lint`, which rebinds the
# `docloom.lint` ATTRIBUTE to the lint() function itself, shadowing the
# submodule -- so `import docloom.lint as x` would silently grab the
# function, not the module. Importing names directly out of the submodule
# sidesteps that shadowing.
from docloom.lint import (
    ARTIFACT_PLACEHOLDER_H_IN, CAPTION_H_IN, CHART_H_IN, DIAGRAM_H_IN,
    IMAGE_CAPTION_H_IN, QUOTE_ATTR_H_IN, SUBTITLE_PAD_IN,
    estimate_depth as lint_estimate_depth,
)
from docloom.render import pptx as pptx_mod


def test_hero_slide_with_450_char_body_has_no_overflow_error():
    # ~450 chars is over the half budget (800 // 2 = 400), so hero now emits a
    # non-blocking warning; the point is it must NOT be an error that blocks
    # export the way it used to.
    body = "word " * 90  # 450 chars
    doc = Document(title="T", slides=[Slide(
        layout="hero", title="t", image=Image(query="office"),
        blocks=[Paragraph(text=body)],
    )])
    findings = lint(doc)
    assert not has_errors(findings)
    assert not any(
        f.rule == "deck/overflow" and f.severity == "error" for f in findings
    )


def test_section_slide_over_800_char_blocks_has_no_overflow_error():
    # section renders only title/subtitle; its blocks are ignored, so even
    # 900 chars of (non-rendered) blocks must not block export. The blocks
    # are still surfaced by the correct deck/ignored-blocks warning.
    doc = Document(title="T", slides=[Slide(
        layout="section", title="t",
        blocks=[Paragraph(text="x" * 900)],
    )])
    findings = lint(doc)
    assert not any(
        f.rule == "deck/overflow" and f.severity == "error" for f in findings
    )
    assert any(f.rule == "deck/ignored-blocks" for f in findings)
    assert not has_errors(findings)


# ------------------------------------------------ finding 13: mirror constants
#
# lint.py duplicates several render/pptx.py layout numbers as literals so it
# can estimate physical slide height without importing the layout engine.
# These tests pin lint.py's copies against pptx.py's real values directly, so
# a future edit to one side without the other fails here instead of silently
# shipping deck/overflow findings that no longer match what the renderer
# actually does.


def test_chart_h_in_mirrors_pptx_chart_max_h_in():
    assert CHART_H_IN == pptx_mod.LAYOUT["chart_max_h_in"]


def test_diagram_h_in_mirrors_pptx_diagram_h_in():
    assert DIAGRAM_H_IN == pptx_mod.DIAGRAM_H_IN


def test_artifact_placeholder_h_in_mirrors_pptx_unresolved_artifact_estimate():
    # pptx.py has no named constant for this (it's the literal 1.6 in both
    # _natural_h's unresolved-Artifact branch and _artifact_block's own
    # `min(max_h, 1.6)` placeholder-height cap), so this pins against that
    # literal directly rather than importing a name that doesn't exist.
    assert ARTIFACT_PLACEHOLDER_H_IN == 1.6


def test_unresolved_artifact_beside_a_chart_now_flags_height_overflow():
    # docs/diagram-status.md finding 13, second half: an unresolved Artifact
    # used to score 0.0in here, but pptx.py now draws it a real 1.6in
    # placeholder box (P5 audit defect 1). A chart (CHART_H_IN=4.8) plus that
    # placeholder plus the inter-block gap comfortably exceeds
    # SLIDE_BODY_H_IN (5.48), so this slide must now be flagged -- before the
    # fix it scored 4.8 + 0.0 + gap and stayed under budget, silently passing
    # a slide that overflows.
    doc = Document(title="T", slides=[Slide(
        layout="content", title="Quarterly view combines chart and diagram",
        blocks=[
            Chart(chart="bar", title="Revenue", labels=["Q1"],
                  series=[Series(name="Revenue", values=[1.0])], caption="c"),
            Artifact(kind="diagram", caption="c"),  # unresolved: no path/artifact_id
        ],
    )])
    findings = lint(doc)
    assert any(
        f.rule == "deck/overflow" and f.where == "slides[0]" for f in findings
    )


# --------------------------------------------------- finding 16: no lint-local
# duplicate of the depth algorithm


def test_lint_estimate_depth_is_the_real_painter_function_not_a_copy():
    # docs/diagram-status.md finding 16: lint.py used to carry its own
    # reimplementation of the painter's layering algorithm as an ImportError
    # fallback, which could silently diverge from the real one. Now it must
    # be the exact same function object, not merely equivalent behavior.
    from docloom.render.diagram_svg import estimate_depth as real_estimate_depth
    assert lint_estimate_depth is real_estimate_depth


# ---------------------------- silent-content-loss class audit: geometry fix
#
# Before this fix, lint's SLIDE_BODY_H_IN/CHART_H_IN modeled neither a
# slide's subtitle (which shrinks the renderer's real available body height)
# nor a block's own caption/attribution (which adds to its real footprint),
# so a slide the renderer silently dropped a trailing block from still
# scored as safe. render/pptx.py now names these reserves instead of
# sprinkling raw literals through several functions; these tests pin lint's
# own mirrored copies against those real names, the same technique already
# used above for CHART_H_IN/DIAGRAM_H_IN/ARTIFACT_PLACEHOLDER_H_IN.


def test_caption_h_in_mirrors_pptx_caption_h_in():
    assert CAPTION_H_IN == pptx_mod.CAPTION_H_IN


def test_image_caption_h_in_mirrors_pptx_image_caption_h_in():
    assert IMAGE_CAPTION_H_IN == pptx_mod.IMAGE_CAPTION_H_IN


def test_quote_attr_h_in_mirrors_pptx_quote_attr_h_in():
    assert QUOTE_ATTR_H_IN == pptx_mod.QUOTE_ATTR_H_IN


def test_subtitle_pad_in_mirrors_pptx_subtitle_pad_in():
    assert SUBTITLE_PAD_IN == pptx_mod.SUBTITLE_PAD_IN


def test_subtitle_presence_now_shrinks_the_height_budget_content_layout():
    # The exact repro: a chart with a caption comfortably fits the
    # subtitle-less 5.48in body budget, but once a real subtitle is added
    # (render/pptx.py's _subtitle_line pushes the body down for it), the
    # SAME content must now be flagged -- before this fix, lint's geometry
    # model was blind to the subtitle entirely and never flagged it.
    chart = Chart(chart="bar", title="Revenue", labels=["Q1"],
                  series=[Series(name="s", values=[1.0])], caption="c")
    no_subtitle = Document(title="T", slides=[Slide(
        layout="content", title="A slide title", blocks=[chart],
    )])
    with_subtitle = Document(title="T", slides=[Slide(
        layout="content", title="A slide title",
        subtitle="word " * 60,  # long enough to wrap to several lines
        blocks=[chart],
    )])
    assert not any(
        f.rule == "deck/overflow" for f in lint(no_subtitle)
    ), "chart + caption alone must fit the subtitle-less budget"
    assert any(
        f.rule == "deck/overflow" for f in lint(with_subtitle)
    ), "the same content with a real subtitle must now be flagged"


def test_captioned_chart_height_now_includes_its_own_caption():
    # docs/... finding: CHART_H_IN alone (4.8) undercounted a captioned
    # chart's real footprint (4.8 + 0.26 = 5.06); this is the other half of
    # the geometry gap that let the audit's repro pass silently.
    from docloom.lint import _block_height

    chart_no_cap = Chart(chart="bar", labels=["Q1"], series=[Series(name="s", values=[1.0])])
    chart_cap = Chart(chart="bar", labels=["Q1"], series=[Series(name="s", values=[1.0])], caption="c")
    assert _block_height(chart_cap, 10.0) == _block_height(chart_no_cap, 10.0) + CAPTION_H_IN

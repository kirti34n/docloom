"""Regression tests for the lint area's ultracode fix pass (2026-07-16):

  1. hero/section/quote/title slides now get a layout-aware height budget
     (mirroring render/pptx.py's real per-layout body-zone geometry --
     _hero_slide/_section_slide/_quote_slide/_title_slide) instead of the
     generic content-slide SLIDE_BODY_H_IN. Those layouts draw their body
     blocks into a much smaller SECONDARY zone (a hero caption band, a
     section divider band, the leftover below a display pull-quote, the
     title-slide cover leftover); before this fix, a slide whose renderer
     squeezed an authored visual block into that small zone and dropped it
     (render/pptx.py's MIN_VISUAL_BLOCK_H_IN floor) emitted zero
     deck/overflow findings.
  2. image_left/image_right/two_column's char-count deck/overflow rule is
     now a non-blocking warning, not an error: the PPTX renderer shrinks
     this text instead of dropping it, so the old error severity (which
     hard-blocks `docloom render` and 422s the studio export) was factually
     wrong for content that renders fine.
"""

from __future__ import annotations

import re
import warnings

from docloom import (
    Chart, Diagram, DiagramEdge, DiagramNode, Document, Image, Paragraph,
    Quote, Series, Slide, Stat, StatRow, has_errors, lint, render,
)
from docloom.lint import _hero_body_budget, _quote_rest_budget, _section_body_budget


def _png(path, w=40, h=40, color=(30, 30, 30)):
    from PIL import Image as PILImage

    PILImage.new("RGB", (w, h), color).save(path)
    return str(path)


# --------------------------------------------- finding 1: layout-aware height budget


def test_hero_photo_backed_chart_flags_overflow(tmp_path):
    png = _png(tmp_path / "hero.png")
    doc = Document(title="T", slides=[Slide(
        layout="hero", title="Revenue grew 40% in Q2", image=Image(path=png),
        blocks=[
            Chart(chart="column", title="Revenue by quarter", labels=["Q1", "Q2"],
                  series=[Series(name="Rev", values=[1.0, 2.0])], caption="Revenue grew"),
            Paragraph(text="Growth was driven by enterprise."),
        ],
    )])
    assert any(f.rule == "deck/overflow" for f in lint(doc))


def test_section_diagram_flags_overflow():
    doc = Document(title="T", slides=[Slide(
        layout="section", title="Part 2: Architecture",
        subtitle="How the pipeline fits together",
        blocks=[
            Diagram(id="d1", nodes=[DiagramNode(id="a", label="API"),
                                     DiagramNode(id="b", label="DB")],
                    edges=[DiagramEdge(source="a", target="b")]),
            Paragraph(text="Ingest is async."),
            Paragraph(text="Storage replicates."),
        ],
    )])
    findings = lint(doc)
    assert any(f.rule == "deck/overflow" and f.where == "slides[0]" for f in findings)


def test_quote_long_pullquote_plus_statrow_flags_overflow():
    doc = Document(title="T", slides=[Slide(
        layout="quote", title="Customer voice",
        blocks=[
            Quote(text="word " * 100, attribution="A. Analyst"),
            StatRow(items=[Stat(label="NPS", value="72", delta="+9"),
                            Stat(label="Churn", value="1.4%")]),
        ],
    )])
    assert any(f.rule == "deck/overflow" for f in lint(doc))


# ------------------------------------------------------ guards: no over-warning


def test_quote_short_pullquote_plus_statrow_stays_quiet():
    doc = Document(title="T", slides=[Slide(
        layout="quote", title="Customer voice",
        blocks=[
            Quote(text="Short quote.", attribution="X"),
            StatRow(items=[Stat(label="NPS", value="72", delta="+9"),
                            Stat(label="Churn", value="1.4%")]),
        ],
    )])
    assert not any(f.rule == "deck/overflow" for f in lint(doc))


def test_imageless_hero_with_captioned_chart_stays_quiet():
    doc = Document(title="T", slides=[Slide(
        layout="hero", title="Revenue grew 40% in Q2",
        blocks=[
            Chart(chart="column", title="Revenue by quarter", labels=["Q1", "Q2"],
                  series=[Series(name="Rev", values=[1.0, 2.0])], caption="Revenue grew"),
        ],
    )])
    assert not any(f.rule == "deck/overflow" for f in lint(doc))


# ------------------------------------------ anti-drift: lint budget vs renderer
#
# Many of the new per-layout constants (4.25, 0.75, 1.7, 2.5, 40pt, 1.4, 4.6,
# 0.6, the 30/24/20/16 pull-quote ladder) are UNNAMED literals in
# render/pptx.py, so an import-equality test (like the CHART_H_IN/DIAGRAM_H_IN
# pins) cannot cover them. Instead, render each overflowing slide above for
# real and parse the renderer's own self-reported
# "available body height (~X.XXin)" out of its warning, then assert lint's
# computed budget for that same slide agrees with it -- so a future edit to
# either side's geometry without the other fails here instead of silently
# drifting the two models apart again.

_AVAIL_RE = re.compile(r"available body height \(~([\d.]+)in\)")


def _rendered_avail(doc: Document, tmp_path, name: str) -> float:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        render(doc, "pptx", tmp_path / name)
    for w in caught:
        m = _AVAIL_RE.search(str(w.message))
        if m:
            return float(m.group(1))
    raise AssertionError(
        "no 'available body height' warning was raised; got: "
        f"{[str(w.message) for w in caught]}"
    )


def test_hero_budget_matches_renderer_reported_avail(tmp_path):
    png = _png(tmp_path / "hero2.png")
    slide = Slide(
        layout="hero", title="Revenue grew 40% in Q2", image=Image(path=png),
        blocks=[
            Chart(chart="column", title="Revenue by quarter", labels=["Q1", "Q2"],
                  series=[Series(name="Rev", values=[1.0, 2.0])], caption="Revenue grew"),
            Paragraph(text="Growth was driven by enterprise."),
        ],
    )
    doc = Document(title="T", slides=[slide])
    avail = _rendered_avail(doc, tmp_path, "hero_avail.pptx")
    assert abs(_hero_body_budget(slide) - avail) < 0.05


def test_section_budget_matches_renderer_reported_avail(tmp_path):
    slide = Slide(
        layout="section", title="Part 2: Architecture",
        subtitle="How the pipeline fits together",
        blocks=[
            Diagram(id="d1", nodes=[DiagramNode(id="a", label="API"),
                                     DiagramNode(id="b", label="DB")],
                    edges=[DiagramEdge(source="a", target="b")]),
            Paragraph(text="Ingest is async."),
            Paragraph(text="Storage replicates."),
        ],
    )
    doc = Document(title="T", slides=[slide])
    avail = _rendered_avail(doc, tmp_path, "section_avail.pptx")
    assert abs(_section_body_budget(slide) - avail) < 0.05


def test_quote_budget_matches_renderer_reported_avail(tmp_path):
    quote_text = "word " * 100
    slide = Slide(
        layout="quote", title="Customer voice",
        blocks=[
            Quote(text=quote_text, attribution="A. Analyst"),
            StatRow(items=[Stat(label="NPS", value="72", delta="+9"),
                            Stat(label="Churn", value="1.4%")]),
        ],
    )
    doc = Document(title="T", slides=[slide])
    avail = _rendered_avail(doc, tmp_path, "quote_avail.pptx")
    budget = _quote_rest_budget(quote_text, "A. Analyst")
    assert abs(budget - avail) < 0.05


# ----------------------------------- finding 2: image/two_column overflow severity


def test_image_left_over_budget_chars_is_warning_not_error():
    doc = Document(title="T", slides=[Slide(
        layout="image_left", title="Enterprise adoption drove the quarter",
        image=Image(query="hq"),
        blocks=[Paragraph(text="word " * 156)],  # 780 chars: over the 400 half-budget
    )])
    findings = lint(doc)
    assert any(f.rule == "deck/overflow" for f in findings)
    assert not any(f.rule == "deck/overflow" and f.severity == "error" for f in findings)
    assert not has_errors(findings)


def test_image_right_over_budget_chars_is_warning_not_error():
    doc = Document(title="T", slides=[Slide(
        layout="image_right", title="Enterprise adoption drove the quarter",
        image=Image(query="hq"),
        blocks=[Paragraph(text="word " * 156)],
    )])
    findings = lint(doc)
    assert any(f.rule == "deck/overflow" for f in findings)
    assert not any(f.rule == "deck/overflow" and f.severity == "error" for f in findings)
    assert not has_errors(findings)


def test_two_column_over_budget_chars_is_warning_not_error():
    doc = Document(title="T", slides=[Slide(
        layout="two_column",
        blocks=[Paragraph(text="word " * 100)],  # 500 chars: over the 400 half-budget
        right=[Paragraph(text="Volume rose 22%.")],
    )])
    findings = lint(doc)
    assert any(f.rule == "deck/overflow" for f in findings)
    assert not any(f.rule == "deck/overflow" and f.severity == "error" for f in findings)
    assert not has_errors(findings)


# content/quote's char-count overflow rule stays severity="error" -- out of
# this fix's scope (text there cannot shrink indefinitely; see lint.py).
def test_content_layout_char_overflow_is_still_an_error():
    doc = Document(title="T", slides=[Slide(
        layout="content", title="t",
        blocks=[Paragraph(text="word " * 170)],  # 850 chars: over the 800 full budget
    )])
    findings = lint(doc)
    assert any(
        f.rule == "deck/overflow" and f.severity == "error" for f in findings
    )
    assert has_errors(findings)


# lint must not warn that title/section blocks "will not appear": the PPTX
# renderer draws them, so that guidance was false and made the LLM delete
# content that renders fine. This ties the lint claim to the real render.
def test_title_section_blocks_render_and_are_not_flagged_ignored(tmp_path):
    import zipfile

    for layout, sentinel in (("title", "TITLE_KEPT_XYZ"), ("section", "SECTION_KEPT_XYZ")):
        doc = Document(title="T", slides=[Slide(
            layout=layout, title="t", blocks=[Paragraph(text=sentinel)])])
        assert "deck/ignored-blocks" not in {f.rule for f in lint(doc)}

        out = tmp_path / f"{layout}.pptx"
        render(doc, "pptx", out)
        blob = b"".join(
            zipfile.ZipFile(out).read(n)
            for n in zipfile.ZipFile(out).namelist() if n.endswith(".xml"))
        assert sentinel.encode() in blob, f"{layout} slide dropped its authored block"

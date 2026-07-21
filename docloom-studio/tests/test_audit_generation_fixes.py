"""Regressions for the generation-vs-export contradictions the audit found:

1. A content/quote slide could be generated cleanly yet exceed the export
   gate's MAX_SLIDE_CHARS (deck/overflow, severity="error" -> HTTP 422), so one
   dense slide blocked the whole deck's export. _budget_errors now mirrors that
   gate exactly.
2. A model that mislabels a body slide as layout="title" escaped the
   NEVER-EMPTY guard (title slides are body-less), then the pipeline rewrote it
   to a content layout -> a blank content slide shipped. The lint closure now
   normalizes the layout before validating.
3. An empty formula cell validated fine but the export gate hard-errors on it
   (sheet/empty-formula), so a saved-but-un-exportable sheet shipped with no
   retry. _sheet_content_errors now catches it.
"""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-auditfix-"))

from docloom.ir import (  # noqa: E402
    BulletList, Column, Formula, ListItem, Paragraph, Sheet, Slide, plain,
)
from docloom.lint import MAX_SLIDE_CHARS  # noqa: E402

from docloom_studio.generate import (  # noqa: E402
    _budget_errors, _sheet_content_errors, _slide_content_errors,
)


# ---- #1 total on-slide character budget mirrors the export gate ------------

def test_content_slide_over_char_budget_is_flagged():
    para = Paragraph(text=plain("word " * ((MAX_SLIDE_CHARS // 5) + 20)))  # > MAX_SLIDE_CHARS
    slide = Slide(layout="content", title="ok", blocks=[para])
    errors = _budget_errors(slide)
    assert any(str(MAX_SLIDE_CHARS) in e and "overflow" in e for e in errors)


def test_content_slide_within_char_budget_is_fine():
    para = Paragraph(text=plain("A short, grounded takeaway sentence."))
    slide = Slide(layout="content", title="ok", blocks=[para])
    assert _budget_errors(slide) == []


def test_two_column_over_half_char_budget_is_flagged():
    half_over = "word " * ((MAX_SLIDE_CHARS // 2 // 5) + 10)  # > MAX_SLIDE_CHARS // 2
    slide = Slide(layout="two_column", title="ok",
                  blocks=[Paragraph(text=plain(half_over))],
                  right=[Paragraph(text=plain("short"))])
    errors = _budget_errors(slide)
    assert any(str(MAX_SLIDE_CHARS // 2) in e and "column" in e for e in errors)


# ---- #2 a mislabeled 'title' body slide is normalized before validating ----

def test_mislabeled_title_slide_normalizes_then_flags_empty():
    # Reproduce the deck pipeline's _lint_fn normalization: a non-opener slide
    # the model returned as layout="title" with an empty body is rewritten to
    # its intended layout, and the NEVER-EMPTY guard must then fire.
    s = Slide(layout="title", blocks=[])
    intended = "content"
    if s.layout == "title" and intended != "title":
        s.layout = intended
    assert _slide_content_errors(s), "an empty content-intended slide must be flagged"


def test_mislabeled_title_slide_for_a_section_stays_body_less():
    # A section break is legitimately body-less: normalizing to 'section' must
    # NOT flag it as empty.
    s = Slide(layout="title", blocks=[])
    intended = "section"
    if s.layout == "title" and intended != "title":
        s.layout = intended
    assert _slide_content_errors(s) == []


# ---- #3 empty formula cells are caught before export --------------------

def test_empty_formula_cell_is_flagged():
    sheet = Sheet(name="S", columns=[Column(header="A"), Column(header="B")],
                  rows=[["item", Formula(formula="")]])
    errors = _sheet_content_errors(sheet)
    assert errors and "formula" in errors[0]


def test_valid_formula_cell_is_fine():
    sheet = Sheet(name="S", columns=[Column(header="A"), Column(header="B")],
                  rows=[["item", Formula(formula="=SUM(A1:A2)")]])
    assert _sheet_content_errors(sheet) == []


def test_whitespace_only_formula_is_flagged():
    sheet = Sheet(name="S", columns=[Column(header="A")], rows=[[Formula(formula="   ")]])
    assert _sheet_content_errors(sheet)

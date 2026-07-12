from docloom import Document, Sheet, Theme
from docloom.render.typst import to_typst


def test_typst_renders_sheet_with_empty_columns():
    # A Sheet with rows but no columns is valid IR; markdown/html/docx/xlsx
    # pad the header to the row width and render it, so typst must too rather
    # than silently dropping the whole sheet.
    doc = Document(
        title="t",
        sheets=[Sheet(name="Data", columns=[], rows=[["a", "b"], ["c", "d"]])],
    )
    typ = to_typst(doc, Theme())
    assert "Data" in typ
    for cell in ("a", "b", "c", "d"):
        assert f"[{cell}]" in typ


def test_typst_skips_sheet_with_no_cells():
    # a degenerate Sheet with rows but zero cells (rows=[[]]) has nothing to
    # render, so it must be skipped entirely, not emit a stray empty heading.
    doc = Document(
        title="t", sheets=[Sheet(name="Empty", columns=[], rows=[[]])]
    )
    typ = to_typst(doc, Theme())
    assert "Empty" not in typ

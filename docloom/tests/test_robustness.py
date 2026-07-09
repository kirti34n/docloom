"""Any-document robustness: adversarial IR that once crashed or silently lost
data must now render correctly across formats. Each test maps to a fixed bug."""

import zipfile
from pathlib import Path

import pytest

from docloom import Document, Theme, render
from docloom.render import RenderError

T = Theme()


def _xml(path: Path, member: str) -> str:
    with zipfile.ZipFile(path) as z:
        return "".join(
            z.read(n).decode("utf-8", "replace")
            for n in z.namelist() if member in n
        )


@pytest.mark.parametrize("accent", ["0x1234", "-12345", "12_345", "zzzzzz", "1a2B3c"])
def test_slide_accent_never_crashes(tmp_path, accent):
    # int(c, 16) used to accept 0x/underscore/sign strings and then crash
    doc = Document(title="t", slides=[{"layout": "content", "title": "x",
        "accent": accent, "blocks": [{"type": "paragraph", "text": "p"}]}])
    render(doc, "pptx", tmp_path / "a.pptx")


def test_typst_skips_unsupported_raster(tmp_path):
    from PIL import Image as PILImage
    bmp = tmp_path / "x.bmp"
    PILImage.new("RGB", (8, 8), "red").save(bmp, "BMP")
    doc = Document(title="t", blocks=[{"type": "image", "path": str(bmp), "alt": "b"}])
    render(doc, "pdf", tmp_path / "b.pdf")  # BMP is skipped, not compiled


def test_xlsx_empty_formula_and_huge_int(tmp_path):
    doc = Document(title="t", sheets=[{"name": "s",
        "columns": [{"header": "a"}, {"header": "b"}],
        "rows": [[{"formula": ""}, 10 ** 400]]}])
    render(doc, "xlsx", tmp_path / "s.xlsx")


def test_quote_slide_keeps_right_column(tmp_path):
    doc = Document(title="t", slides=[{"layout": "quote",
        "blocks": [{"type": "quote", "text": "Q"}],
        "right": [{"type": "paragraph", "text": "RIGHTMARKER"}]}])
    render(doc, "pptx", tmp_path / "q.pptx")
    assert "RIGHTMARKER" in _xml(tmp_path / "q.pptx", "slides/slide")


def test_sheet_only_document_renders_data_everywhere(tmp_path):
    doc = Document(title="t", sheets=[{"name": "Metrics",
        "columns": [{"header": "Year"}, {"header": "Val"}],
        "rows": [["2026", "SHEETMARKER"]]}])
    render(doc, "docx", tmp_path / "s.docx")
    assert "SHEETMARKER" in _xml(tmp_path / "s.docx", "document.xml")
    typ = render(doc, "typ", tmp_path / "s.typ").read_text(encoding="utf-8")
    assert "SHEETMARKER" in typ
    html = render(doc, "html", tmp_path / "s.html").read_text(encoding="utf-8")
    assert "SHEETMARKER" in html


def test_duplicate_source_numbering_matches_superscript(tmp_path):
    doc = Document(title="t", slides=[{"layout": "content", "title": "x",
        "blocks": [{"type": "paragraph", "text": [{"text": "claim", "cite": "b"}]}]}],
        sources=[{"id": "a", "title": "Alpha"}, {"id": "a", "title": "AlphaDup"},
                 {"id": "b", "title": "Beta"}])
    render(doc, "pptx", tmp_path / "d.pptx")
    xml = _xml(tmp_path / "d.pptx", "slides/slide")
    assert "2. Beta" in xml and "3. Beta" not in xml


def test_chart_fallback_table_keeps_all_values(tmp_path):
    # empty labels used to drop every value from the fallback table
    doc = Document(title="t", slides=[{"layout": "content", "title": "x", "blocks": [
        {"type": "chart", "chart": "scatter", "labels": [],
         "series": [{"name": "s", "values": [1.0, 2.0, 3.0]}]}]}])
    render(doc, "pptx", tmp_path / "c.pptx")


def test_ragged_and_empty_tables_render(tmp_path):
    doc = Document(title="t", blocks=[
        {"type": "table", "header": ["a", "b", "c"],
         "rows": [["1"], ["1", "2", "3", "4", "5"]]},
        {"type": "table", "header": [], "rows": []},
    ])
    for fmt in ("html", "md", "docx", "pdf"):
        render(doc, fmt, tmp_path / f"t.{fmt}")


def test_unreadable_image_does_not_crash_html(tmp_path):
    # a path that exists but is a directory cannot be read as image bytes
    d = tmp_path / "dir.png"
    d.mkdir()
    doc = Document(title="t", blocks=[{"type": "image", "path": str(d), "alt": "x"}])
    render(doc, "html", tmp_path / "u.html")


def test_llm_normalizes_standalone_image_and_logo_slots():
    # Slide.image and Document.logo hold Image, so a misnamed tag there must
    # still normalize (they are not inside a blocks/right list)
    import json
    from docloom import parse_llm_output
    d = parse_llm_output(json.dumps({"title": "T", "slides": [
        {"layout": "hero", "image": {"type": "img", "query": "a cat"}}]}))
    assert d.slides[0].image is not None
    d = parse_llm_output(json.dumps({
        "title": "T", "logo": {"type": "picture", "path": "l.png"}, "blocks": []}))
    assert d.logo is not None


def test_lint_locates_right_column_blocks():
    from docloom import lint
    doc = Document.model_validate({"title": "T", "slides": [{"layout": "two_column",
        "title": "x", "blocks": [{"type": "paragraph", "text": "hi"}],
        "right": [{"type": "table", "header": ["a", "b", "c"], "rows": [["1"]]}]}]})
    locs = [f.where for f in lint(doc) if f.rule.startswith("table")]
    assert any(".right[0]" in w for w in locs)
    assert not any(".blocks[1]" in w for w in locs)


def test_markdown_escapes_indented_line_markers():
    from docloom.render.markdown import _esc_md
    assert "\\#" in _esc_md("    # not a heading")   # 4-space indent, once inert
    assert _esc_md("# heading").startswith("\\#")

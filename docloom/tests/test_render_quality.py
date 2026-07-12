"""Render-quality regression tests: the dependency-free chart SVG painter,
its wiring into html/typst/docx/markdown, the docx figure/placeholder
fallbacks and header-link color, and the markdown emphasis-boundary and
assets-mode fixes."""

from __future__ import annotations

import xml.dom.minidom as minidom

import pytest

from docloom import (
    Artifact, Chart, Document, Image as ImageBlock, Paragraph, Series, Span,
    Table as TableBlock, Theme, render,
)
from docloom.render import chart_svg

# ------------------------------------------------------------- chart_svg


@pytest.mark.parametrize("kind", ["column", "bar", "line", "area", "pie", "scatter"])
def test_chart_svg_renders_every_kind_as_valid_xml(kind):
    chart = Chart(
        chart=kind, title="T", labels=["A", "B", "C"],
        series=[Series(name="s1", values=[1.0, 2.0, 3.0]), Series(name="s2", values=[3.0, 1.0, 2.0])],
        caption="cap",
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    minidom.parseString(svg)  # must be well-formed XML


def test_chart_svg_no_data_is_empty_string():
    assert chart_svg.render_svg(Chart(chart="column", labels=[], series=[]), Theme()) == ""
    all_none = Chart(chart="line", labels=["a"], series=[Series(name="s", values=[None, None])])
    assert chart_svg.render_svg(all_none, Theme()) == ""


def test_chart_svg_none_gap_breaks_the_line_not_bridges_it():
    chart = Chart(
        chart="line", labels=["A", "B", "C", "D", "E"],
        series=[Series(name="s", values=[1.0, 2.0, None, 3.0, 4.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    # the None value splits the series into two 2-point runs, each its own
    # path segment, instead of one path interpolating across the gap
    assert svg.count("<path") == 2


def test_chart_svg_column_skips_the_bar_for_none():
    chart = Chart(chart="column", labels=["A", "B"], series=[Series(name="only", values=[1.0, None])])
    svg = chart_svg.render_svg(chart, Theme())
    assert svg.count("<rect") == 1  # the None value draws no bar (and there is no legend to add rects)


def test_chart_svg_single_series_has_no_legend_multi_series_does():
    single = Chart(chart="column", labels=["A"], series=[Series(name="OnlySeries", values=[1.0])])
    multi = Chart(chart="column", labels=["A"],
                  series=[Series(name="Xseries", values=[1.0]), Series(name="Yseries", values=[2.0])])
    assert "OnlySeries" not in chart_svg.render_svg(single, Theme())
    svg_multi = chart_svg.render_svg(multi, Theme())
    assert "Xseries" in svg_multi and "Yseries" in svg_multi


def test_chart_svg_pie_uses_only_the_first_series():
    chart = Chart(
        chart="pie", labels=["A", "B"],
        series=[Series(name="s1", values=[25.0, 75.0]), Series(name="s2", values=[999.0, 1.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert "25%" in svg and "75%" in svg


def test_chart_svg_theme_colors_drive_series_and_axes():
    theme = Theme(primary="#112233", accent="#445566", muted="#778899")
    chart = Chart(chart="column", labels=["A", "B"],
                  series=[Series(name="a", values=[1.0, 2.0]), Series(name="b", values=[2.0, 1.0])])
    svg = chart_svg.render_svg(chart, theme)
    assert "#112233" in svg and "#445566" in svg and "#778899" in svg


def test_chart_svg_escapes_hostile_text():
    chart = Chart(
        chart="bar", title="<script>alert(1)</script>", labels=['"><svg onload=alert(1)>'],
        series=[Series(name="<img src=x onerror=alert(1)>", values=[1.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert "<script>alert(1)</script>" not in svg
    assert "onerror=alert(1)>" not in svg
    minidom.parseString(svg)


def test_chart_svg_never_crashes_on_nan_or_infinity():
    chart = Chart(chart="line", labels=["A", "B", "C"],
                  series=[Series(name="s", values=[float("nan"), float("inf"), 5.0])])
    svg = chart_svg.render_svg(chart, Theme())
    assert svg != ""
    minidom.parseString(svg)


def test_chart_svg_scatter_labels_shorter_than_series_keeps_every_point():
    # regression: zip(numeric_labels, series.values) truncates to the
    # shorter of the two, so a series longer than the label list used to
    # have its trailing (real) values silently dropped instead of falling
    # back to index-based x positions
    chart = Chart(chart="scatter", labels=["1"], series=[Series(name="s", values=[None, 5.0])])
    svg = chart_svg.render_svg(chart, Theme())
    assert "<circle" in svg  # the value 5.0 must still be plotted

    ragged = Chart(
        chart="scatter", labels=["1", "2", "3"],
        series=[Series(name="a", values=[5.0]), Series(name="b", values=[1.0, 2.0, 3.0])],
    )
    assert chart_svg.render_svg(ragged, Theme()).count("<circle") == 4


# -------------------------------------------------- renderer integration


def _chart_doc(**kw) -> Document:
    chart = Chart(
        chart=kw.pop("chart", "column"), title=kw.pop("title", "Revenue"),
        labels=kw.pop("labels", ["Q1", "Q2"]),
        series=kw.pop("series", [Series(name="2026", values=[10.0, 20.0])]),
        caption=kw.pop("caption", None),
    )
    return Document(title="Report", blocks=[chart])


def test_html_chart_embeds_inline_svg_not_a_table(tmp_path):
    html = render(_chart_doc(), "html", tmp_path / "c.html").read_text(encoding="utf-8")
    assert "<svg" in html and 'class="docloom-chart"' in html
    assert "<table" not in html  # a real chart, not the data-table fallback


def test_typst_chart_embeds_svg_bytes():
    from docloom.render.typst import to_typst

    typ = to_typst(_chart_doc(), Theme())
    assert "image(bytes(" in typ and 'format: "svg"' in typ


def test_typst_chart_compiles_to_pdf(tmp_path):
    pytest.importorskip("typst")
    doc = _chart_doc(chart="line", series=[Series(name="s", values=[1.0, None])])
    out = render(doc, "pdf", tmp_path / "c.pdf")
    assert out.read_bytes()[:5] == b"%PDF-"


def test_markdown_chart_title_is_a_heading_caption_below(tmp_path):
    md = render(_chart_doc(caption="source: internal"), "md", tmp_path / "c.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    assert any(ln.startswith("#### Revenue") for ln in lines)
    assert "source: internal" in md
    assert md.index("Revenue") < md.index("source: internal")


def test_docx_chart_without_prerendered_path_is_titled_captioned_table(tmp_path):
    import docx as docx_lib

    out = render(_chart_doc(caption="source: internal"), "docx", tmp_path / "c.docx")
    d = docx_lib.Document(str(out))
    paragraphs = [p.text for p in d.paragraphs]
    assert "Revenue" in paragraphs
    assert any("source: internal" in p for p in paragraphs)
    assert any(t.rows for t in d.tables)  # the chart data still reaches the reader


# --------------------------------------------------------- docx: findings


def test_docx_svg_artifact_gets_placeholder_not_silent_drop(tmp_path):
    import docx as docx_lib

    svg_path = tmp_path / "diagram.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    doc = Document(title="T", blocks=[
        Paragraph(text="before"),
        Artifact(kind="diagram", path=str(svg_path), alt="architecture diagram", caption="Fig 1"),
        Paragraph(text="after"),
    ])
    out = render(doc, "docx", tmp_path / "a.docx")
    d = docx_lib.Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "architecture diagram" in text  # placeholder alt text, not a silent drop
    assert "Fig 1" in text
    assert "before" in text and "after" in text


def test_docx_svg_image_block_gets_placeholder_too(tmp_path):
    import docx as docx_lib

    svg_path = tmp_path / "pic.svg"
    svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>', encoding="utf-8")
    doc = Document(title="T", blocks=[ImageBlock(path=str(svg_path), alt="a circle")])
    out = render(doc, "docx", tmp_path / "i.docx")
    d = docx_lib.Document(str(out))
    assert "a circle" in "\n".join(p.text for p in d.paragraphs)


def test_docx_missing_image_path_still_skipped_silently(tmp_path):
    import docx as docx_lib

    doc = Document(title="T", blocks=[
        Paragraph(text="only text here"),
        ImageBlock(path=str(tmp_path / "does_not_exist.png"), alt="ghost"),
    ])
    out = render(doc, "docx", tmp_path / "m.docx")
    d = docx_lib.Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "ghost" not in text  # a genuinely missing path stays silent, matching every other renderer


def test_docx_header_link_recolors_to_background_not_primary(tmp_path):
    from docx.oxml.ns import qn
    import docx as docx_lib

    theme = Theme()  # primary #1D4ED8, background #FFFFFF
    doc = Document(
        title="T",
        blocks=[TableBlock(header=[[Span(text="link", link="https://example.com")]], rows=[["x"]])],
    )
    out = render(doc, "docx", tmp_path / "h.docx")
    d = docx_lib.Document(str(out))
    header_cell = d.tables[0].cell(0, 0)
    hyperlinks = header_cell._tc.findall(".//" + qn("w:hyperlink"))
    assert len(hyperlinks) == 1
    colors = hyperlinks[0].findall(".//" + qn("w:color"))
    assert len(colors) == 1
    assert colors[0].get(qn("w:val")).upper() == theme.background.lstrip("#").upper()
    assert colors[0].get(qn("w:val")).upper() != theme.primary.lstrip("#").upper()


def test_docx_body_link_color_is_unaffected_by_the_header_fix(tmp_path):
    from docx.oxml.ns import qn
    import docx as docx_lib

    theme = Theme()
    doc = Document(title="T", blocks=[Paragraph(text=[Span(text="link", link="https://example.com")])])
    out = render(doc, "docx", tmp_path / "b.docx")
    d = docx_lib.Document(str(out))
    hyperlink = None
    for p in d.paragraphs:
        found = p._p.findall(".//" + qn("w:hyperlink"))
        if found:
            hyperlink = found[0]
            break
    assert hyperlink is not None
    color = hyperlink.findall(".//" + qn("w:color"))[0]
    assert color.get(qn("w:val")).upper() == theme.primary.lstrip("#").upper()


# ------------------------------------------------------ markdown: findings


def test_markdown_span_emphasis_hoists_boundary_whitespace():
    from docloom.render.markdown import _span_md

    assert _span_md(Span(text=" hi", bold=True), {}) == " **hi**"
    assert _span_md(Span(text="hi ", bold=True), {}) == "**hi** "
    assert _span_md(Span(text=" hi ", italic=True), {}) == " *hi* "
    assert _span_md(Span(text=" hi ", bold=True, italic=True), {}) == " ***hi*** "
    assert _span_md(Span(text="hi", bold=True), {}) == "**hi**"  # no boundary whitespace: unchanged
    assert _span_md(Span(text="   ", bold=True), {}) == "   "  # whitespace-only: no markers at all


def test_markdown_emphasis_boundary_whitespace_end_to_end(tmp_path):
    doc = Document(title="T", blocks=[
        Paragraph(text=[Span(text=" leading", bold=True), Span(text="mid "),
                        Span(text="trailing ", italic=True)]),
    ])
    md = render(doc, "md", tmp_path / "e.md").read_text(encoding="utf-8")
    assert "** leading**" not in md
    assert " **leading**" in md
    assert "*trailing *" not in md
    assert "*trailing* " in md


def test_markdown_assets_mode_copies_images_and_rewrites_relative(tmp_path):
    from PIL import Image as PILImage

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    img_path = src_dir / "pic.png"
    PILImage.new("RGB", (4, 4), "red").save(img_path)
    doc = Document(title="T", blocks=[ImageBlock(path=str(img_path), alt="a pic")])
    out_dir = tmp_path / "out"
    out = render(doc, "md", out_dir / "report.md")
    text = out.read_text(encoding="utf-8")
    assert str(img_path) not in text  # the generating machine's path must not leak
    assert "report_files/pic.png" in text
    assert (out_dir / "report_files" / "pic.png").is_file()


def test_markdown_assets_false_keeps_the_original_path(tmp_path):
    from PIL import Image as PILImage

    from docloom.render.markdown import render as md_render

    img_path = tmp_path / "pic.png"
    PILImage.new("RGB", (4, 4), "red").save(img_path)
    doc = Document(title="T", blocks=[ImageBlock(path=str(img_path), alt="a")])
    out = md_render(doc, Theme(), tmp_path / "out.md", assets=False)
    text = out.read_text(encoding="utf-8")
    assert str(img_path) in text
    assert not (tmp_path / "out_files").exists()


def test_markdown_assets_mode_dedupes_same_source_reused_twice(tmp_path):
    from PIL import Image as PILImage

    img_path = tmp_path / "logo.png"
    PILImage.new("RGB", (2, 2), "blue").save(img_path)
    doc = Document(title="T", blocks=[
        ImageBlock(path=str(img_path), alt="first"),
        ImageBlock(path=str(img_path), alt="second"),
    ])
    out = render(doc, "md", tmp_path / "out" / "d.md")
    text = out.read_text(encoding="utf-8")
    assert text.count("d_files/logo.png") == 2  # same source, same destination
    assert not (tmp_path / "out" / "d_files" / "logo-2.png").exists()


# -------------------------------------------------------------- finding 6


def test_html_sources_list_dedupes_duplicate_ids(tmp_path):
    doc = Document(
        title="T",
        blocks=[Paragraph(text=[Span(text="claim", cite="a")])],
        sources=[{"id": "a", "title": "Alpha"}, {"id": "a", "title": "AlphaDup"}],
    )
    html = render(doc, "html", tmp_path / "s.html").read_text(encoding="utf-8")
    assert html.count('id="src-') == 1
    assert "AlphaDup" not in html


def test_xlsx_sources_sheet_dedupes_duplicate_ids(tmp_path):
    import zipfile

    doc = Document(
        title="T",
        blocks=[
            Paragraph(text=[Span(text="claim", cite="a")]),
            TableBlock(header=["h"], rows=[["v"]]),
        ],
        sources=[{"id": "a", "title": "Alpha"}, {"id": "a", "title": "AlphaDup"}],
    )
    out = render(doc, "xlsx", tmp_path / "s.xlsx")
    with zipfile.ZipFile(out) as z:
        shared = z.read("xl/sharedStrings.xml").decode("utf-8")
    assert "AlphaDup" not in shared

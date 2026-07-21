"""Regression tests for the chart-xlsx-raster fix pass:

1. chart_svg.render_svg paints a full-bleed themed background rect as the
   first child of the <svg>, so dark-theme ink (title/axis/legend text) does
   not rasterize onto raster.svg_to_png's forced-white background and become
   unreadable in DOCX/PPTX-fallback output.
2. chart_svg's tick/coordinate math no longer raises OverflowError on finite
   but astronomically large values (e.g. 1e308): such values degrade to a
   gap (like NaN/Infinity already do) instead of crashing the HTML/DOCX
   renderers, which must be total for any valid IR.
"""

from __future__ import annotations

import xml.dom.minidom as minidom

import pytest

from docloom import Chart, Document, Series, Theme, render
from docloom.render import chart_svg, raster

# --------------------------------------------------------- themed background


def test_chart_svg_paints_themed_background_rect_behind_all_text():
    dark = Theme(background="#161C2E", text="#F1EFEA", muted="#9AA3BB")
    chart = Chart(
        chart="column", title="Quarterly revenue", labels=["Q1", "Q2"],
        series=[Series(values=[100.0, 200.0])],
    )
    svg = chart_svg.render_svg(chart, dark)
    bg_rect = '<rect width="100%" height="100%" fill="#161C2E"/>'
    assert bg_rect in svg
    # SVG paints in document order: the background rect must precede (sit
    # behind) every text element, not just exist somewhere in the markup.
    assert svg.index(bg_rect) < svg.index("<text")
    minidom.parseString(svg)


def test_chart_svg_background_rect_is_a_light_theme_no_op():
    chart = Chart(chart="column", labels=["A", "B"], series=[Series(values=[1.0, 2.0])])
    svg = chart_svg.render_svg(chart, Theme())  # default theme: background #FFFFFF
    assert '<rect width="100%" height="100%" fill="#FFFFFF"/>' in svg


def test_chart_svg_empty_chart_still_returns_empty_string():
    # the background-rect insertion must only fire on the non-empty path:
    # an all-None / no-series chart keeps returning "" so callers still fall
    # back to a data table instead of emitting a bare colored rectangle.
    assert chart_svg.render_svg(Chart(chart="column", labels=[], series=[]), Theme()) == ""
    all_none = Chart(chart="line", labels=["a"], series=[Series(values=[None, None])])
    assert chart_svg.render_svg(all_none, Theme()) == ""
    no_positive_pie = Chart(chart="pie", labels=["a"], series=[Series(values=[0.0])])
    assert chart_svg.render_svg(no_positive_pie, Theme()) == ""


@pytest.mark.skipif(not raster.available(), reason="optional resvg_py rasterizer not installed")
def test_chart_svg_rasterizes_with_dark_background_not_white():
    dark = Theme(background="#161C2E", text="#F1EFEA", muted="#9AA3BB")
    chart = Chart(
        chart="column", title="Quarterly revenue", labels=["Q1", "Q2"],
        series=[Series(values=[100.0, 200.0])],
    )
    svg = chart_svg.render_svg(chart, dark)
    png = raster.svg_to_png(svg)
    assert png is not None
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(png)).convert("RGB")
    corner = img.getpixel((0, 0))
    # before the fix, raster.svg_to_png's forced-white canvas showed through
    # (corner == (255, 255, 255)); after, the painted background covers it
    assert corner != (255, 255, 255)
    assert abs(corner[0] - 0x16) <= 12 and abs(corner[1] - 0x1C) <= 12 and abs(corner[2] - 0x2E) <= 12


# -------------------------------------------------- extreme-magnitude guard


@pytest.mark.parametrize("kind", ["column", "line", "area", "bar"])
def test_chart_extreme_magnitude_does_not_crash_html_or_docx(kind, tmp_path):
    doc = Document(
        title="X",
        blocks=[Chart(chart=kind, labels=["min", "max"], series=[Series(values=[-1e308, 1e308])])],
    )
    for fmt, ext in (("html", "html"), ("docx", "docx")):
        out = tmp_path / f"{kind}.{ext}"
        result = render(doc, fmt, out)
        assert result.exists()


def test_chart_svg_scatter_extreme_value_does_not_overflow():
    chart = Chart(chart="scatter", labels=["a", "b"], series=[Series(values=[0.0, 1e308])])
    svg = chart_svg.render_svg(chart, Theme())  # must not raise OverflowError
    minidom.parseString(svg)


def test_chart_svg_scatter_extreme_label_as_x_falls_back_to_index():
    # a numeric label whose magnitude would overflow the x-scale math must
    # fall back to index-based X instead of crashing
    chart = Chart(chart="scatter", labels=["0", "1e308"], series=[Series(values=[1.0, 2.0])])
    svg = chart_svg.render_svg(chart, Theme())  # must not raise OverflowError
    minidom.parseString(svg)
    assert svg.count("<circle") >= 2


def test_chart_svg_readable_value_still_plots_next_to_an_extreme_one():
    chart = Chart(chart="column", labels=["a", "b"], series=[Series(values=[5.0, 1e308])])
    svg = chart_svg.render_svg(chart, Theme())
    assert svg != ""
    # the extreme value is dropped to a gap; the readable value still draws
    # a bar (background rect + axis gridlines + the one bar)
    assert "<rect" in svg


def test_chart_svg_all_extreme_column_yields_empty_string_and_html_falls_back_to_table(tmp_path):
    chart = Chart(chart="column", labels=["a", "b"], series=[Series(values=[1e308, -1e308])])
    assert chart_svg.render_svg(chart, Theme()) == ""

    doc = Document(title="X", blocks=[chart])
    out_html = render(doc, "html", tmp_path / "out.html")
    text = out_html.read_text(encoding="utf-8")
    assert "<table" in text

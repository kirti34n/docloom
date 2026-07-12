"""Re-audit regressions for the dependency-free chart SVG painter:
_fmt must not collapse small-magnitude tick labels to a hard 2 decimals, and
a pie whose positive values sum to <= 0 must render as "" so html/typst fall
back to their accessible data table instead of silently dropping the data."""

from __future__ import annotations

import pytest

from docloom import Chart, Document, Series, Theme
from docloom.render import chart_svg
from docloom.render.html import to_html


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.005, "0.005"),
        (0.001, "0.001"),
        (0.015, "0.015"),
        (1234.56, "1,234.56"),
        (0.5, "0.5"),
        (-0.015, "-0.015"),
        (1000000, "1,000,000"),
    ],
)
def test_fmt_keeps_small_magnitudes_distinct(value, expected):
    # Before the fix every non-integer used f"{v:,.2f}", so 0.005/0.001/0.015
    # all collapsed to "0.01" or "0.00". Decimals now scale with magnitude.
    assert chart_svg._fmt(value) == expected


def test_pie_with_no_positive_slice_is_empty_and_html_falls_back_to_table():
    chart = Chart(
        chart="pie", title="Variance", labels=["A", "B", "C"],
        series=[Series(name="v", values=[-5.0, -3.0, -2.0])],
    )
    # all-negative: no positive slice, so nothing paintable -> "" (not a
    # title-only, markless SVG that callers would inline over their fallback).
    assert chart_svg.render_svg(chart, Theme()) == ""

    html = to_html(Document(title="Doc", blocks=[chart]), Theme())
    assert "<table>" in html  # the accessible data-table fallback fired
    for cell in ("-5", "-3", "-2"):
        assert cell in html

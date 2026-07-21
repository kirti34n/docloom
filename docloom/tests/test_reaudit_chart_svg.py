"""Re-audit regressions for the dependency-free chart SVG painter:
_fmt must not collapse small-magnitude tick labels to a hard 2 decimals, and
a pie whose positive values sum to <= 0 must render as "" so html/typst fall
back to their accessible data table instead of silently dropping the data.

Also covers the "look designed, not default" polish pass: one brand accent
for a chart's "message" series with neutral greys (not a brand-hue rainbow)
for anything past two series; a legend row suppressed for 2-3 series in
favor of direct on-chart labels; direct value labels replacing the value
axis on small column/bar charts; and gridlines collapsed to a single very
light baseline instead of one per tick.

Also covers a second re-audit round on the polish pass itself: the direct
value labels on horizontal bar charts were drawn past the right edge of the
viewBox and silently clipped (the headline number lost); series identity on
bar charts was attached only to category row 0's bars, so a data gap there
dropped a series' name with no legend fallback; and line/area end-label
margins were reserved even when the labels never fit or never drew, without
ever checking label WIDTH (only vertical separation) as part of the fit
decision.

Also covers a third re-audit round, the mirror-image of the second: bar
charts with NEGATIVE values draw their direct value labels on the LEFT of
the bar, and no left margin was ever reserved for them -- the exact same
clipping defect as the right-edge one, just on the other side, missed
because the detector was never pointed at mixed-sign data. A full sweep of
assert_all_text_within_viewbox across every chart kind with mixed-sign
data, long category/series names, and single-datapoint series then found
three more instances of the same underlying class (a margin/threshold
sized without checking the actual worst-case text extent): rotated
category labels on x-category charts (column/line/area) with many
categories could clip past the left edge (rotation lever-arm) or the
bottom edge (rotation's vertical projection past a fixed margin); a
name-prefixed bar-chart direct label could be too wide for both side
reserves to fit at once, with no fallback; and the shared legend helper
wrapped an over-long name to a fresh row but never truncated a name too
wide to fit any row at all."""

from __future__ import annotations

import math
import xml.dom.minidom as minidom

import pytest

from docloom import Chart, Document, Series, Theme
from docloom.render import chart_svg
from docloom.render.html import to_html


# --------------------------------------------------------------- geometry


def _text_extents(svg: str) -> list[tuple[float, float, float, float, str]]:
    """Parse every <text> element in `svg` and return its estimated
    (x0, y0, x1, y1, content) bounding box in SVG user-space units, using
    the SAME rough glyph-width estimator the painter itself uses for its
    own layout math (chart_svg._text_w) -- so this check is honest about
    what the painter thought it was doing, and still catches a label whose
    real x/y lands off the canvas regardless of how it got there."""
    dom = minidom.parseString(svg)
    out: list[tuple[float, float, float, float, str]] = []
    for el in dom.getElementsByTagName("text"):
        x = float(el.getAttribute("x"))
        y = float(el.getAttribute("y"))
        size = float(el.getAttribute("font-size") or 11)
        anchor = el.getAttribute("text-anchor") or "start"
        content = "".join(n.data for n in el.childNodes if n.nodeType == n.TEXT_NODE)
        if not content:
            continue
        w = chart_svg._text_w(content, size)
        if anchor == "end":
            x0, x1 = x - w, x
        elif anchor == "middle":
            x0, x1 = x - w / 2, x + w / 2
        else:
            x0, x1 = x, x + w
        y0, y1 = y - size, y + size * 0.3  # baseline +/- a rough ascent/descent

        transform = el.getAttribute("transform")
        if transform.startswith("rotate("):
            deg = float(transform[len("rotate("):].split()[0])
            theta = math.radians(deg)
            rx, ry = [], []
            for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                dx, dy = px - x, py - y
                rx.append(x + dx * math.cos(theta) - dy * math.sin(theta))
                ry.append(y + dx * math.sin(theta) + dy * math.cos(theta))
            x0, x1, y0, y1 = min(rx), max(rx), min(ry), max(ry)
        out.append((x0, y0, x1, y1, content))
    return out


def assert_all_text_within_viewbox(svg: str) -> None:
    """The reusable geometric check: every <text> element's estimated
    extent must lie inside the SVG's own viewBox. Substring-presence
    assertions ("1,234" in svg) pass even when the text is rendered
    off-canvas and clipped away by the viewer -- this is the check that
    would have caught that class of bug, so every chart-geometry test in
    this file (and any future one) should call it."""
    dom = minidom.parseString(svg)
    vb_w, vb_h = (float(v) for v in dom.documentElement.getAttribute("viewBox").split()[2:])
    tol = 1.0  # sub-pixel slack for the width estimator's roughness
    for x0, y0, x1, y1, content in _text_extents(svg):
        assert x0 >= -tol, f"text {content!r} starts left of the viewBox at x={x0:.1f}"
        assert x1 <= vb_w + tol, f"text {content!r} extends past the right edge: {x1:.1f} > {vb_w}"
        assert y1 <= vb_h + tol, f"text {content!r} extends past the bottom edge: {y1:.1f} > {vb_h}"


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


# --------------------------------------- message-series + neutral-grey palette


def test_single_series_column_uses_the_brand_accent_not_primary():
    theme = Theme(primary="#112233", accent="#445566")
    chart = Chart(chart="column", labels=["A", "B"], series=[Series(values=[1.0, 2.0])])
    svg = chart_svg.render_svg(chart, theme)
    assert "#445566" in svg  # the lone series is the message: the brand accent
    assert "#112233" not in svg  # primary is unused when there's nothing to contrast it with


def test_three_plus_series_column_is_accent_plus_grey_not_a_rainbow():
    theme = Theme(primary="#112233", accent="#445566", muted="#778899")
    chart = Chart(
        chart="column", labels=["A", "B"],
        series=[Series(name="s1", values=[1.0, 2.0]), Series(name="s2", values=[2.0, 1.0]),
                Series(name="s3", values=[3.0, 1.0])],
    )
    svg = chart_svg.render_svg(chart, theme)
    assert "#445566" in svg  # the message series (index 0) keeps the accent
    assert "#112233" not in svg  # primary is never spent on a "context" series
    minidom.parseString(svg)


# ------------------------------------------------- legend suppressed <= 3 series


def test_two_series_column_has_no_legend_row_names_ride_the_bars_instead():
    chart = Chart(
        chart="column", labels=["Q1"],
        series=[Series(name="Actual", values=[10.0]), Series(name="Target", values=[8.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    # no legend swatch row: a legend rect is 10x10 with rx="2"; the bars
    # themselves are much taller/wider and use a different rx.
    assert 'width="10" height="10" rx="2"' not in svg
    assert "Actual" in svg and "Target" in svg  # identity still reads, directly
    # identity rides a compact inline key (a small circle swatch), never
    # squeezed onto the narrow bars themselves.
    assert "<circle" in svg


def test_four_series_column_still_gets_a_legend_row():
    chart = Chart(
        chart="column", labels=["Q1"],
        series=[Series(name=n, values=[1.0]) for n in ("a", "b", "c", "d")],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert 'width="10" height="10" rx="2"' in svg


def test_two_series_column_falls_back_to_a_legend_when_names_are_too_wide():
    # 4 categories shrink the first band; names long enough that even the
    # inline key can't fit in it -- must not be forced on anyway (measure
    # first, per the skill).
    chart = Chart(
        chart="column", labels=["Q1", "Q2", "Q3", "Q4"],
        series=[Series(name="A very long descriptive series name indeed", values=[10.0, 9, 8, 7]),
                Series(name="Another quite long series name as well", values=[8.0, 7, 6, 5])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert 'width="10" height="10" rx="2"' in svg  # legend row, not a squeeze


def test_line_chart_end_labels_when_series_end_far_apart():
    chart = Chart(
        chart="line", labels=["Jan", "Feb", "Mar"],
        series=[Series(name="High", values=[1.0, 5.0, 20.0]), Series(name="Low", values=[1.0, 2.0, 3.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert 'width="10" height="10" rx="2"' not in svg  # no legend row needed
    assert "High" in svg and "Low" in svg  # identity rides the line ends instead
    assert_all_text_within_viewbox(svg)


def test_line_chart_falls_back_to_legend_when_endpoints_are_close():
    chart = Chart(
        chart="line", labels=["Jan", "Feb", "Mar"],
        series=[Series(name="Alpha", values=[10.0, 11.0, 12.1]), Series(name="Beta", values=[10.0, 10.5, 12.3])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    # colliding end-labels would be worse than no direct label at all --
    # falls back to the legend instead of stacking overlapping text.
    assert 'width="10" height="10" rx="2"' in svg
    minidom.parseString(svg)


# ------------------------------------- direct value labels replace the axis


def test_small_column_chart_labels_bars_directly_and_hides_the_value_axis():
    chart = Chart(chart="column", title="Revenue", labels=["Q1", "Q2"], series=[Series(values=[1234.0, 2500.0])])
    svg = chart_svg.render_svg(chart, Theme())
    assert "1,234" in svg and "2,500" in svg  # explicit-formatted direct labels
    # the value axis's tick numbers (e.g. a "0" or "3,000" reference label
    # off to the left) are gone now that the bars carry their own values
    assert ">0<" not in svg
    assert_all_text_within_viewbox(svg)


def test_many_category_column_chart_keeps_the_value_axis():
    # 13 marks (13 categories x 1 series) clears the <=12 direct-label
    # threshold, so this falls back to a conventional value axis.
    labels = [f"C{i}" for i in range(13)]
    chart = Chart(chart="column", labels=labels, series=[Series(values=[float(i) for i in range(13)])])
    svg = chart_svg.render_svg(chart, Theme())
    assert ">0<" in svg  # too many marks to label directly -- the axis stays
    assert_all_text_within_viewbox(svg)


# --------------------------------------------------- gridlines: one, very light


def test_column_chart_draws_exactly_one_gridline_when_the_axis_is_kept():
    labels = [f"C{i}" for i in range(13)]
    chart = Chart(chart="column", labels=labels, series=[Series(values=[float(i) for i in range(13)])])
    svg = chart_svg.render_svg(chart, Theme())
    # 5+ ticks would each have drawn their own gridline before this change;
    # now only the single baseline gridline remains.
    assert svg.count("<line") == 1


# --------------------------------------------------------- bar (horizontal)


def test_small_bar_chart_labels_bars_directly_and_hides_the_value_axis():
    chart = Chart(chart="bar", labels=["North", "South"], series=[Series(values=[1234.0, 500.0])])
    svg = chart_svg.render_svg(chart, Theme())
    assert "1,234" in svg and "500" in svg
    assert ">0<" not in svg  # the value axis's own tick numbers are gone
    assert_all_text_within_viewbox(svg)


def test_two_series_bar_chart_names_the_first_row_only():
    chart = Chart(
        chart="bar", labels=["North", "South"],
        series=[Series(name="Actual", values=[42.0, 18.0]), Series(name="Target", values=[38.0, 20.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert svg.count("Actual") == 1 and svg.count("Target") == 1  # named once, on the first row
    assert_all_text_within_viewbox(svg)


def test_bar_chart_headline_number_is_not_clipped_past_the_right_edge():
    # HIGH-1 regression: the widest bar (the headline number) reaches
    # closest to vmax, so its direct value label sits closest to the right
    # edge -- exactly the label most at risk of being drawn past the
    # viewBox and clipped away. A substring check ("1,234,567" in svg)
    # would pass even when this happens; only a geometric check catches it.
    chart = Chart(
        chart="bar", title="Headline", labels=["Region A", "Region B", "Region C"],
        series=[Series(values=[1_234_567.0, 42.0, 890.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert "1,234,567" in svg
    assert_all_text_within_viewbox(svg)


def test_bar_chart_headline_number_not_clipped_even_at_narrow_width():
    # Same defect, forced by shrinking the canvas instead of growing the
    # number -- the right margin must scale with the label, not just be a
    # fixed constant that happens to work at the default width.
    chart = Chart(
        chart="bar", labels=["A", "B"],
        series=[Series(values=[9_876_543.0, 12.0])],
    )
    svg = chart_svg.render_svg(chart, Theme(), width=220, height=160)
    assert "9,876,543" in svg
    assert_all_text_within_viewbox(svg)


def test_bar_chart_multi_series_labels_not_clipped_with_name_prefix():
    # The widest drawn label can be "Series-name 1,234,567" (name prefix +
    # value on the first-data row), not just the bare number -- the
    # right-margin reserve must account for that combined width too.
    chart = Chart(
        chart="bar", labels=["Q1", "Q2"],
        series=[
            Series(name="Actual Revenue", values=[1_500_000.0, 900.0]),
            Series(name="Target", values=[1_200_000.0, 800.0]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert_all_text_within_viewbox(svg)


def test_bar_chart_series_identity_survives_a_gap_in_the_first_category():
    # MEDIUM-2 regression: series identity used to be attached only to
    # category row 0's bars. When row 0 is a legitimate data gap (None)
    # for one or both series, the old code drew no name for that series
    # and never fell back to a legend -- zero identity anywhere.
    chart = Chart(
        chart="bar", labels=["Q1", "Q2", "Q3"],
        series=[
            Series(name="Actual", values=[None, 42.0, 18.0]),
            Series(name="Target", values=[None, 38.0, 20.0]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    # identity must show up SOMEWHERE: either directly (on each series' own
    # first real row) or via a legend fallback -- never neither.
    has_legend = 'width="10" height="10" rx="2"' in svg
    assert has_legend or ("Actual" in svg and "Target" in svg)
    assert "Actual" in svg and "Target" in svg
    assert_all_text_within_viewbox(svg)


def test_bar_chart_series_with_no_data_at_all_falls_back_to_legend():
    # A series with values that are ALL None can never get a direct label
    # (there is no row to attach it to) -- this must not silently drop its
    # identity; the chart must fall back to a real legend instead.
    chart = Chart(
        chart="bar", labels=["Q1", "Q2"],
        series=[
            Series(name="Actual", values=[10.0, 12.0]),
            Series(name="Missing", values=[None, None]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert 'width="10" height="10" rx="2"' in svg  # legend fallback fired
    assert "Missing" in svg
    assert_all_text_within_viewbox(svg)


# ------------------------------------------------------------- line/area end-labels


def test_line_chart_long_series_names_do_not_collapse_the_plot_to_a_sliver():
    # MEDIUM-3 regression: end_labels_fit used to check only vertical value
    # separation, never label WIDTH. With far-apart endpoints but very long
    # series names, the old code still tried to draw end labels and
    # reserved (24 + widest-name width) off a 640-wide canvas regardless,
    # squeezing the plot area down to almost nothing.
    chart = Chart(
        chart="line", labels=["Jan", "Feb", "Mar"],
        series=[
            Series(name="A Very Long Descriptive Series Name For The High Line", values=[1.0, 5.0, 50.0]),
            Series(name="Another Quite Long Series Name For The Low Line Too", values=[1.0, 2.0, 3.0]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    # long names must not fit as end-labels (falls back to a legend) --
    # verified geometrically: no text may be drawn in the (nonexistent)
    # sliver past a collapsed plot area, and every element stays on-canvas.
    assert 'width="10" height="10" rx="2"' in svg
    assert_all_text_within_viewbox(svg)


def test_line_chart_pays_no_right_margin_when_legend_fallback_fires():
    # The end-label right margin must only be reserved when labels are
    # actually drawn. When endpoints are too close (legend fallback), the
    # chart must not still pay the (24 + widest-name) cost for labels that
    # are never drawn -- the plot area should use close to the full width.
    chart = Chart(
        chart="line", labels=["Jan", "Feb", "Mar"],
        series=[Series(name="Alpha", values=[10.0, 11.0, 12.1]), Series(name="Beta", values=[10.0, 10.5, 12.3])],
    )
    svg = chart_svg.render_svg(chart, Theme(), width=640)
    dom = minidom.parseString(svg)
    max_path_x = 0.0
    for el in dom.getElementsByTagName("path"):
        d = el.getAttribute("d")
        xs = [float(tok) for pair in d.replace("M ", "").split(" L ") for tok in [pair.split(",")[0]]]
        max_path_x = max(max_path_x, max(xs, default=0.0))
    # The last plotted point sits at (n - 0.5)/n of the plot width (points
    # are centered in their category band), so it never reaches the literal
    # right edge -- but its position still directly reveals whether the
    # end-label margin was paid. With names "Alpha"/"Beta" and this value
    # range, reserving the old (always-on) margin would put it at ~484px;
    # not reserving it (the fix) puts it at ~526px. 505 cleanly separates
    # the two, so this is a real regression guard, not an arbitrary bound.
    assert max_path_x >= 505, (
        f"last line point at x={max_path_x:.1f} -- the end-label right "
        "margin looks like it was paid even though no end labels were drawn"
    )
    assert_all_text_within_viewbox(svg)


# ------------------------------------- bar (horizontal): negative-value labels


def test_bar_chart_negative_value_label_not_clipped_past_the_left_edge():
    # HIGH regression, mirror of the right-edge fix above: negative values
    # draw their direct label to the LEFT of the bar (see the draw loop's
    # `vx >= x0` branch), and no left margin was ever reserved for it. A
    # narrow canvas forces the negative headline number's label past x=0.
    chart = Chart(
        chart="bar", labels=["A", "B"],
        series=[Series(values=[-9_876_543.0, 12.0])],
    )
    svg = chart_svg.render_svg(chart, Theme(), width=220, height=160)
    assert "-9,876,543" in svg
    assert_all_text_within_viewbox(svg)


def test_bar_chart_mixed_sign_values_label_neither_edge_clipped():
    # The exact scenario the task describes: a bar chart with BOTH positive
    # and negative values in the same series -- both the right reserve
    # (existing) and the left reserve (this fix) must hold simultaneously.
    chart = Chart(
        chart="bar", title="Variance", labels=["North", "South", "East"],
        series=[Series(values=[1234.0, -987654.0, 42.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert "1,234" in svg and "-987,654" in svg and "42" in svg
    assert_all_text_within_viewbox(svg)


def test_bar_chart_mixed_sign_name_prefixed_labels_not_clipped_either_side():
    # A name-prefixed label ("Series-name -1,500,000") can be the widest
    # thing drawn on either side; both reserves must account for it.
    chart = Chart(
        chart="bar", labels=["Q1", "Q2"],
        series=[
            Series(name="Actual Revenue", values=[-1_500_000.0, 900.0]),
            Series(name="Target", values=[1_200_000.0, -800.0]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert_all_text_within_viewbox(svg)


def test_bar_chart_extreme_name_prefix_width_falls_back_to_legend():
    # When the combined left+right reserve for name-prefixed direct labels
    # would leave too little (or negative) plot area -- two very long
    # series names, both with large mixed-sign headline values -- the chart
    # must fall back to a real legend instead of clipping or squeezing an
    # unreadable sliver.
    long_a = "A Very Long Descriptive Category Or Series Name That Keeps Going On And On"
    long_b = long_a + "2"
    chart = Chart(
        chart="bar", labels=["Q1", "Q2"],
        series=[
            Series(name=long_a, values=[-1_500_000.0, 900.0]),
            Series(name=long_b, values=[1_200_000.0, -800.0]),
        ],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert 'width="10" height="10" rx="2"' in svg  # legend fallback fired
    assert_all_text_within_viewbox(svg)


# ------------------------------------------------- rotated category labels


def test_column_chart_many_long_categories_rotated_labels_not_clipped():
    # Rotated (-35deg), end-anchored category labels on a many-category
    # column/line/area chart are drawn closest to the left edge (first
    # band) and lowest toward the bottom edge (rotation's vertical lever
    # arm) -- both were clipped for sufficiently long category names before
    # this fix reserved margins sized to the rotation's worst case.
    long_name = "A Very Long Descriptive Category Name That Keeps Going On And On"
    chart = Chart(
        chart="column", labels=[f"{long_name}{i}" for i in range(13)],
        series=[Series(values=[float(i) - 6 for i in range(13)])],  # mixed sign too
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert_all_text_within_viewbox(svg)


# ------------------------------------------------------------------- pie


def test_pie_long_slice_labels_not_clipped_past_the_right_edge():
    # The legend column width is capped at width * 0.34, but the legend
    # text itself was never truncated to fit inside that cap -- an
    # uncapped long slice name ran straight past the right edge.
    long_name = "A Very Long Descriptive Category Name That Keeps Going On And On"
    chart = Chart(
        chart="pie", labels=[f"{long_name}{i}" for i in range(3)],
        series=[Series(values=[10.0, 20.0, 30.0])],
    )
    svg = chart_svg.render_svg(chart, Theme())
    assert_all_text_within_viewbox(svg)

    # same defect, forced by a narrow canvas instead of extreme name length
    svg_narrow = chart_svg.render_svg(chart, Theme(), width=220, height=160)
    assert_all_text_within_viewbox(svg_narrow)


# --------------------------------------------------------- shared legend


def test_legend_name_too_wide_for_any_row_is_truncated_not_clipped():
    # _draw_legend wraps an over-long name to a fresh row, but a name too
    # wide to fit even a lone item on its own row would still run past the
    # right edge every time -- it must be truncated, not just wrapped.
    long_name = "A Very Long Descriptive Category Or Series Name That Keeps Going On And On"
    chart = Chart(
        chart="column", labels=["Q1", "Q2"],
        series=[Series(name=f"{long_name}{i}", values=[10.0 * (-1) ** i, -5.0]) for i in range(4)],
    )
    svg = chart_svg.render_svg(chart, Theme(), width=260, height=180)
    assert 'width="10" height="10" rx="2"' in svg  # legend row (4 series)
    assert_all_text_within_viewbox(svg)

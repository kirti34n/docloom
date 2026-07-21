"""Dependency-free SVG chart painter: Chart IR -> a themed, self-contained
SVG string. No external libraries. Every report renderer that cannot embed a
native chart (html.py inlines it; typst.py embeds it via image()) uses this
instead of falling back to a bare data table.

Colors follow the theme: a single series is the brand accent alone (the
title already names it); two series are the two brand hues, still a
legitimate on-brand comparison; three or more collapse to one brand
accent for the "message" series plus neutral greys for the rest, instead
of cycling through ever-more-derived brand-hue tints (a rainbow). Pie
slices are all equal-weight categories rather than a message-plus-context
series, so they keep the fuller tint/shade palette. Axes, gridlines, and
tick text use theme.muted; text uses theme.font_body, set once on the
<svg> root so every label in the chart shares one typeface. None values
are gaps: bars/points are skipped and lines break rather than interpolate
across them.
"""

from __future__ import annotations

import math
from html import escape as _xml_escape

from ..ir import Chart
from ..theme import Theme, contrast_ratio, hex_to_rgb

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 380
_GAP = 3.0  # surface gap between grouped bars sharing a category band

# ---------------------------------------------------------------- palette


def _tint(color: str, f: float) -> str:
    """Lighten `color` toward white by fraction f (0..1)."""
    r, g, b = hex_to_rgb(color)
    return "#%02X%02X%02X" % tuple(round(c + (255 - c) * f) for c in (r, g, b))


def _shade(color: str, f: float) -> str:
    """Darken `color` toward black by fraction f (0..1)."""
    r, g, b = hex_to_rgb(color)
    return "#%02X%02X%02X" % tuple(round(c * (1 - f)) for c in (r, g, b))


def _categorical_palette(theme: Theme) -> list[str]:
    """On-brand categorical colors derived from primary/accent, for charts
    where every slot is its own identity with equal weight (pie slices --
    unlike a multi-series chart there is no single "message" slice, so the
    fuller tint/shade spread stays)."""
    return [
        theme.primary, theme.accent,
        _tint(theme.primary, 0.42), _shade(theme.accent, 0.28),
        _tint(theme.accent, 0.5), _shade(theme.primary, 0.28),
    ]


def _series_palette(theme: Theme, n: int) -> list[str]:
    """Series colors for column/bar/line/area/scatter. One series is the
    brand accent alone. Two are the two brand hues -- still a legitimate
    on-brand comparison, not a rainbow. Three or more collapse to one
    brand accent for the "message" series (index 0) plus neutral greys
    for the rest, so a wider series count reads as message-plus-context
    instead of cycling through more brand-hue tints."""
    if n <= 1:
        return [theme.accent]
    if n == 2:
        return [theme.primary, theme.accent]
    return [
        theme.accent,
        _tint(theme.muted, 0.35), _shade(theme.muted, 0.2),
        _tint(theme.muted, 0.6), _shade(theme.muted, 0.45), _tint(theme.muted, 0.15),
    ]


def _label_ink(fill: str, theme: Theme) -> str:
    """theme.background or theme.text, whichever contrasts better against a
    colored fill (for a label drawn inside a mark, e.g. a pie slice)."""
    bg_c = contrast_ratio(fill, theme.background)
    text_c = contrast_ratio(fill, theme.text)
    return theme.background if bg_c >= text_c else theme.text


# ----------------------------------------------------------------- numbers


def _finite(v: float | None) -> float | None:
    """None (a gap) for missing or non-finite (NaN/Infinity) values, so
    adversarial data degrades to a gap instead of corrupting layout math."""
    return v if v is not None and math.isfinite(v) else None


_MAX_MAGNITUDE = 1e300


def _plottable(v: float | None) -> float | None:
    """Like _finite but also gaps finite-but-astronomical magnitudes whose
    tick/coordinate math overflows float; realistic data is never affected."""
    f = _finite(v)
    return f if f is not None and abs(f) <= _MAX_MAGNITUDE else None


def _nice_num(x: float, round_: bool) -> float:
    if x <= 0 or not math.isfinite(x):
        return 1.0
    exp = math.floor(math.log10(x))
    frac = x / (10**exp)
    if round_:
        nf = 1 if frac < 1.5 else 2 if frac < 3 else 5 if frac < 7 else 10
    else:
        nf = 1 if frac <= 1 else 2 if frac <= 2 else 5 if frac <= 5 else 10
    return nf * (10**exp)


def _ticks(vmin: float, vmax: float, count: int = 5) -> list[float]:
    """"Nice" round tick values spanning at least [vmin, vmax] (Heckbert),
    robust to degenerate (equal) or non-finite bounds."""
    if not math.isfinite(vmin) or not math.isfinite(vmax):
        vmin, vmax = 0.0, 1.0
    elif vmin == vmax:
        # a flat series or a single point: build a real span around the value
        # (with the baseline included) instead of collapsing to [0, 1], which
        # would map the actual value far outside the plot.
        vmin, vmax = (0.0, 1.0) if vmin == 0 else (min(0.0, vmin), max(0.0, vmax))
    if vmin > vmax:
        vmin, vmax = vmax, vmin
    span = _nice_num(vmax - vmin, False)
    step = _nice_num(span / max(count - 1, 1), True) or 1.0
    lo = math.floor(vmin / step) * step
    hi = math.ceil(vmax / step) * step
    n = min(int(round((hi - lo) / step)), 60)  # hard cap: never a runaway tick count
    ticks = [round(lo + i * step, 10) for i in range(n + 1)]
    return [t for t in ticks if math.isfinite(t)] or [0.0, 1.0]


def _fmt(v: float) -> str:
    if v == int(v):
        return f"{int(v):,}"
    d = 2
    if 0 < abs(v) < 1:  # small fractionals need more places or they collapse
        d = 2 - math.floor(math.log10(abs(v)))
    return f"{v:,.{d}f}".rstrip("0").rstrip(".")


def _text_w(s: str, size: float) -> float:
    """A rough glyph-width estimate for layout only; no font metrics are
    available without a dependency."""
    return 0.55 * size * len(s)


def _truncate(s: str, max_w: float, size: float) -> str:
    if not s or _text_w(s, size) <= max_w:
        return s
    for n in range(len(s) - 1, 0, -1):
        cut = s[:n].rstrip() + "…"
        if _text_w(cut, size) <= max_w:
            return cut
    return s[:1] + "…"


def _runs(points: list[tuple[float, float] | None]) -> list[list[tuple[float, float]]]:
    """Split a value sequence at None into runs of consecutive defined
    points, so a line/area is drawn in pieces instead of bridging a gap."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for p in points:
        if p is None:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(p)
    if current:
        runs.append(current)
    return runs


def _band_marks(band: float, n: int, max_w: float = 22.0) -> tuple[float, float]:
    """Mark thickness and offset for `n` grouped bars sharing a category
    band: capped thickness (never fills the slot), a surface gap between
    marks, centered in the band. Marks fill about half the band -- bars
    read as distinct columns with real air between categories, never a
    wall-to-wall slab or a hairline."""
    if n <= 0:
        return 0.0, 0.0
    usable = band * 0.5
    w = min(max_w, max(1.5, (usable - _GAP * (n - 1)) / n))
    group = w * n + _GAP * (n - 1)
    return w, (band - group) / 2


# --------------------------------------------------------------- primitives


def _esc(s: str) -> str:
    return _xml_escape(s, quote=True)


def _text(
    x: float, y: float, s: str, *, size: float = 11, fill: str = "#000",
    anchor: str = "start", weight: str = "400", rotate: float | None = None,
) -> str:
    if not s:
        return ""
    t = f'transform="rotate({rotate} {x:.1f} {y:.1f})" ' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" {t}font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>'
    )


def _draw_legend(
    parts: list[str], items: list[tuple[str, str]], theme: Theme, width: int, y: float
) -> float:
    """A wrapping row of swatch+name pairs; returns the y offset below it."""
    size = 10.5
    gap = 18.0
    x = 16.0
    row_y = y + 10
    # Wrapping to a fresh row only helps a name that's too wide for the
    # REMAINING space on the current row -- a name too wide for a whole
    # fresh row would still run past the right edge every time. Cap each
    # name to what a lone item on its own row could ever fit.
    max_item_w = max(20.0, width - 16 - 16 - 14)
    for name, color in items:
        name = _truncate(name, max_item_w, size)
        w = 14 + _text_w(name, size) + gap
        if x + w > width - 16 and x > 16.0:
            x = 16.0
            row_y += 18
        parts.append(f'<rect x="{x:.1f}" y="{row_y - 9:.1f}" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(_text(x + 14, row_y, name, size=size, fill=theme.text, anchor="start"))
        x += w
    return row_y + 14


def _wedge(cx: float, cy: float, r: float, a0: float, a1: float, fill: str, stroke: str) -> str:
    if a1 - a0 >= 359.999:  # a single 100%-share slice: an arc can't sweep a full circle
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    a0r, a1r = math.radians(a0), math.radians(a1)
    x0, y0 = cx + r * math.cos(a0r), cy + r * math.sin(a0r)
    x1, y1 = cx + r * math.cos(a1r), cy + r * math.sin(a1r)
    large = 1 if (a1 - a0) > 180 else 0
    d = f"M {cx:.1f},{cy:.1f} L {x0:.1f},{y0:.1f} A {r:.1f},{r:.1f} 0 {large} 1 {x1:.1f},{y1:.1f} Z"
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'


# -------------------------------------------------------------- chart kinds


def _has_data(chart: Chart) -> bool:
    return any(_plottable(v) is not None for s in chart.series for v in s.values)


def _x_category_chart(chart: Chart, theme: Theme, width: int, height: int, kind: str) -> str:
    """column / line / area: categories on X, values on Y."""
    n = max(len(chart.labels), max((len(s.values) for s in chart.series), default=0))
    labels = (list(chart.labels) + [""] * n)[:n]
    series_vals = [([_plottable(v) for v in s.values] + [None] * n)[:n] for s in chart.series]
    n_series = len(chart.series)
    palette = _series_palette(theme, n_series)
    parts: list[str] = []
    names = [s.name or f"Series {i + 1}" for i, s in enumerate(chart.series)]

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28

    all_vals = [v for vals in series_vals for v in vals if v is not None]
    if kind == "line":
        vmin, vmax = min(all_vals), max(all_vals)
    else:  # column/area bars grow from a baseline: it must be in range
        vmin, vmax = min(0.0, min(all_vals)), max(0.0, max(all_vals))
    ticks = _ticks(vmin, vmax)
    vmin, vmax = ticks[0], ticks[-1]
    val_labels = [_fmt(t) for t in ticks]

    # Columns with few enough marks label every bar directly and drop the
    # value axis entirely -- the numbers live on the bars instead of off to
    # the side. Lines/areas keep the axis (only their endpoint gets a
    # direct label, never every point), per direct-label convention.
    direct_values = kind == "column" and n * n_series <= 12
    wants_end_labels = kind in ("line", "area") and 1 < n_series <= 3

    many_cats = n > 7
    # Rotated (-35deg), end-anchored category labels swing their far corner
    # downward by roughly 0.574 * text-width (the rotation's vertical lever
    # arm) beyond their own font size -- with the max truncate width (70)
    # that dip alone is ~40px, well past a plain, non-rotated label's
    # height. 60 keeps the worst case (a full-width truncated label) inside
    # the viewBox instead of clipping its lowest corner off the bottom edge.
    bottom = 60 if many_cats else 32
    left = 16.0 if direct_values else min(
        max(34.0, 16 + max((_text_w(s, 10.5) for s in val_labels), default=0)), width * 0.42
    )
    if many_cats:
        # The first category's rotated (-35deg), end-anchored label is drawn
        # closest to the left edge; even truncated to its max width (70),
        # the rotation's lever arm can still swing part of the glyph run
        # past x=0 if the first band's center sits too close to it. Reserve
        # a floor wide enough to absorb that worst case regardless of how
        # tight the value-axis margin would otherwise be.
        left = max(left, 66.0)
    plot_x0 = left

    # End-label fit requires BOTH enough vertical separation between the
    # series' last points (so the text doesn't overlap) AND enough
    # horizontal room for the label text itself -- a long series name must
    # not collapse the plot to a sliver just because its two endpoints
    # happen to be far apart in value.
    end_labels_fit = False
    widest = 0.0
    if wants_end_labels:
        lasts = []
        for vals in series_vals:
            real = [v for v in vals if v is not None]
            if real:
                lasts.append(real[-1])
        span = (vmax - vmin) or 1.0
        vertical_ok = len(lasts) < 2 or all(
            (b - a) / span >= 0.09 for a, b in zip(sorted(lasts), sorted(lasts)[1:])
        )
        widest = max((_text_w(f"{nm} {val_labels[-1]}", 9.5) for nm in names), default=0)
        width_ok = (24.0 + widest) <= width * 0.4
        end_labels_fit = vertical_ok and width_ok
    label_series = wants_end_labels and end_labels_fit

    # The end-label margin is only worth paying when the labels are
    # actually going to be drawn: a chart that falls back to a legend
    # (label_series False) must not still pay the full right-margin cost
    # for direct labels it never draws.
    right_reserve = 16.0
    if label_series:
        right_reserve = 24.0 + widest
    plot_x1 = max(width - right_reserve, plot_x0 + 20)
    band_w = (plot_x1 - plot_x0) / n

    # A legend row is only worth its space past 3 series; at 2-3, each
    # series is named directly on the chart instead -- a single-line,
    # left-anchored swatch+name key sitting right above the first bar
    # group (columns), or the series' own line-end (line/area) -- but only
    # where that actually fits without colliding. Measure first: a name
    # row too wide for the first band, or two close-together line
    # endpoints, fall back to a real legend rather than an unreadable mess.
    bar_w = offset = 0.0
    identity_inline = False
    if kind == "column":
        bar_w, offset = _band_marks(band_w, n_series)
        if direct_values and 1 < n_series <= 3:
            row_w = sum(16 + _text_w(nm, 10) + 14 for nm in names)
            identity_inline = row_w <= band_w
    show_legend = n_series > 3 or (
        1 < n_series <= 3
        and (
            (kind == "column" and not identity_inline)
            or (wants_end_labels and not end_labels_fit)
        )
    )
    if show_legend:
        legend = [(names[i], palette[i % len(palette)]) for i in range(n_series)]
        top = _draw_legend(parts, legend, theme, width, top)
    elif identity_inline:
        iy = top + 12
        ix = plot_x0
        for i, nm in enumerate(names):
            color = palette[i % len(palette)]
            parts.append(f'<circle cx="{ix + 5:.1f}" cy="{iy - 3.5:.1f}" r="4.5" fill="{color}"/>')
            parts.append(_text(ix + 14, iy, nm, size=10, fill=theme.text, anchor="start"))
            ix += 16 + _text_w(nm, 10) + 14
        top += 18

    plot_y0 = top + (14 if direct_values else 6)
    plot_y1 = max(height - bottom, plot_y0 + 20)

    def yv(v: float) -> float:
        frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return plot_y1 - frac * (plot_y1 - plot_y0)

    if direct_values:
        pass  # no gridlines, no tick numbers -- values ride the bars instead
    else:
        # gridlines off except a single very light baseline -- the tick
        # numbers stay (they carry the values that aren't directly labeled).
        baseline_t = 0.0 if vmin <= 0 <= vmax else ticks[0]
        for t in ticks:
            ty = yv(t)
            if t == baseline_t:
                parts.append(
                    f'<line x1="{plot_x0:.1f}" y1="{ty:.1f}" x2="{plot_x1:.1f}" y2="{ty:.1f}" '
                    f'stroke="{theme.muted}" stroke-opacity="0.35" stroke-width="1"/>'
                )
            parts.append(_text(plot_x0 - 8, ty + 3.5, _fmt(t), size=10, fill=theme.muted, anchor="end"))

    for i in range(n):
        lbl = labels[i]
        if not lbl:
            continue
        lx = plot_x0 + (i + 0.5) * band_w
        if many_cats:
            parts.append(
                _text(lx, plot_y1 + 14, _truncate(lbl, 70, 10), size=10, fill=theme.muted,
                      anchor="end", rotate=-35)
            )
        else:
            parts.append(
                _text(lx, plot_y1 + 16, _truncate(lbl, band_w, 10), size=10, fill=theme.muted, anchor="middle")
            )

    y0 = yv(0.0) if vmin <= 0 <= vmax else plot_y1

    if kind == "column":
        for i in range(n):
            for s_i, vals in enumerate(series_vals):
                v = vals[i]
                if v is None:
                    continue
                bx = plot_x0 + i * band_w + offset + s_i * (bar_w + _GAP)
                vy = yv(v)
                by, h = min(vy, y0), abs(vy - y0)
                rx = min(2.0, bar_w / 2, h / 2) if h > 0 else 0.0
                parts.append(
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                    f'rx="{rx:.1f}" fill="{palette[s_i % len(palette)]}"/>'
                )
                if direct_values:
                    # identity (if any) already rode the inline key above;
                    # the bar itself only needs its own explicit-formatted
                    # value, never squeezed alongside a name.
                    label_y = by - 4 if vy <= y0 else by + h + 12
                    parts.append(
                        _text(bx + bar_w / 2, label_y, _fmt(v), size=9.5, fill=theme.text, anchor="middle")
                    )
    else:  # line / area
        show_dots = n <= 24
        for s_i, vals in enumerate(series_vals):
            color = palette[s_i % len(palette)]
            pts = [None if v is None else (plot_x0 + (i + 0.5) * band_w, yv(v)) for i, v in enumerate(vals)]
            runs = _runs(pts)
            for run in runs:
                if kind == "area":
                    poly = run + [(run[-1][0], y0), (run[0][0], y0)]
                    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in poly) + " Z"
                    parts.append(f'<path d="{d}" fill="{color}" fill-opacity="0.16" stroke="none"/>')
                if len(run) >= 2:
                    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in run)
                    parts.append(
                        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.25" '
                        f'stroke-linejoin="round" stroke-linecap="round"/>'
                    )
                if show_dots or len(run) == 1:
                    for x, y in run:
                        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}"/>')
            if label_series and runs:
                # identity + the endpoint value ride the line itself
                # instead of a legend row -- text stays in the text token,
                # never the series color, per the ink-vs-mark rule.
                last_x, last_y = runs[-1][-1]
                name = names[s_i]
                last_v = next(v for v in reversed(vals) if v is not None)
                parts.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="{color}"/>')
                parts.append(
                    _text(last_x + 8, last_y + 3.5, f"{name} {_fmt(last_v)}", size=9.5,
                          fill=theme.text, anchor="start")
                )

    return "".join(parts)


def _bar_chart(chart: Chart, theme: Theme, width: int, height: int) -> str:
    """bar: categories on Y (top to bottom), values on X - suits long or
    many category names better than a column chart."""
    n = max(len(chart.labels), max((len(s.values) for s in chart.series), default=0))
    labels = (list(chart.labels) + [""] * n)[:n]
    series_vals = [([_plottable(v) for v in s.values] + [None] * n)[:n] for s in chart.series]
    n_series = len(chart.series)
    palette = _series_palette(theme, n_series)
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28

    all_vals = [v for vals in series_vals for v in vals if v is not None]
    vmin, vmax = min(0.0, min(all_vals)), max(0.0, max(all_vals))
    ticks = _ticks(vmin, vmax)
    vmin, vmax = ticks[0], ticks[-1]

    # Few enough marks: label every bar directly and drop the value axis --
    # the numbers live at the bar ends instead of off to the side.
    direct_values = n * n_series <= 12

    # Series identity, when direct-labelled, rides the bar at each series'
    # OWN first row that actually has data -- not category row 0 -- so a
    # legitimate data gap in the first category never silently drops a
    # series' name. A series with no data anywhere gets no direct label.
    name_row: dict[int, int] = {}
    if direct_values and 1 < n_series <= 3:
        for s_i, vals in enumerate(series_vals):
            first_i = next((i for i, v in enumerate(vals) if v is not None), None)
            if first_i is not None:
                name_row[s_i] = first_i

    # See _x_category_chart: a legend row only earns its space past 3
    # series; at 2-3 the series name rides its first-data bar instead --
    # but only when direct labelling actually happened for every series
    # (fewer than n_series names would mean silent, partial identity).
    show_legend = n_series > 3 or (
        1 < n_series <= 3 and (not direct_values or len(name_row) < n_series)
    )

    left = min(
        max(40.0, 16 + max((_text_w(_truncate(lbl, 150, 10.5), 10.5) for lbl in labels), default=0)),
        width * 0.5,
    )

    def _side_reserves(with_names: bool) -> tuple[float, float]:
        """Widest direct-label text on each side (right for v>=0, left for
        v<0), optionally with the series-name prefix included."""
        right_texts: list[str] = []
        left_texts: list[str] = []
        for s_i, vals in enumerate(series_vals):
            for i, v in enumerate(vals):
                if v is None:
                    continue
                text = _fmt(v)
                if with_names and name_row.get(s_i) == i:
                    name = chart.series[s_i].name or f"Series {s_i + 1}"
                    text = f"{name} {text}"
                (right_texts if v >= 0 else left_texts).append(text)
        widest_right = max((_text_w(t, 9.5) for t in right_texts), default=0)
        widest_left = max((_text_w(t, 9.5) for t in left_texts), default=0)
        return widest_right, widest_left

    # Direct value labels are drawn past the end of each bar -- positive
    # values to the right, negative values to the left (see the draw loop
    # below, `vx >= x0`). Without a margin sized to the widest label on
    # EACH side, a headline number runs past that edge of the viewBox and
    # is silently clipped away -- positive bars off the right, negative
    # bars off the left, symmetric defects.
    right_reserve = 16.0
    left_reserve = 0.0
    if direct_values:
        widest_right, widest_left = _side_reserves(not show_legend)
        # A name-prefixed label ("Series-name 1,200,000") can be wide enough
        # that the two side reserves together leave too little (or negative)
        # plot area -- the same "measure the actual width, not just whether
        # a slot was assigned" rule _x_category_chart's end-labels already
        # follow. When that happens, fall back to a legend instead of
        # squeezing an unreadable sliver or clipping the label outright.
        if not show_legend:
            tentative_right = 16.0 + widest_right + 8.0
            tentative_left = (widest_left + 8.0) if widest_left else 0.0
            if width - tentative_right - (left + tentative_left) < max(20.0, width * 0.15):
                show_legend = True
                widest_right, widest_left = _side_reserves(False)
        right_reserve = 16.0 + widest_right + 8.0
        if widest_left:
            left_reserve = widest_left + 8.0

    if show_legend:
        legend = [(s.name or f"Series {i + 1}", palette[i % len(palette)]) for i, s in enumerate(chart.series)]
        top = _draw_legend(parts, legend, theme, width, top)

    plot_x0, plot_y0 = left + left_reserve, top + 6
    plot_x1 = max(width - right_reserve, plot_x0 + 20)
    plot_y1 = max(height - (16.0 if direct_values else 30.0), plot_y0 + 20)

    def xv(v: float) -> float:
        frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return plot_x0 + frac * (plot_x1 - plot_x0)

    if not direct_values:
        # gridlines off except a single very light baseline -- the tick
        # numbers stay (they carry the values that aren't directly labeled).
        baseline_t = 0.0 if vmin <= 0 <= vmax else ticks[0]
        for t in ticks:
            tx = xv(t)
            if t == baseline_t:
                parts.append(
                    f'<line x1="{tx:.1f}" y1="{plot_y0:.1f}" x2="{tx:.1f}" y2="{plot_y1:.1f}" '
                    f'stroke="{theme.muted}" stroke-opacity="0.35" stroke-width="1"/>'
                )
            parts.append(_text(tx, plot_y1 + 16, _fmt(t), size=10, fill=theme.muted, anchor="middle"))

    x0 = xv(0.0) if vmin <= 0 <= vmax else plot_x0
    band_h = (plot_y1 - plot_y0) / n
    bar_h, offset = _band_marks(band_h, n_series, max_w=20.0)
    for i in range(n):
        lbl = labels[i]
        if lbl:
            ly = plot_y0 + (i + 0.5) * band_h
            parts.append(
                _text(plot_x0 - 8, ly + 3.5, _truncate(lbl, left - 24, 10.5), size=10.5,
                      fill=theme.muted, anchor="end")
            )
        for s_i, vals in enumerate(series_vals):
            v = vals[i]
            if v is None:
                continue
            by = plot_y0 + i * band_h + offset + s_i * (bar_h + _GAP)
            vx = xv(v)
            bx, w = min(vx, x0), abs(vx - x0)
            rx = min(2.0, bar_h / 2, w / 2) if w > 0 else 0.0
            parts.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{w:.1f}" height="{bar_h:.1f}" '
                f'rx="{rx:.1f}" fill="{palette[s_i % len(palette)]}"/>'
            )
            if direct_values:
                text = _fmt(v)
                if not show_legend and name_row.get(s_i) == i:
                    # identity rides this series' own first-data bar
                    # instead of a legend row: name the series once, here.
                    name = chart.series[s_i].name or f"Series {s_i + 1}"
                    text = f"{name} {text}"
                label_x = bx + w + 4 if vx >= x0 else bx - 4
                anchor = "start" if vx >= x0 else "end"
                parts.append(
                    _text(label_x, by + bar_h / 2 + 3.5, text, size=9.5, fill=theme.text, anchor=anchor)
                )
    return "".join(parts)


def _scatter(chart: Chart, theme: Theme, width: int, height: int) -> str:
    """Points connected in data order, matching the PPTX XY_SCATTER_LINES
    native chart type. Labels are used as numeric X when every one parses;
    otherwise each series falls back to its own point index as X."""
    palette = _series_palette(theme, len(chart.series))
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28
    if len(chart.series) > 1:
        legend = [(s.name or f"Series {i + 1}", palette[i % len(palette)]) for i, s in enumerate(chart.series)]
        top = _draw_legend(parts, legend, theme, width, top)

    # labels-as-X only if they cover every series: zip() below stops at the
    # shorter of the two, so shorter labels would silently drop trailing
    # values instead of just losing their x-position.
    max_series_len = max((len(s.values) for s in chart.series), default=0)
    xs_numeric: list[float] | None = None
    if chart.labels and len(chart.labels) >= max_series_len:
        try:
            parsed = [float(lb) for lb in chart.labels]
            # reject "inf"/"nan" (which float() accepts): a non-finite x would
            # emit cx="inf" and collapse the whole scale. Also reject extreme
            # magnitudes, whose x-scale math overflows float. Fall back to
            # indices in either case.
            xs_numeric = parsed if all(
                math.isfinite(x) and abs(x) <= _MAX_MAGNITUDE for x in parsed
            ) else None
        except ValueError:
            xs_numeric = None

    series_points: list[list[tuple[float, float]]] = []
    for s in chart.series:
        pts: list[tuple[float, float]] = []
        if xs_numeric is not None:
            for x, raw in zip(xs_numeric, s.values):
                v = _plottable(raw)
                if v is not None:
                    pts.append((x, v))
        else:
            for i, raw in enumerate(s.values):
                v = _plottable(raw)
                if v is not None:
                    pts.append((float(i), v))
        series_points.append(pts)

    all_x = [x for pts in series_points for x, _ in pts]
    all_y = [y for pts in series_points for _, y in pts]
    if not all_x:  # _has_data() already gated this; defensive only
        return "".join(parts)
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    xpad = (xmax - xmin) * 0.08 or 1.0
    ypad = (ymax - ymin) * 0.08 or 1.0
    x_ticks = _ticks(xmin - xpad, xmax + xpad)
    y_ticks = _ticks(ymin - ypad, ymax + ypad)
    xmin, xmax = x_ticks[0], x_ticks[-1]
    ymin, ymax = y_ticks[0], y_ticks[-1]

    y_labels = [_fmt(t) for t in y_ticks]
    left = min(
        max(34.0, 16 + max((_text_w(s, 10.5) for s in y_labels), default=0)), width * 0.42
    )
    plot_x0, plot_y0 = left, top + 6
    plot_x1 = max(width - 16.0, plot_x0 + 20)
    plot_y1 = max(height - 34.0, plot_y0 + 20)

    def xpix(x: float) -> float:
        frac = (x - xmin) / (xmax - xmin) if xmax > xmin else 0.5
        return plot_x0 + frac * (plot_x1 - plot_x0)

    def ypix(y: float) -> float:
        frac = (y - ymin) / (ymax - ymin) if ymax > ymin else 0.5
        return plot_y1 - frac * (plot_y1 - plot_y0)

    # gridlines off except a single very light baseline -- the tick numbers
    # stay (scatter has no direct-label substitute for either axis).
    y_baseline_t = 0.0 if ymin <= 0 <= ymax else y_ticks[0]
    for t in y_ticks:
        ty = ypix(t)
        if t == y_baseline_t:
            parts.append(
                f'<line x1="{plot_x0:.1f}" y1="{ty:.1f}" x2="{plot_x1:.1f}" y2="{ty:.1f}" '
                f'stroke="{theme.muted}" stroke-opacity="0.35" stroke-width="1"/>'
            )
        parts.append(_text(plot_x0 - 8, ty + 3.5, _fmt(t), size=10, fill=theme.muted, anchor="end"))
    for t in x_ticks:
        tx = xpix(t)
        parts.append(_text(tx, plot_y1 + 16, _fmt(t), size=10, fill=theme.muted, anchor="middle"))

    for s_i, pts in enumerate(series_points):
        color = palette[s_i % len(palette)]
        px = [(xpix(x), ypix(y)) for x, y in pts]
        if len(px) >= 2:
            d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in px)
            parts.append(
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.75" stroke-opacity="0.6" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )
        for x, y in px:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')

    return "".join(parts)


def _pie(chart: Chart, theme: Theme, width: int, height: int) -> str:
    """Only the first series is plotted (a pie can only show one), matching
    pptx.py's native-chart constraint."""
    palette = _categorical_palette(theme)
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28

    values = [_plottable(v) for v in (chart.series[0].values if chart.series else [])]
    n = max(len(chart.labels), len(values))
    labels = (list(chart.labels) + [""] * n)[:n]
    values = (values + [None] * n)[:n]
    slices = [(labels[i], values[i]) for i in range(n) if values[i] and values[i] > 0]
    total = sum(v for _, v in slices)
    if total <= 0:  # no positive slice: return empty so callers fall back to a table
        return ""

    legend_items = [(lbl or f"Slice {i + 1}", palette[i % len(palette)]) for i, (lbl, _) in enumerate(slices)]
    legend_w = min(width * 0.34, 40 + max((_text_w(t, 10.5) for t, _ in legend_items), default=0))
    cx = max(60.0, (width - legend_w) / 2)
    cy = top + (height - top) / 2
    r = max(24.0, min((width - legend_w) / 2, height - top) / 2 - 18)

    angle = -90.0  # start at 12 o'clock
    for i, (_, v) in enumerate(slices):
        frac = v / total
        sweep = frac * 360.0
        color = palette[i % len(palette)]
        parts.append(_wedge(cx, cy, r, angle, angle + sweep, color, theme.background))
        if frac >= 0.045:  # skip labels on slivers too thin to hold text
            mid = math.radians(angle + sweep / 2)
            lx, ly = cx + math.cos(mid) * r * 0.62, cy + math.sin(mid) * r * 0.62
            ink = _label_ink(color, theme)
            parts.append(_text(lx, ly + 4, f"{round(frac * 100)}%", size=10.5, weight="600", fill=ink, anchor="middle"))
        angle += sweep

    lx0 = cx + r + 28
    ly = cy - (len(legend_items) - 1) * 9
    # legend_w is capped at width * 0.34 regardless of how wide the longest
    # name actually is; without truncating to the space that's really left
    # between the swatch and the right edge of the viewBox, an uncapped
    # long label runs straight past it.
    avail = max(20.0, width - (lx0 + 11) - 8.0)
    for name, color in legend_items:
        parts.append(f'<circle cx="{lx0:.1f}" cy="{ly - 3.5:.1f}" r="4.5" fill="{color}"/>')
        parts.append(_text(lx0 + 11, ly + 1, _truncate(name, avail, 10.5), size=10.5, fill=theme.text, anchor="start"))
        ly += 18
    return "".join(parts)


# ------------------------------------------------------------------- public


def render_svg(
    chart: Chart, theme: Theme, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT
) -> str:
    """Render `chart` to a themed, self-contained SVG string ("" if it has
    no series values to plot)."""
    if not _has_data(chart):
        return ""
    if chart.chart == "pie":
        body = _pie(chart, theme, width, height)
    elif chart.chart == "scatter":
        body = _scatter(chart, theme, width, height)
    elif chart.chart == "bar":
        body = _bar_chart(chart, theme, width, height)
    else:  # column / line / area
        body = _x_category_chart(chart, theme, width, height, chart.chart)
    if not body:  # only a no-positive-slice pie: let callers use a data table
        return ""
    title = _esc(chart.title or "chart")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{title}" '
        f'class="docloom-chart" font-family="{_esc(theme.font_body)}">'
        f'<rect width="100%" height="100%" fill="{theme.background}"/>'
        f"<title>{title}</title>"
        f"{body}</svg>"
    )

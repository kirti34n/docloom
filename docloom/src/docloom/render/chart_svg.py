"""Dependency-free SVG chart painter: Chart IR -> a themed, self-contained
SVG string. No external libraries. Every report renderer that cannot embed a
native chart (html.py inlines it; typst.py embeds it via image()) uses this
instead of falling back to a bare data table.

Colors follow the theme: series use theme.primary/theme.accent (extended
with tint/shade steps for extra series, mirroring pptx.py's on-brand
palette); axes, gridlines, and tick text use theme.muted; text uses
theme.font_body. None values are gaps: bars/points are skipped and lines
break rather than interpolate across them.
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


def _palette(theme: Theme) -> list[str]:
    """On-brand categorical colors derived from primary/accent (mirrors
    pptx.py's _series_palette so native and painted charts agree)."""
    return [
        theme.primary, theme.accent,
        _tint(theme.primary, 0.42), _shade(theme.accent, 0.28),
        _tint(theme.accent, 0.5), _shade(theme.primary, 0.28),
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
    marks, centered in the band."""
    if n <= 0:
        return 0.0, 0.0
    usable = band * 0.74
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
    for name, color in items:
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
    return any(_finite(v) is not None for s in chart.series for v in s.values)


def _x_category_chart(chart: Chart, theme: Theme, width: int, height: int, kind: str) -> str:
    """column / line / area: categories on X, values on Y."""
    n = max(len(chart.labels), max((len(s.values) for s in chart.series), default=0))
    labels = (list(chart.labels) + [""] * n)[:n]
    series_vals = [([_finite(v) for v in s.values] + [None] * n)[:n] for s in chart.series]
    palette = _palette(theme)
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28
    if len(chart.series) > 1:
        legend = [(s.name or f"Series {i + 1}", palette[i % len(palette)]) for i, s in enumerate(chart.series)]
        top = _draw_legend(parts, legend, theme, width, top)

    all_vals = [v for vals in series_vals for v in vals if v is not None]
    if kind == "line":
        vmin, vmax = min(all_vals), max(all_vals)
    else:  # column/area bars grow from a baseline: it must be in range
        vmin, vmax = min(0.0, min(all_vals)), max(0.0, max(all_vals))
    ticks = _ticks(vmin, vmax)
    vmin, vmax = ticks[0], ticks[-1]
    val_labels = [_fmt(t) for t in ticks]

    many_cats = n > 7
    bottom = 52 if many_cats else 32
    left = min(
        max(34.0, 16 + max((_text_w(s, 10.5) for s in val_labels), default=0)), width * 0.42
    )
    plot_x0, plot_y0 = left, top + 6
    plot_x1 = max(width - 16.0, plot_x0 + 20)
    plot_y1 = max(height - bottom, plot_y0 + 20)

    def yv(v: float) -> float:
        frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return plot_y1 - frac * (plot_y1 - plot_y0)

    for t in ticks:
        ty = yv(t)
        parts.append(
            f'<line x1="{plot_x0:.1f}" y1="{ty:.1f}" x2="{plot_x1:.1f}" y2="{ty:.1f}" '
            f'stroke="{theme.muted}" stroke-opacity="{0.55 if t == 0 else 0.2}" stroke-width="1"/>'
        )
        parts.append(_text(plot_x0 - 8, ty + 3.5, _fmt(t), size=10, fill=theme.muted, anchor="end"))

    band_w = (plot_x1 - plot_x0) / n
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
        bar_w, offset = _band_marks(band_w, len(chart.series))
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
    else:  # line / area
        show_dots = n <= 24
        for s_i, vals in enumerate(series_vals):
            color = palette[s_i % len(palette)]
            pts = [None if v is None else (plot_x0 + (i + 0.5) * band_w, yv(v)) for i, v in enumerate(vals)]
            for run in _runs(pts):
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

    return "".join(parts)


def _bar_chart(chart: Chart, theme: Theme, width: int, height: int) -> str:
    """bar: categories on Y (top to bottom), values on X - suits long or
    many category names better than a column chart."""
    n = max(len(chart.labels), max((len(s.values) for s in chart.series), default=0))
    labels = (list(chart.labels) + [""] * n)[:n]
    series_vals = [([_finite(v) for v in s.values] + [None] * n)[:n] for s in chart.series]
    palette = _palette(theme)
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28
    if len(chart.series) > 1:
        legend = [(s.name or f"Series {i + 1}", palette[i % len(palette)]) for i, s in enumerate(chart.series)]
        top = _draw_legend(parts, legend, theme, width, top)

    all_vals = [v for vals in series_vals for v in vals if v is not None]
    vmin, vmax = min(0.0, min(all_vals)), max(0.0, max(all_vals))
    ticks = _ticks(vmin, vmax)
    vmin, vmax = ticks[0], ticks[-1]

    left = min(
        max(40.0, 16 + max((_text_w(_truncate(lbl, 150, 10.5), 10.5) for lbl in labels), default=0)),
        width * 0.5,
    )
    plot_x0, plot_y0 = left, top + 6
    plot_x1 = max(width - 16.0, plot_x0 + 20)
    plot_y1 = max(height - 30.0, plot_y0 + 20)

    def xv(v: float) -> float:
        frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return plot_x0 + frac * (plot_x1 - plot_x0)

    for t in ticks:
        tx = xv(t)
        parts.append(
            f'<line x1="{tx:.1f}" y1="{plot_y0:.1f}" x2="{tx:.1f}" y2="{plot_y1:.1f}" '
            f'stroke="{theme.muted}" stroke-opacity="{0.55 if t == 0 else 0.2}" stroke-width="1"/>'
        )
        parts.append(_text(tx, plot_y1 + 16, _fmt(t), size=10, fill=theme.muted, anchor="middle"))

    x0 = xv(0.0) if vmin <= 0 <= vmax else plot_x0
    band_h = (plot_y1 - plot_y0) / n
    bar_h, offset = _band_marks(band_h, len(chart.series), max_w=20.0)
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
    return "".join(parts)


def _scatter(chart: Chart, theme: Theme, width: int, height: int) -> str:
    """Points connected in data order, matching the PPTX XY_SCATTER_LINES
    native chart type. Labels are used as numeric X when every one parses;
    otherwise each series falls back to its own point index as X."""
    palette = _palette(theme)
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
            # emit cx="inf" and collapse the whole scale. Fall back to indices.
            xs_numeric = parsed if all(math.isfinite(x) for x in parsed) else None
        except ValueError:
            xs_numeric = None

    series_points: list[list[tuple[float, float]]] = []
    for s in chart.series:
        pts: list[tuple[float, float]] = []
        if xs_numeric is not None:
            for x, raw in zip(xs_numeric, s.values):
                v = _finite(raw)
                if v is not None:
                    pts.append((x, v))
        else:
            for i, raw in enumerate(s.values):
                v = _finite(raw)
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

    for t in y_ticks:
        ty = ypix(t)
        parts.append(
            f'<line x1="{plot_x0:.1f}" y1="{ty:.1f}" x2="{plot_x1:.1f}" y2="{ty:.1f}" '
            f'stroke="{theme.muted}" stroke-opacity="{0.55 if t == 0 else 0.2}" stroke-width="1"/>'
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
    palette = _palette(theme)
    parts: list[str] = []

    top = 14.0
    if chart.title:
        parts.append(_text(16, top + 12, chart.title, size=14, weight="600", fill=theme.text))
        top += 28

    values = [_finite(v) for v in (chart.series[0].values if chart.series else [])]
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
    for name, color in legend_items:
        parts.append(f'<circle cx="{lx0:.1f}" cy="{ly - 3.5:.1f}" r="4.5" fill="{color}"/>')
        parts.append(_text(lx0 + 11, ly + 1, name, size=10.5, fill=theme.text, anchor="start"))
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
        f"<title>{title}</title>"
        f"{body}</svg>"
    )

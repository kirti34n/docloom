"""PPTX renderer: editable-native 16:9 decks built shape-by-shape on the
blank layout with python-pptx, so theme colors and fonts fully apply."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt

from ..ir import (
    Artifact,
    Block,
    BulletList,
    Callout,
    Chart,
    Code,
    Divider,
    Document,
    Heading,
    Image,
    NumberedList,
    Paragraph,
    Quote,
    RichText,
    Slide,
    StatRow,
    Table,
    cited_ids,
    normalize_table,
    plain,
    source_numbers,
    spans,
)
from ..theme import Theme, hex_to_rgb
from . import RenderError

# Geometry/typography constants, exported as plain data for the web app.
LAYOUT = {
    "slide_w_in": 13.333,
    "slide_h_in": 7.5,
    "margin_in": 0.6,
    "gap_in": 0.14,
    "title_pt": 26,
    "body_pt": 14,
    "hero_title_pt": 36,
    "image_pane_ratio": 0.45,
    "chart_max_h_in": 4.5,
    "stat_card_h_in": 1.4,
    "stat_gap_in": 0.25,
    "stat_max_cards": 5,
}

SLIDE_W = LAYOUT["slide_w_in"]
SLIDE_H = LAYOUT["slide_h_in"]
MARGIN = LAYOUT["margin_in"]
GAP = LAYOUT["gap_in"]
MONO = "Consolas"
BODY_PT = LAYOUT["body_pt"]
HEAD_PT = {1: 20, 2: 18, 3: 16, 4: 14}
# Set by _body for the duration of one slide's body render: a >1.0 factor grows
# text on an underfull slide so it fills the space instead of floating small.
_BODY_SCALE = 1.0
GROW_CAP = 1.7
CHART_TYPE = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "area": XL_CHART_TYPE.AREA,
    "pie": XL_CHART_TYPE.PIE,
    "scatter": XL_CHART_TYPE.XY_SCATTER_LINES,
}
CALLOUT_EDGE = {"info": "primary", "success": "accent", "warning": "muted", "danger": "text"}
_SAFE_SCHEMES = {"http", "https", "mailto"}  # matches html.py


def _safe_href(url: str) -> str | None:
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    return url if scheme in _SAFE_SCHEMES else None


def _rgb(color: str) -> RGBColor:
    return RGBColor(*hex_to_rgb(color))


def _tint(color: str, f: float) -> str:
    """Lighten `color` toward white by fraction f (0..1)."""
    r, g, b = hex_to_rgb(color)
    return "#%02X%02X%02X" % tuple(round(c + (255 - c) * f) for c in (r, g, b))


def _shade(color: str, f: float) -> str:
    """Darken `color` toward black by fraction f (0..1)."""
    r, g, b = hex_to_rgb(color)
    return "#%02X%02X%02X" % tuple(round(c * (1 - f)) for c in (r, g, b))


def _series_palette(theme: Theme) -> list[str]:
    """On-brand categorical colors derived from the theme's primary + accent,
    so native charts match the deck instead of Office's default blue/orange."""
    return [
        theme.primary,
        theme.accent,
        _tint(theme.primary, 0.42),
        _shade(theme.accent, 0.28),
        _tint(theme.accent, 0.5),
        _shade(theme.primary, 0.28),
    ]


def _style_chart(chart, b: Chart, theme: Theme) -> None:
    """Recolor a native chart from the theme palette and add tidy data labels.
    Best-effort: callers wrap this so a styling hiccup never drops the chart."""
    palette = _series_palette(theme)
    plot = chart.plots[0]
    if b.chart == "pie":
        ser = plot.series[0]
        for i, pt in enumerate(ser.points):
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(palette[i % len(palette)])
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.show_percentage = True
        dl.show_value = False
        dl.number_format = "0%"
        dl.number_format_is_linked = False
        dl.font.size = Pt(10)
        dl.font.bold = True
        dl.font.color.rgb = _rgb(theme.background)
        return
    for i, ser in enumerate(plot.series):
        color = _rgb(palette[i % len(palette)])
        if b.chart == "line":
            ser.format.line.color.rgb = color
            ser.format.line.width = Pt(2.25)
        elif b.chart == "scatter":
            ser.marker.format.fill.solid()
            ser.marker.format.fill.fore_color.rgb = color
            ser.format.line.color.rgb = color
        else:  # column, bar, area
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = color
    # value labels only when uncluttered (<=2 series of bars/columns)
    if b.chart in ("column", "bar") and len(b.series) <= 2:
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format = "General"  # 58 not "58." (a trailing-dot glitch)
        dl.number_format_is_linked = False
        dl.font.size = Pt(11)
        dl.font.bold = True
        dl.font.color.rgb = _rgb(theme.text)
        try:
            dl.position = XL_LABEL_POSITION.OUTSIDE_END
        except (ValueError, NotImplementedError):
            pass


def _box(slide, x: float, y: float, w: float, h: float):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    return tf


def _rect(slide, x: float, y: float, w: float, h: float, fill: str):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _runs(
    p,
    rt: RichText,
    theme: Theme,
    numbers: dict[str, int],
    size: float,
    color: str,
    font: str,
    bold: bool = False,
    italic: bool = False,
) -> None:
    for sp in spans(rt):
        run = p.add_run()
        run.text = sp.text
        f = run.font
        f.name = MONO if sp.code else font
        f.size = Pt(size)
        f.bold = sp.bold or bold
        f.italic = sp.italic or italic
        f.color.rgb = _rgb(color)
        if sp.link and _safe_href(sp.link):
            run.hyperlink.address = sp.link
        if sp.cite and sp.cite in numbers:
            sup = p.add_run()
            sup.text = str(numbers[sp.cite])
            sup.font.name = font
            sup.font.size = Pt(max(8, round(size * 0.65)))
            sup.font.color.rgb = _rgb(theme.muted)
            sup._r.get_or_add_rPr().set("baseline", "30000")


def _est_lines(text: str, size: float, width: float) -> int:
    per = max(8, int(width * 144 / size))
    return sum(max(1, (len(ln) + per - 1) // per) for ln in text.split("\n"))


def _line_h(size: float) -> float:
    return size * 1.3 / 72


# ------------------------------------------------------------- body blocks


def _text_block(
    slide, rt, theme, numbers, x, y, w, max_h,
    size=BODY_PT, color=None, font=None, bold=False, italic=False, indent=0.0,
) -> float:
    color = color or theme.text
    font = font or theme.font_body
    size = size * _BODY_SCALE
    h = min(max_h, _est_lines(plain(rt), size, w - indent) * _line_h(size))
    tf = _box(slide, x + indent, y, w - indent, h)
    _runs(tf.paragraphs[0], rt, theme, numbers, size, color, font, bold, italic)
    return h


def _list_block(slide, b, theme, numbers, x, y, w, max_h, ordered: bool) -> float:
    bp = BODY_PT * _BODY_SCALE
    est = sum(
        _est_lines(plain(it.text), bp, w - 0.35 * (it.level + 1))
        * _line_h(bp)
        + 0.05 * _BODY_SCALE
        for it in b.items
    )
    h = min(max_h, est)
    tf = _box(slide, x, y, w, h)
    counters: dict[int, int] = {}
    for i, it in enumerate(b.items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(4)
        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", str(Inches(0.35 * (it.level + 1))))
        pPr.set("indent", str(-Inches(0.25)))
        if ordered:
            counters[it.level] = counters.get(it.level, 0) + 1
            for deeper in [lv for lv in counters if lv > it.level]:
                del counters[deeper]
            marker = f"{counters[it.level]}. "
        else:
            marker = "\u2022  "
        m = p.add_run()
        m.text = marker
        m.font.name = theme.font_body
        m.font.size = Pt(bp)
        m.font.color.rgb = _rgb(theme.primary)
        _runs(p, it.text, theme, numbers, bp, theme.text, theme.font_body)
    return h


def _quote_block(slide, b, theme, numbers, x, y, w, max_h) -> float:
    h = _text_block(
        slide, b.text, theme, numbers, x, y, w, max_h, size=15, italic=True, indent=0.4
    )
    if b.attribution and h + 0.28 <= max_h:
        tf = _box(slide, x + 0.4, y + h + 0.04, w - 0.4, 0.24)
        _runs(
            tf.paragraphs[0], "\u2014 " + b.attribution, theme, numbers,
            12, theme.muted, theme.font_body,
        )
        h += 0.28
    return h


def _code_block(slide, b, theme, x, y, w, max_h) -> float:
    size, pad = 12, 0.12
    lines = b.code.split("\n")
    h = min(max_h, len(lines) * _line_h(size) + 2 * pad)
    _rect(slide, x, y, w, h, theme.surface)
    tf = _box(slide, x + pad, y + pad, w - 2 * pad, h - 2 * pad)
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = ln
        run.font.name = MONO
        run.font.size = Pt(size)
        run.font.color.rgb = _rgb(theme.text)
    return h


def _table_block(slide, b, theme, numbers, x, y, w, max_h) -> float:
    header, rows = normalize_table(b.header, b.rows)
    cols = max(len(header), 1)
    n_rows = len(rows) + 1
    tw = min(w, max(3.0, cols * 2.5))
    row_h = min(0.36, max_h / n_rows)
    th = n_rows * row_h
    frame = slide.shapes.add_table(
        n_rows, cols, Inches(x), Inches(y), Inches(tw), Inches(th)
    )
    tbl = frame.table
    tbl.first_row = False
    tbl.horz_banding = False
    for c in range(cols):
        tbl.columns[c].width = Inches(tw / cols)
    for r in range(n_rows):
        tbl.rows[r].height = Inches(row_h)
    for r in range(n_rows):
        cells = header if r == 0 else rows[r - 1]
        fill = theme.primary if r == 0 else (theme.background if r % 2 else theme.surface)
        color = theme.background if r == 0 else theme.text
        for c in range(cols):
            cell = tbl.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(fill)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            if c < len(cells):
                _runs(
                    cell.text_frame.paragraphs[0], cells[c], theme, numbers,
                    12, color, theme.font_body, bold=(r == 0),
                )
    h = th
    if b.caption and h + 0.26 <= max_h:
        tf = _box(slide, x, y + h + 0.04, tw, 0.22)
        _runs(
            tf.paragraphs[0], b.caption, theme, numbers,
            11, theme.muted, theme.font_body, italic=True,
        )
        h += 0.26
    return h


def _callout_block(slide, b, theme, numbers, x, y, w, max_h) -> float:
    size, pad, edge_w = 13 * _BODY_SCALE, 0.14, 0.08
    est = _est_lines(plain(b.text), size, w - edge_w - 2 * pad) * _line_h(size) + 2 * pad
    h = min(max_h, est)
    _rect(slide, x, y, w, h, theme.surface)
    _rect(slide, x, y, edge_w, h, getattr(theme, CALLOUT_EDGE[b.style]))
    tf = _box(slide, x + edge_w + pad, y + pad, w - edge_w - 2 * pad, h - 2 * pad)
    _runs(tf.paragraphs[0], b.text, theme, numbers, size, theme.text, theme.font_body)
    return h


def _image_block(slide, b, theme, x, y, w, max_h) -> float:
    # slots carrying only query/asset_id render as nothing in v0.2 pptx
    if not b.path:
        return 0.0
    path = Path(b.path)
    if not path.is_file():
        return 0.0
    try:
        pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y))
    except Exception:
        return 0.0
    cap_h = 0.3 if b.caption else 0.0
    scale = min(
        Inches(w) / pic.width, Inches(max(0.4, max_h - cap_h)) / pic.height, 1.0
    )
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    pic.left = Inches(x) + (Inches(w) - pic.width) // 2
    h = pic.height / 914400
    if b.caption:
        tf = _box(slide, x, y + h + 0.05, w, 0.22)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        _runs(
            tf.paragraphs[0], b.caption, theme, {}, 11, theme.muted, theme.font_body,
            italic=True,
        )
        h += cap_h
    return h


def _chart_data(b: Chart):
    """Chart block -> python-pptx chart data. Raises on anything the native
    chart path cannot represent; callers fall back to image/table."""
    if b.chart == "scatter":
        xs = [float(lb) for lb in b.labels]  # non-numeric labels -> ValueError
        if not xs:
            raise ValueError("scatter chart has no labels")
        data = XyChartData()
        for s in b.series:
            ser = data.add_series(s.name or "")
            for xv, yv in zip(xs, s.values):
                if yv is not None:
                    ser.add_data_point(xv, yv)
        return data
    n = max(len(b.labels), max((len(s.values) for s in b.series), default=0))
    if n == 0 or not b.series:
        raise ValueError("empty chart data")
    data = CategoryChartData()
    data.categories = (list(b.labels) + [""] * n)[:n]
    for s in b.series[:1] if b.chart == "pie" else b.series:
        # pad ragged series with None (blank points); pptx accepts None values
        data.add_series(s.name or "", (list(s.values) + [None] * n)[:n])
    return data


def _chart_table(b: Chart) -> Table:
    header: list[RichText] = [b.title or ""] + [
        s.name or f"Series {i + 1}" for i, s in enumerate(b.series)
    ]
    rows: list[list[RichText]] = []
    for i, label in enumerate(b.labels):
        row: list[RichText] = [label]
        for s in b.series:
            v = s.values[i] if i < len(s.values) else None
            row.append("" if v is None else f"{v:g}")
        rows.append(row)
    return Table(header=header, rows=rows, caption=b.caption)


def _chart_block(slide, b: Chart, theme, numbers, x, y, w, max_h) -> float:
    cap_h = 0.26 if b.caption else 0.0
    try:
        data = _chart_data(b)
        h = min(max_h - cap_h, LAYOUT["chart_max_h_in"])
        if h < 1.0:
            raise ValueError("not enough room for a native chart")
        frame = slide.shapes.add_chart(
            CHART_TYPE[b.chart], Inches(x), Inches(y), Inches(w), Inches(h), data
        )
        chart = frame.chart
        chart.font.name = theme.font_body
        chart.font.size = Pt(11)
        chart.font.color.rgb = _rgb(theme.text)
        if b.title:
            chart.has_title = True
            chart.chart_title.text_frame.text = b.title
        else:
            chart.has_title = False
        if b.chart == "pie" or len(b.series) > 1:
            chart.has_legend = True
            chart.legend.include_in_layout = False
        try:
            _style_chart(chart, b, theme)
        except Exception:
            pass  # on-brand recolor is best-effort; keep the native chart
    except Exception:
        # fallback chain: pre-rendered image if present, else data as a table
        if b.path and Path(b.path).is_file():
            return _image_block(
                slide, Image(path=b.path, alt=b.title or "", caption=b.caption),
                theme, x, y, w, max_h,
            )
        return _table_block(slide, _chart_table(b), theme, numbers, x, y, w, max_h)
    if b.caption and h + 0.26 <= max_h:
        tf = _box(slide, x, y + h + 0.04, w, 0.22)
        _runs(
            tf.paragraphs[0], b.caption, theme, numbers,
            11, theme.muted, theme.font_body, italic=True,
        )
        h += 0.26
    return h


def _stats_block(slide, b: StatRow, theme, x, y, w, max_h) -> float:
    items = b.items[: LAYOUT["stat_max_cards"]]  # extras dropped; lint budgets it
    if not items:
        return 0.0
    gap = LAYOUT["stat_gap_in"]
    h = min(max_h, LAYOUT["stat_card_h_in"])
    cw = (w - gap * (len(items) - 1)) / len(items)
    pad = 0.18
    for i, st in enumerate(items):
        cx = x + i * (cw + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(cx), Inches(y), Inches(cw), Inches(h)
        )
        card.fill.solid()
        card.fill.fore_color.rgb = _rgb(theme.surface)
        card.line.fill.background()
        card.shadow.inherit = False
        ty = y + pad
        tf = _box(slide, cx + pad, ty, cw - 2 * pad, 0.42)
        _runs(
            tf.paragraphs[0], st.value, theme, {},
            24, theme.primary, theme.font_heading, bold=True,
        )
        ty += 0.5
        if ty + 0.22 <= y + h:
            tf = _box(slide, cx + pad, ty, cw - 2 * pad, 0.22)
            _runs(tf.paragraphs[0], st.label, theme, {}, 11, theme.muted, theme.font_body)
            ty += 0.28
        if st.delta and ty + 0.2 <= y + h:
            tf = _box(slide, cx + pad, ty, cw - 2 * pad, 0.2)
            _runs(tf.paragraphs[0], st.delta, theme, {}, 10, theme.accent, theme.font_body)
    return h


def _block(slide, b: Block, theme, numbers, x, y, w, max_h) -> float:
    if isinstance(b, Heading):
        return _text_block(
            slide, b.text, theme, numbers, x, y, w, max_h,
            size=HEAD_PT[b.level], font=theme.font_heading, bold=True,
        )
    if isinstance(b, Paragraph):
        return _text_block(slide, b.text, theme, numbers, x, y, w, max_h)
    if isinstance(b, (BulletList, NumberedList)):
        return _list_block(
            slide, b, theme, numbers, x, y, w, max_h, isinstance(b, NumberedList)
        )
    if isinstance(b, Quote):
        return _quote_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, Code):
        return _code_block(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Table):
        return _table_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, Callout):
        return _callout_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, Image):
        return _image_block(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Chart):
        return _chart_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, StatRow):
        return _stats_block(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Artifact):
        if b.path and Path(b.path).is_file():
            return _image_block(
                slide, Image(path=b.path, alt=b.alt, caption=b.caption),
                theme, x, y, w, max_h,
            )
        return 0.0  # unresolved artifact: skip silently
    if isinstance(b, Divider):
        _rect(slide, x, y + 0.06, w, 0.02, theme.surface)
        return 0.14
    raise RenderError(f"unhandled block type {type(b).__name__}")


def _natural_h(b: Block, w: float) -> float:
    """Estimate a block's natural (unclamped) height in inches, so _body can
    tell when a slide is underfull and rebalance the whitespace."""
    if isinstance(b, Heading):
        s = HEAD_PT[b.level]
        return _est_lines(plain(b.text), s, w) * _line_h(s)
    if isinstance(b, Paragraph):
        return _est_lines(plain(b.text), BODY_PT, w) * _line_h(BODY_PT)
    if isinstance(b, (BulletList, NumberedList)):
        return sum(
            _est_lines(plain(it.text), BODY_PT, w - 0.35 * (it.level + 1))
            * _line_h(BODY_PT) + 0.05
            for it in b.items
        )
    if isinstance(b, Quote):
        h = _est_lines(plain(b.text), 15, w - 0.4) * _line_h(15)
        return h + (0.28 if b.attribution else 0.0)
    if isinstance(b, Code):
        return len(b.code.split("\n")) * _line_h(12) + 0.24
    if isinstance(b, Table):
        _, rows = normalize_table(b.header, b.rows)
        return (len(rows) + 1) * 0.36 + (0.26 if b.caption else 0.0)
    if isinstance(b, Callout):
        return _est_lines(plain(b.text), 13, w - 0.08 - 0.28) * _line_h(13) + 0.28
    if isinstance(b, StatRow):
        return LAYOUT["stat_card_h_in"] if b.items else 0.0
    if isinstance(b, Chart):
        return LAYOUT["chart_max_h_in"] + (0.26 if b.caption else 0.0)
    if isinstance(b, (Image, Artifact)):
        # a resolved image tends to fill much of the content area; estimate high
        # so a lone image centers near the top instead of floating low.
        return 4.6 if (b.path and Path(b.path).is_file()) else 0.0
    if isinstance(b, Divider):
        return 0.14
    return _line_h(BODY_PT)


def _body(slide, blocks: list[Block], theme, numbers, x, y, w) -> float:
    """Lay body blocks into [y, slide bottom]. When the content is sparse,
    distribute the slack (bigger inter-block gaps) and vertically center it at
    an optical-center bias, so slides don't plant everything in the top third."""
    global _BODY_SCALE
    bottom = SLIDE_H - MARGIN
    if not blocks:
        return y
    avail = bottom - y
    n = len(blocks)
    is_text = (Heading, Paragraph, BulletList, NumberedList, Callout)
    nat = [max(0.0, _natural_h(b, w)) for b in blocks]
    text_h = sum(h for b, h in zip(blocks, nat) if isinstance(b, is_text))
    fixed_h = sum(nat) - text_h
    total = sum(nat) + GAP * (n - 1)

    # Underfull: grow the text blocks toward an 80% fill target (capped), so a
    # sparse slide doesn't leave the bottom half blank.
    scale = 1.0
    if total < avail * 0.65 and text_h > 0.2:
        scale = max(1.0, min(GROW_CAP, (avail * 0.80 - fixed_h) / text_h))
    scaled_total = text_h * scale + fixed_h + GAP * (n - 1)

    gap, y0 = GAP, y
    if 0 < scaled_total < avail:  # distribute residual slack + optical-center
        slack = avail - scaled_total
        extra_gap = min(0.5, slack * 0.5 / (n - 1)) if n > 1 else 0.0
        gap = GAP + extra_gap
        used = scaled_total + extra_gap * (n - 1)
        y0 = y + max(0.0, (avail - used) * 0.42)

    _BODY_SCALE = scale
    try:
        yy = y0
        for b in blocks:
            remaining = bottom - yy
            if remaining < 0.3:
                break  # ponytail: overflow blocks dropped; paginate if decks need it
            yy += _block(slide, b, theme, numbers, x, yy, w, remaining) + gap
    finally:
        _BODY_SCALE = 1.0
    return yy


# ----------------------------------------------------------------- layouts


def _slide_accent(s: Slide, theme: Theme) -> str:
    """Per-slide accent override for rules/edges; invalid hex falls back."""
    c = (s.accent or "").strip().lstrip("#")
    try:
        if len(c) == 6:
            int(c, 16)
            return "#" + c.upper()
    except ValueError:
        pass
    return theme.primary


def _usable_image(img: Image | None) -> bool:
    return img is not None and bool(img.path) and Path(img.path).is_file()


def _logo(slide, img: Image, max_h: float = 0.85) -> None:
    """Place a brand logo in the slide's top-right corner, scaled to fit."""
    try:
        pic = slide.shapes.add_picture(str(img.path), 0, 0)
    except Exception:
        return  # unreadable image: skip rather than fail the render
    max_w = SLIDE_W * 0.28
    scale = min(Inches(max_h) / pic.height, Inches(max_w) / pic.width, 1.0)
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    pic.top = Inches(MARGIN)
    pic.left = Inches(SLIDE_W - MARGIN) - pic.width


def _doc_logo(slide, doc: Document, s: Slide) -> None:
    """Stamp the document's brand logo on a content slide, small, top-right.

    Skipped on full-bleed layouts (section/hero/image panes reach the corner)
    and on the title slide, which places its own image."""
    if s.layout in ("title", "section", "hero", "image_left", "image_right"):
        return
    if not _usable_image(doc.logo):
        return
    _logo(slide, doc.logo, max_h=0.4)


def _title_band(
    slide, title: str | None, theme: Theme,
    x: float = MARGIN, w: float | None = None, accent: str | None = None,
) -> float:
    if not title:
        return MARGIN
    w = SLIDE_W - x - MARGIN if w is None else w
    tf = _box(slide, x, 0.42, w, 0.62)
    _runs(
        tf.paragraphs[0], title, theme, {},
        LAYOUT["title_pt"], theme.text, theme.font_heading, bold=True,
    )
    _rect(slide, x, 1.14, w, 0.028, accent or theme.primary)
    return 1.42


def _title_slide(slide, s: Slide, doc: Document, theme: Theme, accent: str) -> None:
    _rect(slide, 0, 0, 0.3, SLIDE_H, accent)
    if _usable_image(s.image):  # brand logo (or any title-slide image) → corner
        _logo(slide, s.image)
    tf = _box(slide, 1.1, 2.5, SLIDE_W - 1.1 - MARGIN, 1.4)
    _runs(
        tf.paragraphs[0], s.title or doc.title, theme, {},
        40, theme.text, theme.font_heading, bold=True,
    )
    subtitle = s.subtitle or doc.subtitle
    if subtitle:
        tf = _box(slide, 1.1, 4.0, SLIDE_W - 1.1 - MARGIN, 0.6)
        _runs(tf.paragraphs[0], subtitle, theme, {}, 20, theme.muted, theme.font_body)
    byline = "  \u00b7  ".join(p for p in (", ".join(doc.authors), doc.date) if p)
    if byline:
        tf = _box(slide, 1.1, 6.3, SLIDE_W - 1.1 - MARGIN, 0.35)
        _runs(tf.paragraphs[0], byline, theme, {}, 14, theme.muted, theme.font_body)


def _section_slide(slide, s: Slide, theme: Theme) -> None:
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.primary)
    _rect(slide, MARGIN, 2.72, 1.2, 0.05, theme.accent)
    tf = _box(slide, MARGIN, 3.0, SLIDE_W - 2 * MARGIN, 1.1)
    _runs(
        tf.paragraphs[0], s.title or "", theme, {},
        36, theme.background, theme.font_heading, bold=True,
    )
    if s.subtitle:
        tf = _box(slide, MARGIN, 4.25, SLIDE_W - 2 * MARGIN, 0.6)
        _runs(tf.paragraphs[0], s.subtitle, theme, {}, 18, theme.background, theme.font_body)


def _quote_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int]) -> None:
    q = next((b for b in s.blocks if isinstance(b, Quote)), None)
    rest = [b for b in s.blocks if b is not q]
    if s.title:
        tf = _box(slide, MARGIN, 0.5, SLIDE_W - 2 * MARGIN, 0.4)
        _runs(tf.paragraphs[0], s.title, theme, {}, 16, theme.muted, theme.font_heading)
    y = 3.0
    if q is not None:
        qx, qw = 2.3, SLIDE_W - 4.6
        attr_h = 0.6 if q.attribution else 0.0
        avail = SLIDE_H - MARGIN - 1.4 - attr_h
        size = next(
            (pt for pt in (30, 24, 20, 16)
             if _est_lines(plain(q.text), pt, qw) * _line_h(pt) <= avail),
            14,  # ponytail: floor at 14pt; auto_size shrinks text into the clamped box
        )
        qh = min(avail, _est_lines(plain(q.text), size, qw) * _line_h(size))
        y = max(1.4, (SLIDE_H - qh - 0.3 - attr_h) / 2)
        _rect(slide, qx - 0.35, y, 0.07, qh, theme.accent)
        tf = _box(slide, qx, y, qw, qh)
        _runs(tf.paragraphs[0], q.text, theme, numbers, size, theme.text, theme.font_body,
              italic=True)
        y += qh + 0.25
        if q.attribution:
            tf = _box(slide, qx, y, qw, 0.35)
            _runs(
                tf.paragraphs[0], "\u2014 " + q.attribution, theme, {},
                16, theme.muted, theme.font_body,
            )
            y += 0.55
    if rest:
        _body(slide, rest, theme, numbers, MARGIN, y, SLIDE_W - 2 * MARGIN)


def _content_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int],
                   accent: str) -> None:
    y = _title_band(slide, s.title, theme, accent=accent)
    _body(slide, s.blocks + s.right, theme, numbers, MARGIN, y, SLIDE_W - 2 * MARGIN)


def _hero_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int],
                accent: str) -> None:
    try:
        slide.shapes.add_picture(
            str(s.image.path), 0, 0, Inches(SLIDE_W), Inches(SLIDE_H)
        )
    except Exception:  # unreadable file: behave like content layout
        _content_slide(slide, s, theme, numbers, accent)
        return
    blocks = s.blocks + s.right
    if not (s.title or s.subtitle or blocks):
        return
    # scrim: true fill transparency isn't exposed by python-pptx, so the title
    # sits in a solid theme.text band flush with the bottom edge instead
    tw = SLIDE_W - 2 * MARGIN
    pad = 0.3
    title_pt = LAYOUT["hero_title_pt"]
    title_h = (
        _est_lines(s.title, title_pt, tw) * _line_h(title_pt) if s.title else 0.0
    )
    sub_h = _est_lines(s.subtitle, 20, tw) * _line_h(20) + 0.08 if s.subtitle else 0.0
    band_h = min(SLIDE_H * 0.6, 2 * pad + title_h + sub_h + (1.7 if blocks else 0.0))
    band_y = SLIDE_H - band_h
    _rect(slide, 0, band_y, SLIDE_W, band_h, theme.text)
    y = band_y + pad
    if s.title:
        th = min(title_h, SLIDE_H - pad - y)
        tf = _box(slide, MARGIN, y, tw, th)
        _runs(
            tf.paragraphs[0], s.title, theme, {},
            title_pt, theme.background, theme.font_heading, bold=True,
        )
        y += th + 0.08
    if s.subtitle and y + 0.3 <= SLIDE_H - pad:
        tf = _box(slide, MARGIN, y, tw, min(sub_h, SLIDE_H - pad - y))
        _runs(tf.paragraphs[0], s.subtitle, theme, {}, 20, theme.surface, theme.font_body)
        y += sub_h
    if blocks:
        # light-on-dark swap so band body text stays readable
        band_theme = theme.model_copy(
            update={"text": theme.background, "muted": theme.surface}
        )
        _body(slide, blocks, band_theme, numbers, MARGIN, y, tw)


def _image_side_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int],
                      accent: str) -> None:
    pane_w = SLIDE_W * LAYOUT["image_pane_ratio"]
    left_side = s.layout == "image_left"
    px = 0.0 if left_side else SLIDE_W - pane_w
    try:
        pic = slide.shapes.add_picture(str(s.image.path), Inches(px), 0)
    except Exception:  # unreadable file: behave like content layout
        _content_slide(slide, s, theme, numbers, accent)
        return
    scale = min(Inches(pane_w) / pic.width, Inches(SLIDE_H) / pic.height)
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    pic.left = Inches(px) + (Inches(pane_w) - pic.width) // 2
    pic.top = (Inches(SLIDE_H) - pic.height) // 2
    tx = pane_w + MARGIN if left_side else MARGIN
    tw = SLIDE_W - pane_w - 2 * MARGIN
    y = _title_band(slide, s.title, theme, x=tx, w=tw, accent=accent)
    _body(slide, s.blocks + s.right, theme, numbers, tx, y, tw)


def _render_slide(prs, blank, s: Slide, doc: Document, theme: Theme,
                  numbers: dict[str, int]) -> None:
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.background)
    accent = _slide_accent(s, theme)
    if s.layout == "title":
        _title_slide(slide, s, doc, theme, accent)
    elif s.layout == "section":
        _section_slide(slide, s, theme)
    elif s.layout == "quote":
        _quote_slide(slide, s, theme, numbers)
    elif s.layout == "hero" and _usable_image(s.image):
        _hero_slide(slide, s, theme, numbers, accent)
    elif s.layout in ("image_left", "image_right") and _usable_image(s.image):
        _image_side_slide(slide, s, theme, numbers, accent)
    elif s.layout == "two_column":
        y = _title_band(slide, s.title, theme, accent=accent)
        col_w = (SLIDE_W - 2 * MARGIN - 0.5) / 2
        _body(slide, s.blocks, theme, numbers, MARGIN, y, col_w)
        _body(slide, s.right, theme, numbers, MARGIN + col_w + 0.5, y, col_w)
    else:  # content, or an image layout without a usable image path
        _content_slide(slide, s, theme, numbers, accent)
    _doc_logo(slide, doc, s)
    if s.notes:
        slide.notes_slide.notes_text_frame.text = s.notes


def _sources_slide(prs, blank, doc: Document, theme: Theme) -> None:
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.background)
    y = _title_band(slide, "Sources", theme)
    tf = _box(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, SLIDE_H - MARGIN - y)
    for i, src in enumerate(doc.sources):
        line = f"{i + 1}. {src.title}"
        if src.publisher:
            line += f" \u2014 {src.publisher}"
        if src.date:
            line += f" ({src.date})"
        if src.url:
            line += f", {src.url}"
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(6)
        _runs(p, line, theme, {}, 12, theme.muted, theme.font_body)


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    if not doc.slides:
        raise RenderError("document has no slides; add slides[] to render pptx")
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    blank = prs.slide_layouts[6]
    numbers = source_numbers(doc)
    for s in doc.slides:
        _render_slide(prs, blank, s, doc, theme, numbers)
    if doc.sources and cited_ids(doc):
        _sources_slide(prs, blank, doc, theme)
    prs.save(str(out_path))
    return Path(out_path)

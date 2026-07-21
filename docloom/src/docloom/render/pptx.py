"""PPTX renderer: editable-native 16:9 decks built shape-by-shape on the
blank layout with python-pptx, so theme colors and fonts fully apply."""

from __future__ import annotations

import colorsys
import io
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Inches, Pt

from ..ir import (
    Artifact,
    Block,
    BulletList,
    Callout,
    Chart,
    Code,
    Diagram,
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
from ..theme import Theme, contrast_ratio, hex_to_rgb
from . import RenderError, chart_svg, diagram_pptx, diagram_svg, raster, textfit

# target pixel width for rasterized SVG (2x a 640pt-wide chart), so the picture
# stays crisp on a projector and in print without bloating the deck
RASTER_PX = chart_svg.DEFAULT_WIDTH * 2

# Geometry/typography constants, exported as plain data for the web app.
LAYOUT = {
    "slide_w_in": 13.333,
    "slide_h_in": 7.5,
    "margin_in": 0.6,
    "gap_in": 0.16,
    "title_pt": 26,
    "body_pt": 14,
    "hero_title_pt": 36,
    "image_pane_ratio": 0.45,
    # This is the cap for a chart that SHARES its slide with other blocks; a
    # chart alone on its slide instead fills the full remaining body height
    # (see _chart_block's `solo` mode / _body's solo_chart handling), which
    # is the real fix for the old 4.5in cap leaving a permanent ~1.4in dead
    # void below a solo chart (P5 audit defect 6). This shared-slide cap
    # only got a modest bump, not all the way to the audit's suggested
    # 5.4: a slide with a chart PLUS a trailing block (e.g. an artifact
    # placeholder) needs that block to still get drawable room -- 5.4 left
    # a chart-and-artifact slide (the audit's own defect-1 example) with
    # < 0 remaining inches, silently dropping the artifact again via the
    # unrelated overflow-drop in _body's layout loop. NOTE: lint.py:117
    # (CHART_H_IN) mirrors this constant and was NOT updated here (out of
    # this file's ownership) -- flagged as a handoff.
    "chart_max_h_in": 4.8,
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
HEAD_PT = {1: 22, 2: 19, 3: 17, 4: 15}
LIST_ITEM_GAP_PT = 6  # space_after below each bullet/numbered item
# P5 audit defect 4: 1.7 grew body prose to ~92% of the title's own size
# (14pt * 1.7 = 23.8pt) next to a fixed-size block on the same slide (12pt
# code, a 12pt table), destroying the hierarchy. 1.25 keeps the "fill sparse
# space" behavior while MAX_GROWN_PT below puts a hard, size-relative floor
# under how large that can ever get.
GROW_CAP = 1.25
MAX_GROWN_PT = round(LAYOUT["title_pt"] * 0.7)  # grown body text must never rival the title
# Block types whose font size the underfull-slide grow pass in _body scales;
# everything else (quotes, tables, charts, ...) keeps its natural size.
_GROWABLE_BLOCKS = (Heading, Paragraph, BulletList, NumberedList, Callout)
# Presence of any of these on a slide/column suppresses the grow pass
# entirely (see _grow_scale): growing only the prose next to a block whose
# size is fixed by its content (a table's row count, a chart's own cap, a
# code block's monospace size, stat cards) is exactly what produced the
# mismatched hierarchy in the P5 audit (defect 4).
_FIXED_SIZE_BLOCKS = (Table, Code, Chart, StatRow, Image, Artifact, Diagram)
TABLE_PT = 12  # preferred row font size, shrunk toward TABLE_MIN_PT to fit
TABLE_MIN_PT = 9
# Post-layout text-fit floor and trigger. MIN_FIT_PT mirrors TABLE_MIN_PT's
# rationale (9pt is the smallest size that reads from a conference-room
# screen); FIT_SLACK_PT is hysteresis: a frame must measurably overflow by
# more than this before its runs are rewritten, so every frame the 1.3x
# _est_lines allocator already sized generously stays byte-identical.
# Not mirrored into lint.py: lint's height model is unaffected -- this pass
# shrinks font sizes INSIDE unchanged box heights, it never touches geometry.
MIN_FIT_PT = 9.0
FIT_SLACK_PT = 6.0
# Kill switch: flipping this to False makes _fit_text_frames a no-op, so the
# PPTX output is byte-identical to the pre-autofit-fix build. See render().
TEXTFIT_ENABLED = True
TABLE_VPAD = 0.06  # matches the cell.margin_top + cell.margin_bottom set below
CHART_TYPE = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "area": XL_CHART_TYPE.AREA,
    "pie": XL_CHART_TYPE.PIE,
    "scatter": XL_CHART_TYPE.XY_SCATTER,
}
# warning/danger used to map straight to theme.muted/theme.text (gray and
# near-black) -- the two styles that most need to read as urgent signaled
# nothing at all, indistinguishable from a plain divider or body-text color.
# _callout_edge_color below derives real amber/red tones instead, by
# hue-shifting theme.accent's own saturation/lightness rather than injecting
# an unrelated stock stoplight hex, so the result still reads as part of
# this theme instead of fighting the brand.
_WARNING_HUE = 38.0  # amber
_DANGER_HUE = 4.0  # red
_SAFE_SCHEMES = {"http", "https", "mailto"}  # matches html.py
# Shared brand-logo target: 0.5in tall on every layout, matching the docx
# (Inches(0.5)), typst (1.27cm), and html (3rem @ 96dpi) renderers so the
# mark reads as one consistent size across every exported format.
LOGO_MAX_H = 0.5
LOGO_MAX_W = SLIDE_W * 0.28  # width guard: caps a very wide (e.g. horizontal wordmark) logo
# Mirrors lint.py:55's DIAGRAM_H_IN so the physical-height estimate _natural_h
# gives an overflow-checking Diagram block matches what lint already assumes
# when it decides a slide is overfull (docs/diagram-plan.md section 6).
DIAGRAM_H_IN = 4.6

# Named so lint.py's own mirrored copies (plain literals -- lint.py must stay
# import-light and layout-agnostic, see the comment above its SLIDE_BODY_H_IN)
# can be pinned against a real name via test_reaudit_lint.py instead of a
# bare, easy-to-drift number sprinkled across both files. These four used to
# be unnamed literals scattered through this module (0.26 in three different
# functions, 0.3 in a fourth, 0.12 in a fifth) and lint.py's geometry model
# mirrored NONE of them -- the direct cause of the audit's silent-drop
# repro: a subtitle (SUBTITLE_PAD_IN + the estimated line) shrinks the real
# body height, and a chart's own caption (CAPTION_H_IN) adds to its real
# footprint, neither of which lint's SLIDE_BODY_H_IN/CHART_H_IN accounted
# for, so a slide that silently dropped its trailing block still scored as
# safe.
CAPTION_H_IN = 0.26       # table/chart/diagram/placeholder caption strip
IMAGE_CAPTION_H_IN = 0.3  # _place_picture's own (slightly taller) image caption
QUOTE_ATTR_H_IN = 0.28    # quote attribution line
SUBTITLE_PAD_IN = 0.12    # _subtitle_line's fixed pad below its estimated lines
# The floor below which a block isn't worth giving its own shape -- and, as
# of the fix for the "ponytail" class (see _body), the minimum every block is
# now RESERVED ahead of time so an earlier block can never silently eat a
# later authored block's entire share of the slide.
MIN_BLOCK_RESERVE_IN = 0.3
# "Shrink, never drop" (the MIN_BLOCK_RESERVE_IN fix above) stops content
# from vanishing with zero trace, but reserving only 0.3in for a VISUAL block
# (an image, artifact, diagram, chart, or stat row) does not give it a
# legible size -- it gives it a speck. Measured live: a diagram squeezed to
# its 0.3in reserve rendered a 2208x1894 raster picture into a 0.47x0.40in
# box (~4735 effective dpi), a fix wearing a silent failure's clothes: lint
# DOES warn (deck/overflow), but the renderer still emitted something no
# viewer could ever read. Below this floor, a visual block is DROPPED with a
# warning naming it instead -- this is the one place in this file where
# "drop" is the correct answer over "shrink to fit", because a visual block
# has no legible degraded form the way text does (text can still be read at
# a smaller size; a diagram at 0.4in tall cannot).
MIN_VISUAL_BLOCK_H_IN = 1.2
_VISUAL_BLOCKS = (Image, Artifact, Diagram, Chart, StatRow)


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


def _mix(color: str, toward: str, f: float) -> str:
    """Blend `color` toward an arbitrary target color by fraction f (0..1).
    Unlike _tint/_shade (which always move toward white/black), this blends
    toward whatever color is actually behind the mix -- used for the callout
    fill wash below, which needs to land near theme.background whether that
    background is light or dark, not near a hardcoded white."""
    r1, g1, b1 = hex_to_rgb(color)
    r2, g2, b2 = hex_to_rgb(toward)
    return "#%02X%02X%02X" % tuple(
        round(a + (c - a) * f) for a, c in ((r1, r2), (g1, g2), (b1, b2))
    )


def _hue_shift(color: str, hue_deg: float) -> str:
    """Recolor `color` to hue `hue_deg` (0-360) while preserving its own
    saturation and lightness, so a derived tone still reads as part of this
    theme's palette instead of an unrelated stock hex."""
    r, g, b = hex_to_rgb(color)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    r2, g2, b2 = colorsys.hls_to_rgb(hue_deg / 360, l, s)
    return "#%02X%02X%02X" % (round(r2 * 255), round(g2 * 255), round(b2 * 255))


def _callout_edge_color(style: str, theme: Theme) -> str:
    """Per-style callout edge color. info/success reuse the brand's own
    primary/accent; warning/danger hue-shift theme.accent toward amber/red
    (see _WARNING_HUE/_DANGER_HUE) so they actually signal urgency instead of
    the old muted-gray/near-black mapping that made the two most urgent
    styles indistinguishable from ordinary chrome."""
    if style == "success":
        return theme.accent
    if style == "warning":
        return _hue_shift(theme.accent, _WARNING_HUE)
    if style == "danger":
        return _hue_shift(theme.accent, _DANGER_HUE)
    return theme.primary  # info, and any unrecognized style


# Finding D: all four callout fills were the flat theme.surface gray, so only
# the 4px edge bar actually carried the style's color -- across a room, a
# danger callout read exactly as loud as an info one. A heavy wash of the
# edge color itself (not a fixed light/dark stock hex) lets the whole card
# signal urgency while staying inside this theme's own palette.
_CALLOUT_FILL_WASH = 0.86  # fraction of the way from the edge color to theme.background


def _callout_fill_color(style: str, theme: Theme) -> str:
    """A tinted wash of `style`'s own edge color, blended toward
    theme.background (not a fixed white) so it reads correctly on a dark
    theme too, unlike _tint which always moves toward literal white."""
    return _mix(_callout_edge_color(style, theme), theme.background, _CALLOUT_FILL_WASH)


def _band_theme(theme: Theme, fill: str) -> Theme:
    """Theme tokens recolored for blocks drawn on top of an inverted band --
    a solid rectangle painted `fill` that covers part or all of the slide
    (hero's dark title strip, fill=theme.text; section's full-bleed cover,
    fill=theme.primary) -- so any block placed on that band resolves its OWN
    fill/foreground choices against what is ACTUALLY painted behind it,
    instead of against the document's still-light theme.background.

    This is the hero contrast regression's actual root cause: the old swap
    (just {"text": theme.background, "muted": theme.surface}) recolored the
    FOREGROUND tokens a block reads for its text, but left "background"
    pointed at the document's own light color. A callout mixes its wash via
    _callout_fill_color(style, theme) -> _mix(edge_color, theme.background,
    0.86): with only text/muted swapped, that wash still lands near-white
    (mixed toward the document's real light background) while the callout's
    own text, now theme.background too, ALSO renders near-white -- white on
    white, measured 1.1-1.3:1, not just low contrast but genuinely
    invisible. Swapping "background" (and "surface", its lighter sibling) to
    the band's own paint fixes this for ANY block that mixes toward
    "background" -- not just callouts: a diagram edge-label pill (drawn
    filled with theme.background in diagram_pptx.py) and a code block's own
    surface rect are exactly the same seam, fixed once here instead of
    patched per block type.

    "primary" only remaps to "accent" when the band's own fill genuinely IS
    theme.primary (section's full-bleed cover): a bullet marker or group
    label drawn in "primary" would otherwise be theme.primary on a
    theme.primary background -- literally invisible, not just low contrast
    (the original, narrower fix this generalizes). Doing that swap
    unconditionally (for hero's fill=theme.text too) was tried and rejected:
    it collapsed _callout_edge_color's info (theme.primary) and success
    (theme.accent) onto the same accent hex on every hero band, trading one
    contrast bug for a color-distinctness one."""
    update = {
        "background": fill,
        "surface": _mix(fill, theme.background, 0.12),
        "text": theme.background,
        "muted": theme.surface,
    }
    if fill == theme.primary:
        update["primary"] = theme.accent
    return theme.model_copy(update=update)


def _divider_color(theme: Theme) -> str:
    """A rule color that actually reads as a line. theme.surface (the
    default) sits at only a ~1.1 contrast ratio against theme.background on
    the default theme -- a near-invisible ghost, measured on a real deck
    (P5 audit defect 9). Step theme.muted progressively lighter until the
    line clears a real, if still subtle, contrast ratio."""
    for f in (0.6, 0.4, 0.2, 0.0):
        c = _tint(theme.muted, f)
        if contrast_ratio(c, theme.background) >= 1.5:
            return c
    return theme.muted


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


def _label_fg(fill: str, theme: Theme) -> str:
    """theme.background or theme.text, whichever contrasts more with `fill`,
    so a data label stays legible on light and dark slice colors alike."""
    if contrast_ratio(theme.background, fill) >= contrast_ratio(theme.text, fill):
        return theme.background
    return theme.text


def _hide_chart_border(chart) -> None:
    """No python-pptx API exposes the chart-area outline; drop to the
    underlying chartSpace XML, the same technique the citation superscript in
    _runs uses. A styling hiccup here must never skip the rest of the theme
    styling, so failures are swallowed locally."""
    try:
        chartSpace = chart._chartSpace
        if chartSpace.find(qn("c:spPr")) is not None:
            return
        spPr = parse_xml(
            "<c:spPr %s><a:ln><a:noFill/></a:ln></c:spPr>" % nsdecls("c", "a")
        )
        chartSpace.find(qn("c:chart")).addnext(spPr)
    except Exception:
        pass


def _style_axes(chart, theme: Theme) -> None:
    """Recolor axis lines, gridlines, and tick labels on-brand instead of
    Office's default gray. Callers only reach this for chart types that have
    axes (pie does not)."""
    for axis in (chart.category_axis, chart.value_axis):
        axis.format.line.fill.background()  # no axis line, just tick labels
        axis.tick_labels.font.size = Pt(10)
        # font_heading, not font_body: theme.font_body defaults to Georgia,
        # whose old-style (text) figures sit below the cap line with
        # descenders -- fine for prose, but an axis tick like "6 5 4 3 2 1 0"
        # renders looking broken (P5 audit defect 13). Numeric chrome reads
        # cleanly in the heading font's lining figures instead.
        axis.tick_labels.font.name = theme.font_heading
        axis.tick_labels.font.color.rgb = _rgb(theme.muted)
    if chart.value_axis.has_major_gridlines:
        gl = chart.value_axis.major_gridlines.format.line
        gl.color.rgb = _rgb(_tint(theme.muted, 0.65))
        gl.width = Pt(0.75)


def _fix_dlbl_show_percent(dLbl_elm) -> None:
    """Force an already-materialized per-point c:dLbl to show a percentage,
    not the raw value. See the call site in _style_chart for why this is
    needed: python-pptx has no high-level API for a data label's show
    flags, so this reaches into the oxml element directly."""
    show_val = dLbl_elm.find(qn("c:showVal"))
    if show_val is not None:
        show_val.set("val", "0")
    show_pct = dLbl_elm.find(qn("c:showPercent"))
    if show_pct is not None:
        show_pct.set("val", "1")


def _style_chart(chart, b: Chart, theme: Theme) -> None:
    """Recolor a native chart from the theme palette and add tidy data
    labels, on-brand gridlines/legend, and no default Office chrome.
    Best-effort: callers wrap this so a styling hiccup never drops the chart."""
    palette = _series_palette(theme)
    plot = chart.plots[0]
    _hide_chart_border(chart)
    if b.chart == "pie":
        ser = plot.series[0]
        fills = [palette[i % len(palette)] for i in range(len(ser.points))]
        for i, pt in enumerate(ser.points):
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(fills[i])
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.show_percentage = True
        dl.show_value = False
        dl.number_format = "0%"
        dl.number_format_is_linked = False
        dl.font.size = Pt(10)
        dl.font.bold = True
        dl.font.name = theme.font_heading  # numeric chrome; see _style_axes
        for i, pt in enumerate(ser.points):
            lf = pt.data_label.font
            lf.size = Pt(10)
            lf.bold = True
            lf.name = theme.font_heading
            lf.color.rgb = _rgb(_label_fg(fills[i], theme))
            # Materializing pt.data_label.font (above) creates a per-point
            # c:dLbl element that python-pptx emits with its OWN default
            # show flags (showVal=1, showPercent=0), silently overriding the
            # plot-level dl.show_percentage=True set two lines up and turning
            # every slice label back into a raw value like "5.4" instead of
            # "54%" (P5 audit defect 14; confirmed by inspecting chart1.xml,
            # not a LibreOffice rendering artifact). Force this point's own
            # flags to match.
            _fix_dlbl_show_percent(pt.data_label._dLbl)
    else:
        for i, ser in enumerate(plot.series):
            color = _rgb(palette[i % len(palette)])
            if b.chart == "line":
                ser.format.line.color.rgb = color
                ser.format.line.width = Pt(2.25)
            elif b.chart == "scatter":
                ser.marker.format.fill.solid()
                ser.marker.format.fill.fore_color.rgb = color
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
            dl.font.name = theme.font_heading  # numeric chrome; see _style_axes
            dl.font.color.rgb = _rgb(theme.text)
            try:
                dl.position = XL_LABEL_POSITION.OUTSIDE_END
            except (ValueError, NotImplementedError):
                pass
        _style_axes(chart, theme)
    if chart.has_legend:
        leg = chart.legend
        leg.position = XL_LEGEND_POSITION.BOTTOM
        leg.font.size = Pt(11)
        leg.font.name = theme.font_body
        leg.font.color.rgb = _rgb(theme.text)


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
    scale=1.0,
) -> float:
    color = color or theme.text
    font = font or theme.font_body
    size = size * scale
    h = min(max_h, _est_lines(plain(rt), size, w - indent) * _line_h(size))
    tf = _box(slide, x + indent, y, w - indent, h)
    _runs(tf.paragraphs[0], rt, theme, numbers, size, color, font, bold, italic)
    return h


def _list_block(slide, b, theme, numbers, x, y, w, max_h, ordered: bool,
                scale: float = 1.0) -> float:
    bp = BODY_PT * scale
    item_gap = LIST_ITEM_GAP_PT / 72 * scale
    est = sum(
        _est_lines(plain(it.text), bp, w - 0.35 * (it.level + 1))
        * _line_h(bp)
        + item_gap
        for it in b.items
    )
    h = min(max_h, est)
    tf = _box(slide, x, y, w, h)
    counters: dict[int, int] = {}
    for i, it in enumerate(b.items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(LIST_ITEM_GAP_PT)
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
    # Reserve the attribution's own strip BEFORE laying out the quote text
    # (mirroring _place_picture's already-correct caption-reserved-upfront
    # pattern), so an authored attribution is never silently dropped just
    # because the quote text itself used up the whole box -- the same class
    # of silent loss as the trailing-block "ponytail" bug in _body, one
    # field down.
    attr_h = QUOTE_ATTR_H_IN if b.attribution else 0.0
    h = _text_block(
        slide, b.text, theme, numbers, x, y, w, max(0.1, max_h - attr_h),
        size=15, italic=True, indent=0.4,
    )
    if b.attribution:
        tf = _box(slide, x + 0.4, y + h + 0.04, w - 0.4, 0.24)
        _runs(
            tf.paragraphs[0], "\u2014 " + b.attribution, theme, numbers,
            12, theme.muted, theme.font_body,
        )
        h += QUOTE_ATTR_H_IN
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


def _table_row_h(size: float) -> float:
    """The real minimum row height PowerPoint will honor for a run at `size`
    with the cell top/bottom margins set below: text height plus padding."""
    return _line_h(size) + TABLE_VPAD


def _table_col_widths(header, rows, cols: int, tw: float) -> list[float]:
    """Column widths weighted by each column's longest plain-text content,
    instead of an equal tw/cols split. An equal split gives "Vendor" and
    "Time to value" the same track as "$18", so the long label crowds its
    cell while the short value floats in a mostly-empty one (P5 audit
    defect 8). Clamp to [0.9in, 40% of tw] so no column collapses unreadably
    narrow or swallows the whole table, then rescale to land exactly on tw.

    `cols` is the caller's own column count (it may exceed len(header) for a
    genuinely empty table, which normalize_table leaves at width 0 -- the
    caller still renders a 1-column table for it, so this must match)."""
    if cols <= 0:
        return []
    lengths = [
        max(
            [1]
            + ([len(plain(header[c]))] if c < len(header) else [])
            + [len(plain(r[c])) for r in rows if c < len(r)]
        )
        for c in range(cols)
    ]
    total = sum(lengths)
    min_w, max_w = 0.9, 0.4 * tw
    raw = [max(min_w, min(max_w, tw * n / total)) for n in lengths]
    scale = tw / sum(raw)
    return [rw * scale for rw in raw]


def _table_block(slide, b, theme, numbers, x, y, w, max_h) -> float:
    header, rows = normalize_table(b.header, b.rows)
    cols = max(len(header), 1)
    tw = min(w, max(3.0, cols * 2.5))
    tx = x + (w - tw) / 2 if tw < w else x  # center a narrower-than-column table
    cap_h = CAPTION_H_IN if b.caption else 0.0
    budget = max(_table_row_h(TABLE_MIN_PT), max_h - cap_h)

    # shrink the font (down to a floor) until every row fits at that size,
    # since row height is tied to what the font actually needs, not an
    # independent division PowerPoint won't honor
    size = TABLE_PT
    while size > TABLE_MIN_PT and _table_row_h(size) * (len(rows) + 1) > budget:
        size -= 1
    row_h = _table_row_h(size)

    # still too many rows at the floor size: cap the count and add a visible
    # "+N more rows" notice instead of silently overflowing the slide
    room = max(1, int(budget / row_h))
    more = 0
    if len(rows) + 1 > room:
        keep = max(0, room - 2)  # header row + the notice row both cost a slot
        more = len(rows) - keep
        # The "+N more rows" notice IS visible (never a blank gap), but the
        # dropped rows' own cell text carries no trace anywhere else in the
        # deck -- this used to be a genuinely silent loss with zero warning,
        # caught only by rendering-and-reading the actual XML, not by any
        # structural test (none asserted on truncated row CONTENT, only
        # counts).
        ident = f" (id={b.id!r})" if b.id else ""
        warnings.warn(
            f"pptx: table{ident} truncated to {keep} of {len(rows)} rows to "
            f"fit; the dropped rows are summarized by a \"+{more} more "
            "row(s)\" notice but their own cell text is not rendered -- "
            "move the full table to the report or a sheet",
            stacklevel=2,
        )
        rows = rows[:keep]

    n_rows = len(rows) + 1 + (1 if more else 0)
    th = n_rows * row_h
    frame = slide.shapes.add_table(
        n_rows, cols, Inches(tx), Inches(y), Inches(tw), Inches(th)
    )
    tbl = frame.table
    # Table has no authored alt field (unlike Image/Artifact); the caption is
    # the closest authored description, falling back to the column headers
    # so a screen reader still gets SOMETHING beyond "table, 3 by 4" (see
    # _set_alt's docstring for why this reaches into the oxml by hand).
    cols_desc = ", ".join(t for t in (plain(c) for c in header) if t)
    _set_alt(frame, b.caption or (f"Table with columns: {cols_desc}" if cols_desc else "Table"),
             title="Table")
    tbl.first_row = False
    tbl.horz_banding = False
    col_w = _table_col_widths(header, rows, cols, tw)
    for c in range(cols):
        tbl.columns[c].width = Inches(col_w[c])
    for r in range(n_rows):
        tbl.rows[r].height = Inches(row_h)
    for r in range(len(rows) + 1):
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
                # font_heading, not font_body: a table is data-dense chrome
                # (prices, dates, short labels), and Georgia's old-style
                # figures make its numbers look like typos (P5 audit
                # defect 13), same reasoning as _style_axes.
                _runs(
                    cell.text_frame.paragraphs[0], cells[c], theme, numbers,
                    size, color, theme.font_heading, bold=(r == 0),
                )
    if more:
        r = n_rows - 1
        note = tbl.cell(r, 0)
        if cols > 1:
            note.merge(tbl.cell(r, cols - 1))
        note.fill.solid()
        note.fill.fore_color.rgb = _rgb(theme.surface)
        note.vertical_anchor = MSO_ANCHOR.MIDDLE
        note.margin_left = note.margin_right = Inches(0.08)
        note.margin_top = note.margin_bottom = Inches(0.03)
        _runs(
            note.text_frame.paragraphs[0],
            f"+ {more} more row{'s' if more != 1 else ''}",
            theme, numbers, size, theme.muted, theme.font_body, italic=True,
        )
    h = th
    # cap_h was already reserved above (before the row/font-shrink budget
    # was even computed), so the caption always fits and is always drawn --
    # not gated on "if there happens to be room left over", which is exactly
    # the pattern that let a caption vanish silently when a slide ran tight.
    if b.caption:
        tf = _box(slide, tx, y + h + 0.04, tw, 0.22)
        _runs(
            tf.paragraphs[0], b.caption, theme, numbers,
            11, theme.muted, theme.font_body, italic=True,
        )
        h += CAPTION_H_IN
    return h


def _callout_block(slide, b, theme, numbers, x, y, w, max_h,
                   scale: float = 1.0) -> float:
    size, pad, edge_w = 13 * scale, 0.14, 0.08
    est = _est_lines(plain(b.text), size, w - edge_w - 2 * pad) * _line_h(size) + 2 * pad
    h = min(max_h, est)
    _rect(slide, x, y, w, h, _callout_fill_color(b.style, theme))
    _rect(slide, x, y, edge_w, h, _callout_edge_color(b.style, theme))
    tf = _box(slide, x + edge_w + pad, y + pad, w - edge_w - 2 * pad, h - 2 * pad)
    _runs(tf.paragraphs[0], b.text, theme, numbers, size, theme.text, theme.font_body)
    return h


def _placeholder_block(slide, x, y, w, max_h, theme, alt: str, caption: str | None) -> float:
    """A visible stand-in for a block that could not be embedded (an
    unresolved reference, a missing file, or a format python-pptx cannot
    decode), so content never silently disappears from the deck (P5 audit
    defect 1). Mirrors the DOCX placeholder paragraph's intent, as a native
    PPTX shape with its alt text and caption legible on the slide."""
    # Reserve the caption's own strip BEFORE sizing the placeholder box
    # (same upfront-reservation fix as _quote_block/_table_block/
    # _chart_block above), so a caption on a placeholder is never silently
    # dropped just because max_h was tight.
    cap_h = CAPTION_H_IN if caption else 0.0
    h = min(max(0.1, max_h - cap_h), 1.6)
    box = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb(theme.surface)
    box.line.color.rgb = _rgb(_tint(theme.muted, 0.35))
    box.line.width = Pt(1)
    box.shadow.inherit = False
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Inches(0.2)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = alt or "Content unavailable"
    run.font.size = Pt(12)
    run.font.italic = True
    run.font.name = theme.font_body
    run.font.color.rgb = _rgb(theme.muted)
    if caption:
        cap_tf = _box(slide, x, y + h + 0.04, w, 0.22)
        _runs(
            cap_tf.paragraphs[0], caption, theme, {}, 11, theme.muted, theme.font_body,
            italic=True,
        )
        h += CAPTION_H_IN
    return h


def _set_alt(shape, alt: str, title: str | None = None) -> None:
    """Set a shape's accessible name (docPr title) and description (docPr
    descr) from an authored alt/caption string. python-pptx exposes no
    high-level alt-text setter for ANY shape type (the same reason
    diagram_pptx.py's raster fallback reaches into the oxml element
    directly), so this writes the docPr attributes by hand. Originally
    pictures only (`pic._element.nvPicPr.cNvPr`): an Image/Artifact's alt
    text reached HTML (alt=) and Markdown (![alt]) but never PPTX, a genuine
    accessibility gap invisible to every existing structural test because
    alt was never expected to render as slide TEXT in the first place.
    Generalized via `_element._nvXxPr` -- python-pptx names the non-visual
    properties container differently per shape type (nvPicPr for pictures,
    nvGraphicFramePr for charts/tables, nvSpPr for plain autoshapes,
    nvGrpSpPr for groups), but every one of those XML classes exposes the
    same `_nvXxPr` alias to its own container, so one call site now covers
    all of them. Best-effort: a metadata hiccup here must never fail the
    embed itself."""
    if not alt:
        return
    try:
        shape._element._nvXxPr.cNvPr.set("descr", alt)
        shape._element._nvXxPr.cNvPr.set("title", title or alt)
    except Exception:
        pass


def _add_png(slide, png: bytes | None, x, y):
    """Add PNG bytes as a picture shape; None if python-pptx refuses them.
    Best-effort: callers that must never render silently should check for
    None and fall back to _placeholder_block themselves (see
    _image_block_or_placeholder)."""
    if not png:
        return None
    try:
        return slide.shapes.add_picture(io.BytesIO(png), Inches(x), Inches(y))
    except Exception:
        return None


def _add_picture(slide, path: Path, theme, x, y):
    """Add an image file as a picture shape, rasterizing an SVG first:
    python-pptx has no SVG decoder, so without the optional rasterizer extra
    (docloom[diagrams]) an SVG cannot be embedded and None is returned, which
    keeps the caller's existing fallback (silent for _chart_block's own
    tiered fallback chain, a placeholder for _image_block_or_placeholder)."""
    if raster.is_svg(path):
        return _add_png(
            slide,
            raster.svg_file_to_png(
                path, width=RASTER_PX, font_files=raster.theme_font_files(theme)
            ),
            x, y,
        )
    try:
        return slide.shapes.add_picture(str(path), Inches(x), Inches(y))
    except Exception:
        return None


def _place_picture(slide, pic, caption, theme, x, y, w, max_h) -> float:
    """Fit an already-added picture into the (w, max_h) slot, center it, and
    draw its caption. Returns the height consumed, in inches."""
    cap_h = IMAGE_CAPTION_H_IN if caption else 0.0
    scale = min(
        Inches(w) / pic.width, Inches(max(0.4, max_h - cap_h)) / pic.height, 1.0
    )
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    pic.left = Inches(x) + (Inches(w) - pic.width) // 2
    h = pic.height / 914400
    if caption:
        tf = _box(slide, x, y + h + 0.05, w, 0.22)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        _runs(
            tf.paragraphs[0], caption, theme, {}, 11, theme.muted, theme.font_body,
            italic=True,
        )
        h += cap_h
    return h


def _image_block(slide, b, theme, x, y, w, max_h) -> float:
    """Embed b.path as a picture; 0.0 (no shape at all) if there is nothing
    to embed or embedding fails. Used directly by _chart_block's own
    tiered fallback chain (which has a further fallback of its own -- a
    painted chart, then a data table -- so a placeholder here would
    pre-empt a strictly better outcome). Callers that want a visible
    placeholder instead of silence on failure should use
    _image_block_or_placeholder."""
    # slots carrying only query/asset_id render as nothing in v0.2 pptx: a
    # deliberate empty slot, never a failure, so always silent regardless
    # of which wrapper the caller uses.
    if not b.path:
        return 0.0
    path = Path(b.path)
    if not path.is_file():
        return 0.0
    pic = _add_picture(slide, path, theme, x, y)
    if pic is None:
        return 0.0
    _set_alt(pic, b.alt)
    return _place_picture(slide, pic, b.caption, theme, x, y, w, max_h)


def _image_block_or_placeholder(slide, b, theme, x, y, w, max_h) -> float:
    """Like _image_block, but a reference that looked resolvable and then
    failed to embed (a path given but the file is missing, or the file
    exists but python-pptx cannot decode it, e.g. an SVG without the raster
    extra) draws a visible placeholder and warns instead of the block just
    vanishing (P5 audit defect 1). A block with no path at all is left to
    _image_block's silent no-op: that is a deliberate empty slot, not a
    failure, matching the docx renderer's convention."""
    h = _image_block(slide, b, theme, x, y, w, max_h)
    if h > 0.0 or not b.path:
        return h
    warnings.warn(
        f"pptx: image could not be embedded ({b.path!r}); placeholder shown",
        stacklevel=2,
    )
    return _placeholder_block(slide, x, y, w, max_h, theme, b.alt, b.caption)


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
    if b.chart == "pie" and len(b.series) > 1:
        # a native pie can only carry one series; raise so the caller falls
        # back to a data table instead of silently dropping the rest
        raise ValueError("pie chart supports only a single series")
    data = CategoryChartData()
    data.categories = (list(b.labels) + [""] * n)[:n]
    for s in b.series:
        # pad ragged series with None (blank points); pptx accepts None values
        data.add_series(s.name or "", (list(s.values) + [None] * n)[:n])
    return data


def _chart_table(b: Chart) -> Table:
    header: list[RichText] = [b.title or ""] + [
        s.name or f"Series {i + 1}" for i, s in enumerate(b.series)
    ]
    rows: list[list[RichText]] = []
    n = max([len(b.labels)] + [len(s.values) for s in b.series], default=0)
    for i in range(n):
        label = b.labels[i] if i < len(b.labels) else ""
        row: list[RichText] = [label]
        for s in b.series:
            v = s.values[i] if i < len(s.values) else None
            row.append("" if v is None else f"{v:g}")
        rows.append(row)
    return Table(header=header, rows=rows, caption=b.caption)


def _chart_block(slide, b: Chart, theme, numbers, x, y, w, max_h,
                 solo: bool = False) -> float:
    cap_h = CAPTION_H_IN if b.caption else 0.0
    try:
        data = _chart_data(b)
        room = max_h - cap_h
        # a chart alone on its slide fills the whole body instead of
        # stopping at the general cap: capping it there left a permanent
        # dead void below the chart (P5 audit defect 6).
        h = room if solo else min(room, LAYOUT["chart_max_h_in"])
        if h < 1.0:
            raise ValueError("not enough room for a native chart")
        frame = slide.shapes.add_chart(
            CHART_TYPE[b.chart], Inches(x), Inches(y), Inches(w), Inches(h), data
        )
        # Chart has no authored alt field (unlike Image/Artifact); the title
        # is the closest authored accessible name and the caption the closest
        # description, same reasoning as the table's descr above.
        _set_alt(frame, b.caption or b.title or f"{b.chart.capitalize()} chart",
                 title=b.title or "Chart")
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
        else:
            chart.has_legend = False
        if b.chart == "bar":
            # Office's native default draws horizontal-bar categories
            # first-at-bottom; every other docloom painter (chart_svg, used
            # by HTML/DOCX/Typst and this file's own fallback) draws
            # first-at-top. Flip only the category axis so the same Chart IR
            # reads the same way across every format.
            chart.category_axis.reverse_order = True
        try:
            _style_chart(chart, b, theme)
        except Exception:
            pass  # on-brand recolor is best-effort; keep the native chart
    except Exception:
        # fallback chain: pre-rendered image if present and embeddable, else a data table
        if b.path and Path(b.path).is_file():
            try:
                h = _image_block(
                    slide, Image(path=b.path, alt=b.title or "", caption=b.caption),
                    theme, x, y, w, max_h,
                )
            except Exception:
                h = 0.0  # unreadable: fall through to the table
            if h > 0.0:
                return h
            # unembeddable (corrupt, or SVG with no rasterizer installed):
            # fall through to the painted chart, then to the table
        pic = _add_png(
            slide,
            raster.svg_to_png(
                chart_svg.render_svg(b, theme),
                width=RASTER_PX,
                font_files=raster.theme_font_files(theme),
            ),
            x, y,
        )
        if pic is not None:  # docloom's own chart painter, rasterized
            return _place_picture(slide, pic, b.caption, theme, x, y, w, max_h)
        return _table_block(slide, _chart_table(b), theme, numbers, x, y, w, max_h)
    # cap_h was already reserved above (room = max_h - cap_h), so this always
    # fits and is always drawn -- see the same fix on _table_block/
    # _quote_block/_placeholder_block above.
    if b.caption:
        tf = _box(slide, x, y + h + 0.04, w, 0.22)
        _runs(
            tf.paragraphs[0], b.caption, theme, numbers,
            11, theme.muted, theme.font_body, italic=True,
        )
        h += CAPTION_H_IN
    return h


def _stats_block(slide, b: StatRow, theme, x, y, w, max_h) -> float:
    items = b.items[: LAYOUT["stat_max_cards"]]  # extras dropped: more cards than this don't fit legibly on one row
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
        # label/delta raised from 11/10pt: footnote-scale on a 13.3in slide,
        # unreadable from the back of a room (P5 audit defect 12). The card
        # has the room: measured content bottoms out well inside stat_card_h_in.
        if ty + 0.26 <= y + h:
            tf = _box(slide, cx + pad, ty, cw - 2 * pad, 0.26)
            _runs(tf.paragraphs[0], st.label, theme, {}, 13, theme.muted, theme.font_body)
            ty += 0.32
        if st.delta and ty + 0.22 <= y + h:
            tf = _box(slide, cx + pad, ty, cw - 2 * pad, 0.22)
            delta_color = theme.muted if st.delta.strip().startswith("-") else theme.accent
            _runs(tf.paragraphs[0], st.delta, theme, {}, 12, delta_color, theme.font_body)
    return h


def _block(slide, b: Block, theme, numbers, x, y, w, max_h,
          scale: float = 1.0, solo: bool = False) -> float:
    if isinstance(b, Heading):
        return _text_block(
            slide, b.text, theme, numbers, x, y, w, max_h,
            size=HEAD_PT[b.level], font=theme.font_heading, bold=True, scale=scale,
        )
    if isinstance(b, Paragraph):
        return _text_block(slide, b.text, theme, numbers, x, y, w, max_h, scale=scale)
    if isinstance(b, (BulletList, NumberedList)):
        return _list_block(
            slide, b, theme, numbers, x, y, w, max_h, isinstance(b, NumberedList),
            scale=scale,
        )
    if isinstance(b, Quote):
        return _quote_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, Code):
        return _code_block(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Table):
        return _table_block(slide, b, theme, numbers, x, y, w, max_h)
    if isinstance(b, Callout):
        return _callout_block(slide, b, theme, numbers, x, y, w, max_h, scale=scale)
    if isinstance(b, Image):
        return _image_block_or_placeholder(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Chart):
        return _chart_block(slide, b, theme, numbers, x, y, w, max_h, solo=solo)
    if isinstance(b, StatRow):
        return _stats_block(slide, b, theme, x, y, w, max_h)
    if isinstance(b, Diagram):
        # Native, editable PPTX shapes (P2: docs/diagram-plan.md section 4b).
        # All layout/fit/font-floor-degradation/fallback/hash-stamp logic
        # lives in diagram_pptx.py; this hook only solves once at "full"
        # detail and hands off, matching add_diagram(d, solved, theme, ...).
        if not b.nodes:
            return 0.0
        try:
            solved = diagram_svg.solve(
                b, diagram_pptx.theme_dict(theme),
                target_aspect=(w / max_h if max_h > 0 else 2.0),
            )
        except Exception:
            warnings.warn(
                f"pptx: diagram {b.id!r} failed to solve; placeholder shown",
                stacklevel=2,
            )
            return diagram_pptx.placeholder(slide, b, theme, x, y, w, max_h)
        return diagram_pptx.add_diagram(slide, b, solved, theme, x, y, w, max_h)
    if isinstance(b, Artifact):
        # An Artifact is a reference to content the author explicitly put on
        # the slide (a diagram, an infographic); unlike a plain Image slot
        # it never represents a deliberately-empty placeholder, so every
        # failure mode here draws a visible placeholder instead of the
        # block just vanishing with no trace (P5 audit defect 1, confirmed
        # live: an Artifact with no path rendered nothing at all).
        if b.path:
            h = _image_block_or_placeholder(
                slide, Image(path=b.path, alt=b.alt, caption=b.caption),
                theme, x, y, w, max_h,
            )
            if h > 0.0:
                return h
        warnings.warn(
            f"pptx: unresolved artifact (kind={b.kind!r}, no usable path); "
            "placeholder shown",
            stacklevel=2,
        )
        return _placeholder_block(slide, x, y, w, max_h, theme, b.alt, b.caption)
    if isinstance(b, Divider):
        _rect(slide, x, y + 0.06, w, 0.02, _divider_color(theme))
        return 0.14
    raise RenderError(f"unhandled block type {type(b).__name__}")


def _natural_h(b: Block, w: float, scale: float = 1.0,
               theme=None, max_h: float | None = None) -> float:
    """Estimate a block's natural (unclamped) height in inches, so _body can
    tell when a slide is underfull and rebalance the whitespace. `scale`
    mirrors the font-size growth _block applies for the same block types, so
    the grow pass can verify a candidate scale against this same formula.

    `theme`/`max_h` let a Diagram report its TRUE fitted height (a wide, short
    architecture diagram fits to the slide width and renders far shorter than
    the flat DIAGRAM_H_IN reserve): without this, the layout reserves 4.6in for
    a ~1.5in band and leaves the rest as dead space at the slide bottom instead
    of centering the content."""
    if isinstance(b, Heading):
        s = HEAD_PT[b.level] * scale
        return _est_lines(plain(b.text), s, w) * _line_h(s)
    if isinstance(b, Paragraph):
        s = BODY_PT * scale
        return _est_lines(plain(b.text), s, w) * _line_h(s)
    if isinstance(b, (BulletList, NumberedList)):
        s = BODY_PT * scale
        item_gap = LIST_ITEM_GAP_PT / 72 * scale
        return sum(
            _est_lines(plain(it.text), s, w - 0.35 * (it.level + 1))
            * _line_h(s) + item_gap
            for it in b.items
        )
    if isinstance(b, Quote):
        h = _est_lines(plain(b.text), 15, w - 0.4) * _line_h(15)
        return h + (QUOTE_ATTR_H_IN if b.attribution else 0.0)
    if isinstance(b, Code):
        return len(b.code.split("\n")) * _line_h(12) + 0.24
    if isinstance(b, Table):
        _, rows = normalize_table(b.header, b.rows)
        return (len(rows) + 1) * _table_row_h(TABLE_PT) + (CAPTION_H_IN if b.caption else 0.0)
    if isinstance(b, Callout):
        s = 13 * scale
        return _est_lines(plain(b.text), s, w - 0.08 - 0.28) * _line_h(s) + 0.28
    if isinstance(b, StatRow):
        return LAYOUT["stat_card_h_in"] if b.items else 0.0
    if isinstance(b, Chart):
        return LAYOUT["chart_max_h_in"] + (CAPTION_H_IN if b.caption else 0.0)
    if isinstance(b, Diagram):
        cap = CAPTION_H_IN if b.caption else 0.0
        # Aspect-aware: solve once and return the height the diagram will
        # ACTUALLY occupy after fitting to width `w`, capped at DIAGRAM_H_IN.
        # A wide LR pipeline fits to width and renders as a short band, so this
        # is much less than 4.6in -- letting _body center the content rather
        # than stranding it at the top of a phantom 4.6in reserve.
        if theme is not None and max_h and max_h > 0 and b.nodes:
            try:
                s = diagram_svg.solve(
                    b, diagram_pptx.theme_dict(theme),
                    target_aspect=(w / max_h),
                )
                cw, ch = s.width / 96.0, s.height / 96.0
                if cw > 0 and ch > 0:
                    k = min(w / cw, max(0.1, max_h - cap) / ch)
                    return min(DIAGRAM_H_IN, k * ch) + cap
            except Exception:
                pass  # fall back to the flat reserve on any solve failure
        return DIAGRAM_H_IN + cap
    if isinstance(b, (Image, Artifact)):
        # a resolved image tends to fill much of the content area; estimate high
        # so a lone image centers near the top instead of floating low.
        cap = IMAGE_CAPTION_H_IN if b.caption else 0.0
        if b.path and Path(b.path).is_file():
            return 4.6 + cap
        # An unresolved Artifact still renders a real placeholder box now
        # (P5 audit defect 1), never nothing, so it must reserve real
        # layout room too -- an unresolved Image slot, unlike an Artifact,
        # stays a deliberate, genuinely weightless no-op (see
        # _image_block_or_placeholder), so it alone keeps the 0.0 estimate.
        return (1.6 + cap) if isinstance(b, Artifact) else 0.0
    if isinstance(b, Divider):
        return 0.14
    return _line_h(BODY_PT * scale)


def _grow_scale(blocks: list[Block], w: float, avail: float) -> float:
    """The underfull-slide grow factor for laying `blocks` out in `avail`
    inches: how much to scale up text-block font sizes so sparse content
    fills the space instead of floating small in the top third.

    Rendered height grows ~quadratically with font size (see _est_lines'
    chars-per-line and _line_h), so the candidate is a sqrt closed form, then
    verified with the same _natural_h formula the renderer uses and clamped
    down until every block actually fits: a block that fits at scale 1.0 must
    never be dropped by this feature.

    Suppressed entirely when `blocks` also carries a fixed-size block (a
    table, code, a chart, stat cards, an image): growing only the prose next
    to a block whose size the content itself dictates is what produced
    23.8pt paragraphs beside 12pt code on the same slide (P5 audit defect
    4)."""
    if not blocks or any(isinstance(b, _FIXED_SIZE_BLOCKS) for b in blocks):
        return 1.0
    n = len(blocks)
    nat = [max(0.0, _natural_h(b, w)) for b in blocks]
    text_h = sum(h for b, h in zip(blocks, nat) if isinstance(b, _GROWABLE_BLOCKS))
    fixed_h = sum(nat) - text_h
    total = sum(nat) + GAP * (n - 1)
    if not (total < avail * 0.65 and text_h > 0.2):
        return 1.0
    target = avail * 0.80 - fixed_h - GAP * (n - 1)
    candidate = (target / text_h) ** 0.5 if target > 0 else 1.0
    scale = max(1.0, min(GROW_CAP, candidate))
    # Absolute, size-relative safety net independent of GROW_CAP: grown body
    # text must never approach the title's own size (P5 audit defect 4).
    scale = min(scale, MAX_GROWN_PT / BODY_PT)
    for _ in range(20):  # bounded: at most (GROW_CAP - 1) / 0.05 steps
        scaled_text_h = sum(
            _natural_h(b, w, scale) for b in blocks if isinstance(b, _GROWABLE_BLOCKS)
        )
        if scale <= 1.0 or scaled_text_h + fixed_h + GAP * (n - 1) <= avail:
            return scale
        scale = max(1.0, round(scale - 0.05, 2))
    return scale


def _body(slide, blocks: list[Block], theme, numbers, x, y, w,
         scale: float | None = None) -> float:
    """Lay body blocks into [y, slide bottom]. When the content is sparse,
    distribute the slack (bigger inter-block gaps) and vertically center it at
    an optical-center bias, so slides don't plant everything in the top third.

    `scale` grows text-block font sizes on an underfull slide; pass an
    explicit value to share one scale across multiple columns of a slide
    (two_column does), otherwise it is computed from `blocks` alone."""
    bottom = SLIDE_H - MARGIN
    if not blocks:
        return y
    avail = bottom - y
    n = len(blocks)
    # A lone chart fills the whole body (see _chart_block's solo mode)
    # instead of stopping at the general cap and leaving a dead void below
    # it (P5 audit defect 6); treat its natural height as "fills avail" so
    # the slack pass below doesn't also push it down first.
    solo_chart = n == 1 and isinstance(blocks[0], Chart)
    nat = [max(0.0, _natural_h(b, w, theme=theme, max_h=avail)) for b in blocks]
    if solo_chart:
        cap_h = CAPTION_H_IN if blocks[0].caption else 0.0
        nat[0] = max(nat[0], avail - cap_h)
    text_h = sum(h for b, h in zip(blocks, nat) if isinstance(b, _GROWABLE_BLOCKS))
    fixed_h = sum(nat) - text_h
    if scale is None:
        scale = _grow_scale(blocks, w, avail)
    scaled_text_h = text_h if scale <= 1.0 else sum(
        _natural_h(b, w, scale) for b in blocks if isinstance(b, _GROWABLE_BLOCKS)
    )
    scaled_total = scaled_text_h + fixed_h + GAP * (n - 1)

    # Seam gaps, one per block boundary, instead of one uniform gap: a slide
    # gave a heading-to-list seam the same 0.67in of slack as every other
    # seam, orphaning the heading from the very list it introduces while the
    # bottom of the column sat empty (P5 audit defect 5). The seam right
    # after a Heading stays tight; its share of the slack goes to the rest.
    seam_gaps = [GAP] * (n - 1)
    y0 = y
    if 0 < scaled_total < avail:  # distribute residual slack + optical-center
        slack = avail - scaled_total
        tight = [isinstance(b, Heading) for b in blocks[:-1]]
        n_loose = sum(1 for t in tight if not t) or 1
        loose_extra = min(0.5, slack * 0.5 / n_loose)
        tight_gap = GAP * 0.5
        seam_gaps = [tight_gap if t else GAP + loose_extra for t in tight]
        used = scaled_text_h + fixed_h + sum(seam_gaps)
        y0 = y + max(0.0, (avail - used) * 0.42)

    # Finding A: this loop used to hand each block whatever remained after
    # its predecessors ("greedy" allocation), then drop the current block
    # outright ("break") the moment that remainder fell under 0.3in -- an
    # authored block (a chart's own trailing sibling, a subtitle-shrunk
    # slide's last paragraph) could vanish from the XML with zero trace and
    # no lint signal, because an EARLIER fixed-size block (a chart, capped
    # at its own max) was free to eat the entire budget first.
    #
    # Fix: reserve MIN_BLOCK_RESERVE_IN (+ its seam gap) for every block
    # still to come, so the block being laid out right now can never be
    # given more room than leaves its successors with nothing. A block that
    # only needs less than its reservation still gets exactly what it needs
    # (this only lowers the CEILING offered to the current block, not its
    # actual rendered size), so a comfortably-fitting slide is unaffected --
    # this only bites when the slide is genuinely too full, in which case an
    # earlier block (a chart, a table, a placeholder) shrinks/self-clamps to
    # the smaller ceiling instead of starving a later block to nothing.
    total_nat = sum(nat) + GAP * (n - 1) if n > 1 else sum(nat)
    if total_nat > avail + 0.01:
        warnings.warn(
            f"pptx: slide content (~{total_nat:.2f}in) exceeds the available "
            f"body height (~{avail:.2f}in); blocks are shrunk/squeezed to fit "
            "instead of being dropped -- consider splitting the slide",
            stacklevel=2,
        )
    yy = y0
    for i, b in enumerate(blocks):
        remaining = bottom - yy
        n_later = n - i - 1
        reserve = n_later * (MIN_BLOCK_RESERVE_IN + GAP)
        block_max_h = max(MIN_BLOCK_RESERVE_IN, remaining - reserve)
        if isinstance(b, _VISUAL_BLOCKS) and block_max_h < MIN_VISUAL_BLOCK_H_IN:
            # Squeezed past the point of legibility: drop it (never shrink
            # it into a speck) and say so by name, rather than emit a
            # picture/diagram/chart/stat row no one could ever read.
            ident = f" (id={b.id!r})" if getattr(b, "id", None) else ""
            warnings.warn(
                f"pptx: dropping a {type(b).__name__.lower()} block{ident} "
                f"that would render at only {block_max_h:.2f}in tall (floor "
                f"{MIN_VISUAL_BLOCK_H_IN}in) -- too small to be legible; "
                "split the slide or move it to its own slide",
                stacklevel=2,
            )
            continue  # no shape drawn, no space consumed
        yy += _block(slide, b, theme, numbers, x, yy, w, block_max_h, scale, solo=solo_chart)
        if i < n - 1:
            yy += seam_gaps[i]
    return yy


# ----------------------------------------------------------------- layouts


def _slide_accent(s: Slide, theme: Theme) -> str:
    """Per-slide accent override for rules/edges; invalid hex falls back."""
    c = (s.accent or "").strip().lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return "#" + c.upper()
    return theme.primary


def _usable_image(img: Image | None) -> bool:
    return img is not None and bool(img.path) and Path(img.path).is_file()


def _logo(
    slide, img: Image, theme: Theme, *,
    max_h: float = LOGO_MAX_H, corner: str = "top_right", scrim: bool = False,
) -> None:
    """Place a brand logo in a slide corner, scaled to `max_h` tall (small
    source logos upscale to fill it; the max_w guard still caps a very wide
    logo, keeping aspect ratio either way).

    `corner` is "top_right" (default) or "top_left" (image_right puts its
    image pane at top-right, so the logo moves to the opposite corner there
    instead of landing on the image). `scrim` draws a small contrasting
    plate behind the logo, reusing `_rect`, for full-bleed layouts (section's
    solid fill, hero's cover image) where the corner isn't a plain
    background and the logo would otherwise risk being illegible."""
    try:
        pic = slide.shapes.add_picture(str(img.path), 0, 0)
    except Exception:
        return  # unreadable image (e.g. an unembeddable SVG): skip, don't fail the render
    _set_alt(pic, img.alt)
    scale = min(Inches(max_h) / pic.height, Inches(LOGO_MAX_W) / pic.width)
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    w_in, h_in = pic.width / 914400, pic.height / 914400
    left = MARGIN if corner == "top_left" else SLIDE_W - MARGIN - w_in
    top = MARGIN
    if scrim:
        # A stark theme.background (white) plate around a small logo reads
        # as a pasted-on sticker, not brand (P5 audit defect 11), especially
        # on the primary-blue section slides. A soft tint of theme.primary
        # with a larger pad reads as an intentional chip instead.
        pad = 0.14
        plate = _rect(
            slide, left - pad, top - pad, w_in + 2 * pad, h_in + 2 * pad,
            _tint(theme.primary, 0.12),
        )
        pic._element.addprevious(plate._element)  # plate behind the logo picture
    pic.top = Inches(top)
    pic.left = Inches(left)


def _doc_logo(slide, doc: Document, s: Slide, theme: Theme) -> None:
    """Stamp the document's brand logo in a slide corner, small and the same
    ~0.5in target size on every layout.

    section (solid theme.primary fill) and hero (full-bleed cover image, or a
    solid theme.primary fill when the slide has no usable image -- see
    _hero_slide) get a contrasting scrim plate behind the logo since the
    corner there is not a plain background. image_right's image pane sits at
    the top-right
    corner, so its logo moves to top-left instead of landing on the image;
    image_left's pane is already on the left, so the default top-right
    corner is safe as-is. The title layout places its own logo (see
    _title_slide, which also falls back to doc.logo) so it is skipped here
    to avoid stamping it twice."""
    if s.layout == "title":
        return
    if not _usable_image(doc.logo):
        return
    if s.layout == "section":
        _logo(slide, doc.logo, theme, max_h=LOGO_MAX_H, scrim=True)
    elif s.layout == "hero":
        # hero always paints a full-bleed backdrop now (a photo, or a solid
        # theme.primary fill when there is no usable image -- see
        # _hero_slide), never a plain page background, so the logo needs the
        # same contrast scrim in both cases.
        _logo(slide, doc.logo, theme, max_h=LOGO_MAX_H, scrim=True)
    elif s.layout == "image_right" and _usable_image(s.image):
        _logo(slide, doc.logo, theme, max_h=LOGO_MAX_H, corner="top_left")
    else:
        _logo(slide, doc.logo, theme, max_h=LOGO_MAX_H)


def _logo_reserve(doc: Document) -> float:
    """Extra right-edge width `_title_band` should leave blank so a top-right
    logo never overlaps title text. Based on the logo's REAL scaled width
    (a logo is height-capped to LOGO_MAX_H, so a normal ~square logo is only
    ~0.5in wide), not the full-slide LOGO_MAX_W cap, so a small logo does not
    needlessly carve down a narrow image-side title column."""
    if not _usable_image(doc.logo):
        return 0.0
    try:
        from PIL import Image as _PILImage  # python-pptx already depends on Pillow

        with _PILImage.open(doc.logo.path) as im:
            nw, nh = im.size
        scaled_w = min(LOGO_MAX_W, LOGO_MAX_H * (nw / nh)) if nh else LOGO_MAX_H
    except Exception:
        scaled_w = LOGO_MAX_H  # conservative small default if it can't be measured
    return scaled_w + GAP


def _title_band(
    slide, title: str | None, theme: Theme,
    x: float = MARGIN, w: float | None = None, accent: str | None = None,
    reserve: float = 0.0, top: float = 0.42,
) -> float:
    """`reserve` shrinks the title's width to dodge a corner logo sharing the
    same horizontal band; `top` instead pushes the whole band down to dodge a
    logo sitting directly above it. Callers should use whichever actually
    matches the logo's position: carving x as well as width for a logo that
    is above (not beside) the title is what misaligned a title against its
    own body text one column over (P5 audit defect 3)."""
    if not title:
        return MARGIN
    w = SLIDE_W - x - MARGIN if w is None else w
    w = max(1.5, w - reserve)  # room for a corner logo without crowding the text past legibility
    min_h, size = 0.62, LAYOUT["title_pt"]
    box_h = max(min_h, _est_lines(title, size, w) * _line_h(size))
    tf = _box(slide, x, top, w, box_h)
    _runs(
        tf.paragraphs[0], title, theme, {},
        size, theme.text, theme.font_heading, bold=True,
    )
    rule_y = top + box_h + 0.10
    _rect(slide, x, rule_y, w, 0.028, accent or theme.primary)
    return rule_y + 0.028 + 0.252


def _subtitle_line(slide, subtitle: str | None, theme: Theme, x: float, y: float, w: float) -> float:
    """Render a slide's s.subtitle as a muted caption line beneath its title
    band, in the same treatment _hero_slide/_section_slide/_title_slide
    already give it. Returns the height consumed (0.0, drawing nothing, if
    there is no subtitle) so callers can just do `y += _subtitle_line(...)`.

    _content_slide, _image_side_slide, and two_column used to never call
    this at all -- the only layouts, besides title/section/hero/quote, that
    can carry a subtitle, and s.subtitle was silently dropped on every one
    of them despite being present in the IR (same defect class as finding 8's
    quote-slide bug)."""
    if not subtitle:
        return 0.0
    size = 15
    h = _est_lines(subtitle, size, w) * _line_h(size) + SUBTITLE_PAD_IN
    tf = _box(slide, x, y, w, h)
    _runs(tf.paragraphs[0], subtitle, theme, {}, size, theme.muted, theme.font_body)
    return h


def _title_slide(slide, s: Slide, doc: Document, theme: Theme,
                 numbers: dict[str, int], accent: str) -> None:
    _rect(slide, 0, 0, 0.3, SLIDE_H, accent)
    # brand logo (or any title-slide image) → corner; fall back to doc.logo
    # so a logo bound after generation still shows up here at export time
    logo_img = s.image if _usable_image(s.image) else doc.logo
    if _usable_image(logo_img):
        _logo(slide, logo_img, theme)
        if logo_img.caption:
            # This slot is deliberately treated as a small corner logo, not
            # a captioned figure -- there is no legible place to put a
            # caption next to a 0.5in mark. Warn by name instead of
            # silently dropping it (matching the "drawn, or warned about"
            # invariant), rather than squeeze illegible caption text next
            # to the logo just to say something got drawn.
            warnings.warn(
                f"pptx: title slide image caption {logo_img.caption!r} is "
                "not rendered (the image is shown as a small corner logo, "
                "not a captioned figure); move it into a body block if it "
                "must appear on the slide",
                stacklevel=2,
            )
    tx, ty, title_pt = 1.1, 2.5, 40
    tw = SLIDE_W - tx - MARGIN
    title_text = s.title or doc.title
    title_h = max(1.4, _est_lines(title_text, title_pt, tw) * _line_h(title_pt))
    tf = _box(slide, tx, ty, tw, title_h)
    _runs(
        tf.paragraphs[0], title_text, theme, {},
        title_pt, theme.text, theme.font_heading, bold=True,
    )
    y = ty + title_h + 0.1
    subtitle = s.subtitle or doc.subtitle
    if subtitle:
        tf = _box(slide, tx, y, tw, 0.6)
        _runs(tf.paragraphs[0], subtitle, theme, {}, 20, theme.muted, theme.font_body)
        y += 0.6
    byline = "  \u00b7  ".join(p for p in (", ".join(doc.authors), doc.date) if p)
    if byline:
        by_y = max(6.3, y + 0.3)
        tf = _box(slide, tx, by_y, tw, 0.35)
        _runs(tf.paragraphs[0], byline, theme, {}, 14, theme.muted, theme.font_body)
        y = by_y + 0.35
    # Finding 2: _title_slide never read s.blocks/s.right at all -- unlike
    # every other layout (including section and quote, both fixed in
    # earlier audit passes), a title slide's entire body block list vanished
    # from the deck with zero trace. There is no established "body zone" on
    # a cover slide, so give it the remaining room below whatever was
    # actually drawn (subtitle and/or byline), down to the slide's own
    # bottom margin, using the same _body layout every content-bearing
    # layout already uses.
    blocks = s.blocks + s.right
    if blocks:
        body_y = y + 0.25
        if body_y < SLIDE_H - MARGIN - MIN_BLOCK_RESERVE_IN:
            _body(slide, blocks, theme, numbers, tx, body_y, tw)
        else:
            warnings.warn(
                f"pptx: title slide has no room left for its {len(blocks)} "
                "body block(s) below the title/subtitle/byline; move them "
                "to a content slide",
                stacklevel=2,
            )


def _section_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int]) -> None:
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.primary)
    _rect(slide, MARGIN, 2.72, 1.2, 0.05, theme.accent)
    tf = _box(slide, MARGIN, 3.0, SLIDE_W - 2 * MARGIN, 1.1)
    _runs(
        tf.paragraphs[0], s.title or "", theme, {},
        36, theme.background, theme.font_heading, bold=True,
    )
    y = 4.25
    if s.subtitle:
        tf = _box(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, 0.6)
        _runs(tf.paragraphs[0], s.subtitle, theme, {}, 18, theme.background, theme.font_body)
        y += 0.75
    # A section slide used to render only title/subtitle: any block the
    # author put in s.blocks/s.right (unlike every other layout, which at
    # least falls back through _content_slide) rendered nothing at all (P5
    # audit defect 2, a genuine data-loss bug). Draw them in the same
    # light-on-dark swap _hero_slide uses, since the background here is a
    # solid theme.primary fill, not the page background. `primary` also
    # swaps to `accent`: a bullet marker (drawn in theme.primary with
    # nothing behind it but the slide fill) would otherwise be theme.primary
    # on a theme.primary background -- genuinely invisible, not just low
    # contrast, caught by actually rendering and looking at this fix.
    blocks = s.blocks + s.right
    if blocks:
        _body(slide, blocks, _band_theme(theme, theme.primary), numbers,
              MARGIN, y, SLIDE_W - 2 * MARGIN)


def _quote_slide(slide, s: Slide, theme: Theme, numbers: dict[str, int]) -> None:
    """Two different authoring shapes both need to land on the same designed
    pull-quote, not just one of them: a real `Quote` block placed in
    s.blocks/s.right (the attribution living on the block itself), or the
    more obvious `Slide(layout="quote", title=..., subtitle=...)` with no
    Quote block at all. The latter used to fall straight through this
    function untouched: s.title rendered as a stray 16pt caption top-left,
    s.subtitle was never read anywhere in this function, and with q None
    nothing else drew at all -- a ~95% blank white slide with the
    attribution gone from the XML entirely (P5 audit finding 8). Normalize
    both shapes into one (quote_text, attribution, label) triple up front so
    there is exactly one pull-quote treatment: a full-height accent bar,
    real display-scale italic type, and the attribution set as a proper rule
    beneath it, matching the house style used by _quote_block elsewhere in
    this file.
    """
    body = s.blocks + s.right  # right column would otherwise be silently dropped
    q = next((b for b in body if isinstance(b, Quote)), None)
    rest = [b for b in body if b is not q]
    if q is not None:
        # A block-authored quote can still carry a slide title as a small
        # eyebrow label above it (e.g. title="Customer voice"); s.subtitle
        # only ever fills in as the attribution when the block itself didn't
        # set one, so it is never silently dropped either way -- except when
        # BOTH are authored: the block's own attribution wins and s.subtitle
        # is genuinely unused here (report renderers still keep it via
        # flatten_slides, so silence would be a cross-format drop). Warn by
        # name rather than let it vanish with no trace.
        if q.attribution and s.subtitle:
            warnings.warn(
                f"pptx: quote slide subtitle {s.subtitle!r} is not rendered "
                "(the Quote block already has its own attribution, which "
                "takes precedence); drop one of the two",
                stacklevel=2,
            )
        quote_text, attribution, label = q.text, q.attribution or s.subtitle, s.title
    else:
        # The obvious `title=`/`subtitle=` authoring with no Quote block:
        # the title IS the quote, the subtitle IS the attribution. No small
        # label is drawn in this shape -- the title already fills that role
        # as the quote itself, at full pull-quote scale.
        quote_text, attribution, label = s.title, s.subtitle, None
    if label:
        tf = _box(slide, MARGIN, 0.5, SLIDE_W - 2 * MARGIN, 0.4)
        _runs(tf.paragraphs[0], label, theme, {}, 16, theme.muted, theme.font_heading)
    y = 3.0
    if quote_text:
        qx, qw = 2.3, SLIDE_W - 4.6
        attr_h = 0.6 if attribution else 0.0
        avail = SLIDE_H - MARGIN - 1.4 - attr_h
        size = next(
            (pt for pt in (30, 24, 20, 16)
             if _est_lines(plain(quote_text), pt, qw) * _line_h(pt) <= avail),
            14,  # floor the ladder at 14pt; _fit_text_frames measures and bakes any further shrink
        )
        qh = min(avail, _est_lines(plain(quote_text), size, qw) * _line_h(size))
        y = max(1.4, (SLIDE_H - qh - 0.3 - attr_h) / 2)
        _rect(slide, qx - 0.35, y, 0.07, qh, theme.accent)
        tf = _box(slide, qx, y, qw, qh)
        _runs(tf.paragraphs[0], quote_text, theme, numbers, size, theme.text, theme.font_body,
              italic=True)
        y += qh + 0.25
        if attribution:
            tf = _box(slide, qx, y, qw, 0.35)
            _runs(
                tf.paragraphs[0], "\u2014 " + attribution, theme, {},
                16, theme.muted, theme.font_body,
            )
            y += 0.55
    if rest:
        _body(slide, rest, theme, numbers, MARGIN, y, SLIDE_W - 2 * MARGIN)


def _content_slide(slide, s: Slide, doc: Document, theme: Theme,
                   numbers: dict[str, int], accent: str) -> None:
    y = _title_band(slide, s.title, theme, accent=accent, reserve=_logo_reserve(doc))
    y += _subtitle_line(slide, s.subtitle, theme, MARGIN, y, SLIDE_W - 2 * MARGIN)
    _body(slide, s.blocks + s.right, theme, numbers, MARGIN, y, SLIDE_W - 2 * MARGIN)


def _cover_fit(pic, box_x: float, box_y: float, box_w: float, box_h: float,
               max_scale: float | None = None) -> None:
    """Scale `pic` to cover a box, cropping whichever dimension overflows
    instead of distorting the image to force-fit both dimensions. `max_scale`
    caps upscaling (pass 1.0 to never enlarge a low-res image beyond its
    native size; the axis that still doesn't reach the box is centered)."""
    img_w, img_h = pic.width, pic.height
    bw, bh = Inches(box_w), Inches(box_h)
    scale = max(bw / img_w, bh / img_h)
    if max_scale is not None:
        scale = min(scale, max_scale)
    disp_w, disp_h = img_w * scale, img_h * scale
    crop_x = 1 - bw / disp_w if disp_w > bw else 0.0
    crop_y = 1 - bh / disp_h if disp_h > bh else 0.0
    pic.crop_left = pic.crop_right = crop_x / 2
    pic.crop_top = pic.crop_bottom = crop_y / 2
    pic.width = int(min(disp_w, bw))
    pic.height = int(min(disp_h, bh))
    pic.left = Inches(box_x) + (bw - pic.width) // 2
    pic.top = Inches(box_y) + (bh - pic.height) // 2


def _contain_fit(pic, box_x: float, box_y: float, box_w: float, box_h: float,
                 max_scale: float | None = None) -> None:
    """Scale `pic` to fit entirely inside a box (no crop, no distortion) and
    center it. Use for content images (diagrams, charts, screenshots) where
    cropping an edge would lose information; the surrounding matte should be
    filled by the caller so the residual band reads as a frame, not an error."""
    img_w, img_h = pic.width, pic.height
    bw, bh = Inches(box_w), Inches(box_h)
    scale = min(bw / img_w, bh / img_h)
    if max_scale is not None:
        scale = min(scale, max_scale)
    pic.width = int(img_w * scale)
    pic.height = int(img_h * scale)
    pic.left = Inches(box_x) + (bw - pic.width) // 2
    pic.top = Inches(box_y) + (bh - pic.height) // 2


def _hero_slide(slide, s: Slide, doc: Document, theme: Theme, numbers: dict[str, int],
                accent: str) -> None:
    """A hero's whole design is a bold full-bleed backdrop behind a title
    band. That backdrop is s.image when there is one; when there is not (no
    image at all, or the file failed to embed), it used to fall all the way
    through to _content_slide -- a hero WITHOUT an image never reached this
    function at all (the dispatcher gated entry on _usable_image(s.image)),
    landing on a layout with no full-bleed treatment and (until fixed there
    too) that silently dropped s.subtitle from the XML entirely, leaving a
    slide like Slide(layout="hero", title=..., subtitle=...) ~90% blank.
    Fall back to a solid theme.primary fill instead -- the same full-bleed
    language _section_slide already uses -- so an imageless hero still gets
    the real hero treatment (the dark title band, subtitle, and blocks)
    rather than degrading to a generic bullet layout."""
    pic = None
    if _usable_image(s.image):
        try:
            pic = slide.shapes.add_picture(str(s.image.path), 0, 0)
        except Exception:
            pic = None  # unreadable file: fall back to the solid-fill hero below
    if pic is not None:
        _set_alt(pic, s.image.alt)
        _cover_fit(pic, 0.0, 0.0, SLIDE_W, SLIDE_H)
    else:
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(theme.primary)
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
    # Finding 1 (strongest case of the silent-drop invariant): s.image.caption
    # is genuine authored content -- DOCX/HTML/MD all keep it (flatten_slides
    # turns a deck-only document's s.image into a real Image block for the
    # report renderers) -- but this function never drew it at all. Only
    # meaningful when there IS a picture (pic is not None); reserve its strip
    # up front like every other caption in this file.
    cap_h = 0.0
    if pic is not None and s.image.caption:
        cap_h = _est_lines(s.image.caption, 11, tw) * _line_h(11) + 0.08
    if pic is not None or not blocks:
        # A photo-backed hero (or an imageless one with no blocks at all)
        # keeps the original short caption-strip sizing: capped at 60% of
        # the slide so most of the photo stays visible, and a flat 1.7in
        # guess for blocks since they are meant to be a brief takeaway
        # layered over the photo, not a full body.
        band_h = min(SLIDE_H * 0.6, 2 * pad + title_h + sub_h + cap_h + (1.7 if blocks else 0.0))
    else:
        # Finding C: an imageless hero WITH blocks used to get the same
        # flat 1.7in guess meant for a short caption over a photo. There is
        # no photo to protect here (the whole slide is already a solid
        # theme.primary fill), so give blocks their real estimated height
        # -- the same formula every other layout uses -- instead of
        # crushing a diagram into a box so small its label font fell below
        # the legibility floor and silently degraded to an unreadable
        # raster image. Capped only by the slide's own bottom margin.
        blocks_h = sum(max(0.0, _natural_h(b, tw)) for b in blocks)
        blocks_h += GAP * max(0, len(blocks) - 1)
        band_h = min(SLIDE_H - MARGIN, 2 * pad + title_h + sub_h + cap_h + blocks_h)
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
    if cap_h > 0:
        # band_h already reserved exactly this much room (see cap_h above),
        # so clamp-and-draw like the subtitle above rather than gate on a
        # fixed epsilon: a hardcoded threshold here previously skipped the
        # caption on the exact boundary case band_h itself computed as
        # fitting (off by float slack, not a real space shortage).
        box_h = max(0.0, min(cap_h - 0.08, SLIDE_H - pad - y))
        if box_h > 0.02:
            tf = _box(slide, MARGIN, y, tw, box_h)
            _runs(
                tf.paragraphs[0], s.image.caption, theme, {}, 11, theme.surface,
                theme.font_body, italic=True,
            )
        y += cap_h
    if blocks:
        # blocks resolve their own fills/foregrounds against the band's
        # actual paint (theme.text), not the document's light background --
        # see _band_theme's docstring for why the old text/muted-only swap
        # shipped a contrast regression.
        _body(slide, blocks, _band_theme(theme, theme.text), numbers, MARGIN, y, tw)


def _image_side_slide(slide, s: Slide, doc: Document, theme: Theme,
                      numbers: dict[str, int], accent: str) -> None:
    pane_w = SLIDE_W * LAYOUT["image_pane_ratio"]
    left_side = s.layout == "image_left"
    px = 0.0 if left_side else SLIDE_W - pane_w
    pad = 0.35
    cap_h = IMAGE_CAPTION_H_IN if s.image.caption else 0.0
    pic = _add_picture(slide, Path(s.image.path), theme, px, 0.0)
    if pic is None:
        # Unembeddable file (an SVG with no rasterizer extra installed, or a
        # corrupt raster): previously this fell back to _content_slide, which
        # silently dropped the image, its alt text, and its caption, and drew
        # a full-width title that the image_right corner logo then overlapped
        # (a P5-class regression, since _content_slide never reads s.image).
        # Keep the side-pane layout and draw a placeholder in the pane instead,
        # so the logo/title geometry above still accounts for this being an
        # image layout, and nothing authored is silently lost.
        warnings.warn(
            f"pptx: image could not be embedded ({s.image.path!r}); "
            "placeholder shown", stacklevel=2,
        )
        box_h = min(1.6, SLIDE_H - 2 * pad - cap_h)
        ph_y = (SLIDE_H - (box_h + cap_h)) / 2
        _placeholder_block(
            slide, px + pad, ph_y, pane_w - 2 * pad, box_h + cap_h, theme,
            s.image.alt, s.image.caption,
        )
    else:
        _set_alt(pic, s.image.alt)
        # Contain-fit, not cover-fit: a side image is often a diagram or chart, and
        # cropping its edge would silently drop content. Never upscale past native.
        # Reserve the top/bottom pad and the caption strip BEFORE fitting: fitting
        # into the full SLIDE_H and only afterward re-anchoring the picture inside
        # the pad let a tall/portrait image's fitted height alone reach 7.5in, so
        # the pad push and the caption strip both landed off the bottom of the
        # slide (P5-class regression, measured live).
        avail_h = SLIDE_H - 2 * pad - cap_h
        _contain_fit(pic, px, pad, pane_w, avail_h, max_scale=1.0)
        # Matte a band that hugs the fitted image's height, not the full 7.5in
        # pane: contain-fitting a wide/short image (a banner) into this pane
        # otherwise leaves 80% of the pane as dead gray around a sliver of
        # image (P5 audit defect 7, measured live). _contain_fit already
        # centered the picture within the reduced-height box, so a matte band
        # the same height as the fitted image, centered the same way, encloses
        # it with a deliberate pad instead of a near-empty pane.
        #
        # Finding B: this layout never drew s.image.caption at all -- DOCX/HTML/
        # MD all keep it (flatten_slides turns a deck-only document's s.image
        # into a real Image block for the report renderers), only PPTX silently
        # dropped it. Reserve a caption strip below the image (same cap_h
        # literal _place_picture uses) inside the matte, instead of only
        # bracketing the image itself.
        fitted_h = pic.height / 914400
        matte_h = min(SLIDE_H, fitted_h + 2 * pad + cap_h)
        matte_y = (SLIDE_H - matte_h) / 2
        matte = _rect(slide, px, matte_y, pane_w, matte_h, theme.surface)
        pic._element.addprevious(matte._element)  # matte behind the picture
        # Re-anchor the picture to the top of its own pad within the (now
        # possibly taller) matte -- _contain_fit centered it across the
        # reduced-height box, which no longer matches once cap_h grows the
        # matte asymmetrically to make room for the caption strip below.
        pic.top = Inches(matte_y + pad)
        if s.image.caption:
            cap_tf = _box(slide, px, matte_y + pad + fitted_h + 0.05, pane_w, 0.22)
            cap_tf.paragraphs[0].alignment = PP_ALIGN.CENTER
            _runs(
                cap_tf.paragraphs[0], s.image.caption, theme, {}, 11, theme.muted,
                theme.font_body, italic=True,
            )
    tx = pane_w + MARGIN if left_side else MARGIN
    tw = SLIDE_W - pane_w - 2 * MARGIN
    logo_present = _usable_image(doc.logo)
    if left_side:
        # image_left keeps the logo top-right, over this pane's own outer
        # edge, so a width-only reserve (never carving x) keeps the title
        # band's left edge aligned with the body text below it.
        reserve = _logo_reserve(doc)
        y = _title_band(slide, s.title, theme, x=tx, w=tw, accent=accent, reserve=reserve)
    else:
        # image_right flips the logo to top-left, i.e. inside this same text
        # column: carving the title's x (the old behavior) staggered it
        # 0.66in right of the body text it introduces and cost it a wrapped
        # line for no reason (P5 audit defect 3, measured live). Push the
        # whole band down below the logo instead, at the column's real x/w.
        top = MARGIN + LOGO_MAX_H + GAP if (logo_present and s.title) else 0.42
        y = _title_band(slide, s.title, theme, x=tx, w=tw, accent=accent, top=top)
    # a titleless image slide starts its body at the very top, where the corner
    # logo also sits (top-left for image_right, top-right for image_left), so
    # push the body below the logo band to avoid overlapping it
    if not s.title and logo_present:
        y = max(y, MARGIN + LOGO_MAX_H + GAP)
    y += _subtitle_line(slide, s.subtitle, theme, tx, y, tw)
    _body(slide, s.blocks + s.right, theme, numbers, tx, y, tw)


def _render_slide(prs, blank, s: Slide, doc: Document, theme: Theme,
                  numbers: dict[str, int]) -> None:
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.background)
    accent = _slide_accent(s, theme)
    if s.layout == "title":
        _title_slide(slide, s, doc, theme, numbers, accent)
    elif s.layout == "section":
        _section_slide(slide, s, theme, numbers)
    elif s.layout == "quote":
        _quote_slide(slide, s, theme, numbers)
    elif s.layout == "hero":
        _hero_slide(slide, s, doc, theme, numbers, accent)
    elif s.layout in ("image_left", "image_right") and _usable_image(s.image):
        _image_side_slide(slide, s, doc, theme, numbers, accent)
    elif s.layout == "two_column":
        y = _title_band(slide, s.title, theme, accent=accent, reserve=_logo_reserve(doc))
        y += _subtitle_line(slide, s.subtitle, theme, MARGIN, y, SLIDE_W - 2 * MARGIN)
        col_w = (SLIDE_W - 2 * MARGIN - 0.5) / 2
        avail = (SLIDE_H - MARGIN) - y
        col_scales = [
            _grow_scale(cols, col_w, avail) for cols in (s.blocks, s.right) if cols
        ]
        col_scale = min(col_scales) if col_scales else 1.0
        _body(slide, s.blocks, theme, numbers, MARGIN, y, col_w, col_scale)
        _body(slide, s.right, theme, numbers, MARGIN + col_w + 0.5, y, col_w, col_scale)
    else:  # content, or an image layout without a usable image path
        _content_slide(slide, s, doc, theme, numbers, accent)
    _doc_logo(slide, doc, s, theme)
    if s.notes:
        slide.notes_slide.notes_text_frame.text = s.notes


def _sources_slide(prs, blank, doc: Document, theme: Theme) -> None:
    seen: dict[str, int] = {}
    lines: list[str] = []
    for src in doc.sources:
        if src.id in seen:
            continue  # dedupe so numbering matches the citation superscripts
        seen[src.id] = len(seen) + 1
        line = f"{seen[src.id]}. {src.title}"
        if src.publisher:
            line += f", {src.publisher}"
        if src.date:
            line += f" ({src.date})"
        if src.url:
            line += f", {src.url}"
        lines.append(line)

    # P5 audit defect 10: raise the size (12 -> 13 read as a footnote dump
    # under 5in of empty space) and hang-indent so a wrapped URL lines up
    # under the title text, not back under the number.
    size, item_gap, w = 13, 6 / 72, SLIDE_W - 2 * MARGIN
    hang = 0.34  # room for "12. " at this size before the hanging-indent wrap point
    title, tf, y, bottom, first = "Sources", None, 0.0, 0.0, True
    for line in lines:
        h = _est_lines(line, size, w - hang) * _line_h(size) + item_gap
        if tf is None or y + h > bottom:
            slide = prs.slides.add_slide(blank)
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = _rgb(theme.background)
            top = _title_band(slide, title, theme)
            bottom = SLIDE_H - MARGIN
            tf = _box(slide, MARGIN, top, w, bottom - top)
            title, y, first = "Sources (cont.)", top, True
            # Every other slide in the deck carries the brand logo; the
            # sources slide was the sole exception (P5 audit defect 10).
            if _usable_image(doc.logo):
                _logo(slide, doc.logo, theme, max_h=LOGO_MAX_H)
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(6)
        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", str(Inches(hang)))
        pPr.set("indent", str(-Inches(hang)))
        _runs(p, line, theme, {}, size, theme.muted, theme.font_body)
        y += h


def _frame_paras(tf) -> list[textfit.ParaSpec] | None:
    """Extract measurable ParaSpecs from a text frame. Returns None when any
    run lacks an explicit size or font name -- _runs always sets both, so a
    frame that doesn't match that shape is skipped rather than mis-measured."""
    paras = []
    for p in tf.paragraphs:
        runs = []
        for r in p.runs:
            if r.font.size is None or not r.font.name:
                return None
            runs.append(textfit.RunSpec(
                r.text or "", r.font.name, r.font.size.pt,
                bool(r.font.bold), bool(r.font.italic),
            ))
        pPr = p._p.pPr  # may be None; marL/indent are EMU string attributes
        marL = int(pPr.get("marL", "0")) if pPr is not None else 0
        indent = int(pPr.get("indent", "0")) if pPr is not None else 0
        sa = p.space_after
        paras.append(textfit.ParaSpec(
            runs=tuple(runs),
            space_after_pt=sa.pt if sa is not None else 0.0,
            first_indent_in=max(0, marL + indent) / 914400,
            cont_indent_in=max(0, marL) / 914400,
        ))
    return paras


def _fit_one_frame(shape) -> None:
    tf = shape.text_frame
    bodyPr = tf._txBody.bodyPr  # CT_TextBody.bodyPr is a required child (verified)
    if bodyPr.find(qn("a:normAutofit")) is None:
        return  # only _box frames opt in; diagram/table/chart text never carries it
    inner_w = (shape.width - tf.margin_left - tf.margin_right) / 914400
    inner_h = (shape.height - tf.margin_top - tf.margin_bottom) / 914400
    if inner_w <= 0.05 or inner_h <= 0.05:
        return
    paras = _frame_paras(tf)
    if not paras:
        return
    need = textfit.required_height_pt(paras, inner_w, 1.0, 0.0)
    if need <= inner_h * 72.0 + FIT_SLACK_PT:
        return  # fits (within slack): leave today's XML byte-identical
    res = textfit.fit_scale(paras, inner_w, inner_h, MIN_FIT_PT)
    if res.scale >= 1.0 and res.lnspc_reduction == 0.0:
        return
    for p in tf.paragraphs:
        if res.lnspc_reduction:
            p.line_spacing = 1.0 - res.lnspc_reduction  # <a:lnSpc><a:spcPct val="90000"/>
        for r in p.runs:
            # floor to half-point, never round up past the measured fit; the
            # MIN_FIT_PT clamp is defense-in-depth against half-point
            # truncation ever landing a run under the legibility floor that
            # fit_scale's own (protected-run-derived) scale already honours
            # -- but ONLY for runs that started at/above the floor. A run
            # authored below MIN_FIT_PT to begin with (a citation
            # superscript) is deliberate small typography, not a legibility
            # bug: it must scale down proportionally with the rest of the
            # frame, not get bumped back up above its own authored size.
            orig_pt = r.font.size.pt
            scaled_pt = int(orig_pt * res.scale * 2) / 2
            if orig_pt >= MIN_FIT_PT - 1e-9:
                scaled_pt = max(MIN_FIT_PT, scaled_pt)
            r.font.size = Pt(scaled_pt)
    # After baking, fontScale MUST be 100000: PowerPoint multiplies run sz by
    # fontScale at render time, so echoing the applied scale here would shrink
    # the text a second time in PowerPoint while every other renderer draws
    # the baked size once. 100000 == "already fits; keep autofit for edits".
    for tag in ("a:normAutofit", "a:spAutoFit", "a:noAutofit"):
        for el in bodyPr.findall(qn(tag)):
            bodyPr.remove(el)
    bodyPr.insert(0, bodyPr.makeelement(
        qn("a:normAutofit"), {"fontScale": "100000", "lnSpcReduction": "0"},
    ))
    if not res.fits:
        warnings.warn(
            f"pptx: text still exceeds its box at the {MIN_FIT_PT:g}pt "
            "legibility floor; it is drawn at the floor size and may overflow "
            "-- split the slide or shorten the text",
            stacklevel=2,
        )


def _fit_text_frames(prs) -> None:
    """Measured autofit pass over every _box text frame (see textfit.py's
    module docstring for why the bare normAutofit python-pptx writes is a
    lie everywhere except interactive desktop PowerPoint). Best-effort per
    frame: a corrupt font file or unexpected XML shape must never lose a
    render, so each frame failure degrades to today's behavior."""
    if not TEXTFIT_ENABLED:
        return
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            try:
                _fit_one_frame(shape)
            except Exception:
                continue


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
    _fit_text_frames(prs)
    prs.save(str(out_path))
    return Path(out_path)

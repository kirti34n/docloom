"""DOCX report renderer: Document IR to a native .docx via python-docx.

Theme tokens drive every color and font; rich text spans become formatted
runs, links become real hyperlinks, and cited spans get superscript
reference numbers resolved against doc.sources.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor
from docx.text.run import Run

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
    Span,
    StatRow,
    Table,
    cited_ids,
    normalize_table,
    report_blocks,
    source_numbers,
    spans,
)
from ..theme import Theme, hex_to_rgb
from . import RenderError

MONO_FONT = "Consolas"
HEADING_SIZES = {1: 20, 2: 16, 3: 13, 4: 12}
BULLET_STYLES = ("List Bullet", "List Bullet 2", "List Bullet 3")
CALLOUT_TOKEN = {"info": "primary", "success": "accent", "warning": "muted", "danger": "text"}
MAX_IMAGE_WIDTH = Inches(6)
_SAFE_SCHEMES = {"http", "https", "mailto"}  # matches html.py


def _safe_href(url: str) -> str | None:
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    return url if scheme in _SAFE_SCHEMES else None


def _rgb(color: str) -> RGBColor:
    return RGBColor(*hex_to_rgb(color))


def _shade_cell(cell, color: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), color.lstrip("#"))
    cell._tc.get_or_add_tcPr().append(shd)


def _bottom_border(paragraph, color: str, sz: int = 6) -> None:
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color.lstrip("#"))
    pbdr.append(bottom)
    paragraph._p.get_or_add_pPr().append(pbdr)


def _add_hyperlink(paragraph, span: Span, theme: Theme) -> None:
    r_id = paragraph.part.relate_to(
        span.link, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    if span.code:  # rFonts must precede b/i/color/u in rPr
        fonts = OxmlElement("w:rFonts")
        fonts.set(qn("w:ascii"), MONO_FONT)
        fonts.set(qn("w:hAnsi"), MONO_FONT)
        rpr.append(fonts)
    if span.bold:
        rpr.append(OxmlElement("w:b"))
    if span.italic:
        rpr.append(OxmlElement("w:i"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), theme.primary.lstrip("#"))
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = span.text
    r.append(t)
    link.append(r)
    paragraph._p.append(link)


def _add_spans(
    paragraph, rt: RichText, theme: Theme, numbers: dict[str, int]
) -> None:
    for span in spans(rt):
        if span.link and _safe_href(span.link):
            _add_hyperlink(paragraph, span, theme)
        else:
            run = paragraph.add_run(span.text)
            if span.bold:
                run.bold = True
            if span.italic:
                run.italic = True
            if span.code:
                run.font.name = MONO_FONT
        if span.cite and span.cite in numbers:
            ref = paragraph.add_run(f"[{numbers[span.cite]}]")
            ref.font.superscript = True
            ref.font.color.rgb = _rgb(theme.primary)


def _setup_styles(docx_doc, theme: Theme) -> None:
    normal = docx_doc.styles["Normal"]
    normal.font.name = theme.font_body
    normal.font.size = Pt(11)
    normal.font.color.rgb = _rgb(theme.text)
    for level, size in HEADING_SIZES.items():
        style = docx_doc.styles[f"Heading {level}"]
        style.font.name = theme.font_heading
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = _rgb(theme.primary)


def _title_block(docx_doc, doc: Document, theme: Theme) -> None:
    par = docx_doc.add_paragraph()
    run = par.add_run(doc.title)
    run.font.name = theme.font_heading
    run.font.size = Pt(26)
    run.bold = True
    run.font.color.rgb = _rgb(theme.text)
    if doc.subtitle:
        run = docx_doc.add_paragraph().add_run(doc.subtitle)
        run.italic = True
        run.font.color.rgb = _rgb(theme.muted)
    byline = ", ".join(doc.authors)
    if doc.date:
        byline = f"{byline} \u2014 {doc.date}" if byline else doc.date
    if byline:
        run = docx_doc.add_paragraph().add_run(byline)
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb(theme.muted)
    _bottom_border(docx_doc.add_paragraph(), theme.primary)


def _render_list(
    docx_doc, items, styles: tuple[str, ...], theme: Theme, numbers: dict[str, int]
) -> None:
    for item in items:
        style = styles[min(item.level, len(styles) - 1)]
        par = docx_doc.add_paragraph(style=style)
        _add_spans(par, item.text, theme, numbers)


def _render_numbered(docx_doc, items, theme: Theme, numbers: dict[str, int]) -> None:
    # ponytail: literal "1." prefix runs, so every list (and sub-level)
    # restarts at 1; the built-in List Number style shares one numbering
    # instance and keeps counting across lists. Upgrade path: a fresh
    # native <w:num> instance per list for real Word numbering.
    counters: dict[int, int] = {}
    for item in items:
        counters = {lv: n for lv, n in counters.items() if lv <= item.level}
        counters[item.level] = counters.get(item.level, 0) + 1
        par = docx_doc.add_paragraph()
        par.paragraph_format.left_indent = Inches(0.25 * (item.level + 1))
        par.add_run(f"{counters[item.level]}. ")
        _add_spans(par, item.text, theme, numbers)


def _render_quote(docx_doc, block: Quote, theme: Theme, numbers: dict[str, int]) -> None:
    par = docx_doc.add_paragraph()
    par.paragraph_format.left_indent = Inches(0.5)
    _add_spans(par, block.text, theme, numbers)
    # par.runs skips runs nested in w:hyperlink; walk the XML to catch them
    for r in par._p.iter(qn("w:r")):
        run = Run(r, par)
        run.italic = True
        run.font.color.rgb = _rgb(theme.muted)
    if block.attribution:
        par = docx_doc.add_paragraph()
        par.paragraph_format.left_indent = Inches(0.5)
        run = par.add_run("\u2014 " + block.attribution)
        run.font.size = Pt(10)
        run.font.color.rgb = _rgb(theme.muted)


def _render_code(docx_doc, block: Code, theme: Theme) -> None:
    table = docx_doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _shade_cell(cell, theme.surface)
    par = cell.paragraphs[0]
    for i, line in enumerate(block.code.split("\n")):
        if i:
            par = cell.add_paragraph()
        par.paragraph_format.space_after = Pt(0)
        run = par.add_run(line)
        run.font.name = MONO_FONT
        run.font.size = Pt(9.5)
    docx_doc.add_paragraph()


def _render_table(
    docx_doc, block: Table, theme: Theme, numbers: dict[str, int]
) -> None:
    header, rows = normalize_table(block.header, block.rows)
    cols = len(header)
    if not cols:  # fully empty table: zero-cell rows make Word offer "repair"
        return
    table = docx_doc.add_table(rows=1 + len(rows), cols=cols)
    table.style = "Table Grid"
    for j, cell_rt in enumerate(header):
        cell = table.cell(0, j)
        _shade_cell(cell, theme.primary)
        par = cell.paragraphs[0]
        _add_spans(par, cell_rt, theme, numbers)
        for run in par.runs:
            run.bold = True
            run.font.color.rgb = _rgb(theme.background)
    for i, row in enumerate(rows):
        if i % 2 == 1:
            for j in range(cols):
                _shade_cell(table.cell(i + 1, j), theme.surface)
        for j, cell_rt in enumerate(row):
            _add_spans(table.cell(i + 1, j).paragraphs[0], cell_rt, theme, numbers)
    if block.caption:
        run = docx_doc.add_paragraph().add_run(block.caption)
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb(theme.muted)
    else:
        docx_doc.add_paragraph()


def _render_image(docx_doc, block: Image, theme: Theme) -> None:
    if not block.path:
        return
    path = Path(block.path)
    if not path.is_file():
        return
    try:
        picture = docx_doc.add_picture(str(path))
    except Exception:
        return
    if picture.width > MAX_IMAGE_WIDTH:
        picture.height = Emu(round(picture.height * MAX_IMAGE_WIDTH / picture.width))
        picture.width = MAX_IMAGE_WIDTH
    docx_doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if block.caption:
        par = docx_doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = par.add_run(block.caption)
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb(theme.muted)


def _render_chart(
    docx_doc, block: Chart, theme: Theme, numbers: dict[str, int]
) -> None:
    if block.title:
        run = docx_doc.add_paragraph().add_run(block.title)
        run.bold = True
    if block.path and Path(block.path).is_file():
        _render_image(docx_doc, Image(path=block.path, caption=block.caption), theme)
        return
    rows = [
        [s.name] + ["" if v is None else f"{v:g}" for v in s.values]
        for s in block.series
    ]
    _render_table(
        docx_doc,
        Table(header=[""] + list(block.labels), rows=rows, caption=block.caption),
        theme,
        numbers,
    )


def _render_stats(docx_doc, block: StatRow, theme: Theme) -> None:
    if not block.items:
        return
    table = docx_doc.add_table(rows=1, cols=len(block.items))
    for j, stat in enumerate(block.items):
        cell = table.cell(0, j)
        _shade_cell(cell, theme.surface)
        run = cell.paragraphs[0].add_run(stat.value)
        run.bold = True
        run.font.size = Pt(16)
        run.font.color.rgb = _rgb(theme.primary)
        run = cell.add_paragraph().add_run(stat.label)
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb(theme.muted)
        if stat.delta:
            run = cell.add_paragraph().add_run(stat.delta)
            run.font.size = Pt(8)
            run.font.color.rgb = _rgb(theme.accent)
    docx_doc.add_paragraph()


def _render_callout(
    docx_doc, block: Callout, theme: Theme, numbers: dict[str, int]
) -> None:
    table = docx_doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _shade_cell(cell, theme.surface)
    par = cell.paragraphs[0]
    label = par.add_run(block.style.upper() + "  ")
    label.bold = True
    label.font.size = Pt(9)
    label.font.color.rgb = _rgb(getattr(theme, CALLOUT_TOKEN[block.style]))
    _add_spans(par, block.text, theme, numbers)
    docx_doc.add_paragraph()


def _render_block(
    docx_doc, block: Block, theme: Theme, numbers: dict[str, int]
) -> None:
    if isinstance(block, Heading):
        par = docx_doc.add_paragraph(style=f"Heading {min(block.level, 4)}")
        _add_spans(par, block.text, theme, numbers)
    elif isinstance(block, Paragraph):
        _add_spans(docx_doc.add_paragraph(), block.text, theme, numbers)
    elif isinstance(block, BulletList):
        _render_list(docx_doc, block.items, BULLET_STYLES, theme, numbers)
    elif isinstance(block, NumberedList):
        _render_numbered(docx_doc, block.items, theme, numbers)
    elif isinstance(block, Quote):
        _render_quote(docx_doc, block, theme, numbers)
    elif isinstance(block, Code):
        _render_code(docx_doc, block, theme)
    elif isinstance(block, Table):
        _render_table(docx_doc, block, theme, numbers)
    elif isinstance(block, Image):
        _render_image(docx_doc, block, theme)
    elif isinstance(block, Callout):
        _render_callout(docx_doc, block, theme, numbers)
    elif isinstance(block, Chart):
        _render_chart(docx_doc, block, theme, numbers)
    elif isinstance(block, StatRow):
        _render_stats(docx_doc, block, theme)
    elif isinstance(block, Artifact):
        # picture embed if the render path exists; otherwise skip silently
        _render_image(
            docx_doc, Image(path=block.path, alt=block.alt, caption=block.caption), theme
        )
    elif isinstance(block, Divider):
        _bottom_border(docx_doc.add_paragraph(), theme.muted)
    else:
        raise RenderError(f"unhandled block type: {type(block).__name__}")


def _sources_section(docx_doc, doc: Document, theme: Theme) -> None:
    numbers = source_numbers(doc)
    docx_doc.add_paragraph("Sources", style="Heading 1")
    for source in doc.sources:
        par = docx_doc.add_paragraph()
        text = f"{numbers[source.id]}. {source.title}"
        if source.publisher:
            text += " \u2014 " + source.publisher
        if source.date:
            text += f" ({source.date})"
        if source.url:
            text += ", "
        run = par.add_run(text)
        run.font.size = Pt(10)
        if source.url:
            if _safe_href(source.url):
                _add_hyperlink(par, Span(text=source.url, link=source.url), theme)
            else:
                par.add_run(source.url).font.size = Pt(10)


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    docx_doc = DocxDocument()
    _setup_styles(docx_doc, theme)
    _title_block(docx_doc, doc, theme)
    numbers = source_numbers(doc)
    for block in report_blocks(doc):
        _render_block(docx_doc, block, theme, numbers)
    if doc.sources and cited_ids(doc):
        _sources_section(docx_doc, doc, theme)
    docx_doc.save(str(out_path))
    return out_path

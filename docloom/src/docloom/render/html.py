"""HTML renderer: one self-contained page, all styling inline from theme
tokens as CSS custom properties. All user text is escaped; only http/https/
mailto links become anchors; missing images are skipped silently."""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..ir import (
    Artifact,
    Block,
    BulletList,
    Callout,
    Cell,
    Chart,
    Code,
    Divider,
    Document,
    Formula,
    Heading,
    Image,
    ListItem,
    NumberedList,
    Paragraph,
    Quote,
    RichText,
    Sheet,
    Span,
    StatRow,
    Table,
    cited_ids,
    normalize_table,
    report_blocks,
    source_numbers,
    spans,
)
from ..theme import Theme

_SAFE_SCHEMES = {"http", "https", "mailto"}

_CSS_STATIC = """\
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);line-height:1.6}
main{max-width:46rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1,h2,h3,h4,h5{line-height:1.25;margin:2rem 0 .75rem}
h1{font-size:2rem;margin-top:0}
h2{font-size:1.4rem;border-bottom:1px solid var(--surface);padding-bottom:.35rem}
h3{font-size:1.15rem}
header{border-bottom:2px solid var(--primary);padding-bottom:1rem;margin-bottom:2rem}
header p{margin:.25rem 0}
header .subtitle{color:var(--muted);font-size:1.1rem}
header .meta{color:var(--muted);font-size:.85rem}
p{margin:.75rem 0}
a{color:var(--primary)}
code{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:.9em;background:var(--surface);padding:.1em .3em;border-radius:3px}
pre{background:var(--surface);padding:1rem;border-radius:6px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{margin:1.25rem 0;padding:.25rem 1.25rem;border-left:4px solid var(--primary)}
blockquote footer{color:var(--muted);font-size:.9rem}
.table-wrap{overflow-x:auto;margin:1rem 0}
table{border-collapse:collapse;width:100%}
caption{caption-side:bottom;color:var(--muted);font-size:.85rem;padding:.5rem;text-align:left}
th,td{padding:.5rem .75rem;text-align:left;border-bottom:1px solid var(--surface)}
thead th{background:var(--primary);color:var(--bg)}
tbody tr:nth-child(even){background:var(--surface)}
.callout{background:var(--surface);border-left:4px solid var(--primary);padding:.75rem 1rem;border-radius:0 6px 6px 0;margin:1rem 0}
.callout-success{border-left-color:var(--accent)}
.callout-warning{border-left-color:var(--muted)}
.callout-danger{border-left-color:var(--text)}
figure{margin:1.25rem 0}
figure img{max-width:100%;height:auto}
figcaption{color:var(--muted);font-size:.85rem;margin-top:.35rem}
hr{border:none;border-top:1px solid var(--surface);margin:2rem 0}
.chart-title{font-weight:600;margin:1.25rem 0 .25rem}
.stats{display:flex;flex-wrap:wrap;gap:1rem;margin:1.25rem 0}
.stat{flex:1 1 10rem;background:var(--surface);border-radius:6px;padding:.85rem 1.1rem}
.stat .value{font-size:1.5rem;font-weight:700;color:var(--primary);line-height:1.2}
.stat .label{color:var(--muted);font-size:.85rem;margin-top:.15rem}
.stat .delta{color:var(--accent);font-size:.85rem;font-weight:600}
sup.cite a{text-decoration:none;font-weight:bold}
ol.sources li{margin:.35rem 0}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _css_font(name: str) -> str:
    return '"' + re.sub(r"[^\w \-]", "", name) + '"'


_FONT_MIME = {
    ".woff2": "font/woff2", ".woff": "font/woff",
    ".ttf": "font/ttf", ".otf": "font/otf",
}
_FONT_FORMAT = {
    ".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype",
}


def _face(family: str, src: str | None) -> str:
    """An @font-face embedding a local font file as a data URI, or '' if the
    file is missing/unreadable (the family name then just falls back)."""
    if not src:
        return ""
    p = Path(src)
    ext = p.suffix.lower()
    if ext not in _FONT_MIME or not p.is_file():
        return ""
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (
        f"@font-face{{font-family:{_css_font(family)};"
        f"src:url(data:{_FONT_MIME[ext]};base64,{data}) "
        f"format('{_FONT_FORMAT[ext]}');font-display:swap}}\n"
    )


def _css(theme: Theme) -> str:
    faces = _face(theme.font_body, theme.font_body_src)
    if theme.font_heading != theme.font_body or not theme.font_body_src:
        faces += _face(theme.font_heading, theme.font_heading_src)
    return (
        faces
        + f":root{{--primary:{theme.primary};--accent:{theme.accent};"
        f"--bg:{theme.background};--surface:{theme.surface};"
        f"--text:{theme.text};--muted:{theme.muted}}}\n"
        + _CSS_STATIC
        + f"header .brand-logo{{max-height:2.5rem;margin-bottom:.75rem}}\n"
        + f"body{{font-family:{_css_font(theme.font_body)},Georgia,"
        f'"Times New Roman",serif}}\n'
        f"h1,h2,h3,h4,h5{{font-family:{_css_font(theme.font_heading)},"
        f'-apple-system,"Segoe UI",Arial,sans-serif}}\n'
    )


def _logo_html(logo: Image | None) -> str:
    """A brand logo <img> (data-URI embedded) for the document header, or ''."""
    if logo is None or not logo.path:
        return ""
    p = Path(logo.path)
    if not p.is_file():
        return ""
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (f'<img class="brand-logo" src="data:{mime};base64,{data}" '
            f'alt="{_esc(logo.alt or "logo")}">')


def _safe_href(url: str) -> str | None:
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    return url if scheme in _SAFE_SCHEMES else None


def _span_html(sp: Span, numbers: dict[str, int]) -> str:
    out = _esc(sp.text)
    if sp.code:
        out = f"<code>{out}</code>"
    if sp.bold:
        out = f"<strong>{out}</strong>"
    if sp.italic:
        out = f"<em>{out}</em>"
    if sp.link:
        href = _safe_href(sp.link)
        if href:
            out = f'<a href="{_esc(href)}">{out}</a>'
    if sp.cite and sp.cite in numbers:
        n = numbers[sp.cite]
        out += f'<sup class="cite"><a href="#src-{n}">{n}</a></sup>'
    return out


def _rt(rt: RichText, numbers: dict[str, int]) -> str:
    return "".join(_span_html(s, numbers) for s in spans(rt))


def _list_html(items: list[ListItem], tag: str, numbers: dict[str, int]) -> str:
    if not items:
        return ""
    parts = [f"<{tag}>"]
    level = 0
    open_item = False
    for it in items:
        while it.level > level:
            if not open_item:
                parts.append("<li>")
                open_item = True
            parts.append(f"<{tag}>")
            open_item = False
            level += 1
        while it.level < level:
            if open_item:
                parts.append("</li>")
            parts.append(f"</{tag}>")
            open_item = True
            level -= 1
        if open_item:
            parts.append("</li>")
        parts.append(f"<li>{_rt(it.text, numbers)}")
        open_item = True
    while level > 0:
        parts.append(f"</li></{tag}>")
        level -= 1
    parts.append(f"</li></{tag}>")
    return "".join(parts)


def _table_html(
    header: list[RichText],
    rows: list[list[RichText]],
    caption: str | None,
    numbers: dict[str, int],
) -> str:
    header, rows = normalize_table(header, rows)  # align ragged rows to the header
    if not header:
        return ""
    parts = ['<div class="table-wrap"><table>']
    if caption:
        parts.append(f"<caption>{_esc(caption)}</caption>")
    parts.append("<thead><tr>")
    parts.extend(f"<th>{_rt(c, numbers)}</th>" for c in header)
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append(
            "<tr>" + "".join(f"<td>{_rt(c, numbers)}</td>" for c in row) + "</tr>"
        )
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _figure_html(path: str | None, alt: str, caption: str | None) -> str:
    """Embed a local image file as a data-URI figure; "" if no/missing file."""
    if not path or not Path(path).is_file():
        return ""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""  # exists but unreadable: treat like a missing image
    out = f'<figure><img src="data:{mime};base64,{data}" alt="{_esc(alt)}">'
    if caption:
        out += f"<figcaption>{_esc(caption)}</figcaption>"
    return out + "</figure>"


def _chart_html(b: Chart, numbers: dict[str, int]) -> str:
    embedded = _figure_html(b.path, b.title or "chart", b.caption)
    if embedded:
        return embedded
    # no rendered image: accessible data-table fallback (series x labels)
    header, rows = normalize_table(
        [""] + list(b.labels),
        [[s.name] + ["" if v is None else f"{v:g}" for v in s.values]
         for s in b.series],
    )
    out = f'<p class="chart-title">{_esc(b.title)}</p>' if b.title else ""
    return out + _table_html(header, rows, b.caption, numbers)


def _stats_html(b: StatRow) -> str:
    if not b.items:
        return ""
    cards = []
    for st in b.items:
        card = (
            f'<div class="stat"><div class="value">{_esc(st.value)}</div>'
            f'<div class="label">{_esc(st.label)}</div>'
        )
        if st.delta:
            card += f'<div class="delta">{_esc(st.delta)}</div>'
        cards.append(card + "</div>")
    return f'<div class="stats">{"".join(cards)}</div>'


def _block_html(b: Block, numbers: dict[str, int]) -> str:
    if isinstance(b, Heading):
        tag = f"h{min(b.level + 1, 5)}"
        return f"<{tag}>{_rt(b.text, numbers)}</{tag}>"
    if isinstance(b, Paragraph):
        return f"<p>{_rt(b.text, numbers)}</p>"
    if isinstance(b, BulletList):
        return _list_html(b.items, "ul", numbers)
    if isinstance(b, NumberedList):
        return _list_html(b.items, "ol", numbers)
    if isinstance(b, Quote):
        out = f"<blockquote><p>{_rt(b.text, numbers)}</p>"
        if b.attribution:
            out += f"<footer>\u2014 {_esc(b.attribution)}</footer>"
        return out + "</blockquote>"
    if isinstance(b, Code):
        cls = f' class="language-{_esc(b.language)}"' if b.language else ""
        return f"<pre><code{cls}>{_esc(b.code)}</code></pre>"
    if isinstance(b, Table):
        return _table_html(b.header, b.rows, b.caption, numbers)
    if isinstance(b, Image):
        return _figure_html(b.path, b.alt, b.caption)
    if isinstance(b, Chart):
        return _chart_html(b, numbers)
    if isinstance(b, StatRow):
        return _stats_html(b)
    if isinstance(b, Artifact):
        return _figure_html(b.path, b.alt, b.caption)
    if isinstance(b, Callout):
        return f'<div class="callout callout-{b.style}">{_rt(b.text, numbers)}</div>'
    if isinstance(b, Divider):
        return "<hr>"
    return ""


def _cell_html(cell: Cell) -> str:
    if isinstance(cell, Formula):
        return f"<code>{_esc(cell.formula)}</code>"
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    return _esc(str(cell))


def _sheet_html(sheet: Sheet) -> str:
    parts = [
        f"<h3>{_esc(sheet.name)}</h3>",
        '<div class="table-wrap"><table><thead><tr>',
    ]
    parts.extend(f"<th>{_esc(c.header)}</th>" for c in sheet.columns)
    parts.append("</tr></thead><tbody>")
    ncols = len(sheet.columns)
    for row in sheet.rows:
        cells = list(row[:ncols]) + [None] * (ncols - len(row))  # align to header
        parts.append(
            "<tr>" + "".join(f"<td>{_cell_html(c)}</td>" for c in cells) + "</tr>"
        )
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _sources_html(doc: Document) -> str:
    numbers = source_numbers(doc)
    parts = ['<section><h2>Sources</h2><ol class="sources">']
    for src in doc.sources:
        line = _esc(src.title)
        if src.publisher:
            line += f", {_esc(src.publisher)}"
        if src.date:
            line += f" ({_esc(src.date)})"
        if src.url:
            href = _safe_href(src.url)
            if href:
                line += f', <a href="{_esc(href)}">{_esc(src.url)}</a>'
            else:
                line += f", {_esc(src.url)}"
        parts.append(f'<li id="src-{numbers[src.id]}">{line}</li>')
    parts.append("</ol></section>")
    return "".join(parts)


def to_html(doc: Document, theme: Theme) -> str:
    numbers = source_numbers(doc)
    parts = [f"<header>{_logo_html(doc.logo)}<h1>{_esc(doc.title)}</h1>"]
    if doc.subtitle:
        parts.append(f'<p class="subtitle">{_esc(doc.subtitle)}</p>')
    meta = " \u00b7 ".join(x for x in (", ".join(doc.authors), doc.date or "") if x)
    if meta:
        parts.append(f'<p class="meta">{_esc(meta)}</p>')
    parts.append("</header>")
    for b in report_blocks(doc):
        rendered = _block_html(b, numbers)
        if rendered:
            parts.append(rendered)
    if doc.sheets:
        parts.append("<section><h2>Workbook</h2>")
        parts.extend(_sheet_html(s) for s in doc.sheets)
        parts.append("</section>")
    if doc.sources and cited_ids(doc):
        parts.append(_sources_html(doc))
    body = "\n".join(parts)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(doc.title)}</title>\n<style>\n{_css(theme)}</style>\n"
        f"</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    out = Path(out_path)
    out.write_text(to_html(doc, theme), encoding="utf-8")
    return out

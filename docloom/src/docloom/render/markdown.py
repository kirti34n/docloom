"""GitHub-flavored Markdown renderer: report blocks, sheets as GFM tables,
citations as footnotes. Pipes in table cells are escaped so tables never
break; missing images are skipped silently."""

from __future__ import annotations

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

_ALERTS = {"info": "NOTE", "success": "TIP", "warning": "WARNING", "danger": "CAUTION"}
_SAFE_SCHEMES = {"http", "https", "mailto"}  # matches html.py
_TICK_RUN = re.compile(r"`+")


def _longest_tick_run(text: str) -> int:
    return max((len(m) for m in _TICK_RUN.findall(text)), default=0)


def _esc_md(text: str) -> str:
    """Escape markdown specials in plain text (never applied to code)."""
    text = re.sub(r"[\\`*_\[\]<>|]", r"\\\g<0>", text)
    # line-start constructs that would change structure: #, -, + and "1."
    text = re.sub(r"^([#+-])", r"\\\1", text, flags=re.MULTILINE)
    return re.sub(r"^(\d+)\.", r"\1\\.", text, flags=re.MULTILINE)


def _one_line(text: str) -> str:
    """Flatten newlines for single-line constructs (headings, titles, ...)."""
    return " ".join(text.splitlines())


def _code_span(text: str) -> str:
    ticks = "`" * (_longest_tick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _safe_dest(url: str) -> str | None:
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    if scheme not in _SAFE_SCHEMES:
        return None
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _span_md(sp: Span, numbers: dict[str, int]) -> str:
    out = _code_span(sp.text) if sp.code else _esc_md(sp.text)
    if sp.bold and sp.italic:
        out = f"***{out}***"
    elif sp.bold:
        out = f"**{out}**"
    elif sp.italic:
        out = f"*{out}*"
    if sp.link:
        dest = _safe_dest(sp.link)
        if dest:  # unsafe scheme: keep the text, drop the link (as html.py)
            out = f"[{out}]({dest})"
    if sp.cite and sp.cite in numbers:
        out += f"[^{numbers[sp.cite]}]"
    return out


def _rt(rt: RichText, numbers: dict[str, int]) -> str:
    return "".join(_span_md(s, numbers) for s in spans(rt))


def _pipe_safe(text: str) -> str:
    # only unescaped pipes: _esc_md already escaped the ones in plain spans
    return re.sub(r"(?<!\\)\|", r"\\|", text).replace("\n", "<br>")


def _table_md(
    header: list[str], rows: list[list[str]], caption: str | None
) -> str:
    if not header:  # a zero-column table is not valid GFM; emit nothing
        return ""
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    if caption:
        lines += ["", f"*{_esc_md(_one_line(caption))}*"]
    return "\n".join(lines)


def _quoted(text: str) -> str:
    return "\n".join(f"> {ln}" for ln in text.splitlines() or [""])


def _image_md(path: str | None, alt: str, caption: str | None) -> str:
    """Image syntax for a local file; "" if no/missing file (skip silently)."""
    if not path or not Path(path).is_file():
        return ""
    dest = f"<{path}>" if any(c in path for c in " ()") else path
    alt = _one_line(alt).replace("]", "\\]")
    out = f"![{alt}]({dest})"
    if caption:
        out += f"\n\n*{_esc_md(_one_line(caption))}*"
    return out


def _cell_md(text: str) -> str:
    return _pipe_safe(_esc_md(_one_line(text)))


def _chart_md(b: Chart) -> str:
    embedded = _image_md(b.path, b.title or "chart", b.caption)
    if embedded:
        return embedded
    # no rendered image: GFM data-table fallback (series x labels)
    header, rows = normalize_table(
        [""] + list(b.labels),
        [[s.name] + ["" if v is None else f"{v:g}" for v in s.values]
         for s in b.series],
    )
    table = _table_md(
        [_cell_md(c) for c in header],
        [[_cell_md(c) for c in row] for row in rows],
        b.caption,
    )
    if b.title:
        return f"**{_esc_md(_one_line(b.title))}**\n\n{table}"
    return table


def _stats_md(b: StatRow) -> str:
    if not b.items:
        return ""
    values, labels = [], []
    for st in b.items:
        v = _cell_md(st.value).strip()
        values.append(f"**{v}**" if v else "")
        lab = _esc_md(_one_line(st.label))
        if st.delta:
            lab += f" ({_esc_md(_one_line(st.delta))})"
        labels.append(_pipe_safe(lab))
    return _table_md(values, [labels], None)


def _list_md(b: BulletList | NumberedList, numbers: dict[str, int]) -> str:
    # 3-space indent under a numbered marker (GFM minimum), 2 under a bullet;
    # clamp level jumps to previous+1 so 4+ spaces never become a code block
    indent, marker = ("  ", "- ") if isinstance(b, BulletList) else ("   ", "1. ")
    lines: list[str] = []
    prev = -1
    for it in b.items:
        level = min(it.level, prev + 1)
        lines.append(indent * level + marker + _one_line(_rt(it.text, numbers)))
        prev = level
    return "\n".join(lines)


def _block_md(b: Block, numbers: dict[str, int]) -> str:
    if isinstance(b, Heading):
        return "#" * min(b.level + 1, 6) + " " + _one_line(_rt(b.text, numbers))
    if isinstance(b, Paragraph):
        return _rt(b.text, numbers)
    if isinstance(b, (BulletList, NumberedList)):
        return _list_md(b, numbers)
    if isinstance(b, Quote):
        out = _quoted(_rt(b.text, numbers))
        if b.attribution:
            out += f"\n>\n> \u2014 {_esc_md(_one_line(b.attribution))}"
        return out
    if isinstance(b, Code):
        fence = "`" * max(3, _longest_tick_run(b.code) + 1)
        # info string: first token only, safe chars only (no backtick/space)
        lang = re.sub(r"[^\w+#.-]", "", ((b.language or "").split() or [""])[0])
        return f"{fence}{lang}\n{b.code}\n{fence}"
    if isinstance(b, Table):
        header, rows = normalize_table(b.header, b.rows)
        return _table_md(
            [_pipe_safe(_rt(c, numbers)) for c in header],
            [[_pipe_safe(_rt(c, numbers)) for c in row] for row in rows],
            b.caption,
        )
    if isinstance(b, Image):
        return _image_md(b.path, b.alt, b.caption)
    if isinstance(b, Chart):
        return _chart_md(b)
    if isinstance(b, StatRow):
        return _stats_md(b)
    if isinstance(b, Artifact):
        return _image_md(b.path, b.alt, b.caption)
    if isinstance(b, Callout):
        return f"> [!{_ALERTS[b.style]}]\n" + _quoted(_rt(b.text, numbers))
    if isinstance(b, Divider):
        return "---"
    return ""


def _sheet_cell(cell: Cell) -> str:
    if isinstance(cell, Formula):
        return _pipe_safe(_code_span(cell.formula))
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    return _pipe_safe(_esc_md(str(cell)))


def _sheet_md(sheet: Sheet) -> str:
    header, rows = normalize_table(
        [_pipe_safe(_esc_md(c.header)) for c in sheet.columns],
        [[_sheet_cell(c) for c in row] for row in sheet.rows],
    )
    table = _table_md(header, rows, None)
    return f"## Sheet: {_esc_md(_one_line(sheet.name))}\n\n{table}"


def _footnotes_md(doc: Document, numbers: dict[str, int]) -> str:
    defs = []
    for src in doc.sources:
        line = _esc_md(src.title)
        if src.publisher:
            line += f" \u2014 {_esc_md(src.publisher)}"
        if src.date:
            line += f" ({src.date})"
        if src.url:
            line += f", {src.url}"
        defs.append(f"[^{numbers[src.id]}]: {_one_line(line)}")
    return "\n".join(defs)


def to_markdown(doc: Document) -> str:
    numbers = source_numbers(doc)
    parts = [f"# {_esc_md(_one_line(doc.title))}"]
    meta = " \u00b7 ".join(
        x for x in (doc.subtitle or "", ", ".join(doc.authors), doc.date or "") if x
    )
    if meta:
        parts.append(f"*{_esc_md(_one_line(meta))}*")
    for b in report_blocks(doc):
        rendered = _block_md(b, numbers)
        if rendered:
            parts.append(rendered)
    parts.extend(_sheet_md(s) for s in doc.sheets)
    if doc.sources and cited_ids(doc):
        parts.append(_footnotes_md(doc, numbers))
    return "\n\n".join(parts) + "\n"


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    out = Path(out_path)
    out.write_text(to_markdown(doc), encoding="utf-8")
    return out

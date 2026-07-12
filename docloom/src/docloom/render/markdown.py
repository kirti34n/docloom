"""GitHub-flavored Markdown renderer: report blocks, sheets as GFM tables,
citations as footnotes. Pipes in table cells are escaped so tables never
break; missing images are skipped silently."""

from __future__ import annotations

import re
import shutil
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
    # collapse 4+ leading spaces first (they would be an indented code block),
    # so a marker that ends up in the 0-3 space range below still gets escaped
    text = re.sub(r"^ {4,}", "   ", text, flags=re.MULTILINE)
    # line-start constructs, active with up to 3 leading spaces in GFM: #, -, +,
    # ~ (fence), "1." / "1)" ordered markers, and a bare "=" run (setext heading)
    text = re.sub(r"^( {0,3})([#+~-])", r"\1\\\2", text, flags=re.MULTILINE)
    text = re.sub(r"^( {0,3})(\d+)([.)])", r"\1\2\\\3", text, flags=re.MULTILINE)
    text = re.sub(r"^( {0,3})(=+)[ \t]*$", r"\1\\\2", text, flags=re.MULTILINE)
    return text


def _one_line(text: str) -> str:
    """Flatten newlines for single-line constructs (headings, titles, ...)."""
    return " ".join(text.splitlines())


def _code_span(text: str) -> str:
    if not text:  # "``" alone is not a valid empty code span in CommonMark
        return "` `"
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
    return (
        url.replace(" ", "%20")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace("\t", "%09")
        .replace("\n", "%0A")
        .replace("\r", "%0D")
    )


def _wrap_flank(out: str, marker: str) -> str:
    """Wrap `out` in emphasis `marker`, hoisting boundary whitespace outside
    the markers first: CommonMark's flanking rule means a delimiter run
    touching whitespace cannot open/close emphasis, so "** x**" would render
    as literal asterisks instead of bold."""
    core = out.strip()
    if not core:
        return out  # whitespace-only: nothing to emphasize
    lead = out[: len(out) - len(out.lstrip())]
    trail = out[len(out.rstrip()):]
    return f"{lead}{marker}{core}{marker}{trail}"


def _span_md(sp: Span, numbers: dict[str, int]) -> str:
    out = _code_span(sp.text) if sp.code else _esc_md(sp.text)
    if sp.bold and sp.italic:
        out = _wrap_flank(out, "***")
    elif sp.bold:
        out = _wrap_flank(out, "**")
    elif sp.italic:
        out = _wrap_flank(out, "*")
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


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while f"{stem}-{n}{suffix}" in used:
        n += 1
    candidate = f"{stem}-{n}{suffix}"
    used.add(candidate)
    return candidate


class _AssetCopier:
    """Copies referenced local images into `<out-stem>_files/` next to a
    rendered .md file and hands back paths relative to it, so a downloaded
    .md keeps working images instead of pointing at the generating
    machine's filesystem. Each source path is copied at most once."""

    def __init__(self, out_path: Path) -> None:
        self._dir_name = out_path.stem + "_files"
        self._target_dir = out_path.parent / self._dir_name
        self._copied: dict[str, str] = {}
        self._used_names: set[str] = set()

    def dest(self, src: str) -> str | None:
        if src in self._copied:
            return self._copied[src]
        name = _unique_name(Path(src).name, self._used_names)
        try:
            self._target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, self._target_dir / name)
        except OSError:
            return None  # unreadable/unwritable: leave the caller's fallback in place
        rel = f"{self._dir_name}/{name}"
        self._copied[src] = rel
        return rel


def _image_md(
    path: str | None, alt: str, caption: str | None, copier: _AssetCopier | None = None
) -> str:
    """Image syntax for a local file; "" if no/missing file (skip silently).
    With a copier, the file is copied next to the output and the reference
    rewritten relative, so a downloaded .md keeps working images."""
    if not path or not Path(path).is_file():
        return ""
    dest = (copier.dest(path) if copier is not None else None) or path
    dest_ref = f"<{dest}>" if any(c in dest for c in " ()") else dest
    alt = _one_line(alt).replace("[", "\\[").replace("]", "\\]")
    out = f"![{alt}]({dest_ref})"
    if caption:
        out += f"\n\n*{_esc_md(_one_line(caption))}*"
    return out


def _cell_md(text: str) -> str:
    return _pipe_safe(_esc_md(_one_line(text)))


def _chart_md(b: Chart, copier: _AssetCopier | None = None) -> str:
    embedded = _image_md(b.path, b.title or "chart", b.caption, copier)
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
        return f"#### {_esc_md(_one_line(b.title))}\n\n{table}"
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


def _block_md(b: Block, numbers: dict[str, int], copier: _AssetCopier | None = None) -> str:
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
        return _image_md(b.path, b.alt, b.caption, copier)
    if isinstance(b, Chart):
        return _chart_md(b, copier)
    if isinstance(b, StatRow):
        return _stats_md(b)
    if isinstance(b, Artifact):
        return _image_md(b.path, b.alt, b.caption, copier)
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


def to_markdown(doc: Document, copier: _AssetCopier | None = None) -> str:
    numbers = source_numbers(doc)
    parts = [f"# {_esc_md(_one_line(doc.title))}"]
    meta = " \u00b7 ".join(
        x for x in (doc.subtitle or "", ", ".join(doc.authors), doc.date or "") if x
    )
    if meta:
        parts.append(f"*{_esc_md(_one_line(meta))}*")
    for b in report_blocks(doc):
        rendered = _block_md(b, numbers, copier)
        if rendered:
            parts.append(rendered)
    parts.extend(_sheet_md(s) for s in doc.sheets)
    if doc.sources and cited_ids(doc):
        parts.append(_footnotes_md(doc, numbers))
    return "\n\n".join(parts) + "\n"


def render(doc: Document, theme: Theme, out_path: Path, assets: bool = True) -> Path:
    """assets=True (default) copies referenced local images into
    `<out-stem>_files/` next to `out_path` and rewrites their references
    relative, so a downloaded .md keeps working images instead of pointing
    at the generating machine's filesystem."""
    out = Path(out_path)
    copier = _AssetCopier(out) if assets else None
    out.write_text(to_markdown(doc, copier), encoding="utf-8")
    return out

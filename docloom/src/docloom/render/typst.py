"""PDF renderer: compiles the Document IR to Typst source, then to PDF
in-process via the `typst` wheel. `to_typst()` returns the .typ source;
`render()` compiles it. Images are copied next to the temp .typ so Typst
can resolve them regardless of where the originals live; standalone
`to_typst()` output references the original paths (POSIX-style), which
resolve when the .typ is compiled from a location those paths are
relative to. Absolute image paths (which Typst rejects) become comment
lines in standalone output and only embed via `render()`."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image as PILImage

from ..ir import (
    Artifact,
    Block,
    BulletList,
    Callout,
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
    StatRow,
    Table,
    cited_ids,
    normalize_table,
    report_blocks,
    source_numbers,
    spans,
)
from ..theme import Theme
from . import RenderError

_FALLBACK_FONT = "Libertinus Serif"
_MARKUP_SPECIALS = "\\#$%&_*@<>[]~`/=+-"
_HEADING_SIZES = {1: "17pt", 2: "14pt", 3: "12.5pt", 4: "11pt"}
_SAFE_SCHEMES = {"http", "https", "mailto"}


def _safe_link(url: str) -> str | None:
    """None if the scheme is not on the allow-list (matches html.py), so
    javascript:/file:/custom-scheme links do not reach PDF link actions."""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    return url if scheme in _SAFE_SCHEMES else None


def _esc(text: str) -> str:
    """Escape Typst markup special characters in user text. Newlines flatten
    to spaces so text cannot leak out of line-based markup (headings, list
    items); raw/code text goes through _str instead and keeps them."""
    text = " ".join(text.splitlines())
    escaped = "".join("\\" + c if c in _MARKUP_SPECIALS else c for c in text)
    # "1. foo" at line start would parse as a numbered-list item: escape the dot
    return re.sub(r"^(\s*\d+)\.", r"\1\\.", escaped)


_ENUM_MARKER_RE = re.compile(r"^(\s*\d+)\.")


def _guard_line_start(text: str) -> str:
    """Escape a numbered-list marker that only appears after concatenating
    a Paragraph's spans (e.g. Span("1") + Span(". Buy milk")); _esc only
    sees each span in isolation so it cannot catch a marker split like that."""
    return _ENUM_MARKER_RE.sub(r"\1\\.", text)


def _str(text: str) -> str:
    """A Typst string literal."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return '"' + escaped + '"'


def _font(name: str) -> str:
    return f"({_str(name)}, {_str(_FALLBACK_FONT)})"


def _embeddable(path: Path) -> bool:
    """True if Typst can embed the image file. SVGs pass through (Typst
    renders them natively); anything else must decode via PIL, so a file
    that exists but is corrupt gets skipped instead of failing the compile,
    consistent with the missing-file policy."""
    if path.suffix.lower() == ".svg":
        try:
            with path.open("rb") as fh:
                head = fh.read(2048)
        except OSError:
            return False
        # a bare "<" also matches XML/HTML-ish files that Typst's SVG parser
        # rejects (e.g. "failed to parse SVG: missing root node"); require an
        # actual <svg tag instead.
        return b"<svg" in head.lower()
    try:
        with PILImage.open(path) as img:
            fmt = (img.format or "").upper()
            img.verify()
        # Typst decodes only these raster formats; PIL can verify many more
        # (BMP, TIFF, ICO, ...) that would crash the compile, so skip them.
        return fmt in {"PNG", "JPEG", "GIF", "WEBP"}
    except Exception:
        return False


def _image_ref(path: Path) -> str:
    """The embed line for an image path; render() rewrites these to local
    copies. Absolute paths (which Typst rejects) become comment lines that
    only embed via render()."""
    if path.is_absolute():
        return f"// docloom-image: {path.as_posix()}"
    return f"#image({_str(path.as_posix())})"


def _caption(text: str, theme: Theme) -> str:
    return (
        f'#align(center, text(fill: rgb("{theme.muted}"), size: 9pt, '
        f'style: "italic")[{_esc(text)}])'
    )


def _rich(rt: RichText, numbers: dict[str, int], cell_link_color: str | None = None) -> str:
    parts: list[str] = []
    for sp in spans(rt):
        piece = f"#raw({_str(sp.text)})" if sp.code else _esc(sp.text)
        if sp.bold:
            piece = f"#strong[{piece}]"
        if sp.italic:
            piece = f"#emph[{piece}]"
        if sp.link:
            href = _safe_link(sp.link)
            if href and cell_link_color:
                # ponytail: typst 0.15.0 panics ("expected link ancestor in
                # logical tree") when a styled link wraps across lines inside
                # a table cell, so cell links render as colored text without
                # #link — cells lose clickability until that upstream typst
                # bug is fixed.
                piece = f'#text(fill: rgb("{cell_link_color}"))[{piece}]'
            elif href:
                piece = f"#link({_str(href)})[{piece}]"
        if sp.cite and sp.cite in numbers:
            piece += f"#super[{numbers[sp.cite]}]"
        parts.append(piece)
    return "".join(parts)


def _table(b: Table, theme: Theme, numbers: dict[str, int]) -> list[str]:
    header, rows = normalize_table(b.header, b.rows)
    ncols = len(header)
    if ncols == 0:  # columns: 0 does not compile
        return []
    lines = [
        "#table(",
        f"  columns: {ncols},",
        f'  stroke: 0.5pt + rgb("{theme.surface}"),',
        "  inset: 6pt,",
        "  fill: (x, y) => if y == 0 { rgb(%s) } else if calc.even(y) { rgb(%s) } else { none },"
        % (_str(theme.primary), _str(theme.surface)),
        "  table.header(",
    ]
    for cell in header:
        # links in header cells recolor to the background so they stay
        # visible on the primary-colored header fill
        content = _rich(cell, numbers, theme.background)
        lines.append(
            f'    [#text(fill: rgb("{theme.background}"), '
            f'weight: "bold")[{content}]],'
        )
    lines.append("  ),")
    for row in rows:
        lines.append(
            "  " + " ".join(f"[{_rich(c, numbers, theme.primary)}]," for c in row)
        )
    lines.append(")")
    if b.caption:
        lines.append(_caption(b.caption, theme))
    return lines


def _callout_color(style: str, theme: Theme) -> str:
    return {
        "info": theme.primary,
        "success": theme.accent,
        "warning": theme.muted,
        "danger": theme.text,
    }[style]


def _block(b: Block, theme: Theme, numbers: dict[str, int]) -> list[str]:
    if isinstance(b, Heading):
        return ["=" * b.level + " " + _rich(b.text, numbers)]
    if isinstance(b, Paragraph):
        return [_guard_line_start(_rich(b.text, numbers))]
    if isinstance(b, (BulletList, NumberedList)):
        marker = "-" if isinstance(b, BulletList) else "+"
        return [
            "  " * it.level + marker + " " + _rich(it.text, numbers)
            for it in b.items
        ]
    if isinstance(b, Quote):
        attribution = f", attribution: [{_esc(b.attribution)}]" if b.attribution else ""
        return [f"#quote(block: true{attribution})[{_rich(b.text, numbers)}]"]
    if isinstance(b, Code):
        lang = f"lang: {_str(b.language)}, " if b.language else ""
        return [
            f'#block(fill: rgb("{theme.surface}"), inset: 8pt, radius: 4pt, '
            f"width: 100%, raw(block: true, {lang}{_str(b.code)}))"
        ]
    if isinstance(b, Table):
        return _table(b, theme, numbers)
    if isinstance(b, (Image, Artifact)):
        if not b.path:
            return []
        path = Path(b.path)
        if not path.is_file() or not _embeddable(path):
            return []
        lines = [_image_ref(path)]
        if b.caption:
            lines.append(_caption(b.caption, theme))
        return lines
    if isinstance(b, Chart):
        lines = []
        if b.title:
            lines.append(f'#text(weight: "bold")[{_esc(b.title)}]')
        path = Path(b.path) if b.path else None
        if path and path.is_file() and _embeddable(path):
            lines.append(_image_ref(path))
            if b.caption:
                lines.append(_caption(b.caption, theme))
            return lines
        rows = [
            [s.name] + ["" if v is None else f"{v:g}" for v in s.values]
            for s in b.series
        ]
        return lines + _table(
            Table(header=[""] + list(b.labels), rows=rows, caption=b.caption),
            theme,
            numbers,
        )
    if isinstance(b, StatRow):
        if not b.items:
            return []
        cards = []
        for st in b.items:
            inner = (
                f'#text(fill: rgb("{theme.primary}"), size: 15pt, '
                f'weight: "bold")[{_esc(st.value)}]#linebreak()'
                f'#text(fill: rgb("{theme.muted}"), size: 9pt)[{_esc(st.label)}]'
            )
            if st.delta:
                inner += (
                    f'#linebreak()#text(fill: rgb("{theme.accent}"), '
                    f"size: 9pt)[{_esc(st.delta)}]"
                )
            cards.append(
                f'block(fill: rgb("{theme.surface}"), inset: 10pt, radius: 4pt, '
                f"width: 100%, [{inner}])"
            )
        return [
            f"#grid(columns: (1fr,) * {len(b.items)}, gutter: 8pt, "
            + ", ".join(cards)
            + ")"
        ]
    if isinstance(b, Callout):
        color = _callout_color(b.style, theme)
        return [
            f'#block(fill: rgb("{theme.surface}"), '
            f'stroke: (left: 3pt + rgb("{color}")), '
            f"inset: 10pt, radius: 4pt, width: 100%)"
            f"[{_rich(b.text, numbers)}]"
        ]
    if isinstance(b, Divider):
        return [f'#line(length: 100%, stroke: 0.5pt + rgb("{theme.surface}"))']
    raise RenderError(f"unhandled block type {type(b).__name__}")


def _sheet_cell(cell) -> str:
    if isinstance(cell, Formula):
        return cell.formula
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    return str(cell)


def to_typst(doc: Document, theme: Theme) -> str:
    numbers = source_numbers(doc)
    lines = [
        '#set page(paper: "a4", margin: 2.2cm)',
        f'#set text(font: {_font(theme.font_body)}, size: 11pt, '
        f'fill: rgb("{theme.text}"))',
        "#set par(justify: true)",
        f"#show heading: set text(font: {_font(theme.font_heading)}, "
        f'fill: rgb("{theme.primary}"))',
        "#show heading: set block(above: 1.3em, below: 0.7em)",
    ]
    lines += [
        f"#show heading.where(level: {lvl}): set text(size: {size})"
        for lvl, size in _HEADING_SIZES.items()
    ]
    # Logo placeholder: render() swaps this comment for a sized #image if the
    # document carries a usable logo, else it renders as nothing.
    lines += ["", "// __DOCLOOM_LOGO__"]
    lines += [
        "",
        f"#text(font: {_font(theme.font_heading)}, size: 24pt, "
        f'weight: "bold", fill: rgb("{theme.primary}"))[{_esc(doc.title)}]',
    ]
    if doc.subtitle:
        lines += [
            "",
            f'#text(fill: rgb("{theme.muted}"), size: 13pt, '
            f'style: "italic")[{_esc(doc.subtitle)}]',
        ]
    byline = " \u2014 ".join(
        p for p in (", ".join(doc.authors), doc.date or "") if p
    )
    if byline:
        lines += ["", f'#text(fill: rgb("{theme.muted}"), size: 10pt)[{_esc(byline)}]']
    lines += [
        "",
        f'#line(length: 100%, stroke: 1pt + rgb("{theme.primary}"))',
    ]
    for b in report_blocks(doc):
        rendered = _block(b, theme, numbers)
        if rendered:
            lines += [""] + rendered
    for sheet in doc.sheets:  # workbooks would otherwise be silently dropped in PDF
        if sheet.columns:
            tbl = Table(
                header=[c.header for c in sheet.columns],
                rows=[[_sheet_cell(c) for c in row] for row in sheet.rows],
            )
            lines += ["", f"#heading(level: 2)[{_esc(sheet.name)}]", ""] + _table(tbl, theme, {})
    if doc.sources and cited_ids(doc):
        lines += ["", "= Sources", ""]
        seen_ids: set[str] = set()
        for src in doc.sources:
            # duplicate ids keep the first number (matches source_numbers, ir.py);
            # listing every entry here would desync Typst's auto-numbered "+"
            # markers from the citation superscripts, which use source_numbers.
            if src.id in seen_ids:
                continue
            seen_ids.add(src.id)
            entry = _esc(src.title)
            if src.publisher:
                entry += ", " + _esc(src.publisher)
            if src.date:
                entry += f" ({_esc(src.date)})"
            if src.url:
                href = _safe_link(src.url)
                if href:
                    entry += f", #link({_str(href)})[{_esc(src.url)}]"
                else:
                    entry += f", {_esc(src.url)}"
            lines.append("+ " + entry)
    return "\n".join(lines) + "\n"


# Typst's font loader only parses TTF/OTF/TTC; WOFF/WOFF2 containers compile
# without error but silently fall back to the default font, so they are not
# offered here (unlike html.py, which can use them directly via @font-face).
_FONT_EXTS = (".ttf", ".otf", ".ttc")


def _copy_fonts(theme: Theme, tmp_dir: Path) -> list[str]:
    """Copy the theme's brand font files into `tmp_dir` so typst resolves the
    theme's font *names* against them. Returns the font-path list (empty if
    none), to pass to typst.compile(font_paths=...)."""
    copied = False
    for i, src in enumerate((theme.font_body_src, theme.font_heading_src)):
        if not src:
            continue
        p = Path(src)
        if p.suffix.lower() not in _FONT_EXTS or not p.is_file():
            continue
        try:
            shutil.copy(p, tmp_dir / f"font{i}{p.suffix}")
            copied = True
        except OSError:
            pass
    return [str(tmp_dir)] if copied else []


def _inject_logo(source: str, doc: Document, tmp_dir: Path) -> str:
    """Replace the __DOCLOOM_LOGO__ placeholder with a right-aligned, sized
    brand logo (copied local to the compile dir), or drop it."""
    logo = doc.logo
    if logo and logo.path and Path(logo.path).is_file() and _embeddable(Path(logo.path)):
        p = Path(logo.path)
        local = "logo" + p.suffix
        try:
            shutil.copy(p, tmp_dir / local)
            # count=1: the placeholder line is always emitted first, before any
            # body content, so the first occurrence is the genuine one. A count
            # limit stops this from also rewriting the same literal text if it
            # appears inside user content (e.g. a Code block quoting docloom's
            # own output) further down in the source.
            return source.replace(
                "// __DOCLOOM_LOGO__",
                f"#align(right)[#image({_str(local)}, height: 1.4cm)]",
                1,
            )
        except OSError:
            pass
    return source.replace("// __DOCLOOM_LOGO__", "", 1)


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    try:
        import typst
    except ImportError:
        raise RenderError(
            "PDF rendering needs the typst package: pip install docloom[pdf]"
        ) from None
    source = to_typst(doc, theme)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        images = {
            b.path
            for b in report_blocks(doc)
            if isinstance(b, (Image, Chart, Artifact))
            and b.path
            and Path(b.path).is_file()
            and _embeddable(Path(b.path))
        }
        # Longest path first: the absolute-path marker "// docloom-image: <path>"
        # has no terminator, so if one path string is a prefix of another,
        # replacing the shorter one first would also corrupt the longer one's
        # still-unreplaced marker line. Replacing longest-first means a marker
        # is always fully substituted before any shorter path can match inside it.
        for i, path in enumerate(sorted(images, key=len, reverse=True)):
            p = Path(path)
            local = f"img{i}{p.suffix}"
            ref = (
                f"// docloom-image: {p.as_posix()}"
                if p.is_absolute()
                else f"#image({_str(p.as_posix())})"
            )
            try:
                shutil.copy(path, tmp_dir / local)
            except OSError:  # vanished or unreadable since the is_file check
                source = source.replace(ref, f"// docloom-image skipped: {p.as_posix()}")
                continue
            source = source.replace(ref, f"#image({_str(local)})")
        source = _inject_logo(source, doc, tmp_dir)
        # Copy any brand font files so typst resolves the theme's font names.
        font_paths = _copy_fonts(theme, tmp_dir)
        typ_file = tmp_dir / "document.typ"
        typ_file.write_text(source, encoding="utf-8")
        try:
            pdf = (typst.compile(str(typ_file), font_paths=font_paths)
                   if font_paths else typst.compile(str(typ_file)))
        except Exception as exc:
            raise RenderError(f"typst compilation failed: {exc}") from exc
    out_path.write_bytes(pdf)
    return out_path

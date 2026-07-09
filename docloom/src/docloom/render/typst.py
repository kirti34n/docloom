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
    Heading,
    Image,
    NumberedList,
    Paragraph,
    Quote,
    RichText,
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


def _esc(text: str) -> str:
    """Escape Typst markup special characters in user text. Newlines flatten
    to spaces so text cannot leak out of line-based markup (headings, list
    items); raw/code text goes through _str instead and keeps them."""
    text = " ".join(text.splitlines())
    escaped = "".join("\\" + c if c in _MARKUP_SPECIALS else c for c in text)
    # "1. foo" at line start would parse as a numbered-list item: escape the dot
    return re.sub(r"^(\s*\d+)\.", r"\1\\.", escaped)


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
                return fh.read(256).lstrip().startswith(b"<")
        except OSError:
            return False
    try:
        with PILImage.open(path) as img:
            img.verify()
        return True
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
            if cell_link_color:
                # ponytail: typst 0.15.0 panics ("expected link ancestor in
                # logical tree") when a styled link wraps across lines inside
                # a table cell, so cell links render as colored text without
                # #link — cells lose clickability until that upstream typst
                # bug is fixed.
                piece = f'#text(fill: rgb("{cell_link_color}"))[{piece}]'
            else:
                piece = f"#link({_str(sp.link)})[{piece}]"
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
        return [_rich(b.text, numbers)]
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
    if doc.sources and cited_ids(doc):
        lines += ["", "= Sources", ""]
        for src in doc.sources:
            entry = _esc(src.title)
            if src.publisher:
                entry += " \u2014 " + _esc(src.publisher)
            if src.date:
                entry += f" ({_esc(src.date)})"
            if src.url:
                entry += f", #link({_str(src.url)})[{_esc(src.url)}]"
            lines.append("+ " + entry)
    return "\n".join(lines) + "\n"


_FONT_EXTS = (".ttf", ".otf", ".woff2", ".woff", ".ttc")


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
            return source.replace(
                "// __DOCLOOM_LOGO__",
                f"#align(right)[#image({_str(local)}, height: 1.4cm)]",
            )
        except OSError:
            pass
    return source.replace("// __DOCLOOM_LOGO__", "")


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
        for i, path in enumerate(sorted(images)):
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

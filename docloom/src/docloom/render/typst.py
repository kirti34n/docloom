"""PDF renderer: compiles the Document IR to Typst source, then to PDF
in-process via the `typst` wheel. `to_typst()` returns the .typ source;
`render()` compiles it. Images are copied next to the temp .typ so Typst
can resolve them regardless of where the originals live; standalone
`to_typst()` output references the original paths (POSIX-style), which
resolve when the .typ is compiled from a location those paths are
relative to. Absolute image paths (which Typst rejects) become comment
lines in standalone output and only embed via `render()`.

Charts with no pre-rendered path are painted with chart_svg and embedded as
`image(bytes(...), format: "svg")` (typst>=0.13): the SVG text lands straight
in the source, so this works in standalone to_typst() output too, not just
the compiled render() path."""

from __future__ import annotations

import re
import shutil
import tempfile
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image as PILImage

from . import chart_svg, diagram_svg
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


# Typst rejects the string "jpeg" for `format:` (only "jpg" is accepted), so
# PIL's "JPEG" format name cannot be lowercased and used directly.
_TYPST_RASTER = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}


def _svg_wellformed(path: Path) -> bool:
    """True if `path` is well-formed XML with an <svg> root. Typst's SVG
    embedder requires well-formed XML with a real root element; a truncated
    or entity-broken file that merely contains the substring "<svg" (e.g. an
    unclosed tag) would abort the whole compile if that substring alone were
    trusted."""
    try:
        with path.open("rb") as fh:
            root = ET.parse(fh).getroot()
    except Exception:
        return False
    return root.tag.rsplit("}", 1)[-1].lower() == "svg"


def _typst_format(path: Path) -> str | None:
    """The Typst `format:` string to embed `path` as, or None if Typst can't
    decode it. Rasters are classified by DECODED content (PIL's img.format),
    not by file extension: Typst itself dispatches its own auto-detection by
    extension, so a mislabeled file (e.g. JPEG bytes saved as figure1.png)
    must have its true format passed explicitly or the compile aborts."""
    if path.suffix.lower() == ".svg":
        return "svg" if _svg_wellformed(path) else None
    try:
        with PILImage.open(path) as img:
            fmt = (img.format or "").upper()
            img.verify()  # must read .format first: verify() can invalidate img
        # Typst decodes only these raster formats; PIL can verify many more
        # (BMP, TIFF, ICO, ...) that would crash the compile, so skip them.
        return _TYPST_RASTER.get(fmt)
    except Exception:
        return None


def _embeddable(path: Path) -> bool:
    """True if Typst can embed the image file; a file that exists but is
    corrupt or undecodable gets skipped instead of failing the compile,
    consistent with the missing-file policy."""
    return _typst_format(path) is not None


def _image_ref(path: Path, fmt: str) -> str:
    """The embed line for an image path; render() rewrites these to local
    copies. Absolute paths (which Typst rejects) become comment lines that
    only embed via render()."""
    if path.is_absolute():
        return f"// docloom-image: {path.as_posix()}"
    return f"#image({_str(path.as_posix())}, format: {_str(fmt)})"


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
                # #link. Cells lose clickability until that upstream typst
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


def _diagram_theme(theme: Theme) -> dict:
    """diagram_svg's paint/solve pipeline takes a plain dict overlay, not the
    docloom Theme model (docs/diagram-plan.md section 3: "the docloom Theme
    model is adapted by callers"). Every renderer that embeds a diagram
    builds this same six-key adapter."""
    return {
        "primary": theme.primary,
        "accent": theme.accent,
        "surface": theme.surface,
        "text": theme.text,
        "muted": theme.muted,
        "background": theme.background,
    }


def _diagram_placeholder(b: Diagram, theme: Theme) -> list[str]:
    """A visible stand-in for a diagram that had nodes but failed to render
    (matching docx's `[diagram: alt]` paragraph and markdown's `*[diagram:
    alt]*` line): the block never just vanishes from the PDF."""
    display = f"[diagram: {b.alt}]" if b.alt else "[diagram]"
    lines = [
        f'#align(center, block(fill: rgb("{theme.surface}"), inset: 14pt, '
        f'radius: 4pt, width: 100%)[#text(fill: rgb("{theme.muted}"), '
        f'style: "italic")[{_esc(display)}]])'
    ]
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
        if not path.is_file():
            return []
        fmt = _typst_format(path)
        if fmt is None:
            return []
        lines = [_image_ref(path, fmt)]
        if b.caption:
            lines.append(_caption(b.caption, theme))
        return lines
    if isinstance(b, Chart):
        lines = []
        if b.title:
            lines.append(f'#text(weight: "bold")[{_esc(b.title)}]')
        path = Path(b.path) if b.path else None
        fmt = _typst_format(path) if path and path.is_file() else None
        if path and fmt:
            lines.append(_image_ref(path, fmt))
            if b.caption:
                lines.append(_caption(b.caption, theme))
            return lines
        svg = chart_svg.render_svg(b, theme)
        if svg:
            lines.append(f'#image(bytes({_str(svg)}), format: "svg", width: 100%)')
            if b.caption:
                lines.append(_caption(b.caption, theme))
            return lines
        rows = [
            [s.name] + ["" if chart_svg._finite(v) is None else chart_svg._fmt(v) for v in s.values]
            for s in b.series
        ]
        return lines + _table(
            Table(header=[""] + list(b.labels), rows=rows, caption=b.caption),
            theme,
            numbers,
        )
    if isinstance(b, Diagram):
        # Diagrams have no pre-rendered path (coordinate-free IR): the SVG is
        # generated fresh and embedded as bytes straight in the .typ source,
        # exactly like Chart's no-path fallback above -- true vector, no
        # rasterizer, and it works from standalone to_typst() output too
        # (this module's own docstring promise), not just the compiled
        # render() path that copies files into a temp dir. The diagram's own
        # title is already painted inside the SVG by paint_svg, so unlike
        # Chart, no separate #text(...) title line is added here.
        #
        # A diagram with no nodes at all is a deliberate empty slot and
        # skipped silently, matching every other renderer's pathless-block
        # convention. A diagram that HAD nodes but failed to render (solve()
        # raises on anything lint would flag, e.g. a dangling edge) degrades
        # to a visible placeholder plus a warning, never a silent drop
        # (finding 14): this branch used to `return []` here with zero
        # trace, unlike docx/markdown/pptx, which all show something.
        if not b.nodes:
            return []
        try:
            svg = diagram_svg.render_svg(b, _diagram_theme(theme))
        except Exception:
            svg = ""
        if not svg:
            warnings.warn(
                f"typst: diagram {b.id!r} could not be rendered; "
                "placeholder shown",
                stacklevel=2,
            )
            return _diagram_placeholder(b, theme)
        lines = [f'#image(bytes({_str(svg)}), format: "svg", width: 100%)']
        if b.caption:
            lines.append(_caption(b.caption, theme))
        return lines
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
        # justify: true already implies linebreaks: "optimized", so these
        # costs (typst>=0.12, well below this repo's typst>=0.13 floor) take
        # effect immediately: fewer widows/orphans/runts, tamer hyphenation.
        '#set text(lang: "en", hyphenate: true, '
        "costs: (runt: 200%, widow: 250%, orphan: 250%, hyphenation: 150%))",
        "#show heading: set text(hyphenate: false)",
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
        if sheet.columns or any(sheet.rows):  # any() skips a rows=[[]] no-cell sheet
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
    fmt = _typst_format(Path(logo.path)) if logo and logo.path and Path(logo.path).is_file() else None
    if logo and logo.path and fmt:
        p = Path(logo.path)
        local = f"logo.{fmt}"
        try:
            shutil.copy(p, tmp_dir / local)
            # count=1: the placeholder line is always emitted first, before any
            # body content, so the first occurrence is the genuine one. A count
            # limit stops this from also rewriting the same literal text if it
            # appears inside user content (e.g. a Code block quoting docloom's
            # own output) further down in the source.
            # 1.27cm = 0.5in: the shared logo target height also used by
            # docx (Inches(0.5)) and html (3rem = 48px @96dpi), so the brand
            # mark is a consistent size across every rendered format.
            return source.replace(
                "// __DOCLOOM_LOGO__",
                f"#align(right)[#image({_str(local)}, height: 1.27cm, format: {_str(fmt)})]",
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
        # Substitutions apply in a single left-to-right pass over the ORIGINAL
        # source (not sequential str.replace calls): a generated local name
        # (e.g. img0.png) can otherwise collide with another block's real
        # relative path, and a later replace-all would silently re-rewrite
        # text a previous replacement had just inserted.
        replacements: dict[str, str] = {}
        for i, path in enumerate(sorted(images, key=len, reverse=True)):
            p = Path(path)
            fmt = _typst_format(p)
            local = f"img{i}.{fmt}"
            ref = _image_ref(p, fmt)
            try:
                shutil.copy(path, tmp_dir / local)
            except OSError:  # vanished or unreadable since the is_file check
                replacements[ref] = f"// docloom-image skipped: {p.as_posix()}"
                continue
            replacements[ref] = f"#image({_str(local)}, format: {_str(fmt)})"
        if replacements:
            pattern = re.compile("|".join(re.escape(r) for r in replacements))
            source = pattern.sub(lambda m: replacements[m.group(0)], source)
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

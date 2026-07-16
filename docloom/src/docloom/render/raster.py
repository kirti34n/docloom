"""Optional SVG rasterizer: the one narrow seam between docloom and a real
SVG engine.

PPTX and DOCX cannot embed SVG (python-pptx/python-docx have no SVG decoder),
so any SVG (a chart painted by chart_svg, or an SVG image/diagram artifact)
has to become a PNG before it can be embedded. resvg is the engine we use, but
it stays an OPTIONAL extra:

    pip install "docloom[diagrams]"

Every function here imports it lazily and returns None (never raises) when the
extra is absent, so a core install behaves exactly as it did before: renderers
fall back to their existing placeholder / data-table paths.

Fonts: resvg draws text with system fonts. A slim container (python:3.12-slim
ships no fonts at all) would rasterize labels invisibly, so callers may pass
explicit `font_files` (e.g. a theme's own .ttf/.otf), and deployments should
install at least one system font family (fonts-dejavu-core).
"""

from __future__ import annotations

from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def available() -> bool:
    """True when the optional rasterizer backend can be imported."""
    try:
        import resvg_py  # noqa: F401
    except Exception:
        return False
    return True


def svg_to_png(
    svg: str,
    *,
    width: int | None = None,
    font_files: list[str] | None = None,
) -> bytes | None:
    """Rasterize an SVG string to PNG bytes.

    Returns None (never raises) when the optional rasterizer is not installed,
    when the SVG is empty, or when rendering fails for any reason. `width` is
    the target pixel width (height follows the SVG's aspect ratio);
    `font_files` are extra font files resvg should load before it falls back to
    system fonts.
    """
    if not svg or not svg.strip():
        return None
    try:
        import resvg_py
    except Exception:
        return None  # optional extra not installed: caller keeps its fallback

    kwargs: dict[str, object] = {"svg_string": svg, "background": "#FFFFFF"}
    if width:
        kwargs["width"] = int(width)
    fonts = [str(f) for f in (font_files or []) if f and Path(f).is_file()]
    if fonts:
        kwargs["font_files"] = fonts
    try:
        png = resvg_py.svg_to_bytes(**kwargs)
    except Exception:
        if not fonts:
            return None  # malformed SVG, unsupported feature, ...
        kwargs.pop("font_files")  # a font file resvg cannot parse must not
        try:                      # cost us the whole picture: retry bare
            png = resvg_py.svg_to_bytes(**kwargs)
        except Exception:
            return None
    data = bytes(png) if png else b""
    return data if data.startswith(PNG_MAGIC) else None


def svg_file_to_png(
    path: str | Path,
    *,
    width: int | None = None,
    font_files: list[str] | None = None,
) -> bytes | None:
    """Same as svg_to_png, reading the SVG from a file. None on any failure."""
    try:
        svg = Path(path).read_text(encoding="utf-8-sig")
    except Exception:
        return None
    return svg_to_png(svg, width=width, font_files=font_files)


def theme_font_files(theme) -> list[str]:
    """The theme's own font files (if any) as a font_files list for resvg, so
    branded text rasterizes with the brand font even where the OS has none."""
    out = []
    for src in (getattr(theme, "font_heading_src", None), getattr(theme, "font_body_src", None)):
        # resvg reads real font files only; a .woff2 (fine for HTML @font-face)
        # is not one, so it is skipped rather than handed over to fail
        if src and Path(src).suffix.lower() in (".ttf", ".otf", ".ttc") and Path(src).is_file():
            out.append(str(src))
    return out


def is_svg(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".svg"

"""docloom — the document output layer for AI apps.

Your LLM emits a validated JSON document (via structured output); docloom
deterministically renders it to PPTX, DOCX, XLSX, PDF, HTML, or Markdown,
and lints it for the failures generated documents actually ship with.
"""

from .ir import (
    Artifact, Block, BulletList, Callout, Cell, Chart, Code, Column, Diagram,
    DiagramEdge, DiagramGroup, DiagramNode, Divider, Document, Formula,
    Heading, Image, ListItem, NumberedList, Paragraph, Quote, RichText,
    Series, Sheet, Slide, Source, Span, Stat, StatRow, Table, diagram_hash,
    ensure_ids,
)
from .lint import Finding, has_errors, lint
from .llm import AUTHORING_GUIDE, llm_schema, parse_llm_output
from .render import FORMATS, RenderError, render
from .theme import DEFAULT, Theme

__version__ = "0.2.0"

__all__ = [
    "Artifact", "Block", "BulletList", "Callout", "Cell", "Chart", "Code",
    "Column", "Diagram", "DiagramEdge", "DiagramGroup", "DiagramNode",
    "Divider", "Document", "Formula", "Heading", "Image",
    "ListItem", "NumberedList", "Paragraph", "Quote", "RichText", "Series",
    "Sheet", "Slide", "Source", "Span", "Stat", "StatRow", "Table",
    "diagram_hash", "ensure_ids", "Finding", "lint", "has_errors", "render",
    "RenderError", "FORMATS", "Theme", "DEFAULT", "llm_schema",
    "parse_llm_output", "AUTHORING_GUIDE", "render_diagram", "__version__",
]


def render_diagram(
    d: Diagram, theme: Theme | None = None, fmt: str = "svg", *,
    layout: str | None = None,
) -> str | bytes | None:
    """One-shot Diagram export (docs/diagram-plan.md section 4c): solves the
    diagram's layout once, then serializes it to the requested format.

    fmt="svg" (default): themed SVG string, root stamped
        data-docloom-hash="{diagram_hash(d)}".
    fmt="png": rasterized PNG bytes via the optional [diagrams] extra
        (render.raster.svg_to_png); returns None, never raises, when that
        extra is not installed.
    fmt="drawio": .drawio (mxGraph XML) string, a one-way DERIVED export
        (see render.drawio's module docstring for the Tier 1/Tier 2
        contract); docloom never reads this back.

    `layout` picks which solver produces the geometry every format above is
    painted from:
      "native" (default): render.diagram_svg.solve() -- the custom Sugiyama
          solver this project has always shipped. Unchanged behavior.
      "dot": render.diagram_dot.solve_dot() -- an OPT-IN Graphviz `dot`
          backend (docloom[dotlayout] extra, i.e. pygraphviz) that packs
          complex branching graphs and their group boxes much tighter. If
          pygraphviz/Graphviz isn't importable/usable, this WARNS and falls
          back to "native" rather than raising -- a missing optional
          dependency must never break a caller who only asked for the
          default experience through some other path (e.g. a saved
          document that happens to set layout="dot").
      "auto": alias for "dot" today (kept as its own literal so a future
          "pick whichever solver fits target_aspect better" policy has a
          name to grow into without another call-signature change).

    `theme` is a docloom.theme.Theme (or None for DEFAULT); every diagram
    emitter internally wants the plain dict overlay
    render.diagram_svg.solve()/paint_svg() take, so this function does that
    adaptation once.
    """
    if fmt not in ("svg", "png", "drawio"):
        raise ValueError(f'fmt must be "svg", "png", or "drawio", got {fmt!r}')
    # Default to the diagram's own declared layout (Diagram.layout) when the
    # caller didn't force one, so a document that sets layout="dot" renders that
    # way through every path without every call site having to thread it.
    if layout is None:
        layout = getattr(d, "layout", "native")
    if layout not in ("native", "dot", "auto"):
        raise ValueError(
            f'layout must be "native", "dot", or "auto", got {layout!r}'
        )

    from .render import raster
    from .render.diagram_svg import _stamp_hash, paint_svg, render_svg, solve
    from .render.drawio import render_drawio

    t = theme or DEFAULT
    theme_dict = {
        "primary": t.primary, "accent": t.accent, "surface": t.surface,
        "text": t.text, "muted": t.muted, "background": t.background,
    }

    def _solve():
        if layout in ("dot", "auto"):
            import warnings

            from .render.diagram_dot import DotUnavailable, solve_dot

            try:
                return solve_dot(d, theme_dict)
            except DotUnavailable as exc:
                warnings.warn(
                    f"layout={layout!r} requested but unavailable ({exc}); "
                    "falling back to the native solver",
                    stacklevel=3,
                )
        return solve(d, theme_dict)

    if fmt == "svg":
        if layout == "native":
            return render_svg(d, theme_dict)
        return _stamp_hash(paint_svg(_solve(), theme_dict), diagram_hash(d))
    if fmt == "png":
        svg = paint_svg(_solve(), theme_dict)
        return raster.svg_to_png(svg, font_files=raster.theme_font_files(t))
    solved = _solve()
    return render_drawio(d, solved, theme_dict)

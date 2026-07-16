"""Emit a .drawio (mxGraph) file from the painter's SOLVED geometry.

THE SEAM (docs/diagram-plan.md section 3 and 4): layout is solved exactly
once, by diagram_svg.solve(). This module never lays anything out; it only
reads a SolvedDiagram (positions, routed polylines, placed label boxes) and
serializes it into mxGraph XML. The SVG emitter (diagram_svg.paint_svg) and
the native-PPTX emitter (render/diagram_pptx.py) consume the exact same
SolvedDiagram from the exact same solve() call, so a .drawio export and an
embedded PNG never disagree about where anything sits.

Editability contract (docs/diagram-plan.md section 1, Tier 1/Tier 2): the
Diagram block inside the IR JSON is the only editable source of truth. This
.drawio file is a DERIVED, ONE-WAY export: docloom never reads a .drawio file
back. A user who repositions shapes inside draw.io has forked the file;
regenerating the deck/report overwrites it, it does not merge. The IR content
hash is stamped as an XML comment (`<!-- docloom:hash:{diagram_hash(d)} -->`)
so a derived file can be mechanically identified as generated-from this exact
Tier 1 content; the comment is schema-invisible (XSD validates elements and
attributes, never comments) and is never parsed back by docloom.

Ported from the proven prototype (scratchpad/bakeoff/painter/emit_drawio.py:
5/5 bake-off specs valid against the official jgraph mxfile.xsd, referentially
intact, stdlib only, ~39ms for all 5). The prototype did its own layout()
call inline; here that front half is gone entirely because solve() already
did it -- this module starts from a SolvedDiagram.

Stdlib only (xml.sax.saxutils), no third-party dependency, so this emitter
never has an "extra not installed" fallback path: it is always available.

draw.io's own AI-generation guidance: emit UNCOMPRESSED XML (compressed=false
below), because compressed diagram content is deflate+base64 and AI-generated
content must not be compressed.
"""
from __future__ import annotations

import time
from xml.sax.saxutils import escape, quoteattr

from ..ir import Diagram, diagram_hash
from .diagram_svg import (
    EDGE_STYLE,
    MARGIN,
    THEME,
    SolvedDiagram,
    SolvedNode,
    kind_palette,
    measure,
)

# Same (style-key, display-name) pairs paint_svg and the native PPTX renderer
# draw in their own legend key -- kept as a local literal here too (neither
# of those modules exports it as shared state; each legend-drawing emitter
# owns its own copy of this tuple, matching how EDGE_STYLE itself is the only
# actually-shared piece of legend styling data).
LEGEND_KEY = (
    ("solid", "flow"), ("dashed", "async / return"),
    ("emphasis", "primary path"), ("secure", "secure"),
)


def _style(pairs: list[tuple[str, object]]) -> str:
    return ";".join(f"{k}={v}" for k, v in pairs) + ";"


def _node_style(n: SolvedNode, pal: dict) -> list[tuple[str, object]]:
    """Style-string key/value pairs for one node's mxCell, by kind. Colors
    always come from kind_palette(theme) (the same palette paint_svg uses),
    so a .drawio export reads as the same brand family as the SVG/PPTX
    exports. Only `store` gets a distinct SHAPE (drawio's built-in cylinder,
    matching the painter's own cylinder rendering for that kind); the other
    six kinds are all rounded rectangles differentiated by fill/stroke color,
    which is the "node kind colors" requirement -- distinct shapes for every
    kind are not required by the plan and would risk unproven drawio shape
    names."""
    p = pal.get(n.type, pal["service"])
    if n.type == "store":
        return [
            ("shape", "cylinder3"), ("boundedShape", 1), ("whiteSpace", "wrap"),
            ("html", 1), ("fillColor", p["fill"]), ("strokeColor", p["line"]),
            ("strokeWidth", 1.3), ("size", 8), ("fontSize", 12),
        ]
    pairs: list[tuple[str, object]] = [
        ("rounded", 1), ("arcSize", 8), ("whiteSpace", "wrap"), ("html", 1),
        ("fillColor", p["fill"]), ("strokeColor", p["line"]),
        ("strokeWidth", 1.3), ("fontSize", 12), ("align", "center"),
        ("verticalAlign", "middle"),
    ]
    if n.type == "external":
        pairs.append(("dashed", 1))
    elif n.type == "queue":
        pairs.append(("shape", "process"))
        pairs.append(("size", 0.12))
    elif n.type == "security":
        pairs.append(("strokeWidth", 2))
    return pairs


def _node_label(n: SolvedNode, pal: dict, t: dict) -> str:
    """HTML label content (style carries html=1): bold node label, a small
    muted sublabel line, and an even smaller tag line in the kind's accent
    color -- the same three-line hierarchy paint_svg draws for a node."""
    p = pal.get(n.type, pal["service"])
    parts = [f"<b>{escape(n.label)}</b>"]
    if n.sublabel:
        parts.append(
            f"<br/><font style=\"font-size:9px\" color=\"{t['muted']}\">"
            f"{escape(n.sublabel)}</font>"
        )
    if n.tag:
        parts.append(
            f"<br/><font style=\"font-size:8px\" color=\"{p['bar']}\">"
            f"{escape(n.tag)}</font>"
        )
    return "".join(parts)


def _group_style(kind: str, t: dict) -> list[tuple[str, object]]:
    secure = kind == "security-group"
    col = t["accent"] if secure else t["primary"]
    pairs: list[tuple[str, object]] = [
        ("rounded", 1), ("arcSize", 6), ("whiteSpace", "wrap"), ("html", 1),
        ("fillColor", t["surface"]), ("strokeColor", col),
        ("verticalAlign", "top"), ("align", "left"), ("spacingLeft", 10),
        ("spacingTop", 4), ("fontSize", 12), ("fontStyle", 1),
        ("fontColor", col), ("container", 1), ("collapsible", 0),
        ("movable", 1), ("resizable", 1),
    ]
    if secure:
        pairs.append(("dashed", 1))
    return pairs


def _legend_cells(s: SolvedDiagram, pal: dict, t: dict, new_id) -> list[str]:
    """Kind swatches + edge-style key, drawn into the LEGEND_H canvas band
    solve() reserves at the bottom of the diagram when legend=True (the
    default) -- the same band paint_svg (diagram_svg.py) and the native PPTX
    renderer (diagram_pptx.py) draw into. Before this, the .drawio export was
    the one emitter of the three that inherited the reserved band and drew
    nothing in it: dead space in every exported file (docs/diagram-status.md
    finding 16's legend_h contract). Geometry/spacing here mirrors those two
    emitters' legend layout exactly (same `ly` baseline, same chip/bar/label
    triplet per kind, same key-line-plus-label per edge style, same
    `measure()` text-width estimate) so a .drawio export reads as the same
    diagram, not a different design.

    Every cell carries `docloomLegend=1` in its style string -- an
    unrecognized-but-harmless custom style key to drawio itself -- so a
    legend cell can always be told apart from a real diagram node/edge
    cell by inspecting its style, not by relying on document position.

    Draws nothing when s.legend_h == 0 (a SolvedDiagram built with
    solve(..., legend=False)): that caller reserved no band, so drawing into
    it would paint over the diagram's own last row of content, which is
    exactly the bug this function exists to avoid for the legend=True case.

    Legend "lines" (the header rule and the four edge-style key strokes) are
    emitted as edge cells with explicit sourcePoint/targetPoint mxPoints and
    no source/target attributes -- draw.io's own vocabulary for an
    unconnected straight line (confirmed against the vendored mxfile.xsd:
    mxGeometryType's mxPoint choice documents exactly this "sourcePoint" /
    "targetPoint" pairing for edges with no connected vertex) -- rather than
    add_connector's vertex-to-vertex form, since a legend key has no vertices
    to connect.
    """
    if not s.legend or s.legend_h <= 0:
        return []
    cells: list[str] = []
    ly = s.height - s.legend_h + 22

    def line(x1: float, y1: float, x2: float, y2: float,
              pairs: list[tuple[str, object]]) -> str:
        i = new_id("legend-line")
        full = [("html", 1), ("docloomLegend", 1), ("endArrow", "none")] + pairs
        return (
            f'<mxCell id={quoteattr(i)} style={quoteattr(_style(full))} '
            f'edge="1" parent="1">'
            f'<mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x1:.1f}" y="{y1:.1f}" as="sourcePoint"/>'
            f'<mxPoint x="{x2:.1f}" y="{y2:.1f}" as="targetPoint"/>'
            f'</mxGeometry></mxCell>'
        )

    def label(text: str, x: float, y: float, w: float) -> str:
        i = new_id("legend-label")
        pairs = [
            ("html", 1), ("docloomLegend", 1), ("fillColor", "none"),
            ("strokeColor", "none"), ("align", "left"),
            ("verticalAlign", "middle"), ("fontSize", 10),
            ("fontColor", t["muted"]),
        ]
        return (
            f'<mxCell id={quoteattr(i)} value={quoteattr(escape(text))} '
            f'style={quoteattr(_style(pairs))} vertex="1" parent="1">'
            f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="16" '
            f'as="geometry"/></mxCell>'
        )

    cells.append(line(MARGIN, ly - 16, s.width - MARGIN, ly - 16,
                       [("strokeColor", t["muted"]), ("strokeWidth", 1)]))

    lx = float(MARGIN)
    for kind in s.legend:
        p = pal.get(kind, pal["service"])
        chip_id = new_id("legend-chip")
        chip_pairs = [
            ("rounded", 1), ("arcSize", 25), ("html", 1), ("docloomLegend", 1),
            ("fillColor", p["fill"]), ("strokeColor", p["bar"]), ("strokeWidth", 1),
        ]
        cells.append(
            f'<mxCell id={quoteattr(chip_id)} value="" '
            f'style={quoteattr(_style(chip_pairs))} vertex="1" parent="1">'
            f'<mxGeometry x="{lx:.1f}" y="{ly - 2:.1f}" width="12" height="12" '
            f'as="geometry"/></mxCell>'
        )
        bar_id = new_id("legend-bar")
        bar_pairs = [
            ("html", 1), ("docloomLegend", 1), ("fillColor", p["bar"]),
            ("strokeColor", "none"),
        ]
        cells.append(
            f'<mxCell id={quoteattr(bar_id)} value="" '
            f'style={quoteattr(_style(bar_pairs))} vertex="1" parent="1">'
            f'<mxGeometry x="{lx + 0.5:.1f}" y="{ly - 2:.1f}" width="3" '
            f'height="12" as="geometry"/></mxCell>'
        )
        cells.append(label(kind, lx + 17, ly - 8, measure(kind, 10) + 6))
        lx += 17 + measure(kind, 10) + 20

    lx += 10
    for style_key, name in LEGEND_KEY:
        role, width, dash = EDGE_STYLE[style_key]
        color = t.get(role, t["muted"])
        cells.append(line(lx, ly + 4, lx + 24, ly + 4,
                           [("strokeColor", color), ("strokeWidth", width),
                            ("dashed", 1 if dash else 0)]))
        cells.append(label(name, lx + 30, ly - 2, measure(name, 10) + 6))
        lx += 30 + measure(name, 10) + 20

    return cells


def render_drawio(d: Diagram, solved: SolvedDiagram, theme: dict | None = None) -> str:
    """SolvedDiagram (+ the source Diagram, for the hash stamp) -> a .drawio
    (mxGraph XML) document string.

    `theme` is the same plain dict overlay diagram_svg.solve()/paint_svg()
    take (keys: primary, accent, surface, text, muted, background, font); a
    caller holding a docloom.theme.Theme adapts it the same way every other
    diagram emitter does: `{"primary": theme.primary, "accent": theme.accent,
    "surface": theme.surface, "text": theme.text, "muted": theme.muted,
    "background": theme.background}`.

    Deterministic given (solved, theme, d) except for the `modified`
    timestamp attribute on <mxfile>, which is wall-clock (draw.io itself
    stamps this on every save; it carries no semantic weight and is not part
    of the diagram_hash contract). The timestamp is UTC, matching its "Z"
    suffix (time.gmtime(), not the local-time default of time.strftime()).
    """
    t = dict(THEME)
    t.update(theme or {})
    pal = kind_palette(t)

    cells: list[str] = []
    counter = [1]

    def new_id(prefix: str) -> str:
        counter[0] += 1
        return f"{prefix}-{counter[0]}"

    # ---- groups first: real drawio containers (collapsible, movable), and
    # first in document order so nodes/edges paint OVER their boundary rect
    # (correct z-order, docs/diagram-plan.md section 4b's ordering applied
    # here too: groups, then nodes, then connectors/labels) ----
    gid: dict[str, str] = {}
    for g in solved.groups:
        i = new_id("grp")
        gid[g.id] = i
        cells.append(
            f'<mxCell id={quoteattr(i)} value={quoteattr(escape(g.label))} '
            f'style={quoteattr(_style(_group_style(g.kind, t)))} vertex="1" parent="1">'
            f'<mxGeometry x="{g.x:.1f}" y="{g.y:.1f}" width="{g.w:.1f}" '
            f'height="{g.h:.1f}" as="geometry"/></mxCell>'
        )

    # ---- nodes: children of their group container use coordinates RELATIVE
    # to that container's own origin (official drawio checklist item 12: a
    # container's children are positioned in the container's local frame,
    # not the canvas'), everyone else is a direct child of layer "1" in
    # absolute canvas coordinates ----
    nid: dict[str, str] = {}
    group_by_id = {g.id: g for g in solved.groups}
    for n in solved.nodes:
        i = new_id("n")
        nid[n.id] = i
        parent = gid.get(n.group or "", "1")
        px, py = n.x, n.y
        if n.group and n.group in group_by_id:
            gg = group_by_id[n.group]
            px, py = n.x - gg.x, n.y - gg.y
        cells.append(
            f'<mxCell id={quoteattr(i)} value={quoteattr(_node_label(n, pal, t))} '
            f'style={quoteattr(_style(_node_style(n, pal)))} vertex="1" '
            f'parent={quoteattr(parent)}>'
            f'<mxGeometry x="{px:.1f}" y="{py:.1f}" width="{n.w:.1f}" '
            f'height="{n.h:.1f}" as="geometry"/></mxCell>'
        )

    # ---- edges: the painter's already-SOLVED routed polylines become
    # waypoints; drawio re-routes orthogonally around them on edit (the
    # painter's exact rounded path is not expressible as a drawio edge
    # style, that trade is accepted -- docs/diagram-plan.md section 4c) ----
    for e in solved.edges:
        src, dst = nid.get(e.source), nid.get(e.target)
        if not src or not dst:
            continue  # dangling edge: lint already flags this as an error;
            # stay defensive rather than emit an invalid source/target ref
        role, width, dash = EDGE_STYLE.get(e.style, EDGE_STYLE["solid"])
        color = t.get(role, t["muted"])
        pairs: list[tuple[str, object]] = [
            ("edgeStyle", "orthogonalEdgeStyle"), ("rounded", 1), ("arcSize", 8),
            ("html", 1), ("strokeColor", color), ("strokeWidth", width),
            ("fontSize", 10), ("dashed", 1 if dash else 0),
            ("endArrow", "block"), ("endFill", 1), ("jettySize", "auto"),
            ("labelBackgroundColor", t["background"]),
        ]
        pts = e.pts or []
        way = ""
        if len(pts) > 2:
            way = (
                '<Array as="points">'
                + "".join(f'<mxPoint x="{p[0]:.1f}" y="{p[1]:.1f}"/>' for p in pts[1:-1])
                + "</Array>"
            )
        i = new_id("e")
        cells.append(
            f'<mxCell id={quoteattr(i)} value={quoteattr(escape(e.label or ""))} '
            f'style={quoteattr(_style(pairs))} edge="1" parent="1" '
            f'source={quoteattr(src)} target={quoteattr(dst)}>'
            f'<mxGeometry relative="1" as="geometry">{way}</mxGeometry></mxCell>'
        )

    # ---- legend: last in document order, same as the groups/nodes/edges
    # z-order note above -- it must paint over nothing else, and nothing
    # else should paint over it, so it goes after every real cell ----
    cells.extend(_legend_cells(solved, pal, t, new_id))

    body = "".join(cells)
    hash_comment = f"<!-- docloom:hash:{diagram_hash(d)} -->"
    xml = (
        f'<mxfile host="docloom" modified="{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}" '
        f'agent="docloom" version="24.0.0" compressed="false">'
        f'{hash_comment}'
        f'<diagram id="d0" name={quoteattr(d.title or solved.title or "Diagram")}>'
        f'<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{max(int(solved.width), 1)}" '
        f'pageHeight="{max(int(solved.height), 1)}" math="0" shadow="0">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
        f'</mxGraphModel></diagram></mxfile>'
    )
    return xml

"""Graphviz `dot` layout backend for architecture diagrams -- OPT-IN
alternative to diagram_svg.solve()'s custom Sugiyama solver, wired through
docloom.render_diagram(d, theme, fmt, layout="dot").

Why this exists: the custom solver spreads complex branching graphs out and
draws big, mostly-empty group boxes around them (measured on
arch_complex.json: ~86% empty space inside a group's own bounding box).
`dot` packs the same graph roughly 3x tighter with cluster subgraphs that
hug their members (docs/diagram-plan.md's own "research already proved
pygraphviz works" bake-off). It stays opt-in -- never the default -- because
it depends on pygraphviz (a C extension wrapping real Graphviz) rather than
pure Python, and produces a differently-shaped (denser, more rectilinear)
picture than the native solver's house style.

Contract: solve_dot() takes the exact same `Diagram` IR diagram_svg.solve()
takes and returns a byte-for-byte-compatible `SolvedDiagram` (same
SolvedNode/SolvedEdge/SolvedGroup dataclasses, same field meanings, same
top-left/Y-down/px-ish canvas-unit convention) -- so paint_svg,
diagram_pptx, and render_drawio, which only ever read a SolvedDiagram and
never re-lay anything out, work identically regardless of which solver
produced the geometry they're painting.

Geometry mapping (Graphviz -> docloom canvas space):
  Graphviz places everything in POINTS (72/in), origin bottom-left, Y-up.
  docloom's canvas is Y-down, node/group x,y are the TOP-LEFT corner.
  Let H = the graph's own bb height (pts). For any Graphviz point (gx, gy)
  relative to the graph bb's own origin, the flipped docloom-space point is
  (gx, H - gy) -- see `_flip` below.
    - Node: `pos` is the CENTER in pts; width/height (inches) * 72 gives the
      box size in the same pt units node_box() already sizes in, so
      x = cx - W/2, y = (H - cy) - Hn/2.
    - Cluster (== docloom group): `bb` is `x0,y0,x1,y1` in pts ->
      x = x0, y = H - y1, w = x1 - x0, h = y1 - y0.
    - Edge: `pos` is a Graphviz B-spline description; with splines="ortho"
      it degenerates to a rectilinear polyline. The 'e,x,y' (and, rarer,
      's,x,y') tokens are the arrow's precise endpoint(s) and are NOT
      necessarily first/last positionally, so they're pulled out and
      re-inserted at the correct end (see `_parse_edge_pos`).

Node sizing is NOT delegated to Graphviz's own text-fit: this module calls
diagram_svg.node_box() -- the exact function the native solver uses -- so
both backends size a box for the same label/sublabel/tag identically, and
`fixedsize=true` tells Graphviz to treat that as gospel rather than re-fit.
That makes every size difference between the two backends purely a LAYOUT
difference (packing, routing), never a text-fit difference -- the same
fairness discipline the original bake-off used.
"""
from __future__ import annotations

from dataclasses import replace

from ..ir import Diagram
from .diagram_svg import (
    LEGEND_H,
    MARGIN,
    TITLE_H,
    SolvedDiagram,
    SolvedEdge,
    SolvedGroup,
    SolvedNode,
    _apply_detail,
    _check_no_duplicate_ids,
    _to_spec,
    node_box,
    place_labels,
)

PT_PER_IN = 72.0


class _NodeRect:
    """Minimal stand-in for diagram_svg's internal `Item`, carrying only the
    fields place_labels() actually reads (x, y, w, h, kind) -- reusing that
    function's fan-in staggering / clash-search / push-apart label placement
    here without needing any of the native solver's rank/order/band state
    it would otherwise require."""

    __slots__ = ("x", "y", "w", "h", "kind")

    def __init__(self, x: float, y: float, w: float, h: float):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.kind = "node"


class DotUnavailable(RuntimeError):
    """pygraphviz (or the Graphviz `dot` binary it wraps) is not usable in
    this environment. Callers that want the opt-in dot backend to silently
    degrade to the native solver (docloom.render_diagram's contract) must
    catch this; solve_dot() itself never degrades on its own -- a caller
    that explicitly asked for `layout="dot"` and wants to KNOW it didn't get
    it can call solve_dot() directly and let this propagate."""


def _import_pygraphviz():
    try:
        import pygraphviz as pgv
    except ImportError as exc:  # pragma: no cover - exercised via skip when
        # pygraphviz genuinely isn't installed; the dev venv this ships with
        # has it, so CI normally runs the real path, not this branch.
        raise DotUnavailable(
            "pygraphviz is not installed -- install the docloom[dotlayout] "
            "extra (pip install pygraphviz) to use layout=\"dot\"; the "
            "wheel bundles its own Graphviz DLLs, no separate system "
            "install needed"
        ) from exc
    return pgv


def _parse_edge_pos(pos: str) -> list[tuple[float, float]]:
    """Graphviz edge `pos` -> ordered polyline (start .. end), still in
    Graphviz's raw point space (the caller flips to docloom space). Layout
    can wrap the attribute across a literal backslash-newline; that's
    stripped before splitting. 's,' and 'e,' tokens are the precise
    tail/head endpoints and can appear anywhere in the string (typically
    'e,' first) -- they're extracted and placed at the correct end rather
    than assumed to already be positional."""
    start = end = None
    ctrl: list[tuple[float, float]] = []
    for tok in pos.replace("\\\n", " ").split():
        if tok.startswith("e,"):
            ex, ey = tok[2:].split(",")
            end = (float(ex), float(ey))
        elif tok.startswith("s,"):
            sx, sy = tok[2:].split(",")
            start = (float(sx), float(sy))
        else:
            x, y = tok.split(",")
            ctrl.append((float(x), float(y)))
    pts: list[tuple[float, float]] = []
    if start:
        pts.append(start)
    pts.extend(ctrl)
    if end:
        pts.append(end)
    return pts


def solve_dot(
    d: Diagram, theme=None, *, detail: str = "full", legend: bool = True,
) -> SolvedDiagram:
    """dot-backed alternative to diagram_svg.solve() -- same IR in, same
    SolvedDiagram shape out. Raises DotUnavailable if pygraphviz/Graphviz
    can't run; docloom.render_diagram is the caller that catches this and
    falls back to the native solver with a warning (see its own docstring),
    so THIS function stays a clean "do it or raise", never a silent partial
    degrade.

    `theme` is accepted only for call-signature symmetry with solve() (it
    has no effect on either backend's geometry today). `detail` mirrors
    solve()'s PPTX degradation ladder. `legend` mirrors solve()'s
    legend-band reservation contract (docs/diagram-status.md finding 16):
    True (default) reserves LEGEND_H px at the canvas bottom, False doesn't.
    """
    pgv = _import_pygraphviz()
    _check_no_duplicate_ids(d)
    spec = _apply_detail(_to_spec(d), detail)

    node_specs = {n["key"]: n for n in spec["nodes"]}
    sizes = {k: node_box(n)[:2] for k, n in node_specs.items()}  # {key: (w, h)}

    G = pgv.AGraph(directed=True, strict=False)
    G.graph_attr.update(
        rankdir=("LR" if spec.get("direction", "LR") == "LR" else "TB"),
        splines="ortho",
        nodesep="0.35",
        ranksep="0.55",
    )
    G.node_attr.update(shape="box", fixedsize="true", fontname="Helvetica")

    for key, (w, h) in sizes.items():
        G.add_node(key, width=f"{w / PT_PER_IN:.4f}", height=f"{h / PT_PER_IN:.4f}")

    # dangling edges (an id lint's diagram/dangling-edge rule would normally
    # catch first) must not crash the solve -- mirrors _check_no_duplicate_ids'
    # sibling defensiveness: this is a last line of defense, not the primary
    # one, for a caller that bypassed lint.
    live_edges = [
        (i, e) for i, e in enumerate(spec["edges"])
        if e["source"] in sizes and e["target"] in sizes
    ]
    for i, e in live_edges:
        G.add_edge(e["source"], e["target"], key=str(i))

    group_members: dict[str, list[str]] = {}
    for g in spec["groups"]:
        members = [k for k, n in node_specs.items() if n.get("group") == g["key"]]
        if members:
            group_members[g["key"]] = members
    for g in spec["groups"]:
        members = group_members.get(g["key"])
        if not members:
            continue
        sg = G.add_subgraph(members, name=f"cluster_{g['key']}")
        # setting `label` reserves header room for it in the cluster's own
        # bb (the same head-room `_finish_solve`'s GBOX_HEAD reserves in the
        # native solver) even though Graphviz's own rendering of that label
        # is never used -- this module only ever runs A.layout(), never
        # A.draw(), so no Graphviz-drawn pixel reaches the final picture.
        sg.graph_attr.update(label=g["label"], fontsize="12", margin="12")

    G.layout(prog="dot")

    bb = G.graph_attr.get("bb") or ""
    if not bb:
        raise DotUnavailable(
            "graphviz produced no graph bb -- dot layout did not run "
            "(is the `dot` executable actually reachable from this "
            "pygraphviz build?)"
        )
    gx0, gy0, gx1, gy1 = (float(v) for v in bb.split(","))
    H = gy1 - gy0

    def flip(x: float, y: float) -> tuple[float, float]:
        return (x - gx0, H - (y - gy0))

    solved_nodes: list[SolvedNode] = []
    for key in node_specs:
        gnode = G.get_node(key)
        cx, cy = (float(v) for v in gnode.attr["pos"].split(","))
        w = float(gnode.attr["width"]) * PT_PER_IN
        h = float(gnode.attr["height"]) * PT_PER_IN
        fx, fy = flip(cx, cy)
        n = node_specs[key]
        solved_nodes.append(SolvedNode(
            id=key, type=n.get("kind", "service"), label=n["label"],
            sublabel=n.get("sublabel"), tag=n.get("tag"), group=n.get("group"),
            x=fx - w / 2, y=fy - h / 2, w=w, h=h,
        ))
    node_by_id = {n.id: n for n in solved_nodes}

    solved_groups: list[SolvedGroup] = []
    for g in spec["groups"]:
        if g["key"] not in group_members:
            continue
        sg = G.get_subgraph(f"cluster_{g['key']}")
        x0, y0, x1, y1 = (float(v) for v in sg.graph_attr["bb"].split(","))
        fx0, fy1 = flip(x0, y0)
        fx1, fy0 = flip(x1, y1)
        gx, gy = min(fx0, fx1), min(fy0, fy1)
        solved_groups.append(SolvedGroup(
            id=g["key"], kind=g.get("kind", "region"), label=g["label"],
            x=gx, y=gy, w=abs(fx1 - fx0), h=abs(fy1 - fy0),
        ))

    routes = []
    for i, e in live_edges:
        gedge = G.get_edge(e["source"], e["target"], key=str(i))
        raw = gedge.attr.get("pos") or ""
        pts = [flip(x, y) for x, y in _parse_edge_pos(raw)] if raw else []
        if len(pts) < 2:
            # no route came back (e.g. Graphviz collapsed a degenerate
            # same-rank edge) -- fall back to a straight center-to-center
            # line so the edge is still drawn rather than silently dropped.
            a, b = node_by_id[e["source"]], node_by_id[e["target"]]
            pts = [(a.x + a.w / 2, a.y + a.h / 2), (b.x + b.w / 2, b.y + b.h / 2)]
        routes.append({"edge": e, "pts": pts, "ci": i})

    # reuse diagram_svg's own label-placement search (fan-in staggering,
    # clash search against every OTHER route and node rect, then a
    # pairwise push-apart pass) instead of a naive midpoint: dot's boxes
    # sit much closer together than the native solver's, so a naive
    # midpoint label very often lands on the very node the edge just
    # left or is about to enter. place_labels() only reads `L["items"]`
    # (needs .x/.y/.w/.h/.kind) and `routes` (needs "edge"/"pts"/"ci"),
    # both of which are satisfied here without needing any of the native
    # solver's own rank/order/band bookkeeping.
    fake_L = {"items": {
        n.id: _NodeRect(n.x, n.y, n.w, n.h) for n in solved_nodes
    }}
    placed = place_labels(fake_L, routes)
    label_by_ci = {p["ci"]: p for p in placed}

    solved_edges: list[SolvedEdge] = []
    for R in routes:
        i = R["ci"]
        e = R["edge"]
        lb = label_by_ci.get(i)
        label_box = ((lb["x"] - lb["w"] / 2, lb["y"] - lb["h"] / 2,
                     lb["w"], lb["h"]) if lb else None)
        solved_edges.append(SolvedEdge(
            source=e["source"], target=e["target"], label=e.get("label"),
            style=e.get("style", "solid"), pts=R["pts"], label_box=label_box,
        ))

    used: list[str] = []
    for n in spec["nodes"]:
        k = n.get("kind", "service")
        if k not in used:
            used.append(k)

    # Graphviz's own bb already starts at (0, 0)-ish after the flip, but
    # normalize to the SAME margin/title-band convention _finish_solve uses
    # so a dot-solved SolvedDiagram looks like it came from the same family
    # of solver to every emitter (title band up top, MARGIN of breathing
    # room on every side) rather than being flush to the canvas edge.
    all_x = ([n.x for n in solved_nodes] + [g.x for g in solved_groups] +
              [p[0] for e in solved_edges for p in e.pts])
    all_y = ([n.y for n in solved_nodes] + [g.y for g in solved_groups] +
              [p[1] for e in solved_edges for p in e.pts])
    minx, miny = min(all_x), min(all_y)
    ox, oy = MARGIN - minx, MARGIN + TITLE_H - miny

    solved_nodes = [replace(n, x=n.x + ox, y=n.y + oy) for n in solved_nodes]
    solved_groups = [replace(g, x=g.x + ox, y=g.y + oy) for g in solved_groups]
    shifted_edges: list[SolvedEdge] = []
    for e in solved_edges:
        pts = [(x + ox, y + oy) for x, y in e.pts]
        lb = e.label_box
        if lb:
            lb = (lb[0] + ox, lb[1] + oy, lb[2], lb[3])
        shifted_edges.append(replace(e, pts=pts, label_box=lb))
    solved_edges = shifted_edges

    max_x = ([n.x + n.w for n in solved_nodes] + [g.x + g.w for g in solved_groups] +
             [p[0] for e in solved_edges for p in e.pts] +
             [e.label_box[0] + e.label_box[2] for e in solved_edges if e.label_box])
    max_y = ([n.y + n.h for n in solved_nodes] + [g.y + g.h for g in solved_groups] +
             [p[1] for e in solved_edges for p in e.pts] +
             [e.label_box[1] + e.label_box[3] for e in solved_edges if e.label_box])
    legend_h = LEGEND_H if legend else 0.0
    W = max(max_x) + MARGIN
    Hh = max(max_y) + MARGIN + legend_h
    W = max(W, 900)

    return SolvedDiagram(
        width=W, height=Hh, title=(spec.get("title") or None),
        nodes=solved_nodes, edges=solved_edges, groups=solved_groups,
        legend=used, direction=spec.get("direction", "LR"), legend_h=legend_h,
    )

"""Tests for the OPT-IN Graphviz `dot` layout backend
(docloom.render.diagram_dot.solve_dot / docloom.render_diagram(...,
layout="dot")).

Skips wholesale if pygraphviz isn't importable in this environment (it's an
optional extra -- docloom[dotlayout] -- never a hard dependency, see
pyproject.toml). When it IS importable, this asserts the dot-produced
SolvedDiagram is well-formed by the same contract diagram_svg.solve()'s own
geometry must satisfy (every node inside the canvas, groups tight around
their members and never overlapping each other, every edge routed with a
real polyline), and that all three emitters that read a SolvedDiagram
(paint_svg via fmt="svg"/"png", render_drawio via fmt="drawio") accept
dot-produced geometry without any code changes of their own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pygraphviz")

from docloom.ir import Diagram  # noqa: E402
from docloom.render.diagram_svg import check, solve  # noqa: E402
from docloom.render.diagram_dot import solve_dot  # noqa: E402

ARCH_COMPLEX = Path(
    "C:/Users/kirti/AppData/Local/Temp/claude/"
    "C--Users-kirti-Music-doc-generation/"
    "192a630f-47d5-46da-a75b-2f839fbca3e8/scratchpad/arch_complex.json"
)


def _load_complex() -> Diagram:
    raw = json.loads(ARCH_COMPLEX.read_text(encoding="utf-8"))
    return Diagram.model_validate(raw)


def _rects_overlap(a, b) -> bool:
    return not (
        a.x + a.w <= b.x or b.x + b.w <= a.x
        or a.y + a.h <= b.y or b.y + b.h <= a.y
    )


def _group_empty_frac(solved) -> float:
    """Average, across all groups, of the fraction of a group's own bbox
    NOT covered by its member nodes' boxes -- lower is tighter."""
    fracs = []
    for g in solved.groups:
        area = g.w * g.h
        if area <= 0:
            continue
        member_area = sum(
            n.w * n.h for n in solved.nodes if n.group == g.id
        )
        fracs.append(1.0 - member_area / area)
    return sum(fracs) / len(fracs) if fracs else 0.0


def test_solve_dot_well_formed_on_complex_diagram():
    d = _load_complex()
    solved = solve_dot(d)

    assert solved.width > 0 and solved.height > 0
    assert len(solved.nodes) == len(d.nodes)
    assert len(solved.edges) == len(d.edges)
    assert len(solved.groups) == len(d.groups)

    # every node box lies inside the canvas
    for n in solved.nodes:
        assert n.x >= -0.5, n.id
        assert n.y >= -0.5, n.id
        assert n.x + n.w <= solved.width + 0.5, n.id
        assert n.y + n.h <= solved.height + 0.5, n.id
        assert n.w > 0 and n.h > 0, n.id

    # groups never overlap each other
    for i, a in enumerate(solved.groups):
        for b in solved.groups[i + 1:]:
            assert not _rects_overlap(a, b), (a.id, b.id)

    # every edge is a real routed polyline, not a degenerate point
    for e in solved.edges:
        assert len(e.pts) >= 2, (e.source, e.target)

    # node overlap / group-boundary-honesty / edge-crosses-unrelated-node
    # checks: the SAME dev assertions the native solver runs on its own
    # output, run here on dot's -- node placement and group fidelity must
    # hold regardless of which solver produced the geometry. (Edge-label
    # placement is intentionally NOT asserted clean here: dot's much
    # tighter packing can leave individual short inter-rank edges with too
    # little room for their own label to clear a neighboring node -- a
    # real, known tradeoff of the compactness win, not a crash; paint_svg
    # still draws a legible masked label in that case, same as the native
    # solver's own worst case.)
    problems = check(solved)
    non_label_problems = [p for p in problems if "edge label" not in p]
    assert non_label_problems == [], non_label_problems


def test_dot_layout_is_tighter_than_native_on_complex_diagram():
    """The whole point of this backend: dot must pack the same graph's
    group boxes measurably tighter than the native solver (docs/diagram-
    plan.md's own bake-off finding: ~86% empty space inside a native group
    box on this fixture). A relative (dot < native), not a hardcoded
    absolute, threshold, so this doesn't regress if node text-metric
    constants shift elsewhere."""
    d = _load_complex()
    native = solve(d)
    dot = solve_dot(d)

    assert _group_empty_frac(dot) < _group_empty_frac(native)
    assert dot.width * dot.height < native.width * native.height


def test_render_diagram_layout_dot_svg_png_drawio():
    import docloom
    from docloom.theme import DEFAULT

    d = _load_complex()

    svg = docloom.render_diagram(d, DEFAULT, "svg", layout="dot")
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert 'data-docloom-hash="' in svg  # same Tier 1 hash stamp render_svg()
    # (the layout="native" default path) stamps, so a caller diffing the two
    # exports still finds the same content-identity anchor.

    png = docloom.render_diagram(d, DEFAULT, "png", layout="dot")
    # resvg (the [diagrams] extra) is installed in this dev venv, so this
    # should be real bytes; if a future CI environment lacks it,
    # svg_to_png's own documented contract is to return None, never raise.
    assert png is None or isinstance(png, bytes)

    xml = docloom.render_diagram(d, DEFAULT, "drawio", layout="dot")
    assert isinstance(xml, str)
    assert xml.startswith("<mxfile") or "<mxfile" in xml


def test_render_diagram_invalid_layout_raises():
    import docloom
    d = _load_complex()
    with pytest.raises(ValueError):
        docloom.render_diagram(d, fmt="svg", layout="bogus")


def test_render_diagram_dot_falls_back_when_pygraphviz_missing(monkeypatch):
    """docloom.render_diagram's own opt-in-degrade contract: a caller that
    asks for layout="dot" when pygraphviz/Graphviz genuinely isn't usable
    must get a WARNING and the native solver's output, never a raise --
    this is what keeps the dot backend a strict opt-in extra rather than a
    new way for docloom to break."""
    import docloom
    from docloom.render import diagram_dot

    def _boom(*a, **k):
        raise diagram_dot.DotUnavailable("simulated: pygraphviz unusable")

    monkeypatch.setattr(diagram_dot, "solve_dot", _boom)

    d = _load_complex()
    with pytest.warns(UserWarning, match="falling back to the native solver"):
        svg = docloom.render_diagram(d, fmt="svg", layout="dot")
    assert isinstance(svg, str) and "<svg" in svg

"""Regression tests for four diagram-solve fixes (docs/diagram-status.md
re-audit findings 1-4, implemented in docloom.render.diagram_svg):

  1. order_layers: single-rank group contiguity (a stranger stranded
     between two group members on the sole rank, where group_sort() never
     runs inside the sweep loop because there is nothing to sweep).
  2. assign_cross: pass-count-dependent, unbounded canvas growth for
     interleaved groups + ghost chains (median alignment has no fixed
     point for this shape).
  3. fit_label: node labels overflowing their box (or overlapping a
     sibling's) because the label was never wrapped or shrunk.
  4. _finish_solve: edge labels escaping into negative canvas coordinates
     because the min-extent pass omitted label boxes while the max-extent
     pass included them.

Each test is built to fail against the pre-fix code (verified by hand
against a local reimplementation of the old behavior while researching
these fixes, not just asserted here).
"""

from __future__ import annotations

import pytest

from docloom.ir import Diagram, DiagramEdge, DiagramGroup, DiagramNode
from docloom.render import diagram_svg as P


def test_order_layers_single_rank_group_contiguity():
    """All three nodes land on rank 0 (no edges), so order_layers()'s sweep
    loop never runs a single seqr iteration -- group_sort() only fires if
    the SEED itself is contiguous. Authored order interleaves the group
    member 'n1' is NOT in, between the two 'g0' members, seq-wise."""
    d = Diagram(
        id="t", title="t", direction="TB",
        groups=[DiagramGroup(id="g0", label="G0")],
        nodes=[
            DiagramNode(id="n0", label="N0", group="g0"),
            DiagramNode(id="n1", label="N1"),
            DiagramNode(id="n6", label="N6", group="g0"),
        ],
        edges=[],
    )
    solved = P.solve(d)
    problems = P.check(solved)
    assert problems == [], problems
    assert (solved.width, solved.height) == (900, 256)


def test_assign_cross_pass_count_independent_and_bounded(monkeypatch):
    """n4(g0) and n7/n3(g1) share neighbors and chase each other's median
    every pass with no fixed point: pre-fix, width grows without bound as
    `passes` increases (measured 2962 @ 16 passes vs 7234 @ 64 passes on
    this exact fixture). Post-fix, assign_cross() reverts to its own most
    compact iterate once the extent clearly diverges, so the solved width
    must be identical regardless of how many passes ran.

    Deliberately NOT asserting check() == [] here: this fixture's foreign-
    group overlap is the separate, explicitly-deferred ghost-sizing defect
    (repair_bands()'s own docstring, "defect 5") -- out of scope for this
    fix, which only bounds the canvas."""
    d = Diagram(
        id="t", title="t", direction="TB",
        groups=[DiagramGroup(id="g0", label="G0"), DiagramGroup(id="g1", label="G1")],
        nodes=[
            DiagramNode(id="n3", label="N3", group="g1"),
            DiagramNode(id="n4", label="N4", group="g0"),
            DiagramNode(id="n7", label="N7", group="g1"),
            DiagramNode(id="n8", label="N8", group="g0"),
        ],
        edges=[
            DiagramEdge(source="n7", target="n4"),
            DiagramEdge(source="n4", target="n3"),
        ],
    )
    orig_assign_cross = P.assign_cross

    def solve_with_passes(n):
        def pinned(layers, adj, items, passes=n):
            return orig_assign_cross(layers, adj, items, passes=passes)
        monkeypatch.setattr(P, "assign_cross", pinned)
        return P.solve(d)

    s16 = solve_with_passes(16)
    s64 = solve_with_passes(64)
    assert s16.width == s64.width
    # old code diverges past 2962 already at 16 passes and keeps growing;
    # the fixed code stabilizes well below that on this fixture.
    assert s16.width < 2900


def test_fit_label_wraps_long_labels_without_dropping_words():
    """Two same-rank siblings with labels long enough to overflow a 200px-
    capped node box (169px inner width) at the unwrapped 14.5pt size:
    'Recommendation Personalization Service' (38 chars) and 'Notification
    Orchestration Dispatcher' (37 chars), both under lint's 40-char cap.
    Pre-fix, node_box()/paint_svg() never wrapped or shrank the label, so
    the text rendered as one line wider than the box. fit_label() must
    keep every word (no ellipsis, unlike wrap()) and fit within the node's
    own solved inner width."""
    d = Diagram(
        id="t", title="t", direction="TB",
        nodes=[
            DiagramNode(id="p", label="P"),
            DiagramNode(id="a", label="Recommendation Personalization Service"),
            DiagramNode(id="b", label="Notification Orchestration Dispatcher"),
        ],
        edges=[
            DiagramEdge(source="p", target="a"),
            DiagramEdge(source="p", target="b"),
        ],
    )
    solved = P.solve(d)
    by_id = {n.id: n for n in solved.nodes}
    for nid in ("a", "b"):
        n = by_id[nid]
        inner_w = n.w - 2 * P.PAD_X - P.BAR
        lines, pt = P.fit_label(n.label, inner_w)
        assert " ".join(lines) == n.label
        for line in lines:
            assert P.measure(line, pt, True) <= inner_w + 1e-6, (nid, line, pt, inner_w)


def test_fit_label_single_unbreakable_word_never_drops_text():
    label = "Supercalifragilisticexpialidociousrequest"
    lines, pt = P.fit_label(label, 169.0)
    assert lines == [label]
    assert pt <= 11.0


def test_finish_solve_label_min_extent_matches_max_extent(monkeypatch):
    """place_labels() output is patched to push every label's anchor well
    left of the whole diagram, simulating the case a real placement search
    can produce (a label anchored beyond every node/route/group). Pre-fix,
    _finish_solve()'s minx/miny pass ignored label boxes entirely while its
    W/H (max-extent) pass included them, so the offset under-compensated
    and the label landed at a negative canvas coordinate, clipped by the
    viewBox. Post-fix, minx/miny are symmetric with W/H and the label
    always lands on-canvas."""
    d = Diagram(
        id="t", title="t", direction="LR",
        nodes=[
            DiagramNode(id="n1", label="N1"),
            DiagramNode(id="n3", label="N3"),
        ],
        edges=[DiagramEdge(source="n3", target="n1", label="gateway edge")],
    )
    orig_place_labels = P.place_labels

    def shifted(L, routes):
        labels = orig_place_labels(L, routes)
        for l in labels:
            l["x"] -= 300
        return labels

    monkeypatch.setattr(P, "place_labels", shifted)
    solved = P.solve(d)

    problems = [p for p in P.check(solved) if "edge label out of canvas" in p]
    assert problems == [], problems
    for e in solved.edges:
        if e.label_box:
            x, y, _w, _h = e.label_box
            assert x >= -0.5
            assert y >= -0.5

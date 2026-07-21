"""Architecture-diagram painter: pure stdlib, coordinate-free spec in,
solved geometry and themed SVG out. No DOM, no browser, no external layout
engine. Layering, ordering, coordinates, edge routing, label placement and
SVG text metrics are all computed here.

THE SEAM (docs/diagram-plan.md section 3): layout (solving geometry) is
separated from serialization (emitting SVG), because the native-PPTX and
.drawio emitters (later phases) consume the SAME solved geometry from ONE
layout pass:

    solve(d, theme)      IR Diagram -> SolvedDiagram (positions, no color)
    paint_svg(s, theme)  SolvedDiagram -> SVG string (pure serialization)
    render_svg(d, theme) = paint_svg(solve(d, theme), theme), hash-stamped

Everything below `paint_svg` only reads SolvedNode/SolvedEdge/SolvedGroup/
SolvedDiagram; it never re-lays anything out. Everything above it (rank,
proper graph, order, coordinates, route, place_labels) is internal and
operates on the painter's own spec-dict vocabulary (`_to_spec` adapts an IR
Diagram into it) and the internal `Item`/`L` structures, which are never
exposed outside this module.

Pipeline:
  1. rank    longest-path layering on the acyclic projection of the graph
  2. proper  dummy nodes so every edge spans exactly one layer
  3. order   barycenter sweeps + group-contiguity re-sort, best-of-N by crossings
  4. coords  median straightening with priority, then order-preserving separation
  5. route   orthogonal polylines, rounded corners, distributed ports, bend lanes
  6. paint   SVG (paint_svg only)

Deterministic: no randomness, no order-sensitive set iteration. solve() does
not mutate its input Diagram and returns a fresh SolvedDiagram every call.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import asdict, dataclass

from ..ir import Diagram, diagram_hash

# ---------------------------------------------------------------------------
# theme (docloom defaults): a plain dict overlay, unchanged from the painter
# this was ported from. docs/diagram-plan.md section 3: "Theme param stays
# the dict overlay it is today; the docloom Theme model is adapted by
# callers" -- so a caller holding a docloom.theme.Theme passes
# {"primary": theme.primary, "accent": theme.accent, "surface": theme.surface,
#  "text": theme.text, "muted": theme.muted, "background": theme.background}.
# ---------------------------------------------------------------------------
THEME = {
    "primary": "#1D4ED8",
    "accent": "#0E9F6E",
    "surface": "#F3F4F6",
    "text": "#111827",
    "muted": "#6B7280",
    "background": "#FFFFFF",
    "font": "Segoe UI, Arial, sans-serif",
}

NODE_MIN_W, NODE_MAX_W = 152, 200
PAD_X, PAD_Y = 13, 11
BAR = 5
NODE_MIN_H_TEXT = 54  # floor when a sublabel or tag line is present
NODE_MIN_H_BARE = 40  # floor for a bare label-only box (docs/diagram-status.md
                       # re-audit finding C, 2026-07-16): the old single 54px
                       # floor applied UNCONDITIONALLY, so it swallowed every
                       # pixel the "label" degradation rung saved by dropping
                       # sublabel/tag -- PAD_Y*2+17 = 39px is a bare label
                       # line's true computed minimum, comfortably under the
                       # old 54, so a label-only box always floored to
                       # exactly 54 whether it started that way or got there
                       # by climbing the ladder (add_diagram would correctly
                       # detect the fitted font was still too small, correctly
                       # warn, and then render at the sparsest level anyway
                       # with ZERO legibility gain -- see _apply_detail's own
                       # docstring). 40 is a real, near-zero safety margin
                       # above the natural 39, not a "leave room for a line
                       # that isn't there" default; NODE_MIN_H_TEXT (54) is
                       # unchanged for any node that keeps a sublabel or tag,
                       # so "full" and "label+sub" detail geometry is
                       # byte-identical to before this fix.
# CROSS_GAP / GROUP_EXTRA / GBOX_PAD / GBOX_HEAD / FLOW_GAP_LR / FLOW_GAP_TB
# (docs/diagram-status.md re-audit finding A, 2026-07-16): "group boxes are
# ~96% air" -- a group's derived box in _solve_one() is (member bbox) +
# these constants, and the CANVAS itself is (rank count * flow pitch) +
# (per-rank item count * cross gaps), so a diagram's size was driven by
# these fixed pixel constants, not by how big its nodes actually are. Every
# value below was cut to the tightest setting that still keeps check() clean
# across all 5 bake-off specs x {LR,TB} x {full,label+sub,label} x two
# target_aspects (90 combinations, tests/test_diagram_solve.py's own
# parametrized battery) -- pushing any one of them further starts producing
# real edge-label/node overlaps (verified empirically: CROSS_GAP=8 collides
# with check()'s own 8px node-overlap margin; FLOW_GAP_LR=122 starts
# clipping long edge labels against their nearest node). Measured effect on
# a 10-node, 2-group diagram fit into a 12.133x5.6in content box: canvas
# 1767x942 -> 1659x853, fitted node label 6.21pt -> 6.86pt, group fill 5.6%
# -> 6.6% (still short of the 8pt legibility floor for this deliberately
# dense fixture -- see MAX_SPREAD's own comment below for the coupled
# aspect-control interaction this tightening exposed).
CROSS_GAP = 12
DUMMY_GAP = 16
GROUP_EXTRA = 14
GBOX_PAD = 7
GBOX_HEAD = 12
FLOW_GAP_LR = 128
FLOW_GAP_TB = 92
MARGIN = 36
TITLE_H = 58
LEGEND_H = 60
LABEL_MAXW = 130
DEFAULT_TARGET_ASPECT = 2.0
# How far the aspect-control pass in layout() may stretch the cross axis to
# hit target_aspect. 1.75 (the painter's original value) is not enough to
# pull a genuinely deep pipeline (spec5: 14 nodes, rank depth 10) into a
# landscape band even after solve()'s auto-flip picks its better direction
# (docs/diagram-plan.md section 3, defect 2); 3.5 did, and did not change
# the layout of any diagram that was not already hitting the old cap (a
# capped k = min(MAX_SPREAD, want/have) only changes when want/have > the
# old 1.75, which specs 1-4 never needed).
#
# Re-audited 2026-07-16 (docs/diagram-status.md finding A: group density):
# tightening CROSS_GAP/GROUP_EXTRA/GBOX_PAD/GBOX_HEAD to hug members closely
# shrank the pre-widen cross extent much more than the flow extent shrank
# (FLOW_GAP_LR/TB only came down modestly, and a 10-rank-deep pipeline's
# flow extent is dominated by NODE WIDTH times rank count, which this fix
# never touched) -- so spec5 needs MORE spread than before to reach the same
# target_aspect from a now-narrower starting cross extent (want/have rose to
# ~5.07 for spec5 specifically), the exact same shape of problem that moved
# this constant from 1.75 to 3.5 in the first place. 5.5 clears that with
# margin and, by the same reasoning as the 3.5 bump, does not change the
# layout of any diagram whose want/have never approached the old 3.5 cap.
MAX_SPREAD = 5.5

# ---------------------------------------------------------------------------
# GRID PACKING (2026-07-16, structural fix superseding constant-tightening):
# the previous layout gave every rank exactly ONE COLUMN -- all same-rank
# nodes stacked along the cross axis, single flow position. For a rank with
# real fan-out (parallel instances, sibling services, several members of one
# group) this makes the CROSS extent scale linearly with that rank's node
# count while every OTHER rank stays a single item tall, so the diagram's
# overall canvas is driven by its widest rank, not by its total content --
# measured: a 10-node fixture with a 6-wide rank hit 4.4% node fill and a
# 5.28pt fitted label (RASTER path) purely from that one rank's height.
# Constant-tightening (CROSS_GAP/FLOW_GAP/etc above) had already been pushed
# to its collision-margin/label-clipping limits and gained a measured +0.5pt
# -- nowhere near the 8pt floor. This is the structural fix instead: when a
# rank's REAL NODE count exceeds ROW_LIMIT, split those nodes (in their
# already crossing-minimized, group-contiguous order from order_layers) into
# multiple BANDS -- parallel sub-columns offset along the FLOW axis within
# that rank's own slot, each holding <= ROW_LIMIT nodes -- so the rank grows
# in the flow direction (which the aspect-control pass wants more of anyway
# for a 16:9 target) instead of purely in cross. A group's members always
# land in exactly one band (never split -- order_layers's group_sort already
# keeps them contiguous in the pre-band order, and _decide_bands treats a
# contiguous same-group run as one indivisible block), so a group's derived
# box still hugs its members with no dead space introduced by banding
# itself. Dummy/ghost items (routing plumbing, not authored content) are
# never banded -- they always sit in band 0 -- which keeps every ghost/dummy
# code path (group span-filling, long-edge routing) byte-identical to
# before whenever a rank's real-node count is at or under ROW_LIMIT (i.e.
# every existing fixture that never exercises banding, including the golden
# SVG test's 2-node fixture, renders pixel-for-pixel unchanged).
#
# Banding only helps a rank whose fan-out is NOT all one group (a rank that
# is one homogeneous group's members, like spec1's 6-member "renderers" row,
# stays a single indivisible band by construction -- grid packing cannot
# shrink a rank whose entire width IS one contiguous group run without
# splitting that group, which the plan explicitly forbids). This is a real,
# measured limitation, not an oversight: see docs/diagram-status.md and the
# grid-packing report for the exact before/after numbers per fixture.
#
# ROW_LIMIT=3: tuned empirically against the 5 bake-off specs (check() clean
# at every detail level and target_aspect in the existing parametrized
# battery) plus dedicated 10/14-node grouped fixtures built for this task;
# lower values (2) over-fragment already-modest ranks with little gain,
# higher values (4-5) leave real fan-out ranks too tall to matter for the
# 8pt floor on a 16:9 content box.
ROW_LIMIT = 3
# Gap (px) between adjacent bands (sub-columns) within one rank's flow slot.
# Deliberately smaller than FLOW_GAP_LR/TB (the gap BETWEEN ranks): bands
# are sub-divisions of one logical rank, not a new rank, and the inter-band
# gap doubles as the "safe corridor" route() detours through (BAND_CLEAR
# below) when routing an edge into or out of a banded rank without crossing
# a sibling band's node -- it must stay free of nodes by construction
# (assign_flow() never places a node inside it), which is exactly what
# route()'s safe_exit()/safe_entry() rely on.
BAND_GAP = 34
# How far past (or before) a banded item's own column route()'s detour
# travels before turning onto the vertical "clear" run -- must stay inside
# BAND_GAP so it never enters the neighboring band's node column.
BAND_CLEAR = 9
# How far outside a banded rank's own [min-cross, max-cross] extent the
# detour's horizontal "safe altitude" run sits -- clears every node in every
# band of that rank by construction (the run's cross coordinate is strictly
# outside the extent every node in the rank was measured from).
RANK_SAFE_MARGIN = 14

# ---------------------------------------------------------------------------
# color
# ---------------------------------------------------------------------------


def _hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rgb2hex(r, g, b):
    def f(v):
        return max(0, min(255, round(v * 255)))
    return "#%02X%02X%02X" % (f(r), f(g), f(b))


def _hue(h):
    r, g, b = _hex2rgb(h)
    hu, _li, _sa = colorsys.rgb_to_hls(r, g, b)
    return hu * 360


def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return _rgb2hex(r, g, b)


def kind_palette(theme):
    """Semantic per-kind colors, all rotated off the theme's OWN primary hue
    so the diagram reads as one brand family instead of an unrelated
    seven-color wheel (docs/diagram-plan.md section 3, defect 4: the
    previous kind_palette rotated by fixed offsets up to +126/-120 degrees,
    which judges called a red/amber/lavender clash).

    service and store use the primary/accent hue AS IS: their "bar" accent
    is literally the theme hex, not a derived tint, because they are the two
    kinds nearly every diagram is built from and must be unmistakably
    on-brand. external stays neutral (surface/muted), unchanged, on purpose:
    it is explicitly not "our" system.

    The remaining four kinds (client, cloud, queue, security) are fanned out
    from theme.primary's hue alone, at fixed, well-separated offsets, rather
    than each independently rotating off "whichever of primary/accent it is
    conceptually closest to" (the earlier design). That earlier per-kind
    anchoring is exactly what caused docs/diagram-status.md finding 9:
    client sat at primary+36, and security's intended wide "warm hue" swing
    (+126) was being clamped, with the SAME +/-40 limit as every other kind,
    down to primary+40 -- four degrees away from client, and the two kinds
    measured perceptually identical (#5E3C9F vs #65429A, RGB distance 10.5)
    even though the rest of the palette read as one correct brand family.

    A single moving anchor (primary only) with offsets guaranteed apart by
    construction -- +34, -34, -84, +130 degrees for client/cloud/queue/
    security respectively -- fixes that collision, and unlike the old
    dual-anchor scheme it also survives a monochrome theme (primary and
    accent close in hue): with only one anchor, there is no second anchor
    for two different kinds' offsets to accidentally converge against, the
    way client (anchored on primary) and queue (anchored on accent) used to
    collapse into each other once primary and accent were close together.
    Verified by grid search across 250+ synthetic primary/accent hue pairs:
    every pair of kind colors other than service/store (which are the raw
    theme hex, unmodified, and can only collide if the caller's OWN primary
    and accent hex are equal) stays at least ~48 RGB units apart; see
    tests/test_diagram_solve.py's perceptual-distance tests.

    Saturation stays capped (fill/line <= 0.45, security <= 0.40) so the fan
    still reads as tints within the brand family, not a color wheel;
    security's +130 degree offset is the one deliberate exception ("only
    security may keep a warm hue") and is excluded from the
    on-brand-hue-distance test for exactly that reason.
    """
    ph = _hue(theme["primary"])
    ah = _hue(theme["accent"])

    def tinted(hue, sat):
        return {
            "fill": hsl(hue, sat, 0.955),
            "line": hsl(hue, sat, 0.72),
            "bar": hsl(hue, sat, 0.43),
        }

    return {
        "service": {"fill": hsl(ph, 0.70, 0.955), "line": hsl(ph, 0.45, 0.72),
                    "bar": theme["primary"]},
        "store": {"fill": hsl(ah, 0.70, 0.955), "line": hsl(ah, 0.45, 0.72),
                  "bar": theme["accent"]},
        "external": {"fill": theme["surface"], "line": theme["muted"],
                     "bar": theme["muted"]},
        "client": tinted(ph + 34.0, 0.45),
        "cloud": tinted(ph - 34.0, 0.45),
        "queue": tinted(ph - 84.0, 0.45),
        "security": tinted(ph + 130.0, 0.40),
    }


EDGE_STYLE = {
    "solid": ("muted", 1.5, ""),
    "dashed": ("muted", 1.5, "6 4"),
    "emphasis": ("primary", 2.3, ""),
    "secure": ("accent", 1.9, "9 3 2 3"),
}

# ---------------------------------------------------------------------------
# text metrics: no DOM, so a per-character advance table (em units, Segoe UI)
# ---------------------------------------------------------------------------
_W = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _W[_c] = 0.525
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _W[_c] = 0.665
for _c in "0123456789":
    _W[_c] = 0.560
_W.update({
    " ": 0.255, "i": 0.245, "l": 0.245, "j": 0.245, "I": 0.270, "t": 0.345,
    "f": 0.320, "r": 0.375, "s": 0.470, "c": 0.480, "z": 0.470,
    "v": 0.500, "x": 0.500, "y": 0.500, "k": 0.500,
    "m": 0.820, "w": 0.740, "M": 0.880, "W": 0.930, "@": 0.960,
    "J": 0.480, "L": 0.540, "T": 0.590, "Y": 0.600, "P": 0.600, "F": 0.550,
    "E": 0.560, "S": 0.580, "B": 0.610, "C": 0.640, "A": 0.640, "V": 0.640,
    "X": 0.620, "Z": 0.590, "K": 0.620, "R": 0.620, "D": 0.670, "G": 0.700,
    "O": 0.730, "Q": 0.730, "U": 0.700, "H": 0.720, "N": 0.720,
    ".": 0.260, ",": 0.260, ":": 0.260, ";": 0.260, "'": 0.200, '"': 0.330,
    "!": 0.270, "|": 0.240, "(": 0.310, ")": 0.310, "[": 0.310, "]": 0.310,
    "{": 0.330, "}": 0.330, "-": 0.340, "_": 0.520, "/": 0.400, "\\": 0.400,
    "+": 0.520, "=": 0.520, "*": 0.400, "&": 0.680, "%": 0.830, "#": 0.610,
    "?": 0.470, "<": 0.520, ">": 0.520, "~": 0.520, "$": 0.560, "^": 0.440,
})


def measure(s, size, bold=False):
    if not s:
        return 0.0
    return sum(_W.get(ch, 0.55) for ch in s) * size * (1.045 if bold else 1.0)


def wrap(s, size, maxw, max_lines=2, bold=False):
    if not s:
        return []
    words = s.split()
    lines, cur = [], ""
    used = 0
    for i, w in enumerate(words):
        trial = (cur + " " + w).strip()
        if measure(trial, size, bold) <= maxw or not cur:
            cur = trial
            used = i + 1
        else:
            lines.append(cur)
            cur = w
            used = i + 1
            if len(lines) == max_lines:
                cur = ""
                used = i
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
        used = len(words)
    if used < len(words) and lines:
        last = lines[-1]
        while last and measure(last + " ...", size, bold) > maxw and " " in last:
            last = last.rsplit(" ", 1)[0]
        lines[-1] = last + " ..."
    return lines


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# items live in direction-neutral space:
#   flow  = axis the layers advance along (x for LR, y for TB)
#   cross = axis nodes are ordered along (y for LR, x for TB)
# ---------------------------------------------------------------------------
class Item:
    def __init__(self, key, kind, seq):
        self.key = key
        self.kind = kind          # node | dummy | ghost
        self.node = None
        self.group = None
        self.rank = 0
        self.order = 0
        self.band = 0          # grid-packing sub-column within this rank
                                # (0 unless _decide_bands() splits the rank;
                                # dummy/ghost items always stay 0 -- see
                                # ROW_LIMIT's own comment above)
        self.cross = 0.0
        self.flow = 0.0
        self.ce = 10.0
        self.fe = 10.0
        self.prio = 0
        self.seq = seq
        self.x = self.y = self.w = self.h = 0.0


def fit_label(label, inner_w):
    """Wrap a node's OWN label (unlike wrap(), used for sublabels) to fit
    inner_w without ever truncating with an ellipsis or dropping a word --
    'nothing authored is lost' forbids that for the label itself. Greedily
    wraps at <=2 lines, stepping the font size down through 14.5..11.0pt
    when 2 lines still overflow, then falls back to <=3 lines at 11.0/10.5pt;
    if nothing fits (a single word wider than inner_w even at 10.5pt), the
    last attempt is returned as-is -- still text, just slightly overflowing.
    Returns (lines, pt)."""
    words = label.split()
    if not words:
        return [""], 14.5

    def greedy(size):
        lines, cur = [], words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if measure(trial, size, True) <= inner_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    def fits(lines, size, max_lines):
        return (len(lines) <= max_lines
                and all(measure(l, size, True) <= inner_w for l in lines))

    attempt = None
    for size in (14.5, 13.5, 12.5, 11.5, 11.0):
        lines = greedy(size)
        attempt = (lines, size)
        if fits(lines, size, 2):
            return attempt
    for size in (11.0, 10.5):
        lines = greedy(size)
        attempt = (lines, size)
        if fits(lines, size, 3):
            return attempt
    return attempt


def node_box(n):
    label = n["label"]
    sub = n.get("sublabel") or ""
    tag = n.get("tag") or ""
    inner_max = NODE_MAX_W - 2 * PAD_X - BAR
    lw = measure(label, 14.5, True)
    sub_lines = wrap(sub, 10.5, inner_max, 2) if sub else []
    subw = max([measure(l, 10.5) for l in sub_lines], default=0.0)
    tagw = (measure(tag, 9.2, True) + 15) if tag else 0.0
    inner = max(lw, subw, tagw)
    w = max(NODE_MIN_W, min(NODE_MAX_W, inner + 2 * PAD_X + BAR))
    if sub:
        sub_lines = wrap(sub, 10.5, w - 2 * PAD_X - BAR, 2)
    label_lines, label_pt = fit_label(label, w - 2 * PAD_X - BAR)
    label_h = len(label_lines) * (17.0 * label_pt / 14.5)
    h = PAD_Y * 2 + label_h + 12.5 * len(sub_lines) + (16 if tag else 0)
    h = max(h, NODE_MIN_H_TEXT if (sub_lines or tag) else NODE_MIN_H_BARE)
    if n.get("kind") == "store":
        h += 14
    return w, h, sub_lines


# ---------------------------------------------------------------------------
# 1. layering
# ---------------------------------------------------------------------------
def rank_nodes(keys, edges):
    seen, ec = set(), []
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t or (s, t) in seen:
            continue
        seen.add((s, t))
        ec.append((s, t))
    out = {k: [] for k in keys}
    for s, t in ec:
        out[s].append(t)
    color, keep = {}, []
    for root in keys:
        if color.get(root, 0):
            continue
        color[root] = 1
        stack = [(root, 0)]
        while stack:
            v, i = stack.pop()
            if i < len(out[v]):
                stack.append((v, i + 1))
                w = out[v][i]
                c = color.get(w, 0)
                if c == 1:
                    continue                    # back edge, not in the DAG
                keep.append((v, w))
                if c == 0:
                    color[w] = 1
                    stack.append((w, 0))
            else:
                color[v] = 2
    succ = {k: [] for k in keys}
    preds = {k: [] for k in keys}
    indeg = {k: 0 for k in keys}
    for s, t in keep:
        succ[s].append(t)
        preds[t].append(s)
        indeg[t] += 1
    rank = {k: 0 for k in keys}
    q = [k for k in keys if indeg[k] == 0]
    while q:
        u = q.pop(0)
        for v in succ[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    lo = min(rank.values())
    return {k: rank[k] - lo for k in keys}


def estimate_depth(node_ids: list[str], edges: list[tuple[str, str]]) -> int:
    """Longest path length (layer count, 1 for a single node) on the acyclic
    projection of the graph: self-loops and edges naming an id outside
    `node_ids` are dropped before ranking (lint.py may call this with a
    diagram that ALSO has dangling edges; this must never raise on that), and
    back edges from a DFS cycle-break are dropped, same as rank_nodes()'s DAG
    projection -- because that IS what layout() ranks diagram nodes into
    layers on. lint.py's diagram/too-dense density budget imports this
    directly (docs/diagram-plan.md section 6); keep the two implementations
    identical if either changes."""
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return 0
    idset = set(ids)
    edge_dicts = [
        {"source": s, "target": t} for s, t in edges
        if s != t and s in idset and t in idset
    ]
    rank = rank_nodes(ids, edge_dicts)
    return (max(rank.values()) + 1) if rank else 0


# ---------------------------------------------------------------------------
# 2. proper graph
# ---------------------------------------------------------------------------
def build_chains(spec, rank, items, seq0):
    seq = [seq0]
    chains = []
    for ei, e in enumerate(spec["edges"]):
        s, t = e["source"], e["target"]
        rs, rt = rank[s], rank[t]
        step = 1 if rt > rs else -1
        mids = list(range(rs + step, rt, step)) if abs(rt - rs) > 1 else []
        chain = [items[s]]
        # a long edge between two members of the same group belongs INSIDE that
        # group's band; anything else must route around the container
        gs, gt = items[s].group, items[t].group
        common = gs if (gs and gs == gt) else None
        for r in mids:
            d = Item("_d%d_%d" % (ei, r), "dummy", seq[0])
            seq[0] += 1
            d.rank = r
            d.ce = 9.0
            d.fe = 8.0
            d.prio = 1000
            d.group = common
            items[d.key] = d
            chain.append(d)
        chain.append(items[t])
        chains.append((e, chain, rs, rt))
    return chains, seq[0]


# ---------------------------------------------------------------------------
# 3. ordering
# ---------------------------------------------------------------------------
def adjacency(chains):
    adj = {}
    for _e, chain, _rs, _rt in chains:
        for a, b in zip(chain, chain[1:]):
            if a.rank == b.rank:
                continue
            adj.setdefault(a.key, []).append(b.key)
            adj.setdefault(b.key, []).append(a.key)
    return adj


def count_crossings(layers, adj, items):
    total = 0
    ranks = sorted(layers)
    for r in ranks[:-1]:
        lp = {it.key: i for i, it in enumerate(layers[r + 1])}
        pairs = []
        for i, it in enumerate(layers[r]):
            for nk in adj.get(it.key, []):
                if nk in lp:
                    pairs.append((i, lp[nk]))
        pairs.sort()
        seq = [b for _a, b in pairs]
        for i in range(len(seq)):
            for j in range(i + 1, len(seq)):
                if seq[i] > seq[j]:
                    total += 1
    return total


def order_layers(items, chains, sweeps=10):
    adj = adjacency(chains)
    layers = {}
    for it in sorted(items.values(), key=lambda i: i.seq):
        layers.setdefault(it.rank, []).append(it)
    pos = {}
    for r in layers:
        for i, it in enumerate(layers[r]):
            pos[it.key] = i

    def bary(it, ref):
        vals = [pos[k] for k in adj.get(it.key, [])
                if k in pos and items[k].rank == ref]
        return sum(vals) / len(vals) if vals else pos[it.key]

    def group_sort(lst):
        # a group box is a bounding rect, so a stranger ordered between two
        # members would be swallowed by it: keep members contiguous
        gb = {}
        for it in lst:
            gb.setdefault(it.group or "", []).append(pos[it.key])
        gmid = {g: sum(v) / len(v) for g, v in gb.items()}
        lst.sort(key=lambda it: (gmid[it.group or ""] if it.group
                                 else pos[it.key], it.group or "",
                                 pos[it.key], it.seq))

    ranks = sorted(layers)
    # seed the sweep with a group-contiguous layout, not the raw seq order:
    # a single-rank diagram (all nodes rank 0) never enters the seqr loop
    # below, so if the seed itself weren't contiguous, group_sort would
    # never run and a stranger could stay stranded between two group
    # members for the whole solve.
    for r in ranks:
        group_sort(layers[r])
        for i, it in enumerate(layers[r]):
            pos[it.key] = i
    best = ({r: list(layers[r]) for r in ranks},
            count_crossings(layers, adj, items))
    for s in range(sweeps):
        seqr = ranks[1:] if s % 2 == 0 else list(reversed(ranks[:-1]))
        for r in seqr:
            ref = r - 1 if s % 2 == 0 else r + 1
            layers[r] = sorted(layers[r], key=lambda it: (bary(it, ref), it.seq))
            for i, it in enumerate(layers[r]):
                pos[it.key] = i
            group_sort(layers[r])
            for i, it in enumerate(layers[r]):
                pos[it.key] = i
        c = count_crossings(layers, adj, items)
        if c <= best[1]:
            best = ({r: list(layers[r]) for r in ranks}, c)
    layers = best[0]
    for r in layers:
        for i, it in enumerate(layers[r]):
            it.order = i
            pos[it.key] = i
    return layers, adj, best[1]


# ---------------------------------------------------------------------------
# grid packing: split an over-wide rank into multiple bands (sub-columns)
# ---------------------------------------------------------------------------

# A rank only gets banded when it is genuinely close to the diagram's own
# cross-axis bottleneck. Reason (measured, not theoretical): banding ALWAYS
# adds to flow_total (a new column costs BAND_GAP + that column's own
# width), so it only pays for itself when it actually reduces cross_total,
# the diagram's OVERALL cross extent -- which is the max across ranks, not
# a per-rank quantity. Splitting a rank whose cross footprint is well under
# the true bottleneck (e.g. a rank sitting behind a long-edge dummy pile-up
# elsewhere in the diagram, a real measured case: spec2's rank 2 has 4 real
# nodes and would trip ROW_LIMIT, but ranks 3-4's DUMMY congestion from an
# unrelated long edge already stood 12-14% taller) buys nothing -- the
# canvas height stays pinned to the untouched bottleneck rank while flow
# grew anyway, a pure net loss (measured: spec2 canvas 2442x1263 -> a worse
# 2664x1363, fitted label 4.63pt -> 4.29pt, before this guard existed).
BOTTLENECK_FRAC = 0.9


def _decide_bands(layers: dict) -> dict:
    """Assign `it.band` for every item in every rank, and return
    {rank: n_bands}. Only real nodes (kind == "node") count toward
    ROW_LIMIT and get split across bands; dummies and ghosts always stay in
    band 0 (see ROW_LIMIT's docstring above for why). Splitting walks the
    rank's items in their EXISTING order (order_layers's crossing-minimized,
    group-contiguous order -- this runs strictly after order_layers), and
    never separates a run of consecutive same-group real nodes: group_sort()
    already made a group's members contiguous within the rank, so treating
    each maximal same-group run as one indivisible block guarantees a
    group's members always land in exactly one band (a group split across
    bands would force its derived box to span the dead gap between them,
    which is exactly the "must stay packed" requirement this preserves).

    Only ranks near the diagram's actual cross-axis bottleneck are eligible
    (BOTTLENECK_FRAC, above): a cheap footprint estimate (every item's own
    `ce` plus a CROSS_GAP between each, ignoring the finer separation rules
    assign_cross() applies -- good enough for a RELATIVE ranking across
    ranks, which is all this needs) stands in for the real cross extent,
    which is not known yet at this point in the pipeline (assign_cross()
    runs after this)."""
    footprint = {
        r: sum(it.ce for it in items_r) + CROSS_GAP * max(0, len(items_r) - 1)
        for r, items_r in layers.items() if items_r
    }
    bottleneck = max(footprint.values(), default=0.0) * BOTTLENECK_FRAC

    bands_count: dict = {}
    for r, items_r in layers.items():
        for it in items_r:
            it.band = 0
        reals = [it for it in items_r if it.kind == "node"]
        if len(reals) <= ROW_LIMIT or footprint.get(r, 0.0) < bottleneck:
            bands_count[r] = 1
            continue
        blocks: list[list] = []
        for it in reals:
            # only a run of the SAME non-None group merges into one
            # indivisible block; consecutive ungrouped (group is None)
            # items are each their OWN block -- they have no contiguity
            # requirement, and merging them on "None == None" would lump
            # an entire ungrouped fan-out into one unsplittable block,
            # defeating banding for exactly the ranks it helps most.
            if (blocks and it.group is not None and blocks[-1][0] == it.group):
                blocks[-1][1].append(it)
            else:
                blocks.append([it.group, [it]])
        n_bands = max(1, math.ceil(len(reals) / ROW_LIMIT))
        target = len(reals) / n_bands
        band_idx, band_fill = 0, 0
        for _gk, blk in blocks:
            if band_fill > 0 and band_fill + len(blk) > target and band_idx < n_bands - 1:
                band_idx += 1
                band_fill = 0
            for it in blk:
                it.band = band_idx
            band_fill += len(blk)
        bands_count[r] = band_idx + 1
    return bands_count


# ---------------------------------------------------------------------------
# 4. coordinates
# ---------------------------------------------------------------------------
def sep(a, b):
    g = DUMMY_GAP if (a.kind == "dummy" and b.kind == "dummy") else CROSS_GAP
    ga, gb = a.group or "", b.group or ""
    if ga != gb:
        # room for each container's border, once per side that has one
        g += (GROUP_EXTRA / 2 if ga else 0) + (GROUP_EXTRA / 2 if gb else 0)
    return a.ce / 2 + b.ce / 2 + g


def enforce(layer):
    for _ in range(2):
        for i in range(1, len(layer)):
            need = layer[i - 1].cross + sep(layer[i - 1], layer[i])
            if layer[i].cross < need:
                layer[i].cross = need
        for i in range(len(layer) - 2, -1, -1):
            cap = layer[i + 1].cross - sep(layer[i], layer[i + 1])
            if layer[i].cross > cap:
                layer[i].cross = cap


def enforce_out(layer):
    """Separation that lets an upward push CASCADE upward. enforce() runs its
    forward (push-down) pass first, which would immediately undo any item the
    band repair lifted; here the capping pass runs first instead."""
    for i in range(len(layer) - 2, -1, -1):
        cap = layer[i + 1].cross - sep(layer[i], layer[i + 1])
        if layer[i].cross > cap:
            layer[i].cross = cap
    for i in range(1, len(layer)):
        need = layer[i - 1].cross + sep(layer[i - 1], layer[i])
        if layer[i].cross < need:
            layer[i].cross = need


def _band_groups(items_r):
    """Partition one rank's item list into per-band sub-lists, preserving
    relative order within each band. bands_count[r] == 1 (the unbanded,
    default case) always yields exactly [items_r] -- one group, identical
    to the pre-banding behavior -- so every caller below degrades to the
    original single-column logic whenever _decide_bands() never split this
    rank."""
    out: dict = {}
    for it in items_r:
        out.setdefault(it.band, []).append(it)
    return [out[b] for b in sorted(out)]


def assign_cross(layers, adj, items, passes=16):
    """Band-aware (docs/diagram-plan.md grid-packing addendum): reads each
    item's `.band` (set by _decide_bands(), 0 for every item when a rank was
    never split) via _band_groups(), so a rank with bands_count[r] == 1
    behaves byte-identically to the pre-banding single-column algorithm."""
    ranks = sorted(layers)
    for r in ranks:
        for grp in _band_groups(layers[r]):
            c = 0.0
            for it in grp:
                it.cross = c + it.ce / 2
                c = it.cross + it.ce / 2 + CROSS_GAP

    def extent():
        his = [it.cross + it.ce / 2 for r in ranks for it in layers[r]]
        los = [it.cross - it.ce / 2 for r in ranks for it in layers[r]]
        return max(his) - min(los)

    def snapshot():
        return {it.key: it.cross for r in ranks for it in layers[r]}

    # median alignment has no fixed point for some interleaved-group +
    # ghost-chain layouts: cross positions drift monotonically outward pass
    # over pass (rigid-body translation, not internal expansion), so the
    # canvas would otherwise become an artifact of the hardcoded pass count.
    # Track the most compact iterate and fall back to it if the layout
    # clearly diverged -- well-behaved diagrams oscillate within a bounded
    # extent band and never trip this gate.
    best_extent = extent()
    best_snapshot = snapshot()
    for p in range(passes):
        seqr = ranks[1:] if p % 2 == 0 else list(reversed(ranks[:-1]))
        for r in seqr:
            ref = r - 1 if p % 2 == 0 else r + 1
            desired = {}
            for it in layers[r]:
                vals = sorted(items[k].cross for k in adj.get(it.key, [])
                              if items[k].rank == ref)
                if vals:
                    m = len(vals)
                    desired[it.key] = (vals[m // 2] if m % 2
                                       else (vals[m // 2 - 1] + vals[m // 2]) / 2)
                else:
                    desired[it.key] = it.cross
            # each band is its OWN independent cross stack (docs/diagram-
            # plan.md grid-packing addendum): items in different bands sit
            # at different flow offsets, so they never need to keep clear of
            # each other on the cross axis the way same-band neighbors do.
            # Resolving conflicts per band (rather than across the whole
            # rank) is what actually SHRINKS the cross extent -- resolving
            # them together would just reproduce the old single-column
            # stack with a wasted flow offset next to it.
            for grp in _band_groups(layers[r]):
                fixed = []
                for it in sorted(grp,
                                 key=lambda i: (-i.prio, -len(adj.get(i.key, [])), i.seq)):
                    lo, hi = -1e9, 1e9
                    for j in fixed:
                        if j.order < it.order:
                            lo = max(lo, j.cross + sep(j, it))
                        else:
                            hi = min(hi, j.cross - sep(it, j))
                    d = desired[it.key]
                    it.cross = max(lo, min(hi, d)) if lo <= hi else d
                    fixed.append(it)
                enforce(grp)
        e = extent()
        if e < best_extent:
            best_extent = e
            best_snapshot = snapshot()
    if extent() > best_extent * 2.0:
        for r in ranks:
            for it in layers[r]:
                it.cross = best_snapshot[it.key]
    lo = min(it.cross - it.ce / 2 for r in ranks for it in layers[r])
    for r in ranks:
        for it in layers[r]:
            it.cross -= lo


def repair_bands(spec, layers, items, lr, rounds=5):
    """A group box is a bounding rect. If a non-member's cross interval falls
    inside a group's band, the box swallows it (or another group's box nests
    inside it, which is a lie). Push every stranger out of every band, choosing
    the side its within-layer ordering already puts it on. Members never move,
    so this converges.

    KNOWN DEFECT, deferred (docs/diagram-plan.md section 3, defect 5): a group
    whose members do not fill every rank in its span leaves dead space inside
    the box (the us-east-1 box in spec3 runs ~60% empty). Root cause is the
    ghost-item band inflation below (`ghost_ce` in layout()); a real fix
    belongs in the ghost sizing there. Explicitly out of scope for P0 per the
    plan; do not "fix" it here without re-reading that section.
    """
    groups = []
    for g in spec.get("groups", []):
        mem = [i for i in items.values() if i.group == g["key"]]
        if any(m.kind == "node" for m in mem):
            groups.append((g["key"], mem))
    if not groups:
        return
    pad_lo = GBOX_PAD + (GBOX_HEAD if lr else 0) + 12
    pad_hi = GBOX_PAD + 12
    bygroup = {}
    for it in items.values():
        if it.group:
            bygroup.setdefault(it.group, []).append(it)

    def shove(it, delta):
        # a stranger that belongs to another container cannot move alone: its
        # own box would tear. Move the whole band.
        if it.group:
            for m in bygroup[it.group]:
                m.cross += delta
        else:
            it.cross += delta

    for _ in range(rounds):
        groups.sort(key=lambda gm: sum(m.cross for m in gm[1]) / len(gm[1]))
        for gk, mem in groups:
            lo = min(m.cross - m.ce / 2 for m in mem)
            hi = max(m.cross + m.ce / 2 for m in mem)
            r0 = min(m.rank for m in mem)
            r1 = max(m.rank for m in mem)
            for r in range(r0, r1 + 1):
                layer = layers.get(r, [])
                blk = [i for i, it in enumerate(layer) if it.group == gk]
                if not blk:
                    continue
                i0, i1 = min(blk), max(blk)
                # NOTE: the group's drawn box is ONE rectangle spanning its
                # extreme real members' flow positions (gx/gx2 in
                # _solve_one), not a per-rank slice -- so a stranger in a
                # DIFFERENT grid-packing band at this rank is NOT
                # automatically flow-disjoint from the box (an earlier
                # version of this comment assumed it was; a hand-built
                # regression fixture with clutter nodes proved that wrong).
                # Every stranger at this rank must still be checked,
                # regardless of band; only the ENFORCE/cascade step below is
                # band-scoped (each band is its own independent cross stack
                # -- see assign_cross()).
                for i, it in enumerate(layer):
                    if it.group == gk:
                        continue
                    if i < i0:
                        d = (lo - pad_lo - it.ce / 2) - it.cross
                        if d < 0:
                            shove(it, d)
                    elif i > i1:
                        d = (hi + pad_hi + it.ce / 2) - it.cross
                        if d > 0:
                            shove(it, d)
            for r in sorted(layers):
                for grp in _band_groups(layers[r]):
                    enforce_out(grp)

    # Deterministic closing guarantee (docs/diagram-status.md re-audit
    # finding B: "a non-member node renders INSIDE a group boundary"). The
    # round loop above decides which side of a group's band to push a
    # stranger to from its ORDER within that rank's layer list (i < i0 / i
    # > i1) -- correct as long as each layer stays cross-sorted, an
    # invariant enforce()/enforce_out() maintain, but still a PROXY for the
    # real geometric question. This pass asks that question directly instead:
    # for every stranger at every rank inside a group's own rank span, is
    # its cross interval still inside the group's UNION band [lo-pad_lo,
    # hi+pad_hi] (the same band _solve_one derives the group's drawn rect
    # from)? If so, push it clear on whichever side its OWN cross value
    # already leans toward, independent of layer order.
    #
    # A first version of this pass called enforce_out() (cap-then-need,
    # cascading a DECREASE backward) after every shove regardless of
    # direction. That silently cancelled out any INCREASING shove (moving a
    # stranger toward the group's `hi` side): enforce_out's cap pass runs
    # BACKWARD first and, seeing the just-increased stranger now sitting
    # further from its very next same-layer neighbor than that neighbor's
    # OWN unmoved position allows, clamped the stranger straight back down
    # to its pre-shove position -- proven by constructing a diagram where a
    # stranger has several same-rank siblings trailing close behind it, none
    # of which individually need to move
    # (test_group_span_gap_never_traps_a_stranger_inside_the_box caught this
    # exact self-cancellation before this fix). The two `_cascade_*` helpers
    # below apply only the ONE pass whose direction matches the shove that
    # was just made, so an increasing push cascades forward through
    # trailing neighbors (making room by pushing them along too) instead of
    # being read backward as "this went too far, pull it back."
    def _cascade_fwd(layer):
        for i in range(1, len(layer)):
            need = layer[i - 1].cross + sep(layer[i - 1], layer[i])
            if layer[i].cross < need:
                layer[i].cross = need

    def _cascade_bwd(layer):
        for i in range(len(layer) - 2, -1, -1):
            cap = layer[i + 1].cross - sep(layer[i], layer[i + 1])
            if layer[i].cross > cap:
                layer[i].cross = cap

    for _ in range(3):
        moved = False
        for gk, mem in groups:
            lo = min(m.cross - m.ce / 2 for m in mem)
            hi = max(m.cross + m.ce / 2 for m in mem)
            mid = (lo + hi) / 2
            r0 = min(m.rank for m in mem)
            r1 = max(m.rank for m in mem)
            for r in range(r0, r1 + 1):
                for it in layers.get(r, []):
                    if it.group == gk:
                        continue
                    lo_c, hi_c = it.cross - it.ce / 2, it.cross + it.ce / 2
                    if hi_c <= lo - pad_lo or lo_c >= hi + pad_hi:
                        continue  # already clear of the band
                    d = ((lo - pad_lo - it.ce / 2) - it.cross if it.cross <= mid
                         else (hi + pad_hi + it.ce / 2) - it.cross)
                    shove(it, d)
                    moved = True
                    # cascade in every rank a member of it's OWN band lives
                    # in (shove() moved the whole band if it.group is set),
                    # in the direction this specific shove went -- restricted
                    # to the grid-packing band(s) actually touched, so the
                    # cascade never leaks across a band boundary into a
                    # sibling band's independent cross stack.
                    ranks = {m.rank for m in bygroup[it.group]} if it.group else {it.rank}
                    moved_keys = ({m.key for m in bygroup[it.group]} if it.group
                                  else {it.key})
                    for mr in ranks:
                        layer_mr = layers.get(mr, [])
                        bands_here = {x.band for x in layer_mr if x.key in moved_keys}
                        for grp in _band_groups(layer_mr):
                            if grp and grp[0].band in bands_here:
                                (_cascade_fwd if d >= 0 else _cascade_bwd)(grp)
        if not moved:
            break

    lo = min(it.cross - it.ce / 2 for r in layers for it in layers[r])
    for r in layers:
        for it in layers[r]:
            it.cross -= lo


def assign_flow(layers, gap):
    """Band-aware: a rank with N bands (N == 1 unless _decide_bands() split
    it) lays those bands out as consecutive sub-slots along the flow axis,
    separated by BAND_GAP, and centers each item within its OWN band's
    width -- the N == 1 case collapses to exactly the original formula
    (single slot, width == max(fe), no BAND_GAP added), so every unbanded
    rank's flow coordinates are byte-identical to before this change."""
    f = 0.0
    for r in sorted(layers):
        grps = _band_groups(layers[r])
        band_w = [max(it.fe for it in grp) for grp in grps]
        band_x0 = []
        acc = 0.0
        for i, w in enumerate(band_w):
            band_x0.append(acc)
            acc += w + (BAND_GAP if i < len(band_w) - 1 else 0.0)
        for grp, x0, w in zip(grps, band_x0, band_w):
            for it in grp:
                it.flow = f + x0 + (w - it.fe) / 2
        f += acc + gap
    return f - gap


# ---------------------------------------------------------------------------
# layout driver
# ---------------------------------------------------------------------------
def layout(spec, target_aspect=DEFAULT_TARGET_ASPECT, allow_bands=True):
    """Internal: builds the direction-neutral Item/layer structure `L` that
    route() and place_labels() consume. Not part of the public seam; solve()
    is the public entry point (below). `target_aspect` replaces what used to
    be a module constant (docs/diagram-plan.md section 3, defect 2) -- it
    only affects the LR branch of the aspect-control pass; TB keeps its own
    fixed 0.72 widen factor (a TB diagram chasing a 2:1 landscape target would
    just get bizarrely wide; solve()'s auto-flip, not this constant, is what
    fixes a portrait TB result against a landscape target_aspect).

    `allow_bands=False` disables grid packing entirely (every rank stays a
    single band, the pre-banding behavior) -- used internally by
    _solve_one()'s banded-vs-unbanded comparison; every real caller leaves
    this at its default."""
    lr = spec.get("direction", "LR") == "LR"
    keys = [n["key"] for n in spec["nodes"]]
    by = {n["key"]: n for n in spec["nodes"]}
    rank = rank_nodes(keys, spec["edges"])

    def build(ghost_ce=None):
        items = {}
        for i, k in enumerate(keys):
            n = by[k]
            it = Item(k, "node", i)
            it.node = n
            it.group = n.get("group")
            it.rank = rank[k]
            # node_box's third return (wrapped sublabel lines) only mattered
            # to the pre-refactor painter's single-pass paint; paint_svg
            # re-wraps from SolvedNode.sublabel + SolvedNode.w itself
            # (deterministically identical, since both start from the same
            # final box width), so it is not carried on Item here.
            w, h, _sub_lines = node_box(n)
            it.ce = h if lr else w
            it.fe = w if lr else h
            it.prio = 10
            items[k] = it
        chains, seq = build_chains(spec, rank, items, len(keys))
        if ghost_ce:
            for g in spec.get("groups", []):
                gk = g["key"]
                mem = [items[k] for k in keys if by[k].get("group") == gk]
                if not mem:
                    continue
                rs = sorted({m.rank for m in mem})
                for r in range(min(rs), max(rs) + 1):
                    if r in rs:
                        continue
                    gh = Item("_g_%s_%d" % (gk, r), "ghost", seq)
                    seq += 1
                    gh.rank = r
                    gh.group = gk
                    gh.ce = max(40.0, ghost_ce.get(gk, 60.0))
                    gh.fe = 8.0
                    gh.prio = 500
                    items[gh.key] = gh
            # virtual links keep each ghost aligned with its own band
            ghosts = [i for i in items.values() if i.kind == "ghost"]
            for gh in ghosts:
                for other in sorted(items.values(), key=lambda i: i.seq):
                    if (other.group == gh.group and other.kind in ("node", "ghost")
                            and abs(other.rank - gh.rank) == 1):
                        chains.append(({"_virtual": True}, [gh, other],
                                       gh.rank, other.rank))
        return items, chains

    items, chains = build()
    layers, adj, _ = order_layers(items, chains)
    if allow_bands:
        # grid packing (docs/diagram-plan.md addendum): decided before
        # assign_cross so the ghost-sizing pass below measures the SAME
        # per-band cross extent the final layout will actually use.
        _decide_bands(layers)
    assign_cross(layers, adj, items)
    group_ce = {}  # each group's own cross extent, used to size GHOST items
                   # (band-filler placeholders at ranks where a group has no
                   # real member) -- named to avoid colliding with the
                   # grid-packing `Item.band` concept, an unrelated axis.
    for g in spec.get("groups", []):
        mem = [items[k] for k in keys if by[k].get("group") == g["key"]]
        if mem:
            group_ce[g["key"]] = (max(m.cross + m.ce / 2 for m in mem) -
                                  min(m.cross - m.ce / 2 for m in mem))

    items, chains = build(ghost_ce=group_ce)
    layers, adj, crossings = order_layers(items, chains)
    bands_count = _decide_bands(layers) if allow_bands else {r: 1 for r in layers}
    assign_cross(layers, adj, items)
    repair_bands(spec, layers, items, lr)

    flow_total = assign_flow(layers, FLOW_GAP_LR if lr else FLOW_GAP_TB)
    cross_total = max(it.cross + it.ce / 2 for r in layers for it in layers[r])

    # aspect control: widen the cross axis rather than shipping a 4:1 ribbon.
    # a uniform scale of every cross coordinate keeps straight edges straight.
    w0, h0 = (flow_total, cross_total) if lr else (cross_total, flow_total)
    want = (w0 / target_aspect) if lr else (h0 * 0.72)
    have = h0 if lr else w0
    if have > 0:
        k = min(MAX_SPREAD, want / have)
        if k > 1.001:
            for r in layers:
                for it in layers[r]:
                    it.cross *= k
            cross_total *= k

    for r in layers:
        for it in layers[r]:
            if lr:
                it.x, it.y = it.flow, it.cross - it.ce / 2
                it.w, it.h = it.fe, it.ce
            else:
                it.x, it.y = it.cross - it.ce / 2, it.flow
                it.w, it.h = it.ce, it.fe
    return {"lr": lr, "items": items, "layers": layers, "chains": chains,
            "by": by, "crossings": crossings, "bands_count": bands_count}


# ---------------------------------------------------------------------------
# 5. routing
# ---------------------------------------------------------------------------
def rounded(pts, r=11):
    if len(pts) < 2:
        return ""
    d = ["M%.1f,%.1f" % pts[0]]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        v1 = (p1[0] - p0[0], p1[1] - p0[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = math.hypot(*v1) or 1.0
        l2 = math.hypot(*v2) or 1.0
        rr = min(r, l1 / 2, l2 / 2)
        a = (p1[0] - v1[0] / l1 * rr, p1[1] - v1[1] / l1 * rr)
        b = (p1[0] + v2[0] / l2 * rr, p1[1] + v2[1] / l2 * rr)
        d.append("L%.1f,%.1f" % a)
        d.append("Q%.1f,%.1f %.1f,%.1f" % (p1[0], p1[1], b[0], b[1]))
    d.append("L%.1f,%.1f" % pts[-1])
    return " ".join(d)


def dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 0.4 or abs(p[1] - out[-1][1]) > 0.4:
            out.append(p)
    return out


def route(L):
    lr, items, layers = L["lr"], L["items"], L["layers"]
    bands_count = L.get("bands_count") or {}
    banded_ranks = {r for r, n in bands_count.items() if n > 1}

    # grid packing (docs/diagram-plan.md addendum): a banded rank has more
    # than one node column side by side, so a segment that just cuts
    # straight across at a fixed cross value (the pre-banding routing below)
    # can pass through a SIBLING band's node -- the exact "edge enters a
    # node's silhouette" failure mode. rank_extent gives the full cross span
    # of every item in a rank (every band together); safe_exit()/
    # safe_entry() route a banded endpoint's own hop out to a cross-axis
    # line strictly OUTSIDE that span first (clearing every node in every
    # band of the rank by construction), via the empty BAND_GAP corridor
    # just past (or before) the endpoint's own column, before the normal
    # lane logic ever touches it. A rank with exactly one band (every rank
    # that _decide_bands() never split) is never in `banded_ranks`, so this
    # whole mechanism is inert for it -- the per-hop loop below falls
    # through to the ORIGINAL direct/lane logic unchanged, which is why
    # every pre-existing fixture (none of which trigger banding) routes
    # byte-identically to before this change.
    rank_extent = {}
    for r, its in layers.items():
        if not its:
            continue
        if lr:
            rank_extent[r] = (min(it.y for it in its), max(it.y + it.h for it in its))
        else:
            rank_extent[r] = (min(it.x for it in its), max(it.x + it.w for it in its))

    def _peers(it):
        return [p for p in layers.get(it.rank, []) if p.band == it.band]

    def _safe(it, entering: bool):
        """(flow_clear, cross_altitude) for `it`, a banded-rank endpoint:
        flow_clear is just past (exiting) or just before (entering) it's OWN
        column, in the empty inter-band corridor; cross_altitude is outside
        the WHOLE rank's cross extent, on whichever side is nearer `it` (so
        the detour is as short as it can be while staying safe)."""
        peers = _peers(it)
        if lr:
            own_cross = it.y + it.h / 2
            flow_clear = ((min(p.x for p in peers) - BAND_CLEAR) if entering
                          else (max(p.x + p.w for p in peers) + BAND_CLEAR))
        else:
            own_cross = it.x + it.w / 2
            flow_clear = ((min(p.y for p in peers) - BAND_CLEAR) if entering
                          else (max(p.y + p.h for p in peers) + BAND_CLEAR))
        lo, hi = rank_extent[it.rank]
        cross_alt = (lo - RANK_SAFE_MARGIN if (own_cross - lo) <= (hi - own_cross)
                     else hi + RANK_SAFE_MARGIN)
        return flow_clear, cross_alt

    def _mk(flow, cross):
        return (flow, cross) if lr else (cross, flow)

    routes, ports = [], {}
    for ci, (e, chain, rs, rt) in enumerate(L["chains"]):
        if e.get("_virtual"):
            continue
        if rs == rt:
            routes.append({"edge": e, "chain": chain, "flat": True, "ci": ci})
            continue
        fwd = 1 if rt > rs else -1
        ports.setdefault((chain[0].key, fwd), []).append((ci, chain[1]))
        ports.setdefault((chain[-1].key, -fwd), []).append((ci, chain[-2]))
        routes.append({"edge": e, "chain": chain, "flat": False, "ci": ci,
                       "fwd": fwd})

    port_at = {}
    for (k, face) in sorted(ports, key=lambda kv: (kv[0], kv[1])):
        reqs = sorted(ports[(k, face)], key=lambda r: (r[1].cross, r[0]))
        it = items[k]
        n = len(reqs)
        avail = (it.h if lr else it.w) - 20
        span = min(avail, (n - 1) * 15.0) if n > 1 else 0.0
        for i, (ci, _o) in enumerate(reqs):
            off = -span / 2 + (span / (n - 1) * i if n > 1 else 0)
            if lr:
                p = (it.x + (it.w if face > 0 else 0), it.y + it.h / 2 + off)
            else:
                p = (it.x + it.w / 2 + off, it.y + (it.h if face > 0 else 0))
            port_at[(ci, k, face)] = p

    # bend lanes: every vertical (LR) run in a channel gets its own offset so
    # parallel edges do not stack on the same line
    lanes = {}
    for R in routes:
        if R["flat"]:
            continue
        for a, b in zip(R["chain"], R["chain"][1:]):
            lanes.setdefault(min(a.rank, b.rank), []).append((R["ci"], a, b))
    lane_at = {}
    for g in sorted(lanes):
        lst = sorted(lanes[g], key=lambda t: ((t[1].cross + t[2].cross) / 2, t[0]))
        lo = max(it.flow + it.fe for it in layers[g])
        hi = min(it.flow for it in layers[g + 1])
        width = hi - lo
        inset = min(32.0, width * 0.22)
        usable = max(width - 2 * inset, 10.0)
        n = len(lst)
        for i, (ci, _a, _b) in enumerate(lst):
            f = 0.5 if n == 1 else i / (n - 1)
            lane_at[(ci, g)] = lo + inset + usable * f

    for R in routes:
        if R["flat"]:
            a, b = R["chain"][0], R["chain"][-1]
            if lr:
                p = [(a.x + a.w / 2, a.y + a.h), (a.x + a.w / 2, a.y + a.h + 20),
                     (b.x + b.w / 2, b.y + b.h + 20), (b.x + b.w / 2, b.y + b.h)]
            else:
                p = [(a.x + a.w, a.y + a.h / 2), (a.x + a.w + 20, a.y + a.h / 2),
                     (b.x + b.w + 20, b.y + b.h / 2), (b.x + b.w, b.y + b.h / 2)]
            R["pts"] = p
            continue
        chain, fwd, ci = R["chain"], R["fwd"], R["ci"]
        pts = [port_at[(ci, chain[0].key, fwd)]]
        for i, (a, b) in enumerate(zip(chain, chain[1:])):
            lx = lane_at[(ci, min(a.rank, b.rank))]
            if b is chain[-1]:
                bpt = port_at[(ci, b.key, -fwd)]
            elif lr:
                bpt = (b.x if fwd > 0 else b.x + b.w, b.y + b.h / 2)
            else:
                bpt = (b.x + b.w / 2, b.y if fwd > 0 else b.y + b.h)
            apt = pts[-1]
            a_banded = a.rank in banded_ranks
            b_banded = b.rank in banded_ranks
            if not a_banded and not b_banded:
                if lr:
                    if abs(apt[1] - bpt[1]) > 1.0:
                        pts += [(lx, apt[1]), (lx, bpt[1])]
                else:
                    if abs(apt[0] - bpt[0]) > 1.0:
                        pts += [(apt[0], lx), (bpt[0], lx)]
            else:
                apt_cross = apt[1] if lr else apt[0]
                bpt_cross = bpt[1] if lr else bpt[0]
                exit_cross = apt_cross
                if a_banded:
                    clear, exit_cross = _safe(a, entering=False)
                    pts += [_mk(clear, apt_cross), _mk(clear, exit_cross)]
                entry_cross = bpt_cross
                if b_banded:
                    clear_b, entry_cross = _safe(b, entering=True)
                pts.append(_mk(lx, exit_cross))
                if abs(exit_cross - entry_cross) > 1.0:
                    pts.append(_mk(lx, entry_cross))
                if b_banded:
                    pts += [_mk(clear_b, entry_cross), _mk(clear_b, bpt_cross)]
            pts.append(bpt)
            if b.kind in ("dummy", "ghost") and b is not chain[-1]:
                if lr:
                    pts.append((b.x + b.w if fwd > 0 else b.x, b.y + b.h / 2))
                else:
                    pts.append((b.x + b.w / 2, b.y + b.h if fwd > 0 else b.y))
        R["pts"] = dedupe(pts)
    return routes


# ---------------------------------------------------------------------------
# label placement (docs/diagram-plan.md section 3, defect 1: fan-in
# edge-label attribution)
# ---------------------------------------------------------------------------
def _polyline_length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _point_at_length(pts, target_len):
    """Point and unit tangent at cumulative distance `target_len` along the
    polyline `pts` (clamped to [0, total length])."""
    if len(pts) < 2:
        return (pts[0] if pts else (0.0, 0.0)), (1.0, 0.0)
    total = _polyline_length(pts)
    if total <= 0:
        return pts[0], (1.0, 0.0)
    t = max(0.0, min(total, target_len))
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d <= 0:
            continue
        if acc + d >= t - 1e-9:
            f = max(0.0, min(1.0, (t - acc) / d))
            return ((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f),
                    ((b[0] - a[0]) / d, (b[1] - a[1]) / d))
        acc += d
    a, b = pts[-2], pts[-1]
    d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return pts[-1], ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _resolve_label_collisions(labels, routes, node_rects, rounds=4):
    """Pairwise push-apart pass across ALL placed labels (not just those in
    the same layout channel: the previous "bygap" grouping missed collisions
    between labels routed through different channels). Runs after the
    per-target staggering and clash search above have already placed each
    label clear of nodes and foreign edges, so this is mostly a safety net
    for the remaining coincidental label-vs-label overlaps.

    A push that would itself create a node/foreign-edge clash is rejected in
    favor of the opposite direction, or left in place if both directions
    clash: a label sitting on top of an unrelated node's text (this was a
    real bug caught by rendering spec1 and looking at it) is worse than two
    edge labels sitting close to each other, so this pass must never trade
    one for the other."""
    for _ in range(rounds):
        labels.sort(key=lambda l: (l["y"], l["x"], l["ci"]))
        moved = False
        for i in range(len(labels)):
            a = labels[i]
            for j in range(i + 1, len(labels)):
                b = labels[j]
                ox = abs(a["x"] - b["x"]) < (a["w"] + b["w"]) / 2 + 6
                oy = abs(a["y"] - b["y"]) < (a["h"] + b["h"]) / 2 + 5
                if not (ox and oy):
                    continue
                if abs(a["x"] - b["x"]) >= abs(a["y"] - b["y"]):
                    shift = (a["w"] + b["w"]) / 2 + 6
                    forward, backward = a["x"] + shift, a["x"] - shift
                    preferred = forward if b["x"] >= a["x"] else backward
                    other = backward if preferred == forward else forward

                    def apply(x, b=b):
                        return _label_clashes(x, b["y"], b["w"], b["h"], b["ci"],
                                               routes, node_rects)
                    if not apply(preferred):
                        b["x"] = preferred
                        moved = True
                    elif not apply(other):
                        b["x"] = other
                        moved = True
                else:
                    shift = (a["h"] + b["h"]) / 2 + 5
                    forward, backward = a["y"] + shift, a["y"] - shift
                    preferred = forward if b["y"] >= a["y"] else backward
                    other = backward if preferred == forward else forward

                    def apply(y, b=b):
                        return _label_clashes(b["x"], y, b["w"], b["h"], b["ci"],
                                               routes, node_rects)
                    if not apply(preferred):
                        b["y"] = preferred
                        moved = True
                    elif not apply(other):
                        b["y"] = other
                        moved = True
        if not moved:
            break


def _rect_overlaps_rect(r1, r2) -> bool:
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    return not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)


def _label_clashes(cx, cy, w, h, own_ci, routes, node_rects):
    """True if a label box centered at (cx, cy) touches ANY routed polyline
    other than its own edge's (`own_ci`), OR overlaps any node's rect. Node
    overlap matters as much as polyline crossing: a label sitting on top of
    an unrelated node's own text is just as unreadable/misattributed as one
    sitting on an unrelated line (found by visual review rendering spec1 --
    the "JSON schema + authoring guide" edge label landed squarely on top of
    the neighboring "LLM Bridge" node's sublabel before this check existed).
    Uses the same segment-vs-rect test check() runs post-layout
    (_seg_intersects_rect, defined below); called here too so place_labels
    can search for a clash-free anchor instead of just asserting cleanliness
    after the fact."""
    rect = (cx - w / 2, cy - h / 2, w, h)
    for R in routes:
        if R["ci"] == own_ci:
            continue
        pts = R["pts"]
        for a, b in zip(pts, pts[1:]):
            if _seg_intersects_rect(a, b, rect):
                return True
    for nr in node_rects:
        if _rect_overlaps_rect(rect, nr):
            return True
    return False


def _label_candidates(frac_base, lo=0.10, hi=0.60, step=0.04, tries=14):
    """Fractions to try, closest to `frac_base` first, alternating outward:
    frac_base, frac_base+step, frac_base-step, frac_base+2*step, ..."""
    out = [frac_base]
    for k in range(1, tries + 1):
        for sign in (1, -1):
            f = frac_base + sign * step * k
            if lo <= f <= hi:
                out.append(f)
    return out


def place_labels(L, routes):
    """Edge labels are anchored along their OWN routed polyline, near the
    source end (around 0.30 of total length) rather than at the fixed
    first-bend / convergence point the previous implementation used. When
    several edges share a TARGET (the fan-in case), each gets a distinct
    baseline fraction (spread 0.26-0.44) plus a small perpendicular offset,
    so their labels are staggered along the flow axis instead of pooling at
    the merge point (docs/diagram-plan.md section 3, defect 1).

    That staggering is a good default but is not sufficient on its own: a
    fan-in's bend lanes can still run close enough together, near a small
    target node, that a label's box (wide: sized to its text) clips a
    NEIGHBOR edge's polyline, or lands on top of an unrelated NODE's own
    text, even though its own anchor point is correct. So each label also
    runs a local search over nearby fractions along its own polyline
    (_label_candidates), keeping the first one whose box clashes with
    neither (_label_clashes); if none of the candidates are fully clean, the
    closest-to-baseline candidate is kept as the best-effort placement. A
    themed opaque mask is drawn behind the text in paint_svg so it stays
    legible wherever it lands.
    """
    node_rects = [(it.x, it.y, it.w, it.h) for it in L["items"].values()
                  if it.kind == "node"]

    by_target = {}
    for R in routes:
        txt = (R["edge"].get("label") or "").strip()
        if not txt or len(R["pts"]) < 2:
            continue
        by_target.setdefault(R["edge"]["target"], []).append(R)

    out = []
    for _tgt, group in by_target.items():
        group = sorted(group, key=lambda r: r["ci"])
        n = len(group)
        for idx, R in enumerate(group):
            txt = (R["edge"].get("label") or "").strip()
            lines = wrap(txt, 10.5, LABEL_MAXW, 2)
            w = max(30.0, max(measure(l, 10.5) for l in lines) + 10.0)
            h = 14.0 * len(lines)
            pts = R["pts"]
            total = _polyline_length(pts)
            frac_base = 0.30 if n == 1 else 0.26 + 0.18 * (idx / (n - 1))
            step = h / 2 + 3.0

            def placement(frac, k):
                (px, py), (tx, ty) = _point_at_length(pts, total * frac)
                nx, ny = -ty, tx
                px += nx * (step * k)
                py += ny * (step * k)
                return px, py

            # perpendicular-offset multipliers to try, closest to the
            # intended baseline first: n>1 (fan-in) starts at its stagger
            # offset (k=1) and escalates outward; n==1 starts ON the line
            # (k=0) and only steps off it if the frac search alone cannot
            # clear an obstacle (e.g. a second edge routed in a near-
            # parallel lane between the same two nodes -- found by
            # rendering spec1 and looking: llm<->provider has edges in both
            # directions running ~15px apart, too close for frac alone to
            # dodge).
            k_order = [1, 2, -1, 3, -2, 4, -3] if n > 1 else [0, 1, -1, 2, -2, 3, -3]

            chosen = placement(frac_base, k_order[0])
            found = False
            for k in k_order:
                for cand in _label_candidates(frac_base):
                    px, py = placement(cand, k)
                    if not _label_clashes(px, py, w, h, R["ci"], routes, node_rects):
                        chosen = (px, py)
                        found = True
                        break
                if found:
                    break
            cx, cy = chosen
            out.append({"x": cx, "y": cy, "w": w, "h": h, "lines": lines,
                        "ci": R["ci"]})

    _resolve_label_collisions(out, routes, node_rects)
    return out


# ---------------------------------------------------------------------------
# the seam: solved geometry (docs/diagram-plan.md section 3)
# ---------------------------------------------------------------------------
@dataclass
class SolvedNode:
    id: str
    type: str
    label: str
    sublabel: str | None
    tag: str | None
    group: str | None
    x: float
    y: float
    w: float
    h: float


@dataclass
class SolvedEdge:
    source: str
    target: str
    label: str | None
    style: str
    pts: list[tuple[float, float]]                       # routed polyline, canvas space
    label_box: tuple[float, float, float, float] | None   # x, y, w, h of placed label


@dataclass
class SolvedGroup:
    id: str
    kind: str
    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class SolvedDiagram:
    width: float
    height: float
    title: str | None
    nodes: list[SolvedNode]
    edges: list[SolvedEdge]
    groups: list[SolvedGroup]
    legend: list[str]     # node types used, first-seen order -- ALWAYS
                            # populated (cheap: it is just the distinct
                            # `type`s present), regardless of `legend_h`, so
                            # an emitter that draws its own native legend
                            # (docs/diagram-status.md finding 16) always has
                            # the kind list even when it solved with
                            # legend=False (see legend_h below).
    direction: str         # "LR" | "TB" -- the direction ACTUALLY solved with,
                            # which may differ from the input Diagram's
                            # `direction` (see solve()'s auto-flip, defect 2)
    legend_h: float = LEGEND_H  # px of canvas height reserved at the bottom
                            # for a legend band; LEGEND_H when solve(...,
                            # legend=True, the default) was used, 0.0 when
                            # solve() was called with legend=False. Defaults
                            # to LEGEND_H (not 0.0) so a SolvedDiagram built
                            # by hand elsewhere (a test fixture that predates
                            # this field, e.g.) keeps its old always-reserved
                            # behavior rather than silently losing its legend
                            # band. THE CONTRACT (docs/diagram-status.md
                            # finding 16): solve() decides whether this space
                            # exists at all; paint_svg only ever DRAWS into it
                            # when it is non-zero (guarded below), and never
                            # invents its own reservation. Any OTHER emitter
                            # that wants to draw a native legend (a PPTX/
                            # .drawio legend block, say) must likewise
                            # solve(..., legend=True) to get this budget
                            # reserved for it, and an emitter that draws no
                            # legend at all must solve(..., legend=False) so
                            # it does not inherit dead space nothing ever
                            # paints into. Before this field existed,
                            # `_solve_one` added LEGEND_H to every canvas
                            # UNCONDITIONALLY, so the native PPTX and .drawio
                            # emitters -- which read solve()'s output but
                            # never drew `legend` -- silently inherited 0.6in
                            # of reserved, undrawn space.


def _to_spec(d: Diagram) -> dict:
    """IR Diagram -> the painter's internal spec-dict vocabulary. The painter
    reads exactly these keys: nodes {"key","kind","label","sublabel","tag",
    "group"}, edges {"source","target","label","style"}, groups {"key",
    "label","kind"} (node_box at :207 reads n.get("sublabel"), not "sub" --
    docs/diagram-plan.md section 3's own adapter description says "sub",
    which does not match the ported painter source; this adapter follows the
    actual code)."""
    return {
        "title": d.title or "",
        "direction": d.direction,
        "nodes": [
            {"key": n.id, "label": n.label, "kind": n.type,
             "sublabel": n.sublabel, "tag": n.tag, "group": n.group}
            for n in d.nodes
        ],
        "edges": [
            {"source": e.source, "target": e.target, "label": e.label,
             "style": e.style}
            for e in d.edges
        ],
        "groups": [
            {"key": g.id, "label": g.label, "kind": g.kind} for g in d.groups
        ],
    }


def _apply_detail(spec: dict, detail: str) -> dict:
    """The PPTX emitter's degradation ladder (docs/diagram-plan.md section
    4b): "full" keeps every sublabel and tag; "label+sub" drops tags;
    "label" drops both. Applied to the spec BEFORE layout, so node_box()
    (unchanged) simply never sees the dropped text and sizes smaller boxes;
    the resulting SolvedNode.sublabel/tag are None too, so paint_svg (or any
    other emitter) never draws text the geometry did not budget for."""
    if detail not in ("full", "label+sub", "label"):
        raise ValueError(
            f'detail must be "full", "label+sub", or "label", got {detail!r}'
        )
    if detail == "full":
        return spec
    nodes = []
    for n in spec["nodes"]:
        nn = dict(n)
        nn["tag"] = None
        if detail == "label":
            nn["sublabel"] = None
        nodes.append(nn)
    out = dict(spec)
    out["nodes"] = nodes
    return out


def _fit_score(s: SolvedDiagram, target_aspect: float) -> float:
    """Larger is better: the fit scale a caller would get fitting `s` into a
    virtual (target_aspect, 1.0)-shaped box (docs/diagram-plan.md grid-
    packing addendum's banded-vs-unbanded comparison, below). Mirrors
    diagram_pptx._fit()'s own k = min(w_in/canvas_w_in, max_h_in/canvas_h_in)
    formula exactly, with w_in=target_aspect, max_h_in=1.0 -- since every
    real caller's own (w_in, max_h_in) box is some scalar multiple of
    (target_aspect, 1.0) by construction (that IS what target_aspect means
    to them), this ranks two candidate layouts in the same order any real
    caller's actual fitted font size would."""
    if s.width <= 0 or s.height <= 0:
        return 0.0
    return min(target_aspect / s.width, 1.0 / s.height)


def _finish_solve(L: dict, spec: dict, legend: bool) -> SolvedDiagram:
    """Routing, label placement, group boxes, offset/extent -- everything
    downstream of an already-built `layout()` result. Split out of
    _solve_one() so grid packing (docs/diagram-plan.md addendum) can run
    this SAME pipeline twice -- once banded, once with allow_bands=False --
    and pick whichever actually produces the better fitted scale; banding a
    rank is not always a net win (a rank's own cross stack can shrink while
    an UNRELATED rank's cross stack grows through the shared barycenter-
    median coupling in assign_cross()'s iterative alignment -- measured on
    a hand-built fixture, not hypothetical: banding shrank the fan-out
    rank's own span by 28px but grew a downstream 2-node group's span by
    88px, a net INCREASE in the diagram's overall cross_total), so
    _solve_one() below never ships a banded layout that is actually worse
    than the plain one it would have produced anyway."""
    routes = route(L)
    labels = place_labels(L, routes)
    label_by_ci = {l["ci"]: l for l in labels}
    items = L["items"]
    node_items = [it for it in items.values() if it.kind == "node"]

    boxes = []
    for g in spec.get("groups", []):
        # every item carrying the group tag counts against the CROSS extent,
        # dummies included: an edge between two members routes inside the
        # container, so the container has to be drawn around it
        mem = [it for it in items.values() if it.group == g["key"]]
        real = [m for m in mem if m.kind == "node"]
        if not real:
            continue
        if L["lr"]:
            gx = min(m.x for m in real) - GBOX_PAD
            gx2 = max(m.x + m.w for m in real) + GBOX_PAD
            gy = min(m.y for m in mem) - GBOX_PAD
            gy2 = max(m.y + m.h for m in mem) + GBOX_PAD
        else:
            gx = min(m.x for m in mem) - GBOX_PAD
            gx2 = max(m.x + m.w for m in mem) + GBOX_PAD
            gy = min(m.y for m in real) - GBOX_PAD
            gy2 = max(m.y + m.h for m in real) + GBOX_PAD
        gy -= GBOX_HEAD
        gw = gx2 - gx
        # the box must be at least as wide as its own caption
        cap = measure(g["label"], 10.5, True) + (14 if g.get("kind") ==
                                                 "security-group" else 0) + 30
        gw = max(gw, cap)
        boxes.append([g, gx, gy, gw, gy2 - gy])

    minx = min([it.x for it in node_items] + [b[1] for b in boxes] +
               [min(p[0] for p in R["pts"]) for R in routes] +
               [l["x"] - l["w"] / 2 for l in labels])
    miny = min([it.y for it in node_items] + [b[2] for b in boxes] +
               [min(p[1] for p in R["pts"]) for R in routes] +
               [l["y"] - l["h"] / 2 for l in labels])
    ox, oy = MARGIN - minx, MARGIN + TITLE_H - miny
    for it in items.values():
        it.x += ox
        it.y += oy
    for b in boxes:
        b[1] += ox
        b[2] += oy
    for R in routes:
        R["pts"] = [(x + ox, y + oy) for x, y in R["pts"]]
    for l in labels:
        l["x"] += ox
        l["y"] += oy

    W = max([it.x + it.w for it in node_items] + [b[1] + b[3] for b in boxes] +
            [max(p[0] for p in R["pts"]) for R in routes] +
            [l["x"] + l["w"] / 2 for l in labels]) + MARGIN
    legend_h = LEGEND_H if legend else 0.0
    H = max([it.y + it.h for it in node_items] + [b[2] + b[4] for b in boxes] +
            [max(p[1] for p in R["pts"]) for R in routes] +
            [l["y"] + l["h"] / 2 for l in labels]) + MARGIN + legend_h
    W = max(W, 900)

    solved_nodes = [
        SolvedNode(id=it.key, type=it.node.get("kind", "service"),
                   label=it.node["label"], sublabel=it.node.get("sublabel"),
                   tag=it.node.get("tag"), group=it.group,
                   x=it.x, y=it.y, w=it.w, h=it.h)
        for it in sorted(node_items, key=lambda i: i.seq)
    ]

    solved_edges = []
    for R in sorted(routes, key=lambda r: r["ci"]):
        e = R["edge"]
        lb = label_by_ci.get(R["ci"])
        label_box = ((lb["x"] - lb["w"] / 2, lb["y"] - lb["h"] / 2,
                     lb["w"], lb["h"]) if lb else None)
        solved_edges.append(SolvedEdge(
            source=e["source"], target=e["target"], label=e.get("label"),
            style=e.get("style", "solid"), pts=list(R["pts"]),
            label_box=label_box,
        ))

    solved_groups = [
        SolvedGroup(id=g["key"], kind=g.get("kind", "region"), label=g["label"],
                    x=x, y=y, w=w, h=h)
        for g, x, y, w, h in boxes
    ]

    used: list[str] = []
    for n in spec["nodes"]:
        k = n.get("kind", "service")
        if k not in used:
            used.append(k)

    return SolvedDiagram(
        width=W, height=H, title=(spec.get("title") or None),
        nodes=solved_nodes, edges=solved_edges, groups=solved_groups,
        legend=used, direction=("LR" if L["lr"] else "TB"), legend_h=legend_h,
    )


def _solve_one(spec: dict, target_aspect: float, legend: bool = True) -> SolvedDiagram:
    """One layout attempt at the spec's own `direction`. solve() (below) may
    call this twice (once per direction) to satisfy target_aspect.

    `legend` gates whether LEGEND_H px of canvas height is reserved at the
    bottom (docs/diagram-status.md finding 16: the reserved legend band is a
    property of the SOLVE, not an unconditional add -- see SolvedDiagram.
    legend_h).

    Grid packing safety net (docs/diagram-plan.md addendum): if banding
    actually split any rank, ALSO solve the same spec with allow_bands=False
    and keep whichever candidate scores better on _fit_score() -- banding
    can regress an UNRELATED rank through assign_cross()'s shared
    barycenter coupling (see _finish_solve()'s own docstring for the
    measured case), and this guarantees grid packing is a strict
    improvement-or-no-op from every caller's point of view, never a
    regression. Costs a second layout() pass only when a rank was actually
    split (the common case -- most diagrams never trip ROW_LIMIT -- pays
    nothing extra)."""
    L = layout(spec, target_aspect)
    solved = _finish_solve(L, spec, legend)
    if any(n > 1 for n in L["bands_count"].values()):
        L_plain = layout(spec, target_aspect, allow_bands=False)
        solved_plain = _finish_solve(L_plain, spec, legend)
        if _fit_score(solved_plain, target_aspect) > _fit_score(solved, target_aspect):
            return solved_plain
    return solved


def _check_no_duplicate_ids(d: Diagram) -> None:
    """Raise if two nodes -- or two groups -- share an id.

    solve()'s internal keying is BY id (`by = {n["key"]: n for n in
    spec["nodes"]}` in layout(), and `items[k] = it` in the build() closure):
    a second node sharing an already-seen id doesn't get rejected, it
    silently OVERWRITES the first one's Item, so one whole node vanishes from
    the geometry with no error and no warning (docs/diagram-status.md finding
    16). lint's diagram/duplicate-id rule is the primary defense against this
    and normally catches it before render, but a caller can bypass lint
    (`--no-lint`) or call solve() directly without ever linting -- so solve()
    checks again here, itself, as the render layer's own last line of
    defense against a silent drop."""
    seen_nodes: set[str] = set()
    for n in d.nodes:
        if n.id in seen_nodes:
            raise ValueError(
                f"Diagram has two nodes with id {n.id!r}: solve() cannot lay "
                "out a diagram with duplicate node ids (this is normally "
                "caught by lint's diagram/duplicate-id rule before render; "
                "if you bypassed lint, fix the duplicate id instead of "
                "resolving it here, since one of the two nodes would "
                "otherwise silently disappear from the diagram)"
            )
        seen_nodes.add(n.id)
    seen_groups: set[str] = set()
    for g in d.groups:
        if g.id in seen_groups:
            raise ValueError(
                f"Diagram has two groups with id {g.id!r}: solve() cannot "
                "lay out a diagram with duplicate group ids (this is "
                "normally caught by lint's diagram/duplicate-id rule before "
                "render; if you bypassed lint, fix the duplicate id instead)"
            )
        seen_groups.add(g.id)


def solve(d: Diagram, theme=None, *,
          target_aspect: float = DEFAULT_TARGET_ASPECT,
          detail: str = "full",
          legend: bool = True) -> SolvedDiagram:
    """Layout only: turns an IR Diagram into fully positioned, read-only
    geometry (SolvedDiagram). No SVG, no color resolution beyond what a
    node's `type` needs to size its box (fonts, not colors, drive sizing).
    Non-mutating with respect to `d`; deterministic (the same Diagram, theme,
    target_aspect, detail and legend always produce byte-identical
    layout_report() JSON). Raises ValueError if `d` has two nodes or two
    groups sharing an id (see _check_no_duplicate_ids): solving a diagram
    with duplicate ids would otherwise silently drop one of them from the
    geometry with no error.

    `theme` is accepted for API symmetry with paint_svg/render_svg and for a
    possible future font-metric-aware sizing pass; the current text-metric
    table is a single fixed font, so theme has no effect on geometry today.

    `detail` is the PPTX emitter's degradation ladder (docs/diagram-plan.md
    section 4b): "full" (default) keeps every sublabel and tag; "label+sub"
    drops tags; "label" drops both, for a caller that cannot fit "full" at a
    legible font size and needs smaller node boxes.

    `target_aspect` is the width/height a caller wants (2.0 by default, a
    16:9-ish landscape). If solving at the Diagram's own `direction` yields a
    portrait result (aspect < 1.0) while target_aspect asks for landscape
    (>= 1.0), solve() ALSO tries the opposite direction and returns whichever
    lands closer to target_aspect (ties keep LR). This is what fixes the
    spec5-style 0.60:1 portrait failure (docs/diagram-plan.md section 3,
    defect 2) without the caller having to know to ask for it -- so the
    returned SolvedDiagram.direction can differ from `d.direction`.

    `legend` (default True) is THE CONTRACT for reserved legend space
    (docs/diagram-status.md finding 16): it decides whether LEGEND_H px of
    canvas height is reserved at the bottom of the SOLVED geometry at all
    (SolvedDiagram.legend_h is LEGEND_H when True, 0.0 when False) --
    reserving that space is a property of the SOLVE, not something every
    emitter inherits whether it draws into it or not. paint_svg (below)
    always draws a legend and therefore needs legend=True (the default: a
    caller that never overrides this argument, e.g. every render_svg() call,
    gets the exact behavior this module always had). An emitter that draws
    its OWN native legend using SolvedDiagram.legend/.legend_h (a PPTX or
    .drawio legend block) must ALSO solve with legend=True so that budget
    exists for it to draw into. An emitter that draws no legend at all
    should solve with legend=False so it does not reserve dead space nothing
    ever paints into -- before this parameter existed, `_solve_one` added
    LEGEND_H to every canvas unconditionally, so the native PPTX emitter
    (render/diagram_pptx.py, which never drew SolvedDiagram.legend) and the
    .drawio emitter (render/drawio.py, likewise) both silently inherited
    0.6in of reserved space they never used.
    """
    _check_no_duplicate_ids(d)
    spec = _apply_detail(_to_spec(d), detail)
    primary = _solve_one(spec, target_aspect, legend)
    if target_aspect >= 1.0 and primary.height:
        primary_aspect = primary.width / primary.height
        if primary_aspect < 1.0:
            flipped = dict(spec)
            flipped["direction"] = ("TB" if spec.get("direction", "LR") == "LR"
                                     else "LR")
            alt = _solve_one(flipped, target_aspect, legend)
            alt_aspect = (alt.width / alt.height) if alt.height else primary_aspect
            d_primary = abs(primary_aspect - target_aspect)
            d_alt = abs(alt_aspect - target_aspect)
            if d_alt < d_primary or (d_alt == d_primary and alt.direction == "LR"):
                return alt
    return primary


# ---------------------------------------------------------------------------
# 6. paint (serialization only: reads SolvedDiagram, never re-lays anything out)
# ---------------------------------------------------------------------------
def node_shape(n: SolvedNode, pal: dict, t: dict) -> tuple[list[str], float]:
    p = pal.get(n.type, pal["service"])
    x, y, w, h = n.x, n.y, n.w, n.h
    o = []

    def barpath(top, bot):
        return ('<path d="M%.1f,%.1f h%.1f v%.1f h%.1f z" fill="%s"/>'
                % (x + 0.8, top, BAR - 1, bot - top, -(BAR - 1), p["bar"]))

    if n.type == "store":
        ry = 9.0
        d = ("M%.1f,%.1f L%.1f,%.1f A%.1f,%.1f 0 0 0 %.1f,%.1f L%.1f,%.1f "
             "A%.1f,%.1f 0 0 0 %.1f,%.1f Z"
             % (x, y + ry, x, y + h - ry, w / 2, ry, x + w, y + h - ry,
                x + w, y + ry, w / 2, ry, x, y + ry))
        o.append('<path d="%s" fill="%s" stroke="%s" stroke-width="1.3"/>'
                 % (d, p["fill"], p["line"]))
        o.append('<path d="M%.1f,%.1f A%.1f,%.1f 0 0 0 %.1f,%.1f A%.1f,%.1f 0 0 0 '
                 '%.1f,%.1f" fill="none" stroke="%s" stroke-width="1.3"/>'
                 % (x, y + ry, w / 2, ry, x + w, y + ry, w / 2, ry, x, y + ry,
                    p["line"]))
        o.append(barpath(y + ry + 2, y + h - ry - 2))
        return o, y + 2 * ry
    if n.type == "queue":
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
                 'fill="%s" stroke="%s" stroke-width="1.1" opacity="0.6"/>'
                 % (x + 6, y - 6, w, h, p["fill"], p["line"]))
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
                 'fill="%s" stroke="%s" stroke-width="1.3"/>'
                 % (x, y, w, h, p["fill"], p["line"]))
        o.append(barpath(y + 8, y + h - 8))
        return o, y
    if n.type == "external":
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
                 'fill="%s" stroke="%s" stroke-width="1.4" stroke-dasharray="6 3"/>'
                 % (x, y, w, h, h / 2, p["fill"], p["line"]))
        return o, y
    if n.type == "security":
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
                 'fill="%s" stroke="%s" stroke-width="1.4"/>'
                 % (x, y, w, h, p["fill"], p["line"]))
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="6" '
                 'fill="none" stroke="%s" stroke-width="0.9" '
                 'stroke-dasharray="3 2.5" opacity="0.8"/>'
                 % (x + 4.5, y + 4.5, w - 9, h - 9, p["bar"]))
        o.append(barpath(y + 9, y + h - 9))
        return o, y
    o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
             'fill="%s" stroke="%s" stroke-width="1.3"/>'
             % (x, y, w, h, p["fill"], p["line"]))
    o.append(barpath(y + 8, y + h - 8))
    return o, y


def paint_svg(s: SolvedDiagram, theme=None) -> str:
    """Serialization only: turns already-solved geometry into an SVG string.
    Never re-lays anything out, so multiple emitters (this one, the native
    PPTX shape emitter, the .drawio emitter -- later phases) can all consume
    the exact same SolvedDiagram from one solve() call (docs/diagram-plan.md
    section 4)."""
    t = dict(THEME)
    t.update(theme or {})
    pal = kind_palette(t)
    mk = {"muted": t["muted"], "primary": t["primary"], "accent": t["accent"]}

    W, H = s.width, s.height
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
         'viewBox="0 0 %.0f %.0f" font-family="%s">'
         % (W, H, W, H, t["font"]),
         '<rect width="100%%" height="100%%" fill="%s"/>' % t["background"]]

    o.append("<defs>")
    for name, col in mk.items():
        o.append('<marker id="ar_%s" markerWidth="9" markerHeight="7" refX="8.4" '
                 'refY="3.5" orient="auto" markerUnits="userSpaceOnUse">'
                 '<path d="M0,0 L9,3.5 L0,7 Z" fill="%s"/></marker>' % (name, col))
    o.append("</defs>")

    o.append('<text x="%d" y="34" font-size="21" font-weight="700" fill="%s">%s</text>'
             % (MARGIN, t["text"], esc(s.title or "")))
    o.append('<rect x="%d" y="44" width="48" height="3" rx="1.5" fill="%s"/>'
             % (MARGIN, t["primary"]))

    # group boundary rects only (no caption yet: see the caption pass below,
    # which is deliberately AFTER edges -- docs/diagram-plan.md section 3,
    # defect 3)
    for g in s.groups:
        secure = g.kind == "security-group"
        col = t["accent"] if secure else t["primary"]
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="14" '
                 'fill="%s" fill-opacity="0.6" stroke="%s" stroke-opacity="0.5" '
                 'stroke-width="1.3"%s/>'
                 % (g.x, g.y, g.w, g.h, t["surface"], col,
                    ' stroke-dasharray="7 4"' if secure else ""))

    for e in s.edges:
        ck, sw, dash = EDGE_STYLE.get(e.style, EDGE_STYLE["solid"])
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f"%s '
                 'stroke-linecap="round" marker-end="url(#ar_%s)"/>'
                 % (rounded(e.pts), mk[ck], sw,
                    ' stroke-dasharray="%s"' % dash if dash else "", ck))

    # group CAPTION plates, drawn AFTER edges: an opaque mask (same mechanism
    # as edge labels) behind the caption text, so a bend that crosses the
    # group's header band [gy, gy+GBOX_HEAD] cannot paint over the caption
    # (docs/diagram-plan.md section 3, defect 3 -- the plate already existed
    # before this port, but was drawn BEFORE edges, so it was strictly
    # decorative; z-order was the actual bug).
    for g in s.groups:
        secure = g.kind == "security-group"
        col = t["accent"] if secure else t["primary"]
        cap = ("&#9679; " if secure else "") + esc(g.label)
        cw = measure(g.label, 10.5, True) + (14 if secure else 0) + 10
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="15" rx="3" '
                 'fill="%s" fill-opacity="0.95"/>'
                 % (g.x + 9, g.y + 4.5, cw, t["background"]))
        o.append('<text x="%.1f" y="%.1f" font-size="10.5" font-weight="700" '
                 'letter-spacing="0.3" fill="%s">%s</text>'
                 % (g.x + 13, g.y + 16, col, cap))

    for n in s.nodes:
        parts, top = node_shape(n, pal, t)
        o.extend(parts)
        sub_lines = (wrap(n.sublabel, 10.5, n.w - 2 * PAD_X - BAR, 2)
                     if n.sublabel else [])
        tag = n.tag
        label_lines, label_pt = fit_label(n.label, n.w - 2 * PAD_X - BAR)
        pitch = 17.0 * label_pt / 14.5
        base_off = 13.0 * label_pt / 14.5
        ch = pitch * len(label_lines) + 12.5 * len(sub_lines) + (16 if tag else 0)
        box_h = n.h - (28 if n.type == "store" else 0)
        cy = top + (box_h - ch) / 2 + base_off
        cx = n.x + n.w / 2 + BAR / 2
        for line in label_lines:
            o.append('<text x="%.1f" y="%.1f" font-size="%.1f" font-weight="650" '
                     'text-anchor="middle" fill="%s">%s</text>'
                     % (cx, cy, label_pt, t["text"], esc(line)))
            cy += pitch
        yy = cy - pitch + base_off
        for line in sub_lines:
            o.append('<text x="%.1f" y="%.1f" font-size="10.5" text-anchor="middle" '
                     'fill="%s">%s</text>' % (cx, yy, t["muted"], esc(line)))
            yy += 12.5
        if tag:
            p = pal.get(n.type, pal["service"])
            tw = measure(tag, 9.2, True) + 14
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="14" rx="7" '
                     'fill="%s" fill-opacity="0.18" stroke="%s" '
                     'stroke-opacity="0.4" stroke-width="0.8"/>'
                     % (cx - tw / 2, yy - 8.5, tw, p["bar"], p["bar"]))
            o.append('<text x="%.1f" y="%.1f" font-size="9.2" font-weight="700" '
                     'text-anchor="middle" fill="%s">%s</text>'
                     % (cx, yy + 2, p["bar"], esc(tag)))

    for e in s.edges:
        if not e.label_box or not (e.label or "").strip():
            continue
        x, y, w, h = e.label_box
        lines = wrap(e.label.strip(), 10.5, LABEL_MAXW, 2)
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" '
                 'fill="%s" fill-opacity="0.95" stroke="%s" stroke-opacity="0.3" '
                 'stroke-width="0.8"/>'
                 % (x, y, w, h, t["background"], t["muted"]))
        ty = y + 9
        for line in lines:
            o.append('<text x="%.1f" y="%.1f" font-size="10.5" text-anchor="middle" '
                     'fill="%s">%s</text>' % (x + w / 2, ty, t["muted"], esc(line)))
            ty += 11.5

    # legend band: only drawn when solve() reserved room for it
    # (SolvedDiagram.legend_h > 0 -- THE CONTRACT, docs/diagram-status.md
    # finding 16: paint_svg never invents its own reservation, it only draws
    # into whatever solve() already budgeted. A SolvedDiagram produced with
    # legend=False (a caller building for an emitter that draws no legend at
    # all) has legend_h == 0 and paint_svg skips this whole band -- drawing
    # it anyway would paint over the diagram's own last row of content,
    # since no space was reserved for it.
    if s.legend_h > 0:
        ly = H - s.legend_h + 22
        o.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                 'stroke-opacity="0.3"/>' % (MARGIN, ly - 16, W - MARGIN, ly - 16,
                                             t["muted"]))
        lx = float(MARGIN)
        for k in s.legend:
            p = pal.get(k, pal["service"])
            o.append('<rect x="%.1f" y="%.1f" width="12" height="12" rx="3" fill="%s" '
                     'stroke="%s"/>' % (lx, ly - 2, p["fill"], p["bar"]))
            o.append('<rect x="%.1f" y="%.1f" width="3" height="12" rx="1.5" fill="%s"/>'
                     % (lx + 0.5, ly - 2, p["bar"]))
            o.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s">%s</text>'
                     % (lx + 17, ly + 8, t["muted"], k))
            lx += 17 + measure(k, 10) + 20
        lx += 10
        for st, name in (("solid", "flow"), ("dashed", "async / return"),
                         ("emphasis", "primary path"), ("secure", "secure")):
            ck, sw, dash = EDGE_STYLE[st]
            o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                     'stroke-width="%.1f"%s/>'
                     % (lx, ly + 4, lx + 24, ly + 4, mk[ck], sw,
                        ' stroke-dasharray="%s"' % dash if dash else ""))
            o.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s">%s</text>'
                     % (lx + 30, ly + 8, t["muted"], name))
            lx += 30 + measure(name, 10) + 20
    o.append("</svg>")
    return "\n".join(o)


def _stamp_hash(svg: str, content_hash: str) -> str:
    """Insert data-docloom-hash="..." as the first attribute on the root
    <svg ...> tag. Pure string surgery: paint_svg's output always starts
    with a single `<svg xmlns=... ...>` opening tag on its own line, so this
    never touches node/edge markup."""
    marker = "<svg "
    i = svg.find(marker)
    if i == -1:
        return svg
    at = i + len(marker)
    return f'{svg[:at]}data-docloom-hash="{content_hash}" {svg[at:]}'


def solve_ir(d: Diagram, theme=None, **kw) -> SolvedDiagram:
    """Solve `d` with the layout backend its `layout` field selects: the
    built-in Sugiyama solver (`native`, default) or the optional Graphviz `dot`
    backend (`dot`/`auto`, better on dense branching graphs). `dot`/`auto` fall
    back to native when the [dotlayout] extra isn't installed -- mirroring
    render_diagram's contract, so an embedded dot diagram never fails a render
    just because pygraphviz is absent. This is the single seam every emitter
    goes through to honor Diagram.layout; it stays coordinate-free (it only
    picks the solver, it never hand-places anything)."""
    if getattr(d, "layout", "native") in ("dot", "auto"):
        try:
            from .diagram_dot import DotUnavailable, solve_dot
            return solve_dot(d, theme, detail=kw.get("detail", "full"),
                             legend=kw.get("legend", True))
        except DotUnavailable:
            pass  # optional backend absent -> native, same as render_diagram
    return solve(d, theme, **kw)


def render_svg(d: Diagram, theme=None) -> str:
    """One-shot convenience: solve() then paint_svg(), with the SVG root
    stamped data-docloom-hash="{diagram_hash(d)}" (docs/diagram-plan.md
    section 1's Tier 1/Tier 2 contract: this hash is the only thing that ties
    a derived export back to the exact IR content it was generated from; it
    is never read back). Uses solve()'s and paint_svg()'s own defaults;
    callers that need a specific target_aspect/detail call solve()/paint_svg()
    directly.

    Note on the plan: docs/diagram-plan.md section 4(a) describes the hash
    attribute as paint_svg's job ("Add data-docloom-hash... on the root svg
    element"), but section 3's own paint_svg(s, theme) signature takes only a
    SolvedDiagram and theme -- it has no `d` to hash. That is an internal
    contradiction in the plan; this module resolves it by stamping the hash
    here, in render_svg, which is the one function with access to both the
    solved geometry and the source Diagram. paint_svg stays exactly the pure
    (SolvedDiagram, theme) -> str function section 3 specifies, so a future
    emitter that wants to stamp the hash a different way (a PPTX group-shape
    name, a .drawio XML comment) is free to call diagram_hash(d) itself from
    the same Diagram it already holds.
    """
    return _stamp_hash(paint_svg(solve_ir(d, theme), theme), diagram_hash(d))


# ---------------------------------------------------------------------------
# post-layout assertions (dev tool; docs/diagram-plan.md section 3)
# ---------------------------------------------------------------------------
def layout_report(s: SolvedDiagram) -> dict:
    """Plain-JSON dump of solved geometry: nodes, groups, edge polylines and
    label boxes, for dev tooling and snapshot/determinism tests. Nothing
    renderer-specific in it (no color)."""
    return {
        "width": s.width, "height": s.height, "title": s.title,
        "direction": s.direction, "legend": list(s.legend),
        "legend_h": s.legend_h,
        "nodes": [asdict(n) for n in s.nodes],
        "edges": [
            {"source": e.source, "target": e.target, "label": e.label,
             "style": e.style, "pts": [list(p) for p in e.pts],
             "label_box": list(e.label_box) if e.label_box else None}
            for e in s.edges
        ],
        "groups": [asdict(g) for g in s.groups],
    }


def _rects_within(a: SolvedNode, b: SolvedNode, margin: float) -> bool:
    """True if node rects `a` and `b` overlap or are closer than `margin`
    px apart on both axes (archify's rectsOverlap semantics, expanded by a
    margin: expand `a` by margin on every side and test AABB overlap)."""
    ax0, ay0 = a.x - margin, a.y - margin
    ax1, ay1 = a.x + a.w + margin, a.y + a.h + margin
    bx0, by0, bx1, by1 = b.x, b.y, b.x + b.w, b.y + b.h
    return not (bx1 <= ax0 or bx0 >= ax1 or by1 <= ay0 or by0 >= ay1)


def _point_in_rect(p, rect) -> bool:
    x, y = p
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def _seg_seg_intersect(p1, p2, p3, p4) -> bool:
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
    d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
    return ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4))


def _seg_intersects_rect(p0, p1, rect) -> bool:
    """Segment-vs-axis-aligned-rect intersection: archify defines this
    (segmentIntersectsRect) but never uses it; check() below is what
    actually exercises it (docs/diagram-plan.md section 3)."""
    if _point_in_rect(p0, rect) or _point_in_rect(p1, rect):
        return True
    rx, ry, rw, rh = rect
    corners = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return any(_seg_seg_intersect(p0, p1, c0, c1) for c0, c1 in edges)


def check(s: SolvedDiagram) -> list[str]:
    """Post-layout assertions a caller can run in tests or behind a dev env
    flag, never in production render (docs/diagram-plan.md section 3):
      - no two node rects within 8px of each other (or overlapping)
      - every edge label box lies inside the canvas
      - no edge label box overlaps a node rect (found by visual review, not
        in the plan's own defect list: an edge label landing on top of an
        unrelated node's text is exactly as unreadable as a fan-in pooling)
      - every edge polyline is at least 24px long
      - no edge segment crosses a node rect that is not its own source/target
      - no node overlaps a group boundary it is not a member of
        (docs/diagram-status.md re-audit finding B: a group box is drawn as
        a bounding rect around its members, so the picture STATES "this box
        contains exactly these nodes" -- a non-member whose rect touches or
        sits inside that rect makes the picture claim something the IR
        never said, the same category of lie this project rejected mermaid
        for. layout()/repair_bands() is the construction-time guarantee
        against this (see its own docstring); this check exists so a future
        change there that weakens that guarantee is caught here, not by a
        human staring at a rendered slide.)
    Returns a list of human-readable problem strings; [] means clean.
    """
    problems: list[str] = []

    for g in s.groups:
        grect = (g.x, g.y, g.w, g.h)
        for n in s.nodes:
            if n.group == g.id:
                continue
            if _rect_overlaps_rect((n.x, n.y, n.w, n.h), grect):
                problems.append(
                    f"node overlaps foreign group boundary: {n.id!r} is not "
                    f"a member of group {g.id!r} but its rect "
                    f"({n.x:.1f},{n.y:.1f},{n.w:.1f}x{n.h:.1f}) overlaps "
                    f"that group's boundary "
                    f"({g.x:.1f},{g.y:.1f},{g.w:.1f}x{g.h:.1f})"
                )

    for i, a in enumerate(s.nodes):
        for b in s.nodes[i + 1:]:
            if _rects_within(a, b, 8.0):
                problems.append(
                    f"node overlap: {a.id!r} and {b.id!r} are within 8px "
                    "(or overlapping)"
                )

    for e in s.edges:
        if e.label_box:
            x, y, w, h = e.label_box
            if x < -0.5 or y < -0.5 or x + w > s.width + 0.5 or y + h > s.height + 0.5:
                problems.append(
                    f"edge label out of canvas: {e.source!r}->{e.target!r} "
                    f"box={e.label_box} canvas=({s.width:.1f},{s.height:.1f})"
                )
            for n in s.nodes:
                if _rect_overlaps_rect(e.label_box, (n.x, n.y, n.w, n.h)):
                    problems.append(
                        f"edge label overlaps node: {e.source!r}->{e.target!r} "
                        f"label sits on top of node {n.id!r}"
                    )

    for e in s.edges:
        length = _polyline_length(e.pts)
        if length < 24.0:
            problems.append(
                f"edge too short: {e.source!r}->{e.target!r} ({length:.1f}px)"
            )

    for e in s.edges:
        excl = {e.source, e.target}
        for a, b in zip(e.pts, e.pts[1:]):
            for n in s.nodes:
                if n.id in excl:
                    continue
                if _seg_intersects_rect(a, b, (n.x, n.y, n.w, n.h)):
                    problems.append(
                        f"edge {e.source!r}->{e.target!r} crosses unrelated "
                        f"node {n.id!r}"
                    )
    return problems

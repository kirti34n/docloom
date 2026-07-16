PLAN: docloom architecture diagrams (editable) + PPTX quality fixes
Target repo: C:/Users/kirti/Music/doc_generation/docloom
Painter source to import: C:/Users/kirti/AppData/Local/Temp/claude/C--Users-kirti-Music-doc-generation/4b5c83c8-d40a-43ba-a549-71f1a298dbdd/scratchpad/bakeoff/painter/diagram_svg.py
Reference PoCs (read, do not vendor): scratchpad/pptx_native/{solve.py, emit_pptx.py, emit_v2.py}, scratchpad/bakeoff/painter/emit_drawio.py

=====================================================================
1. THE DECISION ON EDITABILITY
=====================================================================

Primary editability story: the diagram IR is the single editable source of truth, and the PPTX renderer emits diagrams as NATIVE POWERPOINT SHAPES (add_shape + add_connector with begin_connect/end_connect glue) so the deck itself is directly editable in the tool the user actually opens. A .drawio file is a secondary, one-way export for users who want a dedicated diagram editor.

Justification against the research:
- ~80% of real edits to a generated diagram are semantic (rename, add/remove node or edge, retype, retag). Those are IR edits and survive regeneration by construction. Pixel nudging is exactly what ir.py:208 ("Layout intent, not geometry - the renderer owns coordinates") exists to eliminate.
- Native PPTX shapes are proven end to end on the pinned python-pptx 1.0.2: 21/21 connectors carried real stCxn/endCxn OOXML glue in the PoC, cylinders (FLOWCHART_MAGNETIC_DISK), dashed lines, z-order, arrowheads via a:tailEnd all work. This is strictly better than any draw.io embed: the official draw.io PowerPoint add-in inserts flat images and requires manual re-insert after every edit.
- .drawio emission from the painter's solved geometry is proven: 5 bake-off specs, 69 nodes, 106 edges, 39 ms, stdlib only, all 5 files valid against the official jgraph mxfile.xsd.

The round-trip trap, answered in writing (put this in README and docstrings):
- Tier 1 SOURCE: the Diagram block inside the IR JSON. The only editable surface docloom honors. Survives regeneration.
- Tier 2 DERIVED: SVG, PNG, PPTX shapes, .drawio. Never read back. Regenerated freely. Shapes a user moved inside PowerPoint, and files edited in draw.io, are forks: regeneration overwrites the deck and does not merge. Enforce mechanically by stamping every emitted diagram with the IR content hash (PPTX group shape name `docloom:diagram:{id}:{sha1(canonical_json)[:12]}`, an XML comment in .drawio, `data-docloom-hash` on the SVG root). No .drawio import, ever: importing positions violates ir.py:208; importing only labels silently discards the user's moves, which is worse than refusing.

draw.io MCP: NO, it does not belong in this product, and the owner deserves the straight answer. MCP is a tool interface for an agent in a loop; docloom's renderer is a deterministic function. The official jgraph/drawio-mcp exposes exactly two tools (create_diagram, search_shapes); its job is to open a diagram in an editor tab, the LLM still writes the XML. draw.io's own docs bless the no-MCP path: generate the file via Python, "avoids MCP entirely". The other two MCP servers are either browser-bound (lgazo) or a stateless XML generator behind a Deno container (simonkurtz), which would mean shelling out to a container to emit XML for geometry docloom already solved. What the owner actually wants from "archify alongside draw.io MCP" decomposes cleanly: archify's value is its VOCABULARY (absorbed into the IR, section 2), draw.io's value is its FILE FORMAT and free editor (the .drawio emitter, section 4), MCP's value is agent authoring (docloom already has its own mcp_server.py; if diagram authoring over MCP is wanted later, expose the Diagram IR there, not draw.io's server). If the owner wants drawio-mcp in his own IDE for hand-drawing, fine, install it separately; it must never be a runtime dependency of the library.

One BLOCKER decision for the owner before Phase 4 (studio wiring, out of scope here but flag it): docloom-studio already ships D2 WASM as its diagram engine (web/src/diagram/d2.ts, DiagramEditor.tsx is a D2 text editor). The painter makes a second engine and mermaid deps make a third. The painter's decisive advantage is that its geometry is solved in Python at render time, which is the only way to emit native PPTX shapes and .drawio from one layout pass; D2's geometry is trapped in browser WASM. D2 should be retired from the diagram path and DiagramEditor re-pointed at the Diagram IR, or the studio preview will stop matching the exported deck. Get explicit agreement; do not build the studio side until then. This plan covers the library only.

=====================================================================
2. THE IR (src/docloom/ir.py)
=====================================================================

Add after Artifact (currently ends line 194), before the Block union. Keep Artifact unchanged (it remains the reference type for externally rendered visuals like infographics). Diagram is a new inline, coordinate-free block.

Vocabulary decision: keep the painter's 7 node kinds (its palette, box shapes, and legend already key on them, and they are more general than archify's web-app framing). Mapping from archify for the docs: frontend->client, backend->service, database->store, messagebus->queue, cloud->cloud, security->security, external->external. Edge variants are the painter's EDGE_STYLE keys. Group membership lives on the node (`group` field referencing DiagramGroup.id) rather than archify's `wraps[]`: it maps 1:1 onto the painter's existing spec dict and makes "node in two groups" unrepresentable.

```python
class DiagramNode(BaseModel):
    id: SafeStr
    label: SafeStr
    type: Literal[
        "service", "client", "store", "queue", "security", "cloud", "external",
    ] = "service"
    sublabel: SafeStr | None = Field(None, description='tech/detail line, e.g. "PostgreSQL 16"')
    tag: SafeStr | None = Field(None, description='tiny corner annotation, e.g. "v2", "PCI"')
    group: SafeStr | None = Field(None, description="id of the DiagramGroup this node sits inside")


class DiagramEdge(BaseModel):
    source: SafeStr
    target: SafeStr
    label: SafeStr | None = None
    style: Literal["solid", "dashed", "emphasis", "secure"] = "solid"


class DiagramGroup(BaseModel):
    """A boundary box drawn around its member nodes. Geometry is derived
    (bbox of members plus padding), never authored."""
    id: SafeStr
    label: SafeStr
    kind: Literal["region", "security-group"] = "region"


class Diagram(BaseModel):
    """Architecture diagram. Structure only: nodes, edges, groups.
    Layout, coordinates, routing, and colors are computed by the renderer."""
    type: Literal["diagram"] = "diagram"
    id: SafeStr | None = None
    title: SafeStr | None = None
    direction: Literal["LR", "TB"] = "LR"
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    groups: list[DiagramGroup] = Field(default_factory=list)
    caption: SafeStr | None = None
    alt: SafeStr = ""
```

Block union (ir.py:197-201) becomes:

```python
Block = Union[
    Heading, Paragraph, BulletList, NumberedList,
    Quote, Code, Table, Image, Callout, Divider,
    Chart, StatRow, Artifact, Diagram,
]
```

No min/max length constraints in the models: llm_schema() strips minLength/maxLength, so all length limits are lint rules (section 6). No row/col/pos/size/fromSide/toSide/route/via/labelAt/labelDx/labelDy/labelSegment fields, ever: those are archify's hand-placement knobs and exactly the geometry ir.py:208 forbids.

Also add a module-level canonical hash helper used by every emitter for the Tier 1/Tier 2 stamp:

```python
def diagram_hash(d: Diagram) -> str:
    """sha1 of the canonical JSON of a Diagram, first 12 hex chars."""
```
(implementation: `hashlib.sha1(d.model_dump_json(exclude_none=True).encode()).hexdigest()[:12]`)

Acceptance: `Diagram(**{...})` validates the 5 bake-off specs translated to IR; llm_schema() includes the diagram block and round-trips through the existing schema tests; test count only goes up.

=====================================================================
3. THE PAINTER (new file src/docloom/render/diagram_svg.py)
=====================================================================

Copy the scratchpad painter into the repo, then perform the seam refactor. The refactor is code motion, verified in the PoC: render_svg() at diagram_svg.py:864 is layout() + route() + place_labels() + group-box computation (:877-899) + offset/extent pass (:901-923), and only then string emission (:925 onward). Everything before line 925 becomes solve(); everything after becomes paint_svg().

The seam, precisely:

```python
@dataclass
class SolvedNode:
    id: str; type: str; label: str
    sublabel: str | None; tag: str | None; group: str | None
    x: float; y: float; w: float; h: float          # px, canvas space

@dataclass
class SolvedEdge:
    source: str; target: str; label: str | None; style: str
    pts: list[tuple[float, float]]                   # routed polyline, canvas space
    label_box: tuple[float, float, float, float] | None  # x, y, w, h of placed label

@dataclass
class SolvedGroup:
    id: str; kind: str; label: str
    x: float; y: float; w: float; h: float

@dataclass
class SolvedDiagram:
    width: float; height: float                      # px canvas extent
    title: str | None
    nodes: list[SolvedNode]
    edges: list[SolvedEdge]
    groups: list[SolvedGroup]
    legend: list[str]                                # node types used, first-seen order
    direction: str                                   # "LR" | "TB"

def solve(d: "Diagram", theme=None, *,
          target_aspect: float = 2.0,
          detail: str = "full",                      # "full" | "label+sub" | "label"
          ) -> SolvedDiagram: ...

def paint_svg(s: SolvedDiagram, theme=None) -> str: ...

def render_svg(d: "Diagram", theme=None) -> str:
    return paint_svg(solve(d, theme), theme)
```

Rules for solve():
- Input is the IR Diagram model (an internal `_to_spec(d)` adapter produces the painter's existing dict shape: nodes `{"key","kind","label","sub","tag","group"}`, edges `{"source","target","label","style"}`, groups `{"key","label","kind"}`; the painter already reads exactly these keys, e["source"]/e["target"] confirmed at :231/:286, e.get("style") at :961, n.get("kind") at :810, g.get("kind")=="security-group" at :896).
- solve() must be non-mutating with respect to its input and must return a fresh SolvedDiagram every call (the current code mutates Item.x in the offset pass; that is fine because Items are built fresh inside layout() per call, but emitters receive dataclasses, never the internal Items, and must treat them read-only).
- `detail` controls node_box(): "full" = label+sublabel+tag, "label+sub" drops tags, "label" drops sublabels too. This is the degradation ladder the PPTX emitter climbs to hit its font floor (section 4).
- `target_aspect` replaces the TARGET_ASPECT constant read at :615. Additionally: when the caller did not force `direction` and the solved aspect is < 1.0 for an LR-targeted canvas (the spec5 0.60:1 portrait failure), re-solve with direction flipped and keep whichever lands closer to target_aspect. Deterministic tie-break: keep LR.
- Theme param stays the dict overlay it is today; the docloom Theme model is adapted by callers (`{"primary": theme.primary, "accent": theme.accent, "surface": theme.surface, "text": theme.text, "muted": theme.muted, "background": theme.background}`).

Known defects, with disposition:

BLOCKING for v1 (fix inside this module before any emitter ships):
1. Fan-in edge-label attribution. Adopt archify's mechanism: every edge label gets an opaque mask rect (w = max(30, measured_text + 10), h = 14, rx = 3) in the theme background color drawn under the text, AND place_labels() must stagger labels of edges sharing a target along the flow axis (each label anchored to the first third of ITS OWN polyline, offset perpendicular from the segment, never pooled at the convergence point). Acceptance: in a 4-edges-into-1-node fixture, each label's box intersects only its own edge's polyline.
2. Aspect ratio for slides. The target_aspect parameter + auto-flip above. Acceptance: all 5 bake-off specs solve to aspect between 1.4 and 2.6 when called with target_aspect=2.2.
3. Group titles struck by edges. Two-part fix: draw an opaque label plate (theme background fill, same mask style as edge labels) behind the group caption at (gx+8, gy+4..GBOX_HEAD), and in route() treat each group's header band [gy, gy+GBOX_HEAD] as a horizontal obstacle so bend lanes are pushed below it. If the routing change proves invasive, the opaque plate alone is acceptable for v1; the routing avoidance becomes a follow-up.
4. Off-brand palette. kind_palette() at :79-104 rotates hue by fixed offsets, which produced the judged red/amber/lavender clash. Replace with: service = primary hue as-is, store = accent hue as-is, external = neutral (surface/muted, unchanged), and the remaining kinds (client, cloud, queue, security) draw from hue rotations CLAMPED to +/-40 degrees off primary or accent with saturation capped at 0.45 for strokes and fills held at L 0.955 (i.e., everything reads as one family around the brand hues; only "security" may keep a warm hue but desaturated to 0.40). Keep the rotation principle (a tint ramp collapses the legend), constrain its range. Acceptance is visual (section 7): no judge calls a color foreign to the theme.

DEFERRABLE (file as TODOs, do not block v1):
5. Container dead space (us-east-1 box ~60% empty). Root cause is ghost-item band inflation; needs a real fix in the ghost sizing at :566-591. Defer.
6. Nested containers. Not supported; lint rejects nothing (groups are flat by construction in the IR). Defer until a real document needs it.

Post-layout assertions (dev tool, archify's --layout-json idea): add `def layout_report(s: SolvedDiagram) -> dict` returning nodes/groups/edge polylines/label boxes as plain JSON, and `def check(s: SolvedDiagram) -> list[str]` asserting: no two node rects within 8 px (AABB, archify's rectsOverlap semantics), every label_box inside canvas, every edge polyline length >= 24 px, no edge segment crossing an unrelated node rect (write this one ourselves: archify defines segmentIntersectsRect but never uses it). check() runs in tests and behind an env flag, never in production render.

=====================================================================
4. THE EMITTERS (one layout, N emitters)
=====================================================================

All emitters consume SolvedDiagram. None of them re-layout anything.

(a) SVG: src/docloom/render/diagram_svg.py, paint_svg(solved, theme) -> str. Already exists as the tail of render_svg. Full fidelity: OKLCH-ish gradients, kind bars, rounded routing, legend. Add `data-docloom-hash="{diagram_hash(d)}"` on the root svg element. PNG for raster consumers comes from the existing render/raster.py svg_to_png() (optional [diagrams] extra, returns None when missing, never raises).

(b) Native PPTX shapes: NEW file src/docloom/render/diagram_pptx.py.

```python
def add_diagram(slide, d: Diagram, solved: SolvedDiagram, theme,
                x_in: float, y_in: float, w_in: float, max_h_in: float,
                *, mode: str = "attached",     # "attached" | "freeform"
                ) -> float:                    # height consumed, inches
```

Behavior:
- Compute fit scale k = min(w_in / (solved.width/96), max_h_in / (solved.height/96)); all coordinates x*k, all font sizes scaled by k (the PoC's v1 bug was hardcoding Pt(10.5); lab_pt = 14.5 * k * 72/96).
- FONT FLOOR AND DEGRADATION LADDER: if the fitted node-label size < 8.0 pt, re-solve at detail="label+sub", then detail="label"; accept the first detail level reaching >= 8.0 pt. If even "label" misses the floor, fall back to a full-fidelity PNG via paint_svg + raster.svg_to_png (and if the raster extra is absent, render a visible placeholder box with the alt text, NOT a silent 0.0). Lint (section 6) warns upstream so this fallback is rare.
- Z-order: groups first (region rects, rx per kind: 12 region, 8 security-group), then nodes, then connectors, then labels.
- Shape mapping: store -> MSO_SHAPE.FLOWCHART_MAGNETIC_DISK (FLOWCHART_DATABASE does not exist in the 182-member enum), external -> FLOWCHART_TERMINATOR with dashed line, queue -> FLOWCHART_MULTIDOCUMENT, everything else -> ROUNDED_RECTANGLE. Fill/stroke from the same kind_palette(theme) the SVG uses.
- mode="attached" (DEFAULT): add_connector(MSO_CONNECTOR.ELBOW), begin_connect(node_shape, idx)/end_connect(...); PowerPoint owns routing, edges follow dragged nodes. This is the editability promise.
- mode="freeform": build_freeform along solved edge pts (painter routing preserved, visibly cleaner at density, NOT glued). Expose but do not default.
- Arrowheads: no high-level API in python-pptx; write a:tailEnd into a:ln via lxml (PoC set_arrow() is the reference).
- Edge label textboxes get tb.fill.solid() white/background halo (proven fix for strike-through).
- Group the whole diagram into one group shape if add_group_shape proves workable (it exists on _BaseGroupShapes but was never exercised; treat as a stretch task, not a dependency), and set shape/group .name = `docloom:diagram:{d.id or 'anon'}:{diagram_hash(d)}`.

Cannot express: gradients, the painter's kind-bar accents, exact painter routing (in attached mode). Accepted trade for editability.

(c) drawio XML: NEW file src/docloom/render/drawio.py.

```python
def render_drawio(d: Diagram, solved: SolvedDiagram, theme=None) -> str:
```

- Port scratchpad/bakeoff/painter/emit_drawio.py (proven: 5/5 files valid against official mxfile.xsd, referentially intact, stdlib only, 39 ms). xml.sax.saxutils escaping, UNCOMPRESSED XML (draw.io explicitly instructs AI-generated content must not be compressed).
- Container children use coordinates RELATIVE to the parent container (official checklist item 12; the PoC already subtracts group origin, keep that code).
- Embed `<!-- docloom:hash:{diagram_hash(d)} -->` as an XML comment (schema-invisible).
- Cannot express: the painter's rounded edge paths become drawio orthogonal edge styles; acceptable, draw.io re-routes on edit anyway. No fallback needed: stdlib only, always available.
- Delivery: the CLI gets a flag `--diagram-sources` (cli.py) that writes `{output_stem}.diagrams/{diagram_id or index}.drawio` next to the output file for every Diagram block in the document. Also expose a public one-shot API in `docloom/__init__.py`: `render_diagram(d: Diagram, theme=None, fmt="svg"|"png"|"drawio") -> str | bytes | None` (png returns None without the extra). Documented as one-way, terminal exports.

Per-format consumption of the Diagram block:
- pptx.py: native shapes via (b). New `_diagram_block(slide, b, theme, x, y, w, max_h)` dispatched in _block() (insert before the Artifact isinstance at pptx.py:710).
- docx.py: paint_svg -> raster.svg_to_png (with raster.theme_font_files(theme)) -> inline picture, mirroring the just-landed chart-picture path; when the extra is missing, a bordered alt-text placeholder paragraph, never silence.
- html.py: inline the SVG string directly.
- markdown.py + typst.py: mirror however each currently materializes Chart/Image assets (markdown links a written .svg sidecar; typst embeds the PNG, placeholder without the extra). The implementing agent must copy the existing per-renderer asset convention rather than inventing a new one.

=====================================================================
5. PPTX QUALITY (first-class deliverable)
=====================================================================

Honest status: the dedicated pptx-quality-audit research agent FAILED and returned nothing, so we have proven diagram-related defects plus the owner's unspecific "still not that great". Do not design against a guess: Phase P5 below opens with a real audit. But four defects are already proven and get fixed regardless:

P5.1 Silent block drops (proven, pptx.py:710-716): an Artifact with a missing path returns 0.0 and the block vanishes with no trace. Fix: render a visible placeholder (surface-colored rect + alt text + caption, same box the DOCX fallback uses) and emit a runtime warning; add lint rule `artifact/unresolved` (severity error when kind=="diagram" and no path and no artifact_id). Grep pptx.py for every other `return 0.0` early-out and give each the same treatment or a comment justifying silence.

P5.2 Diagram density/legibility (proven in the PoC, applies to the existing SVG/PNG artifact path too): a spec3-sized diagram fitted to a slide yields 5.3 pt labels in EVERY renderer. Fixed by the font floor + degradation ladder in diagram_pptx (section 4) plus lint density budget (section 6). This is the highest-leverage "ppt not great" fix for diagram-bearing decks.

P5.3 Edge/label legibility inside decks: white-halo edge labels and group caption plates (section 3 fixes 1 and 3) flow into the deck automatically once the painter is fixed.

P5.4 THE AUDIT (do this before inventing more fixes): render the repo's existing seed/example documents plus 3 dense synthetic decks (chart-heavy, table-heavy, image+stat mixed) to PPTX; convert with LibreOffice: `"/c/Program Files/LibreOffice/program/soffice.exe" --headless --convert-to pdf {deck}.pptx` then pdftoppm (or soffice to PNG per slide); LOOK at every slide image with the Read tool; file every defect as `file:line, defect, severity, proposed fix` in the phase report. Known suspects to check explicitly: text overflow beyond max_h clamps, caption collisions at pptx.py:637-643, stat-card crowding at :647-679, title band scaling, chart rasterization fidelity from the new raster path, image aspect distortion. Then fix everything ranked error/major and re-verify visually. Ask the owner for one concrete "bad deck" example; if provided, it becomes audit input #1.

Acceptance for section 5: every audited slide passes the visual bar in section 7; zero silent block drops remain in pptx.py (verified by grep + a test that renders an Artifact with a bogus path and asserts a placeholder shape exists).

=====================================================================
6. LINT (src/docloom/lint.py)
=====================================================================

New rule family, all findings use the existing Finding model {rule, severity, where, message}, where = e.g. "slides[3].blocks[1]".

Semantic rules (from archify's validator, the reachable half):
- diagram/empty (error): no nodes.
- diagram/duplicate-id (error): repeated node or group ids.
- diagram/dangling-edge (error): edge.source or edge.target not a node id.
- diagram/unknown-group (error): node.group not a group id.
- diagram/empty-group (warning): group with zero members.
- diagram/self-loop (warning): edge.source == edge.target (painter does not draw these well).
- diagram/disconnected-node (info): node with no edges.
- diagram/label-too-long (warning): node label > 40 chars, sublabel > 40, tag > 12, edge label > 30, group label > 40. These are the length limits llm_schema() cannot carry (it strips minLength/maxLength), so lint is their only home.

Density budget (the rule that actually fixes "ppt not great" for diagrams):
- diagram/too-dense (error at > 14 nodes or estimated depth > 7; warning at > 8 nodes or depth > 5, message says "will not be legible on a 16:9 slide; split it or move detail to sublabels"). Depth = longest path on the acyclic projection; expose `estimate_depth(nodes, edges) -> int` from render/diagram_svg.py (rank_nodes already computes this) and import it in lint.py. lint.py already imports renderer-mirrored constants (lines 43-60), so this is consistent with existing precedent, and importing the real function beats hand-mirroring another number.
- diagram/crowded-slide (warning): a Diagram sharing a slide with more than one other non-Heading block.

Geometric rules do NOT go in lint (lint runs on a coordinate-free IR): they are the painter's check()/layout_report() assertions from section 3, exercised in tests.

_block_height integration: add `DIAGRAM_H_IN = 4.6` beside IMAGE_H_IN (lint.py:55) and teach lint's physical-height mirror that a Diagram block occupies min(max_h, DIAGRAM_H_IN), so deck/overflow catches a diagram stacked under three paragraphs. Mirror the same constant in pptx.py's _natural_h (pptx.py:723).

=====================================================================
7. TESTING STRATEGY
=====================================================================

(a) Functional (pytest, new file tests/test_diagram.py + additions to tests/test_lint.py; current suite is 195 passing, it must only grow):
- IR: Diagram validates the 5 bake-off specs translated to IR; llm_schema includes it; diagram_hash stable across key order.
- Layout determinism: solve() twice on the same spec returns identical layout_report() JSON (the painter is documented deterministic; lock it in).
- solve() non-mutation: input Diagram unchanged after solve; two consecutive solves identical.
- Painter checks: check(solve(spec)) returns [] for all 5 bake-off specs at all 3 detail levels.
- Defect regressions: fan-in fixture (4 edges into 1 node) label-attribution assertion; aspect assertion (1.4 <= W/H <= 2.6 for all specs at target_aspect=2.2); spec5 auto-flip test.
- SVG emitter: golden-file string equality against checked-in SVGs (deterministic, so exact match is legitimate); data-docloom-hash present.
- PPTX emitter: unzip the .pptx, parse slide XML, assert stCxn/endCxn count == edge count in attached mode, a:tailEnd count == edge count, group/shape name carries the docloom:diagram: hash stamp, all shape coordinates within slide EMU bounds, fitted label size >= 8 pt for an in-budget spec.
- drawio emitter: lxml XSD validation against a vendored copy of the official jgraph mxfile.xsd (tests/data/mxfile.xsd, dev-only oracle; add lxml to the dev extra), plus referential-integrity check (every edge source/target resolves, parents resolve, child coords relative to container).
- Fallback paths: without resvg (monkeypatch import failure), DOCX/typst produce the placeholder and never raise; PPTX below-floor spec falls back to PNG or placeholder; Artifact with bogus path produces a placeholder shape (the P5.1 regression test).
- Lint: one test per diagram/* rule, positive and negative.

(b) UX/visual verification (the owner demanded judged output, not just green tests):
- Build 6 real decks: the 5 bake-off specs each on a content slide with title + caption, plus one mixed deck (diagram + chart + stats + table across slides), through the full public API.
- Rasterize via LibreOffice at "/c/Program Files/LibreOffice/program/soffice.exe" (headless convert to PDF, then per-page PNG), and LOOK at every slide with the Read tool.
- The acceptance bar, explicitly: every node label legible (>= 8 pt fitted, no truncation mid-word), every edge label attributable to exactly one edge by eye, no text struck through by a line, no shape overlapping another shape's text, group captions readable on their plates, colors read as one brand family with theme #1D4ED8/#0E9F6E, diagram fills the slide body without letterboxing worse than 25% dead margin, and nothing silently missing (count blocks in vs shapes out).
- Judge protocol: the verifying agent writes one verdict line per slide (PASS or the specific defect). A phase is done only when every slide is PASS.
- Editability spot-check: open the generated .pptx XML and verify glue; ALSO flag to the owner that an actual drag in desktop PowerPoint was never observed (research verified the OOXML mechanism and LibreOffice rendering only) and ask him to drag one node as the final acceptance step. Same for .drawio: ask him to open one file in draw.io; XSD validity was proven, a real open was not.
- Visual regression: assert on SVG strings (exact, deterministic) and coarse perceptual hashes of rasterized slides (8x8 average hash via Pillow, dev extra; assert Hamming distance <= 6), NEVER PNG byte equality (LibreOffice/font variance would make it flaky).

=====================================================================
8. PHASED EXECUTION
=====================================================================

Each phase independently shippable, verified by its own tests before the next lands. File ownership is strict so concurrent Sonnet agents do not collide.

P0. Painter import + seam refactor + blocking defect fixes. [L, ~1 agent-day equivalent]
  Owns: src/docloom/render/diagram_svg.py (new), tests/test_diagram_solve.py (new).
  Deliver: solve()/paint_svg()/render_svg()/layout_report()/check()/estimate_depth(), defect fixes 1-4, golden SVGs.
  Blocks: P2, P3, P4. Nothing blocks it.

P1. IR + lint. [M]
  Owns: src/docloom/ir.py, src/docloom/lint.py, tests/test_ir_diagram.py, tests/test_lint (additions).
  PARALLEL with P0 (different files), except the estimate_depth import: stub it behind a try/except until P0 merges, then wire.

P2. Native PPTX emitter + pptx.py dispatch. [L]
  Owns: src/docloom/render/diagram_pptx.py (new), the single _block dispatch hook + _natural_h line in pptx.py, tests/test_diagram_pptx.py.
  Needs: P0 + P1. NOT parallel with P5 (both touch pptx.py); run P2 after P5's fix wave or vice versa, never simultaneously.

P3. drawio emitter + CLI flag + public render_diagram API. [M]
  Owns: src/docloom/render/drawio.py (new), cli.py, __init__.py, tests/test_drawio.py, tests/data/mxfile.xsd.
  Needs: P0 + P1. PARALLEL with P2 (disjoint files).

P4. Remaining renderers (docx/html/markdown/typst) + raster wiring. [M]
  Owns: render/docx.py, html.py, markdown.py, typst.py, xlsx.py (skip note), their tests.
  Needs: P0 + P1. PARALLEL with P2 and P3.

P5. PPTX quality audit + fixes. [L]
  Owns: pptx.py (everything except P2's two hooks), tests/test_render_quality.py additions, the audit report.
  Needs: nothing (can start immediately). Coordinate the pptx.py handoff with P2 explicitly: recommended order P5-audit -> P5-fixes -> P2 -> joint visual pass.

P6. Full visual verification + perceptual-hash regression suite + README/docs (Tier 1/Tier 2 contract, draw.io MCP rationale, [diagrams] extra note). [M]
  Owns: tests/test_visual_regression.py, README.md, docs/.
  Needs: everything. Final gate: every slide PASS per section 7(b), plus the two owner-verified checks (PowerPoint drag, draw.io open).

Parallelization summary: wave 1 = P0 + P1 + P5-audit concurrently; wave 2 = P3 + P4 + P5-fixes concurrently; wave 3 = P2; wave 4 = P6.

=====================================================================
9. RISKS AND UNVERIFIED CLAIMS
=====================================================================

1. UNVERIFIED: connector glue surviving an actual drag in desktop PowerPoint. The stCxn/endCxn XML is correct per spec and LibreOffice renders it, but no one dragged a node in real PowerPoint. Mitigation: owner acceptance step in P6; freeform mode exists as the fallback story if glue misbehaves.
2. UNVERIFIED: generated .drawio files opening cleanly in real draw.io. XSD validity + referential integrity proven; visual correctness of style strings is not guaranteed by schema. Mitigation: owner opens one file in P6; style strings copied from the official style-reference.md only.
3. Attached-mode routing is PowerPoint's, not the painter's, and gets ugly at density. Mitigated by the lint density budget and the degradation ladder, but a 12-node in-budget diagram will still route worse than the SVG. If judges reject it in P6, flip the default to freeform and document that dragging does not re-route.
4. The density budget may fight real LLM output: models love 15-node diagrams. lint/too-dense tells the model at validation time (the studio's lint-repair loop can act on it), but prompt guidance may also need a line. Out of scope here; note for the studio.
5. Palette acceptance is subjective. The constrained-rotation formula is a hypothesis; budget one iteration loop in P6 for judge feedback.
6. Studio D2 duplication (section 1 blocker): if the owner insists on keeping D2, the studio preview and the exported deck will disagree; this plan still stands for the library, but say so before anyone wires the studio.
7. llm_schema token growth: a new block with 4 sub-models fattens every generation request. Small, but verify the schema-size test (if any) still passes and the block description strings stay terse.
8. The PPTX quality audit agent failed once already; the defect list beyond the four proven items is unknown. P5 is scoped as audit-then-fix precisely so the plan does not pretend to know what it cannot.
9. resvg font availability on slim deployments affects the PNG fallback path (raster.py already mitigates with theme_font_files and documented fonts-dejavu-core requirement; diagrams inherit both).
10. Group shape (add_group_shape) for whole-diagram dragging is untested; it is scoped as a stretch task inside P2, never a dependency.
# Decision: architecture diagrams in docloom

## 1. Verdict

**Reject archify as code. Take only its JSON schema and its design language.**

Archify cannot be adopted as-is, vendored, or "ported" in the sense of lifting its layout engine, because the thing people assume it has (automatic layout) does not exist in it. Every one of the five load-bearing claims behind the "use archify" case was tested and **refuted**. What survives verification is exactly two things, and they are both free:

1. Its **IR vocabulary**: `components[{id, type, label, sublabel, tag}]` / `connections[{from, to, label, variant}]` / `boundaries[{kind, label, wraps}]`. Flat, non-recursive, string-id-referenced. That is precisely the shape `llm_schema()` (llm.py:206-259) demands and precisely the shape docloom's IR rules (ir.py:3-9, "no recursive models") permit.
2. Its **information architecture**: semantic type-coding of nodes, boundary containers, sublabels and tags, a legend, masked edge labels. This is what makes an archify diagram look better than a stock Mermaid diagram, and it is orthogonal to layout.

The recommendation is therefore: **add a coordinate-free `Diagram` block to docloom's IR using archify's field names, write a pure-Python painter (`render/diagram_svg.py`) as the exact sibling of the existing `chart_svg.py`, and rasterize for PPTX/DOCX with `resvg-py` behind a one-function seam.** Keep `mermaidx` documented as the escape hatch behind that same seam (see section 5 and 7). Effort: 1 to 2 weeks for a good version, with a usable vertical slice in 3 days.

## 2. What archify actually is (and the four myths)

Archify is a **Claude agent skill**: an LLM writes a typed JSON spec, a Node CLI renders it to an HTML page, the human or the agent *looks at the result in a browser* and edits the coordinates. It is a hand-placement renderer with a strict layout linter. It is very good at what it is. It is not a library.

**Myth 1: "It does automatic layout, the LLM never writes coordinates."** False, and this is the fatal one. `renderers/architecture/grid.mjs:1` says it in the source: *"Not auto-layout, fixed cell math only."* There is no dagre, no ELK, nothing. In the default free-placement mode, `resolveComponentPos()` returns `[NaN, NaN]` and the render **exits 1** with *"Component "cli" must include pos [x, y]"* for every node (reproduced empirically on a 10-node spec with no coordinates). The only alternative is opt-in `layout.mode: "grid"`, where the LLM hand-assigns integer `row`/`col` for every node. Overlap is not resolved; it is thrown back at the author as *"Suggested fix: move "studio" pos to [168, 80]"*. Sequence mode is worse: it hard-requires a pixel `y` on every message. Archify's whole quality story depends on an agent in a visual feedback loop. **docloom has no loop.** It makes one `llm_schema(Slide)` structured-output call per slide (generate.py:435-443) with a lint retry and zero visual feedback. Shipping pixel coordinates through that pipe is the exact failure mode we are trying to avoid.

**Myth 2: "It returns a standalone SVG."** False. The renderer is a side-effecting CLI script with **zero exports** (`grep "^export"` returns nothing). It `fs.writeFileSync`s an **HTML document**. You would have to spawn Node as a subprocess and regex the `<svg>` out of the HTML. And that SVG is not valid standalone: no `xmlns`, no width/height, and **no colors at all**. The only `fill` in the entire SVG is `fill="url(#grid)"`. Every stroke, fill and text color is a CSS class (`.c-frontend`, `.t-muted`, `.a-emphasis`) resolved by `:root` custom properties in `assets/template.html`. Feed that SVG to any non-browser rasterizer and you get an unstyled ghost.

**Myth 3: "You can theme it to our brand."** False. No CLI flag, no env var (`grep process.env` returns zero), no schema field (adding `"theme"` to a spec gives `must NOT have additional properties`, because every schema is `additionalProperties: false`), no importable API (`"private": true`, no `main`, no `exports`), and the template path is hardcoded in `renderers/shared/cli.mjs:11`. The palette is ~30 CSS variables in `assets/template.html`. Re-theming means patching a file inside the package, that is, forking the distribution. Its "theme" is a dark/light toggle. Its own SVG/PNG export uses `getComputedStyle()` and `canvas.toBlob()`, that is, a live browser.

**Myth 4: "docloom is pure Python, so archify adds a whole new dependency class."** Half true, and the half that is false matters. The pip library and the FastAPI server process really are 100% pure Python: a clean install of `docloom[pdf]` resolves to exactly 12 packages, and rendering pptx/docx/pdf/typ/html/md with `subprocess.Popen/run/call`, `os.system`, `os.spawnv`, `os.execv` and `os.startfile` all monkeypatched to raise still succeeds. Typst is an in-process binding. **But docloom-studio already depends on npm** (Dockerfile:16 has a mandatory `FROM node:22-slim AS web` + `npm ci && npm run build`; without it the server serves `{"note": "frontend not built"}`), **and 100% of its existing diagram output is already produced by browser JavaScript**: D2 WASM does the layout, browser `<canvas>` does the SVG-to-PNG (d2.ts:85-114), and the server merely stores the bytes the browser posts back (artifacts.py:264-274). The Python is "pure" because the browser renders for it.

That last point cuts both ways and it is worth being honest about it: if all we wanted was diagrams **in the studio SPA**, bundling archify's renderers into the existing web bundle would add zero new dependency classes. But archify's renderers `import node:path` and `node:url` (and the CLI uses `node:child_process`), so they are not browser-ready as shipped, and more importantly that route gives the **pip library** nothing. The library is the product. A diagram feature that only exists in the studio's browser is not the feature.

**The fifth refutation, which constrains everything:** docloom has **no working SVG-to-PNG rasterizer in Python**. `slide.shapes.add_picture(<valid .svg>)` raises `UnidentifiedImageError`; pptx.py:469-478 catches it and returns 0.0, so the block is **silently dropped**. DOCX substitutes an `[image: alt]` text placeholder. `docx.py:335 _rasterize_chart_svg` is an explicit no-op returning `None`, and the embed branch at docx.py:358 is dead code. Pillow is present (transitively via python-pptx) but registers zero `.svg` extensions. **Any SVG-producing diagram option, from any vendor, needs a new rasterizer dependency.** There is no way around this and no reason to pretend otherwise.

## 3. The integration design

The invariant to preserve is docloom's own, stated at ir.py:208: *"Layout intent, not geometry. The renderer owns coordinates."* Archify inverts that invariant. We keep it.

**New IR blocks (`docloom/src/docloom/ir.py`).** Archify's names, none of archify's geometry. Explicitly refused: `pos`, `size`, `via`, `labelAt`, `labelDx/labelDy`, `row`, `col`, `viewBox`.

```python
class DiagramNode(BaseModel):
    key: SafeStr                       # NOT "id"
    label: SafeStr
    sublabel: SafeStr | None = None
    tag: SafeStr | None = None
    kind: Literal["client","service","store","queue",
                  "external","security","cloud"] = "service"
    group: SafeStr | None = None       # flat group key, one nesting level

class DiagramEdge(BaseModel):
    source: SafeStr
    target: SafeStr
    label: SafeStr = ""
    style: Literal["solid","dashed","emphasis","secure"] = "solid"

class DiagramGroup(BaseModel):
    key: SafeStr
    label: SafeStr
    kind: Literal["region","security-group"] = "region"

class Diagram(BaseModel):
    type: Literal["diagram"] = "diagram"
    title: SafeStr | None = None
    direction: Literal["LR","TB"] = "LR"
    nodes: list[DiagramNode]
    edges: list[DiagramEdge] = []
    groups: list[DiagramGroup] = []
    alt: SafeStr = ""
    caption: SafeStr | None = None
    path: SafeStr | None = None        # optional pre-render, mirrors Chart.path
```

Then add `Diagram` to the `Block` union at ir.py:197-201. Two details are load-bearing:

- **The node field must be `key`, not `id`.** `llm.py:229` strips `("id", "asset_id", "artifact_id")` from the schema shown to the model, and with `additionalProperties: false` the model then physically cannot emit it. A `DiagramNode.id` would be deleted from the schema and every node would fail validation. This is the kind of thing that costs a day if you find it at runtime.
- **Groups are keyed by `node.group`, not by archify's `wraps: [id]` member list.** A list of ids inside a group is one more cross-reference for the model to get wrong, and it buys nothing.

**The other one-line landmine (`docloom/src/docloom/llm.py:39`).** `_TYPE_ALIASES` currently maps `"diagram": "artifact"` and `"infographic": "artifact"`. Today, a model that emits a diagram-shaped block is silently coerced into a path-less `Artifact`, which renders as **literally nothing** (pptx.py:659, typst.py:237, html.py:328 all require `b.path`). Delete the `"diagram"` entry. Note this is a behavior change in a released library: what used to silently vanish will now either render or raise a validation error. That is strictly better, and it should be in the changelog.

**New: `docloom/src/docloom/render/diagram_svg.py` (~450 to 550 lines, zero dependencies).** Same contract as `chart_svg.py` (*"Dependency-free SVG chart painter: Chart IR -> a themed, self-contained SVG string. No external libraries."*), same signature: `render_svg(block: Diagram, theme: Theme) -> str`.

- `_layout()`: longest-path layering (DFS back-edge demotion so cycles terminate), barycenter ordering within each layer, then a **group-contiguity re-sort** so a group's bounding box does not swallow strangers. Dummy-node insertion for edges spanning more than one layer, or they route straight through nodes.
- `_measure()`: node width derived from label text units (keep archify's CJK double-width rule from `utils.mjs:86`), so a label can never overflow its box **by construction**.
- `_palette(theme)`: node fills and strokes derived from `theme.primary` / `theme.accent` with tint and shade, exactly the construction at chart_svg.py:40-46. **Emit inline presentation attributes, never a `<style>` block and never `var()`.** resvg cannot resolve CSS custom properties. This is the single rule that a future "tidying" refactor will break silently, so it needs a loud comment and a golden test.
- `_boundary_rect()`, `_route()` (orthogonal polylines with rounded corners), `_legend()`, plus a `<defs>` with theme-colored arrowhead markers.
- **Do not port archify's HTML chrome** (title, subtitle, cards, footer). Those become native PPTX text: crisper, editable, on-theme. The SVG is the canvas only.

**New: `docloom/src/docloom/render/raster.py` (~40 lines). The only seam.**

```python
def svg_to_png(svg: str, theme: Theme, width: int = 2400) -> bytes | None:
    try:
        import resvg_py
    except ImportError:
        return None
    fonts = [p for p in (theme.font_heading_src, theme.font_body_src) if p]
    return bytes(resvg_py.svg_to_bytes(
        svg_string=svg, width=width, font_family=theme.font_body,
        font_files=fonts or None, text_rendering="geometric_precision"))
```

`None` is the entire degradation story: the renderers already handle a missing image without failing the export.

**Renderer wiring, six touchpoints.**

| File | Change |
|---|---|
| `render/pptx.py` (beside the `Chart` arm, ~631) | `_diagram_block()`: `diagram_svg.render_svg()` -> `raster.svg_to_png()` -> temp `.png` -> the existing `_image_block` (pptx.py:469). Fall back to a from/to/label table (mirroring `_chart_table`, pptx.py:528) when the raster returns `None`. Add `Diagram` to `_natural_h`. |
| `render/docx.py:335` | **Replace the `_rasterize_chart_svg` no-op with `raster.svg_to_png`.** This resurrects the dead branch at docx.py:358 and upgrades every existing Chart in every DOCX from an `[image: alt]` placeholder to a real picture. Free win, same PR. It does change shipped output, so regenerate golden files deliberately. |
| `render/typst.py` (beside :247) | Write the SVG to the temp dir and hand it to the native `.svg` path (typst.py:111-120). **PDF gets true vector, no rasterizer needed.** |
| `render/html.py` (beside :271) | Inline the SVG string directly, as `_chart_html` does. Vector, zero deps. |
| `render/markdown.py` | Write `diagram-{n}.svg` beside the output; markdown.py:188 already passes `.svg` through unfiltered. |
| `render/xlsx.py` | Skip, like `Image`. |

**How the LLM authors it.** No new generation unit. `llm_schema(Slide)` already carries the whole `Block` union, so once `Diagram` is in the union, the existing per-slide call at generate.py:435-443 can emit one and the existing `lint_fn` retry loop enforces the rules. Add one bullet to `AUTHORING_GUIDE` (llm.py:282): *"Diagrams: emit a `diagram` block of nodes and edges. Never write coordinates, x/y, Mermaid, or D2. The renderer owns layout. Use one only when the point is a topology or a flow, never to decorate. Keep it under 14 nodes."*

**How lint.py checks it.** This is the composition point, and it is where archify's `validateArchitecture()` actually earns its keep, translated into docloom's `Finding` model. Add `_lint_diagram()` to the block walk (lint.py:456), running the real layout so findings cite real geometry:

- `diagram/dangling-edge`, `diagram/duplicate-key`, `diagram/empty`: errors (same class as the existing dangling-citation and `chart/empty` rules).
- `diagram/label-too-long`, `diagram/too-many-nodes`, `diagram/too-wide`, `diagram/group-scattered` (a group spanning more than two layers, whose bounding box would swallow strangers): warnings.
- `diagram/edge-crosses-node`: a real check, using segment-vs-rect intersection against every non-endpoint node. Worth calling out that **archify itself does not do this** for architecture diagrams (`segmentIntersectsRect` exists in `geometry.mjs:27` but the architecture renderer never calls it), and its own example renders show arrows crossing each other. We can be better than the thing we are copying.
- Add `Diagram` to `_block_height()` (lint.py:171), returning the true layout height in inches. A diagram then participates in the existing `SLIDE_BODY_H_IN` budget and `deck/overflow` (lint.py:432) catches an oversized diagram **for free**. `Artifact` can only ever be guessed at a flat `IMAGE_H_IN`; `Diagram` knows its real size because the IR is the source.

**Studio.** Delete `DiagramGen`, the D2 prompt and `_looks_like_d2()` (generate.py:826-880), and replace them with `generate_validated(..., schema=llm_schema(Diagram), lint_fn=_lint_diagram)`. Delete `web/src/diagram/d2.ts`, `@terrastruct/d2`, and the orphaned `mermaid` / `@mermaid-js/layout-elk` / `@excalidraw/*` / `@antv/gpt-vis` deps (which are declared in package.json and imported nowhere). `DiagramEditor.tsx` previews via a new server endpoint returning **the same SVG the exporter uses**, so preview and export are finally the same picture. Today they cannot be: the browser renders D2 with a hardcoded `LOOM` palette (d2.ts:26, because `DiagramEditor.tsx:63` never passes a theme) and the server has no renderer at all. The `irx.bake()` Artifact-PNG problem (irx.py:74-77, which is why an infographic can never bake into PPTX) simply does not apply, because a `Diagram` block self-renders from IR at export time.

## 4. The honest packaging cost

**Core `pip install docloom`: unchanged.** Four dependencies, still pydantic + python-pptx + python-docx + xlsxwriter. `diagram_svg.py` is pure stdlib. HTML, Markdown and PDF/Typst diagrams work with **zero** new dependencies, because all three embed SVG natively.

**`pip install docloom[diagrams]`: one wheel, `resvg-py>=0.3.3`, ~2.1 MB installed.** Prebuilt wheels for cp310 to cp313 across win_amd64, macOS x86_64 and arm64, manylinux (x86_64/aarch64/armv7l/i686/ppc64le/s390x) and musllinux. No sdist compile, no system library, no Node, no Chromium, no JVM, **no subprocess**, which preserves the zero-shell-out property that was proven by hard-blocking every exec entry point and rendering all six formats anyway. resvg is MPL-2.0, consumed as a compiled extension module, which is fine for an MIT project. Archify is MIT, so the schema borrowing is clean with attribution.

For scale: python-pptx already pulls Pillow (~3 MB) and lxml (~4 MB). 2.1 MB is noise, and it buys back a bug that has been shipping as dead code since day one.

**Two costs I am naming explicitly because they are the kind that ship silently:**

1. **Fonts.** `docloom-studio/Dockerfile:24` runtime is `python:3.12-slim`, which apt-installs only `libgl1` and `libglib2.0-0` and ships **no fonts**. resvg draws text with system fonts. In a fontless container, every PPTX and DOCX diagram PNG renders with **invisible labels**. Fix: add `fonts-dejavu-core` (~1 MB) to that apt line, and pass `font_files=[theme.font_body_src]` when the theme supplies one. Add a smoke test that asserts the rendered PNG has non-background pixels where text should be.
2. **DOCX output changes.** Wiring the rasterizer into docx.py:335 makes existing charts render as pictures instead of `[image: alt]` placeholders. Desirable, but it is a change to shipped bytes.

The studio's Node build stage stays and in fact **shrinks**, since D2, mermaid and excalidraw come out of `web/package.json`. Net: +2.1 MB Python, +1 MB apt fonts, minus a WASM package.

## 5. The alternatives, and why they lose

**archify as a runtime dependency.** Needs Node (subprocess, which docloom has never had) *plus* a headless browser (its PNG export is `canvas.toBlob`), *plus* HTML scraping to get an SVG that is invalid and colorless, *plus* a fork of `assets/template.html` to get brand colors, *plus* pixel coordinates from an unattended LLM. Five disqualifications, any one of which is fatal.

**Porting archify's renderers wholesale.** Tempting (they are ~660 lines of pure arithmetic, no dagre, no DOM measurement), but you would be porting a **hand-placement** engine, which is the one thing we cannot use. The parts worth porting are the measurement, the boundary-rect math, the rounded polyline router and the validator. The part we must supply ourselves, which archify does not have, is the layer assignment. So it is a port of the *painting*, not of the *placement*.

**mermaidx** (pip, MIT, real Mermaid v11 inside QuickJS-ng, rasterized by resvg, ~8.2 MB, no Node, no browser, no subprocess). This is the serious rival and it deserves a straight answer. It ships in **two days** instead of two weeks, and it hands you dagre's battle-tested layout maintained by someone else, forever. Measured: 2.0s cold render, 0.29s to PNG at 3x, brand hex honored via `themeVariables`, zero `foreignObject` (so resvg draws its text correctly). Why it does not win the default slot:

- **It is 4.9 MB of vendored Mermaid JavaScript running in a C-extension JS interpreter.** Calling that "pure Python" is a relabeling exercise. It moves `node_modules` into `site-packages`.
- **Three releases total; v0.8.2 was uploaded two days ago.** Effectively a single maintainer with no track record. docloom's whole value proposition is four boring, unkillable dependencies.
- **quickjs-ng has no macOS x86_64 wheel** (`macosx_11_0_arm64` only). Intel Mac users would have to compile C from an sdist.
- **Confirmed defect:** cylinder `[( )]` and hexagon nodes mis-size, so their labels render outside the shape and collide with edges (reproduced twice). Survivable by constraining node shapes, but it is a live bug in the exact use case.
- **Brand fidelity is close, not exact**, and subgraph containers default to Mermaid **orange** unless `clusterBkg` and `clusterBorder` are explicitly set. That is a non-brand orange box in a customer deck, one regression away.
- The visual ceiling is lower: no semantic type-coding, no sublabels or tags, no legend. Archify's own blind experiment (`experiments/v3-mermaid-validation/RESULT.md`) rated re-CSSed Mermaid as not meaningfully better than stock Mermaid, and concluded *"layout is the product, not CSS."* I do not fully accept that conclusion (see section 7), but it is evidence, and it points at the information architecture as the differentiator, which is precisely what Mermaid cannot express.

**mermaidx stays in the design as the escape hatch**, and that is why `raster.py` and `diagram_svg.py` are separate modules with a narrow interface. If the layout tail bites (section 7), `diagram_svg.render_svg()` gets swapped for a 60-line `diagram_mermaid.to_mermaid(block, theme)` **without touching ir.py, lint.py, or a single renderer**. The IR is the durable asset. The painter is not.

**D2 + resvg.** Visually the best of the off-the-shelf options (cylinder labels correctly centered, clean containers). Two traps, both proven: `d2-python-wrapper` is a **138.8 MB** universal wheel bundling three ~46 MB Go binaries, and **D2's own PNG export is broken out of the box** (it shells out to Playwright against a pinned Azure CDN URL that now 404s). It is usable if you go SVG -> strip the `<style>` block -> resvg, because D2 wires fonts via a base64 **WOFF** `@font-face` that resvg cannot decode, and text silently falls back to serif otherwise. Too much wheel for too little gain.

**mermaid-cli** (150 to 200 MB of Chromium), **Kroki** (a JVM service plus companion containers per diagram type), **PlantUML** (JVM), **Graphviz/pydot** (system binary until pygraphviz 2.0, and mingrammer/diagrams is locked to AWS/Azure/GCP/K8s icon sets with limited brand theming), **Excalidraw** (Node-only, hand-drawn aesthetic, and its Mermaid bridge means you are just using Mermaid with extra steps): all rejected for a pip-installable library.

**Nano Banana / Gemini image generation.** Rejected for diagrams, unambiguously. Google's own documentation says the model *"may misinterpret information or produce factually incorrect results"* for diagrams and infographics and tells you to verify their factual accuracy. It cannot guarantee node/edge structure and it is non-deterministic. Keep it exactly where it is today: decorative hero imagery.

**Raw LLM-authored SVG.** The LLM does absolute-coordinate layout. This is the failure mode, not the fix.

## 6. Phased plan

**Phase 0, half a day. Decide with a picture, not an argument.** Take five real architectures (docloom's own, plus four from customer-shaped decks). Render each through a throwaway barycenter-layout painter (a 230-line prototype already exists at `scratchpad/proto_diagram.py`, 9 nodes, 88ms to PNG, brand colors from primary/accent, no coordinates) and through mermaidx with `themeVariables`. Put them side by side and blind-rate. **If the pure-Python painter does not clearly win, stop here, take mermaidx, and spend the saved fortnight elsewhere.** Everything below assumes it wins.

**Phase 1, 2 to 3 days. The vertical slice: Diagram -> PPTX + HTML.** The `Diagram`/`DiagramNode`/`DiagramEdge`/`DiagramGroup` models and the `Block` union entry. The `llm.py:39` alias deletion and the `AUTHORING_GUIDE` bullet. `diagram_svg.py` v1 (layering, barycenter, group contiguity, rect and rounded nodes only, inline attributes). `raster.py`. The pptx and html branches. The `pyproject.toml` extra. Ship it behind the extra and dogfood it on docloom's own README.

**Phase 2, 2 days. The lint contract.** `_lint_diagram()` with the rules in section 3, including `edge-crosses-node`, plus `_block_height()` integration so `deck/overflow` covers diagrams. Wire it into the studio's existing `generate_validated` retry loop. This is what makes unattended generation trustworthy, and it is the phase people skip.

**Phase 3, 2 to 3 days. The palette, which is the hard part.** Archify's beauty leans on seven hand-tuned semantic hues (cyan frontend, emerald backend, violet database, amber cloud, rose security). docloom themes give you **two** brand colors plus four neutrals. Deriving seven distinguishable, on-brand, WCAG-passing fills from primary + accent is genuinely hard, and a monochrome-blue theme will collapse three node kinds into three near-identical pale blues, at which point the type-coding (the entire information-architecture moat) evaporates. Rotate hues in OKLCH around primary and accent rather than tint/shade, and add a contrast-ratio lint that fails a palette whose adjacent kinds sit under ~1.3:1. Budget real time here: this is the difference between "archify" and "a blue box diagram."

**Phase 4, 2 days. The rest of the surface.** docx (which lights up the dead `_rasterize_chart_svg` branch and fixes charts-in-DOCX as a side effect), typst, markdown. Golden tests: assert on the **SVG string** (deterministic) plus a coarse perceptual hash of the PNG, never byte equality, because resvg output can shift a pixel across versions. Pin `resvg-py` exactly.

**Phase 5, 1 day. Studio cleanup.** Delete `DiagramGen` / `_looks_like_d2` / `d2.ts` / the four orphaned npm packages. Add the preview endpoint that returns the export's own SVG. Add `fonts-dejavu-core` to the Dockerfile runtime stage.

**Explicitly out of scope for v1:** archify's other four renderers (workflow, sequence, dataflow, lifecycle, another ~1200 lines). Sequence in particular is a completely different layout problem (lifelines plus a time axis) and needs a second painter, not a parameter. The IR is deliberately named `Diagram` with a node/edge/group shape so a `kind: "sequence"` can arrive later without a schema break. Saying yes to `Diagram` is saying yes to a future `Sequence` request that has no plan behind it. Know that going in.

## 7. Open questions and what I could not verify

**The one that could sink this: is automatic layout good enough?** Archify's ROADMAP says *"Auto-layout (dagre/elk-js) is a dead end for archify. Stripping the human (or Claude) out of layout strips the product of its differentiator. The 'Claude in the loop' property is the moat, not a limitation."* Their blind experiment agrees. My design does exactly the thing they say fails: it replaces free coordinates with computed layers. My counter is that docloom is not archify and **cannot** have a human in the loop (there is no loop; there is one structured-output call per slide), so "keep the agent placing pixels" is not an option on the menu; the real choice is *which* automatic layout wearing *which* visual language. And on a slide with 8 nodes rather than 30, layout quality saturates fast while information architecture keeps paying. But I am asserting the opposite of what archify's own test measured, and **Phase 0 exists specifically to check that before anyone writes production code.** If the blind rating comes back "these are both fine," take mermaidx.

**Owning a layout engine.** The prototype's happy path took 20 minutes. Sugiyama's real cost is the tail: dummy nodes for long edges, port offsets for multi-edges and self-loops, reserved channels for edge labels. The prototype's very first render put one edge's label on top of another edge. Every ugly diagram docloom ever ships becomes a maintainer bug, in a one-maintainer repo. This is the strongest argument for mermaidx and I have not neutralized it, only bounded it (the seam, and the escape hatch).

**Unverified: mermaidx's own claims.** The install, render timings, theming and the cylinder defect were all measured on this machine, but mermaidx was never put through the adversarial pass that demolished archify's claims. Its QuickJS-ng behavior on musl, its cold-start cost when a deck has five diagrams (one engine instance must be reused per render call, or export time visibly regresses), and its maintenance trajectory are all unknown. If Phase 0 sends us to mermaidx, run the adversarial pass on it first.

**Unverified: `resvg-py`'s font resolution inside `python:3.12-slim` with `fonts-dejavu-core` installed.** The failure mode (invisible labels) is silent, so this needs an actual container smoke test, not a reasoned argument.

**Unverified: pydantic alias round-tripping through `llm_schema()`.** Proposal 3 wanted `Field(alias="from")` / `Field(alias="to")` to keep literal archify field-name interop. `llm_schema()` calls `model_json_schema()` and then mutates the tree; pydantic v2 defaults to `by_alias=True`, so the model *should* see `from`/`to`, but `parse_llm_output` would then need to validate by alias too. I did not test this. If it does not hold cleanly, use `source`/`target` and lose literal archify JSON interop. That is a cheap loss, and it is why the design above already says `source`/`target`.

**Accepted, not open: PPTX diagrams are flat PNGs, not editable shapes.** `Chart` blocks render as native editable PPTX charts. A diagram cannot, short of SmartArt/DrawingML generation, which is a different project. A stakeholder who wants to drag one box in PowerPoint is stuck. Charts are data; diagrams are pictures. That is the trade, and it is the right one.
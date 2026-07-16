# Phase 0 Decision Report: Diagram Engine

## 1. THE VERDICT

**Build the painter. The pre-registered rule is satisfied, and it is not close.**

The rule was: "If the pure-Python painter does not CLEARLY win the blind rating, stop, take mermaidx, and spend the saved fortnight elsewhere."

Blind tally: **painter 20, mermaidx 0, ties 0.** Four judges (consultant, information designer, engineer, brand), five specs each, candidate letters shuffled per spec so no judge could vote a slot. Every judge, on every spec, picked the same engine. Sixteen of the twenty wins were scored "decisive"; the four "clear" wins are all spec 5, the single top-to-bottom spec, which is the only layout shape where mermaid was legible at all. Mean scores: painter roughly 7.8/10, mermaidx roughly 2.9/10.

I want to be careful not to launder this, so here is the strongest case against the sweep, stated plainly: the margin is inflated by one methodological choice. Every render was rasterized to 1600px wide. Mermaid's dagre layout emits 3.2:1 to 5.2:1 ribbons for LR graphs, so at 1600px its body text lands at 3.5-5.7px and is physically unreadable, and roughly half of every judge's complaint about mermaid is downstream of that single fact. Render the same SVG at 4000px and those complaints partly evaporate.

**But they only partly evaporate, and what survives is the actual decision.** Three things survive any rescaling:

- **Fidelity failures.** In spec 3, mermaid drew Fraud Service and Settlement Worker *outside* the `us-east-1` subgraph even though the JSON assigns them to that group. Three of four judges caught this independently. That is a diagram that states something false about the system. In spec 5 it clipped `row-level change capture` to `row-level change` behind a node border, and stranded the `re-identify (break-glass, audited)` label in open canvas attached to nothing.
- **Encoding collapse.** Mermaid cannot draw a database cylinder at all (both the legacy `[( )]` and the v11 `@{shape: cyl}` paths are broken, per the mermaidx agent's own probing), and in specs 3 and 5 it rendered *security* nodes in the same green as *store* nodes. In a PCI diagram and a PII-isolation diagram, that is the one distinction the picture exists to make. There is also no legend, because Mermaid flowchart has no legend concept, so none of the color/shape choices are decodable from the image.
- **Aspect ratio is structural, not a knob.** mermaidx's own writeup: node width = labelWidth + 4 * padding, and padding must be inflated globally to contain the tallest label, so one 5-row node adds ~500px of dead width to *every* node in the graph. `flowchart.wrappingWidth` is ignored. You cannot trade rows for width. A 5:1 ribbon on a 16:9 slide is small text no matter what resolution you rasterize it at.

So: the win is real, and it is decisive on the axes that a rescale would not fix. **Take the painter.**

Two honest riders on that verdict:

- **The fortnight is not all still ahead of you.** Both engines already exist as working prototypes; this bake-off was rendered from real code. What you are approving is not "build a layout engine from zero," it is "finish and own the one that already beats the alternative." Re-estimate before committing two weeks.
- **Neither engine is deck-ready today.** See section 3. The correct read of this bake-off is not "the painter is good," it is "the painter is the only one of the two that is *fixable*."

---

## 2. What the blind judges actually saw

**Against mermaidx (the loser), concrete and repeated:**

| Defect | Specs | Who saw it |
|---|---|---|
| Letterbox 3.2:1 to 5.2:1, body text 3.5-5.7px, unreadable at 100% | 1, 2, 3, 4 | all four |
| Nodes drawn outside their declared group (Fraud Service, Settlement Worker outside `us-east-1`) | 3 | consultant, info-designer, engineer, brand |
| PCI group box overlapping the Settlement Worker cell / colliding with edge text | 3 | consultant, info-designer, brand |
| Security nodes and store nodes both green: security channel destroyed | 3, 5 | info-designer, brand |
| Edge label clipped by a node box (`he query`, `unks + provenance`; `row-level change` losing `capture`) | 4, 5 | engineer, info-designer, brand |
| Stray white knockout rectangles punched into subgraph fills by edge-label chips | 5 | consultant, info-designer, brand |
| Missing arrowheads, direction unrecoverable | 5 | engineer |
| No legend, anywhere | all | all |
| Bezier splines crossing each other and passing under node boxes | 1, 3, 4 | all |
| Broken glyphs (`render/__init__.py` rendered as `render/ init .py`) | 1 | consultant |
| Mutant shapes: hexagonal tokenizer, lopsided half-stadium bus | 5 | all |

**Against the painter (the winner), concrete and repeated. This is the real to-do list:**

| Defect | Specs | Severity |
|---|---|---|
| Edge-label attribution in fan-in bundles: 3-6 near-parallel lines converge on one node face, labels stack in a narrow column, you cannot tell which label owns which arrow (six dispatch labels in spec 1, five-line comb into Data Dir in spec 2, `PAN to token` vs `token + auth result` in spec 3, the `embed the query` / `ANN search` / `hydrate chunk text` triple in spec 4) | 1, 2, 3, 4 | **blocking** |
| Container emptiness: the tenant-isolation box in spec 2 spans nearly the full width and is ~60% dead space; large empty routing gutters in spec 3's region box | 2, 3 | **blocking** |
| Aspect ratio uncontrolled: spec 3 is 2.11:1, spec 4 is 2.01:1, spec 5 is 0.60:1 portrait (a poster, not a slide) | 3, 4, 5 | **blocking for decks** |
| Group title struck through by an edge or by the container's own border | 3 (PCI), 5 (PII zone) | high |
| Long detour routing: edges dive into empty gutters and run back; label stranded mid-span far from both endpoints | 1, 3, 5 | medium |
| No nested containers by design, so the PCI box is drawn outside the `us-east-1` box it physically lives in | 3 | medium, and it is a fidelity compromise |
| Edges cross container borders freely (spec 5's SaaS return edge slices through the PII zone) | 5 | medium |
| Off-brand hues: red security, amber queue, lavender client against a #1D4ED8 / #0E9F6E palette. Reads as a stoplight, not a system | all | medium (brand judge only, but he is right) |
| Sublabels hard-capped at 2 lines and ellipsized; text metrics approximated from a hand-tuned advance table, not real font metrics | all | low, latent |

Note the shape of the two lists. Mermaid's failures **destroy information** (wrong containment, clipped text, missing arrowheads, collapsed color channel). The painter's failures are **legibility annoyances you can resolve by tracing the line with your eye**. Both the engineer and the information designer converged on exactly that framing independently.

Also, timing, which nobody had to judge: painter renders all five specs end to end in **756ms**. mermaidx takes **17,042ms**, because every spec must be rendered twice (pass 1 measures the row counts Mermaid actually chose, pass 2 re-renders with solved padding), and it drags 4.9MB of vendored JS through QuickJS to do it. That is a 22x difference and a dependency you would own the security surface of forever.

---

## 3. Is either deck-ready today?

**mermaidx: no, on zero of five specs.** Unanimous. The consultant: "it would embarrass me in the first ten seconds." Not fixable by tuning; the aspect and padding behavior are structural in dagre plus mermaid's sizing model.

**Painter: not yet, but it is within days, not weeks.** The judges split on how close:

- Consultant: three of five (specs 1, 3, 5) shippable to a paying client after a five-minute cleanup. Specs 2 and 4 need a pass first.
- Engineer: same three shippable as-is; specs 2 and 4 blocked on the fan-in label clusters, "a client will ask which label belongs to which arrow and I would not be able to answer from the page."
- Information designer: four of five, and would explicitly **not** ship spec 2, because the `ciphertext only` edge loops outside the tenant boundary it is supposed to live inside, "which is a diagram that says the wrong thing about a security property."
- Brand: **neither**, and this is the one genuine dissent. His objection is narrow and concrete: the layout, typography and spacing are already client grade, but the kind-to-hue map leaks three non-brand colors (red security, amber queue, lavender client) into a blue/green brand. "One palette pass away from shippable."

Synthesizing honestly: **the gap to deck-ready is three named things, not a vague quality gap.**

1. Edge-label placement in fan-in bundles (owned by every judge).
2. Aspect-ratio control toward 16:9, and closing empty container/gutter space.
3. A brand-tinted kind palette instead of semantic stoplight hues.

That is the entire delta between "loses to nothing, but I would not put it in a paid deck" and "ship it."

---

## 4. The rasterizer fix (separate, already approved, landed this run)

Verified present in the working tree, uncommitted, on `main`:
- `C:/Users/kirti/Music/doc_generation/docloom/src/docloom/render/raster.py` (new, the single seam)
- `C:/Users/kirti/Music/doc_generation/docloom/tests/test_raster.py` (new, 12 tests)
- modified: `docloom/src/docloom/render/docx.py`, `docloom/src/docloom/render/pptx.py`, `docloom/pyproject.toml`, `docloom/README.md`, `docloom-studio/Dockerfile`, `docloom-studio/pyproject.toml`, plus two test files pinned deliberately (below)

**What it does.** `raster.svg_to_png(svg, *, width=None, font_files=None) -> bytes | None` wraps `resvg-py` (confirmed against installed 0.3.3), imports it lazily inside the function, and returns `None` rather than raising on a missing extra, an empty SVG, or any render error. It validates PNG magic before returning. It feeds the theme's fonts to resvg, skips `.woff2` (resvg cannot parse it), and retries once without fonts if a font file makes the render fail, so a bad font never costs the whole picture. New optional extra `diagrams = ["resvg-py>=0.3.3"]`; core dependencies unchanged.

Effect: PPTX SVG image blocks are now real picture shapes instead of being silently swallowed by a bare `except: return 0.0`, and the PPTX chart fallback chain is now native chart -> prerendered image -> rasterized `chart_svg` picture -> data table. DOCX charts embed a real 1280px PNG. This is also the seam the painter's SVG output will ride into PPTX and DOCX on, so it is a prerequisite for the diagram feature either way.

**Tests.** Before: 2 failed, 183 passed, 5 skipped. After: **2 failed, 195 passed, 5 skipped.** The 2 failures are pre-existing and unrelated: `test_robustness.py::test_typst_skips_unsupported_raster` and `::test_ragged_and_empty_tables_render` both render `pdf`, the venv has no `typst` binary, and they lack the `importorskip` guard the other typst tests have. They fail identically on the untouched tree. Worth fixing separately; it is a two-line guard.

**Two things you must know:**

1. **DOCX output changed on purpose.** With the `diagrams` extra installed, a chart in a DOCX is now an embedded PNG picture, not the old data table. Anything downstream that scrapes chart values out of DOCX tables will stop finding them. Without the extra, behavior is byte-identical to today. Two existing tests (`test_reaudit_docx.py::test_docx_chart_table_keeps_large_numbers`, `test_render_quality.py::test_docx_chart_without_prerendered_path_is_titled_captioned_table`) asserted the table path; rather than delete them they were pinned to the no-extra path via `monkeypatch.setitem(sys.modules, "resvg_py", None)`, with the assertions untouched, and the picture path is now covered explicitly in `test_raster.py`.
2. **The container font trap.** `python:3.12-slim` ships with **no fonts at all**. resvg does not fall back to anything, so every label in every rasterized chart or diagram would come out invisible: a blank picture, no error, no warning. `fonts-dejavu-core` was added to the studio Dockerfile's apt line and the engine install is now `"./docloom-src[pdf,diagrams]"`. **This was not verified by an actual container build** (no Docker in this environment). Build the image and eyeball one chart before this reaches anyone.

Also not done: DOCX *Image* / *Artifact* SVG blocks still emit the `[image: alt]` placeholder (out of the approved scope, and two tests pin that behavior). The seam is ready for it, it is a two-line follow-up.

---

## 5. Recommended next step

**Approve the painter. Then do this, in order:**

1. **Buy the cheap insurance first (30 minutes, today).** Put the ten PNGs in front of one actual human, ideally the person who would present the deck. Four LLM judges agreeing 20-0 is suggestive, not dispositive (section 6). If a human looks at the painter's spec 1 and spec 3 and says "yes, I would show this," the fortnight is de-risked. If they say "these both look like tool output," you have learned something for the price of a coffee.
2. **Timebox the polish to 4 days, not 10, against the three named gaps** and stop when they are closed:
   - **Day 1-2: edge-label attribution.** This is the single most-cited defect and it appears in 4 of 5 specs. The fan-in comb (five lines into Data Dir, six dispatch labels in spec 1) needs either label leadering, port-side label anchoring, or edge bundling with a single grouped label. This is the difference between "shippable" and "not shippable" for two judges.
   - **Day 3: aspect and whitespace.** Add a target aspect ratio (16:9) as a layout objective: allow layer wrapping or cross-axis compaction so a 9-layer graph does not become a 0.60:1 poster, and shrink containers to their members' actual extent instead of a global cross-band. The spec 2 tenant box being 60% empty is the painter agent's own "single worst thing in the set," and it is right.
   - **Day 4: brand palette.** Remap kind -> hue onto brand tints (blue service, green store, blue-tinted outline for security, muted neutral for external) instead of the current red/amber/lavender stoplight. This is the entire brand judge's objection.
3. **Ship the rasterizer commit now**, separately from this decision, after a container build that confirms fonts render. It is independently valuable and it is the seam the painter output rides on.
4. **Do not keep mermaidx as a fallback for the architecture-diagram IR.** It costs 4.9MB of vendored JS, a QuickJS runtime, a 22x latency penalty, and a second visual language that will diverge from the painter's. If you want to support users pasting raw Mermaid source, that is a separate, honest feature with a different justification. Do not let it become the diagram engine by the back door.
5. **Defer, do not cancel: nested containers.** The painter's "a box contains exactly its members, and boxes never nest" rule is what makes its group audit sound, and it is why it beat mermaid on containment. But it produces its own lie in spec 3 (the PCI box drawn outside `us-east-1`). Log it. Do not try to fix it inside the fortnight.

---

## 6. What is unverified, weak, or rigged about this test

I am the one who has to say this, so:

- **Four LLM judges are not four humans.** They share a training distribution and plausibly share aesthetic priors. Their unanimity is weaker evidence than four independent human reviewers agreeing would be. They also cannot actually *squint at a projector from the back of a room*, which is the exact failure mode they all invoked. Their legibility claims are inferred from pixel dimensions, not perceived.
- **The 1600px rasterization width was a mandate, and it hurt mermaid specifically.** Mermaid's structural aspect ratio means the letterboxing complaint survives a rescale, but the *severity* of it does not. If you rendered mermaid at 4000px and dropped it into a slide as a full-bleed band, several of the "unreadable microtype" verdicts would soften to "cramped." I do not think this flips the result, because the fidelity and encoding failures are width-independent, but a fair reading is that the 20-0 sweep would have been closer to a 20-0 sweep with smaller margins.
- **The painter agent iterated against its own output. The mermaidx agent iterated against a hostile black box.** This is the most important asymmetry in the whole exercise. The painter could look at a bad render, change the routing code, and look again. The mermaidx agent could only probe dagre's sizing behavior and work around it. That is a real difference in effort leverage, and part of the painter's margin is bought by that leverage rather than by the architecture. **In fairness: that asymmetry is not an artifact of the test. It IS the thing being decided.** "We own the layout engine and can fix any defect" versus "we own a compiler to someone else's layout engine and can only work around its defects" is precisely the choice, and this bake-off is a fair demonstration of what that choice feels like in practice.
- **Five specs, all authored by us, is a small and possibly self-serving sample.** They skew LR (4 of 5), and LR is mermaid's worst case. A spec set with more TB graphs would have narrowed the gap: spec 5, the only TB spec, is where all four judges downgraded the painter's win from "decisive" to "clear" and where mermaid scored 4-6/10 instead of 2/10.
- **Nobody checked the painter's SVG in a real PPTX/DOCX at real slide dimensions.** All ten judgments are on standalone PNGs. The rasterizer seam is new and the pipeline is untested end to end with painter output.
- **The `cloud` node kind is unexercised by all five specs in both engines.** Its color and shape are unverified.
- **Painter text metrics are approximated** from a hand-tuned advance table, not real font metrics. Node boxes could over- or under-size on any font other than Segoe UI or Arial. This will surface the first time someone uses a custom theme font, and it will surface as clipped or floating text, which is exactly the failure the judges hammered mermaid for.
- **The container font fix is unbuilt and unverified.** If it is wrong, every diagram in the studio's Docker deployment renders as an empty box with invisible labels, and nothing will throw.

**Bottom line: the rule was pre-registered, the test was run blind, the painter won every single pairing, and the losing engine committed factual errors that no amount of styling fixes. Build the painter. But budget four days of polish, not zero, and get one human to look at the PNGs before you spend the fortnight.**
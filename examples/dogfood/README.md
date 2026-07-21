# Dogfood samples — docloom explaining docloom

Every file here was produced by docloom's **own** pipeline. A language model was
given the project's [`PROJECT.md`](../../PROJECT.md) as its only source and
authored the validated IR (the `*.json` files); docloom's deterministic
renderers then produced everything in [`output/`](output/). This is the real
output the system generates — not a hand-tuned mock-up.

Regenerate the rendered files:

```bash
python examples/dogfood/render_samples.py   # -> examples/dogfood/output/
```

## What's here

| Source IR | Kind | Rendered output |
| --- | --- | --- |
| [`deck.json`](deck.json) | Presentation | [`deck.pptx`](output/deck.pptx), [`deck.pdf`](output/deck.pdf) |
| [`whitepaper.json`](whitepaper.json) | Report (95 blocks) | [`.pdf`](output/whitepaper.pdf), [`.docx`](output/whitepaper.docx), [`.html`](output/whitepaper.html), [`.md`](output/whitepaper.md) |
| [`infographic.json`](infographic.json) | Stat-forward deck | [`infographic.pptx`](output/infographic.pptx), [`.pdf`](output/infographic.pdf) |
| [`workbook.json`](workbook.json) | Spreadsheet | [`workbook.xlsx`](output/workbook.xlsx) |
| [`architecture.json`](architecture.json) | Diagram (clean pipeline) | [`.svg`](output/architecture.svg), [`.png`](output/architecture.png), [`.drawio`](output/architecture.drawio) |
| [`architecture-full.json`](architecture-full.json) | Diagram (full branching) | [`.svg`](output/architecture-full.svg), [`.png`](output/architecture-full.png), [`.drawio`](output/architecture-full.drawio) |

All rendered with the studio's **aurora** theme (inlined in
[`render_samples.py`](render_samples.py) so the example is self-contained).

## The two architecture diagrams

They show the same system at two levels of detail, and demonstrate docloom's two
diagram-layout backends:

- **`architecture.json`** — the clean end-to-end pipeline, laid out by the
  built-in coordinate-free solver (`layout="native"`, the default).
- **`architecture-full.json`** — the full branching architecture (auth, jobs,
  retrieval, assets, the diagram-solver branch), laid out by the optional
  **Graphviz `dot`** backend (`layout="dot"`, `pip install "docloom[dotlayout]"`),
  which keeps complex graphs compact with tight, non-overlapping group boxes.

The `.drawio` files open in [draw.io / diagrams.net](https://app.diagrams.net)
as editable diagrams (every node is a movable shape). Note the `.drawio` is a
one-way *derived* export: the IR JSON is the source of truth.

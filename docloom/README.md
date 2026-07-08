# docloom

**The document output layer for AI apps.** Your LLM emits a validated JSON schema; docloom deterministically renders it to **PPTX, DOCX, XLSX, PDF, HTML, and Markdown** — with a linter that catches broken slides *before* anyone opens the file.

```
LLM (structured output) ──► Document JSON ──► lint ──► deterministic renderers
                                  ▲             │
                                  └── findings ─┘        deck.pptx  report.pdf
                                  (self-correct)         report.docx  data.xlsx
                                                         page.html  notes.md
```

Think *Pandoc for the LLM era*: [Docling](https://github.com/docling-project/docling) and [Marker](https://github.com/datalab-to/marker) own "documents → LLM"; docloom is the reverse direction — "LLM → documents".

## Why

Every AI product eventually has to ship a file a human opens in PowerPoint, Word, or Excel. Today that means one of two bad options:

1. **Let an agent write one-off `python-pptx` scripts.** Non-deterministic, unreviewable, and nothing stops `left=Inches(14)` on a 13.3" slide — the text just silently clips off-canvas.
2. **Convert markdown with Pandoc/Marp.** Deterministic, but markdown can't express slide layouts, spreadsheet formulas, citations, or brand themes — and PPTX export is often rasterized images, not editable shapes.

docloom takes the third path:

- **A schema LLMs can actually emit.** Non-recursive, no `oneOf`, every object closed with `additionalProperties: false` — `docloom.llm_schema()` works as-is with Anthropic structured outputs and OpenAI `json_schema` mode, and the `Document` Pydantic model plugs straight into OpenAI/Anthropic `.parse()`, [instructor](https://github.com/instructor-ai/instructor), and [pydantic-ai](https://github.com/pydantic/pydantic-ai) (which handle OpenAI strict mode's all-fields-required transform). This is deliberate: recursive schemas are rejected by Anthropic, and `oneOf` (what Pydantic discriminated unions emit) is rejected by both major providers. Lists nest via a flat `level` field instead of recursion.
- **Deterministic renderers.** Same JSON in, same bytes out. Editable-native PPTX shapes (never screenshots), real DOCX styles, real XLSX formulas, PDF via [Typst](https://typst.app) compiled fully in-process (no LaTeX, no headless browser, no external binary).
- **A layout linter with machine-readable findings.** Off-canvas budgets, walls of text, oversized tables, dangling citations, WCAG contrast — returned as JSON your LLM can self-correct against in one retry.
- **Citations as a first-class primitive.** Spans carry `cite: "source-id"`; every renderer emits superscript references and a sources section. Grounded generation survives all the way into the `.docx`.
- **One theme, every format.** Semantic tokens (`primary`, `accent`, `font_heading`, …) map to native mechanisms per format — swap the theme JSON and the deck, report, workbook, and page are all on-brand.

## Install

```
pip install docloom            # pptx, docx, xlsx, html, md
pip install "docloom[pdf]"     # + PDF via bundled Typst compiler
pip install "docloom[mcp]"     # + MCP server for agents
```

## 30 seconds to a deck

```python
import anthropic
from docloom import AUTHORING_GUIDE, Document, lint, render

client = anthropic.Anthropic()
doc = client.messages.parse(
    model="claude-opus-4-8",
    max_tokens=16000,
    system=AUTHORING_GUIDE,
    messages=[{"role": "user", "content": "A 6-slide deck on why standups should be async"}],
    output_format=Document,          # docloom's Pydantic model, used directly
).parsed_output

print(lint(doc))                     # [] — or findings the model can fix
render(doc, "pptx")                  # why-standups-should-be-async.pptx
render(doc, "pdf")                   # same content, typeset by Typst
```

Any provider works — `docloom.llm_schema()` returns the raw JSON Schema for OpenAI strict mode or anything else. See [`examples/generate_with_llm.py`](examples/generate_with_llm.py) for the full lint-and-self-correct loop.

### Local models (Ollama, llama.cpp, …)

Smaller and local models are messier: some Ollama model integrations silently ignore the `format` schema, and models then wrap JSON in markdown fences, invent tag names ("bulletlist"), or add a `{"document": ...}` envelope. docloom ships a lenient parser for exactly this — strict validation, tolerant unwrapping:

```python
from docloom import AUTHORING_GUIDE, llm_schema, parse_llm_output
import json, requests

r = requests.post("http://localhost:11434/api/chat", json={
    "model": "qwen3.5:9b",
    "messages": [
        # put the schema in the prompt: don't rely on format enforcement
        {"role": "system", "content": AUTHORING_GUIDE
            + "\nReturn ONLY one JSON object matching this schema:\n"
            + json.dumps(llm_schema())},
        {"role": "user", "content": "A 5-slide deck on ..."},
    ],
    "format": llm_schema(),  # enforced where supported, harmless where not
    "stream": False, "think": False,
})
doc = parse_llm_output(r.json()["message"]["content"])
```

`parse_llm_output` strips fences/prose, unwraps envelopes, normalizes common block-tag aliases, and turns unknown tags into one clear error your retry loop can feed back — instead of a 50-line union mismatch.

No LLM required, either — the renderers are just a good multi-format document engine:

```bash
docloom render examples/quarterly_report.json -f pptx,docx,xlsx,pdf,html,md -o out/
docloom lint examples/quarterly_report.json
docloom schema        # JSON schema to paste into any structured-output call
docloom theme         # default theme JSON — edit, then pass with --theme
```

## The document model

One `Document` carries any mix of three bodies; each renderer takes what it needs:

| Field    | Renders to                       | Blocks |
|----------|----------------------------------|--------|
| `blocks` | DOCX, PDF, HTML, MD (reports)    | heading, paragraph, bullets, numbered, quote, code, table, image, callout, divider |
| `slides` | PPTX (decks)                     | layouts: `title`, `section`, `content`, `two_column`, `quote` + any blocks, speaker `notes` |
| `sheets` | XLSX (workbooks)                 | typed cells, `{"formula": "=SUM(B2:B4)"}`, number formats, column widths |

Plus `sources` — evidence records that spans cite by id. Text everywhere is either a plain string or spans (`bold`, `italic`, `code`, `link`, `cite`), so simple content stays cheap to generate.

A deck-only document still renders to PDF/DOCX/HTML (slides flatten to sections); a report with tables still renders to XLSX (tables become worksheets).

## The linter

`lint(doc, theme)` returns findings like:

```json
{"rule": "deck/overflow", "severity": "error", "where": "slides[3]",
 "message": "~1240 chars of content (budget 800); this will overflow the slide — split it"}
```

Rules cover slide overflow, bullet count/length, title length, oversized tables, empty slides, heading-level skips, unknown/unused citation sources, missing image files, and theme contrast (WCAG AA). `docloom render` refuses to render documents with lint *errors* (override with `--no-lint`); feed the JSON findings back to your model and it fixes its own deck.

## Theming

```json
{
  "primary": "#1D4ED8", "accent": "#0E9F6E",
  "background": "#FFFFFF", "surface": "#F3F4F6",
  "text": "#111827", "muted": "#6B7280",
  "font_heading": "Arial", "font_body": "Georgia"
}
```

Renderers honor tokens, never literal colors: PPTX title bars and table headers, DOCX heading styles, XLSX header fills, Typst set-rules, and CSS variables all resolve from the same eight tokens.

## Agents (MCP)

```bash
pip install "docloom[mcp]"
docloom-mcp
```

Three tools: `get_document_schema` → `lint_document` → `render_document`. An agent authors the JSON, lints, self-corrects, renders — no bespoke `python-pptx` script-writing, no off-canvas shapes, reviewable output.

```json
{ "mcpServers": { "docloom": { "command": "docloom-mcp" } } }
```

## How it compares

| | docloom | Agent skills (write python-pptx scripts) | Pandoc / Marp | python-pptx / docx / xlsxwriter |
|---|---|---|---|---|
| Deterministic | ✅ same JSON → same file | ❌ new script every run | ✅ | ✅ |
| Validated schema for LLMs | ✅ provider-safe JSON Schema | ❌ | ❌ markdown | ❌ |
| Layout linting | ✅ machine-readable | ❌ | ❌ | ❌ (off-canvas clips silently) |
| Multi-format from one source | ✅ 6 formats | ❌ per-script | ⚠️ lossy for slides/sheets | ❌ one format each |
| Editable-native PPTX | ✅ real shapes | ✅ | ⚠️ often rasterized | ✅ |
| Citations end-to-end | ✅ | ❌ | ⚠️ | ❌ |
| Embeddable library | ✅ `pip install` | ❌ needs agent runtime | ⚠️ Haskell binary | ✅ |

docloom builds *on* python-pptx/python-docx/xlsxwriter — it's the schema, layout, lint, and theming layer they don't have.

## Roadmap

- Charts (`ChartBlock` → Vega-Lite spec → embedded SVG/native charts)
- Diagrams (Mermaid/D2 with validate-and-repair loop)
- A reproducible broken-slide benchmark: docloom vs. agent-written scripts
- Google Slides/Docs export, more built-in themes, template galleries

Contributions welcome — the renderer contract is one function per format: `render(doc, theme, out_path)`.

## License

MIT

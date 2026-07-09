<!-- krt -->
# docloom

docloom turns a language model's structured output into documents. Your model emits a validated document, and deterministic renderers produce PPTX, DOCX, XLSX, PDF, HTML, and Markdown, with a linter that returns machine-readable findings the model can correct against.

![Python](https://img.shields.io/badge/python-3.10%2B-4F46E5)
![License](https://img.shields.io/badge/license-MIT-0D9488)

This repository contains two projects: [`docloom`](docloom/), the render engine (a pip-installable Python library), and [`docloom-studio`](docloom-studio/), a local-first app built on it.

## Install

docloom is not published to PyPI yet, so install the engine from this repository:

```bash
git clone https://github.com/kirti34n/docloom.git
cd docloom
pip install -e "./docloom[pdf]"      # editable install of the engine
```

Or in one line, without cloning:

```bash
pip install "docloom[pdf] @ git+https://github.com/kirti34n/docloom.git#subdirectory=docloom"
```

## Getting started

Build a document and render it. The same document renders to any format.

```python
from docloom import Document, render

doc = Document(title="Q3 review", slides=[
    {"layout": "title", "title": "Q3 review"},
    {"layout": "content", "title": "Highlights", "blocks": [
        {"type": "stats", "items": [{"label": "Revenue", "value": "$4.2M", "delta": "+24%"}]},
        {"type": "bullets", "items": [{"text": "Enterprise pipeline doubled"}]},
    ]},
])

render(doc, "pptx", "q3.pptx")
render(doc, "pdf", "q3.pdf")
```

Runnable samples for every feature, with their rendered output, are in [`examples/`](examples/).

## Rendering to formats

One document carries slides (a deck), blocks (a report), and sheets (a workbook). Each renderer takes what it needs.

| Format | Engine | Output |
| --- | --- | --- |
| PPTX | python-pptx | Native editable charts, tables, speaker notes |
| DOCX | python-docx | Styled headings, callouts, numbered citations |
| XLSX | xlsxwriter | Real formulas and number formats |
| PDF | Typst | Typeset in-process, embedded fonts |
| HTML | built-in | One self-contained file |
| Markdown | built-in | Portable text |

## Generating from a model

The document is a Pydantic model, so it doubles as a structured-output target. `llm_schema()` returns the JSON Schema for strict structured-output modes, and `parse_llm_output()` accepts output that is fenced or wrapped.

```python
from docloom import llm_schema, parse_llm_output, lint, render

# call your model with llm_schema() as the response format, then:
doc = parse_llm_output(model_output)   # tolerant of fenced or wrapped JSON
findings = lint(doc)                    # [] or machine-readable findings
render(doc, "pptx", "deck.pptx")
```

The schema is non-recursive and uses plain tagged unions, so it validates under both OpenAI strict mode and Anthropic structured outputs.

## What it produces

| Presentation with a native chart | Grounded, cited document |
| :---: | :---: |
| ![deck](docs/assets/deck.png) | ![document](docs/assets/document.png) |

| Infographic | Diagram |
| :---: | :---: |
| ![infographic](docs/assets/infographic.png) | ![diagram](docs/assets/diagram.png) |

## docloom studio

docloom studio is a free, local-first app built on the engine. You add sources to a notebook, ask questions that are answered with citations, and generate editable decks, documents, spreadsheets, diagrams, and infographics that export through docloom.

![studio](docs/assets/guides.png)

- Notebooks with your uploaded sources or agent web research
- Retrieval-grounded chat that cites its sources
- One-click guides: study guide, briefing, FAQ, timeline, and mind map
- D2 diagrams, and a brand kit applied to every export
- Runs on your machine; a local model works offline

```bash
# from docloom-studio/
pip install -e "../docloom[pdf]" && pip install -e ".[dev]"
cd web && npm install && npm run build
python -m docloom_studio.main        # http://127.0.0.1:8899
```

> [!NOTE]
> Set the generation model in Settings. A local Ollama model (qwen3.5 works well) runs fully offline; a hosted API key is optional.

## Repository

| Path | What it is |
| --- | --- |
| [`docloom/`](docloom/) | The render engine (pip installable, MIT) |
| [`docloom-studio/`](docloom-studio/) | The local-first studio app |
| [`examples/`](examples/) | Runnable samples for each feature, with rendered output |

## License

MIT.

<sub>Maintained by <b>krt</b>.</sub>

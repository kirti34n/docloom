# Examples

Install the engine from the repo root first (docloom is not on PyPI yet):

```bash
pip install -e "./docloom[pdf]"
```

## Detailed showcase

[`showcase.py`](showcase.py) builds one comprehensive document, a multi-section field report (blocks), a visual deck with charts, stats, and a table (slides), and a workbook (sheets), and renders it to every format.

```bash
python examples/showcase.py
```

The rendered output is committed under [`output/`](output/) so you can open it without running anything:

| File | What it is |
| --- | --- |
| [`output/showcase.pptx`](output/showcase.pptx) | A 13-slide deck with native charts, a stats row, and a table |
| [`output/showcase.pdf`](output/showcase.pdf) | The full multi-page report, typeset by Typst |
| [`output/showcase.docx`](output/showcase.docx) | The same report as a Word document |
| [`output/showcase.html`](output/showcase.html) | A self-contained web page |
| [`output/showcase.xlsx`](output/showcase.xlsx) | A workbook with formulas |

The report prose was drafted in docloom studio; the figures, charts, tables, and deck are assembled around it in `showcase.py`. Edit that file to change the content.

## Minimal, per-feature scripts

Short scripts that each show one part of the API:

| Script | Shows |
| --- | --- |
| [`presentation.py`](presentation.py) | A deck: title/section/content, stats, a native chart, a table |
| [`document.py`](document.py) | A report: headings, rich text with a citation, a callout, a table |
| [`spreadsheet.py`](spreadsheet.py) | A workbook with real formulas |
| [`all_formats.py`](all_formats.py) | One document rendered to all five formats |
| [`branded_theme.py`](branded_theme.py) | A custom `Theme` (six colors plus two fonts) |

## Diagrams (docloom studio)

Diagrams render in the studio with the D2 engine, offline. Paste a sample into the Diagram editor:

- [`diagrams/architecture.d2`](diagrams/architecture.d2)
- [`diagrams/mindmap.d2`](diagrams/mindmap.d2)

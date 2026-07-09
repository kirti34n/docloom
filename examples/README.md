# Examples

Runnable samples for each piece of docloom. Install the engine first:

```bash
pip install "docloom[pdf]"
```

Then run any sample from the repo root:

| Sample | What it shows | Output |
| --- | --- | --- |
| [`presentation.py`](presentation.py) | A deck: title/section/content, stats, a native chart, a table | `presentation.pptx`, `.pdf` |
| [`document.py`](document.py) | A report: headings, rich text with a citation, a callout, a table | `report.docx`, `.pdf`, `.html` |
| [`spreadsheet.py`](spreadsheet.py) | A workbook with real formulas | `budget.xlsx` |
| [`all_formats.py`](all_formats.py) | One IR rendered to all five formats | `overview.{pptx,docx,xlsx,pdf,html}` |
| [`branded_theme.py`](branded_theme.py) | A custom `Theme` (six colors + two fonts) applied to a deck | `branded.pptx` |

```bash
python examples/presentation.py
python examples/all_formats.py
```

## Diagrams (docloom studio)

Diagrams render in the studio with the D2 engine, offline. Paste a sample into
the Diagram editor:

- [`diagrams/architecture.d2`](diagrams/architecture.d2) - a system architecture
- [`diagrams/mindmap.d2`](diagrams/mindmap.d2) - a mind map

## Studio guides

Inside a notebook with sources, the one-click **Guides** (Study guide, Briefing,
FAQ, Timeline, Mind map) generate grounded, cited documents through the same
pipeline. No code needed.

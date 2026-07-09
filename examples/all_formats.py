"""Sample: one IR, every format. A single Document carries slides (deck), blocks
(report), and a sheet (workbook), and renders to all five formats.

    python examples/all_formats.py   ->  overview.{pptx,docx,xlsx,pdf,html}
"""
from docloom import Document, render

doc = Document(
    title="docloom overview",
    subtitle="One schema in, real documents out",
    slides=[
        {"layout": "title", "title": "docloom"},
        {"layout": "content", "title": "One IR, five formats", "blocks": [
            {"type": "stats", "items": [
                {"label": "output formats", "value": "5"},
                {"label": "block types", "value": "13"},
                {"label": "layout code from the LLM", "value": "0"},
            ]},
        ]},
    ],
    blocks=[
        {"type": "paragraph", "text": "The model emits a validated JSON IR; "
                                      "deterministic renderers do the rest."},
        {"type": "bullets", "items": [
            {"text": "PPTX with native editable charts"},
            {"text": "DOCX, PDF, and self-contained HTML"},
            {"text": "XLSX with real formulas"},
        ]},
    ],
    sheets=[
        {"name": "Formats",
         "columns": [{"header": "Format"}, {"header": "Engine"}],
         "rows": [["PPTX", "python-pptx"], ["DOCX", "python-docx"],
                  ["XLSX", "xlsxwriter"], ["PDF", "typst"], ["HTML", "built-in"]]},
    ],
)

if __name__ == "__main__":
    for fmt in ("pptx", "docx", "xlsx", "pdf", "html"):
        print(render(doc, fmt, f"overview.{fmt}"))

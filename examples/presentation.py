"""Sample: a presentation (PPTX) from a docloom IR.

Shows title / section / content layouts with stats, a native chart, and a table.
    python examples/presentation.py   ->  presentation.pptx + presentation.pdf
"""
from docloom import Document, Theme, render

aurora = Theme(primary="#4F46E5", accent="#0D9488", surface="#F4F6FB",
               font_heading="Segoe UI", font_body="Segoe UI")

doc = Document(
    title="Q3 Business Review",
    subtitle="Results and outlook",
    authors=["Analytics team"],
    date="2026",
    slides=[
        {"layout": "title", "title": "Q3 Business Review", "subtitle": "Results and outlook"},
        {"layout": "section", "title": "Where we landed"},
        {"layout": "content", "title": "By the numbers", "blocks": [
            {"type": "stats", "items": [
                {"label": "Revenue", "value": "$4.2M", "delta": "+24% YoY"},
                {"label": "Net revenue retention", "value": "124%"},
                {"label": "Churn", "value": "1.1%", "delta": "-0.4pt"},
            ]},
            {"type": "bullets", "items": [
                {"text": "Enterprise pipeline doubled quarter over quarter"},
                {"text": "Two new integrations shipped ahead of schedule"},
            ]},
        ]},
        {"layout": "content", "title": "Revenue by quarter", "blocks": [
            {"type": "chart", "chart": "column", "title": "Revenue ($M)",
             "labels": ["Q1", "Q2", "Q3", "Q4 (proj.)"],
             "series": [{"name": "Revenue", "values": [2.8, 3.4, 4.2, 5.0]}]},
        ]},
        {"layout": "content", "title": "Regional split", "blocks": [
            {"type": "table",
             "header": ["Region", "Revenue", "Growth"],
             "rows": [["Americas", "$2.4M", "+21%"],
                      ["EMEA", "$1.2M", "+29%"],
                      ["APAC", "$0.6M", "+33%"]],
             "caption": "Q3 revenue by region"},
        ]},
    ],
)

if __name__ == "__main__":
    print(render(doc, "pptx", "presentation.pptx", aurora))
    print(render(doc, "pdf", "presentation.pdf", aurora))

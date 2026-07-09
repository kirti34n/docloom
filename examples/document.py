"""Sample: a written report (DOCX / PDF / HTML) from a docloom IR.

Shows headings, rich text with a citation, bullets, a callout, and a table.
    python examples/document.py   ->  report.docx + report.pdf + report.html
"""
from docloom import Document, render

doc = Document(
    title="The State of Async Work",
    subtitle="A 2026 field guide",
    authors=["Research desk"],
    date="2026",
    sources=[
        {"id": "gartner", "title": "Future of Work 2026", "publisher": "Gartner",
         "url": "https://example.com/future-of-work"},
    ],
    blocks=[
        {"type": "paragraph", "text": [
            {"text": "Remote-first hiring has "},
            {"text": "doubled since 2020", "bold": True},
            {"text": ", and written-first culture is now the default for "
                     "distributed teams", "cite": "gartner"},
            {"text": "."}]},
        {"type": "heading", "level": 2, "text": "Why it works"},
        {"type": "bullets", "items": [
            {"text": "Deep-work hours rise when meetings fall"},
            {"text": "Decisions become searchable, not ephemeral"},
            {"text": "Time zones stop being a constraint"},
        ]},
        {"type": "callout", "style": "success",
         "text": "Teams that default to async report 24% more deep-work hours."},
        {"type": "heading", "level": 2, "text": "Adoption"},
        {"type": "table",
         "header": ["Year", "Async-first teams", "Change"],
         "rows": [["2024", "31%", "-"], ["2025", "44%", "+13pts"],
                  ["2026", "58%", "+14pts"]],
         "caption": "Share of surveyed engineering orgs"},
    ],
)

if __name__ == "__main__":
    for fmt in ("docx", "pdf", "html"):
        print(render(doc, fmt, f"report.{fmt}"))

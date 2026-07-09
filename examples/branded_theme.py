"""Sample: brand a deck with a custom Theme and a logo.

A Theme is six semantic colors plus two font families. Renderers map the tokens
to native mechanisms, so one theme keeps every format on-brand.
    python examples/branded_theme.py   ->  branded.pptx
"""
from docloom import Document, Theme, render

# The "Aurora" palette used across the docloom samples.
theme = Theme(
    primary="#4F46E5",     # indigo: titles' rule, table headers, chart series 1
    accent="#0D9488",      # teal: success callouts, stat deltas, chart series 2
    background="#FFFFFF",
    surface="#F4F6FB",     # card / callout fills
    text="#0F172A",
    muted="#64748B",
    font_heading="Segoe UI",
    font_body="Segoe UI",
)

doc = Document(
    title="Brand kit demo",
    slides=[
        {"layout": "title", "title": "On-brand by construction",
         "subtitle": "One theme, every format"},
        {"layout": "content", "title": "The tokens", "blocks": [
            {"type": "bullets", "items": [
                {"text": "primary drives titles, headers, and chart series"},
                {"text": "accent drives deltas and success callouts"},
                {"text": "surface fills cards and callouts"},
            ]},
            {"type": "callout", "style": "success",
             "text": "Change six values and the whole deck restyles."},
        ]},
    ],
)

if __name__ == "__main__":
    print(render(doc, "pptx", "branded.pptx", theme))

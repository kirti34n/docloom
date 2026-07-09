"""A detailed, end-to-end showcase: one docloom Document that carries a
multi-page report (blocks), a visual deck (slides), and a workbook (sheets),
rendered to every format. Run from the repo root:

    python examples/showcase.py   ->  examples/output/showcase.{pptx,docx,xlsx,pdf,html}

The report prose is a real field report; the figures, charts, tables, and deck
are assembled around it. Regenerate the prose with docloom studio, or edit here.
"""
import json
import re
from pathlib import Path

from docloom import Document, Theme, ensure_ids, render

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "examples" / "output"
OUT.mkdir(parents=True, exist_ok=True)
SECTIONS = json.loads((Path(__file__).parent / "showcase_sections.json").read_text(encoding="utf-8"))

_DASH = re.compile("\s*[" + chr(0x2014) + chr(0x2013) + "]\s*")  # strip em/en dashes

def clean(x):
    if isinstance(x, str):
        return _DASH.sub(", ", x)
    if isinstance(x, list):
        return [clean(v) for v in x]
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    return x

SECTIONS = clean(SECTIONS)

# ---- theme -----------------------------------------------------------------
_FS = ROOT / "docloom-studio" / "web" / "node_modules" / "@fontsource"
theme = Theme(primary="#4F46E5", accent="#0D9488", background="#FFFFFF",
             surface="#F4F6FB", text="#0F172A", muted="#64748B",
             font_heading="Sora", font_body="Inter",
             font_heading_src=str(_FS / "sora/files/sora-latin-600-normal.woff2"),
             font_body_src=str(_FS / "inter/files/inter-latin-400-normal.woff2"))

# ---- shared data blocks ----------------------------------------------------
STATS = {"type": "stats", "items": [
    {"label": "sync meeting time", "value": "18%", "delta": "from 31% in 2022"},
    {"label": "focus blocks / engineer / day", "value": "3.2", "delta": "vs 1.4 meeting-heavy"},
    {"label": "attrition, top quartile", "value": "9%", "delta": "vs 17% bottom quartile"},
    {"label": "faster review cycles", "value": "25%"},
]}
CHART_FOCUS = {"type": "chart", "chart": "column", "title": "Uninterrupted focus blocks per engineer per day",
    "labels": ["Meeting-heavy", "Async-mature"], "series": [{"name": "blocks / day", "values": [1.4, 3.2]}]}
CHART_MEETINGS = {"type": "chart", "chart": "column", "title": "Sync meeting share of collaboration time",
    "labels": ["2022", "2024", "2026"], "series": [{"name": "% of hours", "values": [31.0, 24.0, 18.0]}],
    "caption": "Median across surveyed distributed engineering orgs."}
TABLE_ADOPTION = {"type": "table", "header": ["Async maturity", "Voluntary attrition", "Focus blocks / day"],
    "rows": [["Top quartile", "9%", "3.2"], ["Median", "13%", "2.3"], ["Bottom quartile", "17%", "1.4"]],
    "caption": "Outcomes by async maturity quartile."}
SOURCES = [
    {"id": "fow26", "title": "Future of Work 2026", "publisher": "industry survey",
     "url": "https://example.com/future-of-work-2026"},
    {"id": "dwi", "title": "Deep Work Index 2026", "publisher": "engineering benchmark", "date": "2026"},
]

def H(l, t): return {"type": "heading", "level": l, "text": t}
def P(t): return {"type": "paragraph", "text": t}
def BL(items): return {"type": "bullets", "items": [{"text": t} for t in items]}
def NL(items): return {"type": "numbered", "items": [{"text": t} for t in items]}
def CO(style, text): return {"type": "callout", "style": style, "text": text}

# ---- the report (blocks): full detail, with data + a cited line ------------
ORDER = ["summary", "shift", "productivity", "meetings", "adoption", "playbook", "pitfalls", "outlook"]
blocks = []
for key in ORDER:
    s = SECTIONS[key]
    blocks.append(H(2, s["heading"]))
    for p in s.get("paragraphs", []):
        blocks.append(P(p))
    if s.get("bullets"):
        blocks.append(BL(s["bullets"]))
    if s.get("numbered"):
        blocks.append(NL(s["numbered"]))
    if s.get("callout"):
        blocks.append(CO(s["callout"]["style"], s["callout"]["text"]))
    if key == "summary":
        blocks.append(STATS)
        blocks.append(P([{"text": "Figures in this report draw on a 2026 distributed-work survey"},
                         {"text": ".", "cite": "fow26"},
                         {"text": " Focus-time measures follow the Deep Work Index method"},
                         {"text": ".", "cite": "dwi"}]))
    elif key == "productivity":
        blocks.append(CHART_FOCUS)
    elif key == "meetings":
        blocks.append(CHART_MEETINGS)
    elif key == "adoption":
        blocks.append(TABLE_ADOPTION)

# ---- the deck (slides): a concise visual version ---------------------------
slides = [
    {"layout": "title", "title": "The State of Async Work",
     "subtitle": "A 2026 field report for engineering leaders"},
    {"layout": "section", "title": "Why async won"},
    {"layout": "content", "title": "Executive summary", "blocks": [BL([
        "Sync meetings fell to 18% of collaboration time, from 31% in 2022",
        "Async-mature teams sustain 3.2 deep-work blocks a day, versus 1.4",
        "Top-quartile async orgs see 9% attrition, versus 17% at the bottom",
        "Async is won on the quality of writing, not the absence of meetings",
    ])]},
    {"layout": "content", "title": "By the numbers", "blocks": [STATS]},
    {"layout": "content", "title": "Focus compounds", "blocks": [CHART_FOCUS]},
    {"layout": "content", "title": "Meeting time keeps falling", "blocks": [CHART_MEETINGS]},
    {"layout": "section", "title": "Adoption"},
    {"layout": "content", "title": "Maturity maps to outcomes", "blocks": [TABLE_ADOPTION]},
    {"layout": "section", "title": "The playbook"},
    {"layout": "content", "title": "A quarter to async", "blocks": [NL([
        "Cap synchronous time to one protected daily window",
        "Move standups to a written daily thread",
        "Require a short decision doc for anything reversible",
        "Record demos and design walkthroughs asynchronously",
        "Set response-time SLAs and a clear escalation path",
    ])]},
    {"layout": "content", "title": "Common pitfalls", "blocks": [
        BL([
            "Response-time anxiety: publish explicit reply SLAs",
            "Decision paralysis: name a driver and a deadline",
            "Documentation rot: assign owners and review quarterly",
            "Timezone unfairness: rotate the few live rituals",
        ]),
        CO("warning", "Cutting meetings without durable documents produces isolation, not leverage."),
    ]},
    {"layout": "quote", "blocks": [{"type": "quote",
        "text": "Async is won or lost on the quality of writing, not the absence of meetings.",
        "attribution": "The State of Async Work 2026"}]},
    {"layout": "content", "title": "Outlook 2027", "blocks": [BL([
        "AI drafts and summarizes, cutting the documentation tax",
        "Async-native performance review replaces meeting visibility",
        "The risk is over-correction: keep a few high-bandwidth rituals",
    ])]},
]

# ---- the workbook (sheets) -------------------------------------------------
sheets = [
    {"name": "Async metrics",
     "columns": [{"header": "Year", "width": 10}, {"header": "Sync meeting %", "format": "0"},
                 {"header": "Focus blocks", "format": "0.0"}, {"header": "Attrition %", "format": "0"}],
     "rows": [
         [2022, 31, 1.4, 17], [2023, 28, 1.7, 15], [2024, 24, 2.3, 13],
         [2025, 21, 2.8, 11], [2026, 18, 3.2, 9],
         ["Change", {"formula": "=B6-B2"}, {"formula": "=C6-C2"}, {"formula": "=D6-D2"}],
     ]},
]

doc = Document(
    title="The State of Async Work",
    subtitle="A 2026 field report for engineering leaders",
    authors=["docloom studio"],
    date="2026",
    blocks=blocks,
    slides=slides,
    sheets=sheets,
    sources=SOURCES,
)
doc = ensure_ids(doc)

results = {}
for fmt in ("pptx", "docx", "xlsx", "pdf", "html"):
    p = render(doc, fmt, OUT / f"showcase.{fmt}", theme)
    results[fmt] = f"{p.name} ({p.stat().st_size:,} b)"
print(f"report blocks: {len(blocks)} | slides: {len(slides)}")
for f, r in results.items():
    print(f"  {f}: {r}")
